"""Authenticated persistence and deployment contracts; fixtures are not human GT."""
import copy,json,sqlite3,tempfile,time,unittest,uuid
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import jwt
import pymupdf
from cryptography.hazmat.primitives.asymmetric import rsa
from fastapi.testclient import TestClient

from backend.integrated_app import Catalog,create_app
from backend.web_auth import AuthConfig,password_record


class StudioAPITests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.catalog=Catalog();cls.private=rsa.generate_private_key(public_exponent=65537,key_size=2048)
        cls.other=rsa.generate_private_key(public_exponent=65537,key_size=2048)
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.folder=Path(self.temp.name)
        users=[{'username':name,'subject':'TEST_ONLY:'+name,'display':'TEST ONLY '+name,**password_record('test-password-'+name)} for name in ['alice','bob']]
        self.user_file=self.folder/'local_auth.json';self.user_file.write_text(json.dumps({'users':users}))
        self.config=AuthConfig(user_file=self.user_file)
        self.app=create_app(data_dir=self.folder,auth_config=self.config,catalog=self.catalog,instance_kind='TEST_ONLY');self.client=TestClient(self.app)
    def tearDown(self):self.client.close();self.temp.cleanup()
    def login(self,client=None,name='alice'):
        c=client or self.client;r=c.post('/api/auth/login',json={'username':name,'password':'test-password-'+name},headers={'Origin':'http://127.0.0.1:8020'});self.assertEqual(r.status_code,200);return r
    def packet(self):return copy.deepcopy(self.catalog.initial)
    def save(self,pkg=None,base=0,key=None,client=None):
        return (client or self.client).put('/api/fpue/review',json={'package':pkg or self.packet(),'base_revision':base,'save_id':key or str(uuid.uuid4())},headers={'Origin':'http://127.0.0.1:8020'})
    def test_unauthenticated_API_images_advanced_are_blocked(self):
        for path in ['/api/session','/api/fpue/catalog','/api/fpue/images/Target','/advanced/review.html','/advanced/review_data.js']:
            self.assertEqual(self.client.get(path).status_code,401,path)
    def test_spoofed_identity_header_does_not_authenticate(self):
        self.assertEqual(self.client.get('/api/session',headers={'Cf-Access-Authenticated-User-Email':'alice','Cf-Access-Jwt-Assertion':'fake'}).status_code,401)
    def test_origin_required_for_login_and_save(self):
        self.assertEqual(self.client.post('/api/auth/login',json={}).status_code,403);self.login()
        self.assertEqual(self.client.put('/api/fpue/review',json={},headers={'Origin':'https://evil.invalid'}).status_code,403)
    def test_bad_login_and_Unicode_username_return_401(self):
        for username in ['alice','없는사용자']:
            self.assertEqual(self.client.post('/api/auth/login',json={'username':username,'password':'wrong'},headers={'Origin':'http://127.0.0.1:8020'}).status_code,401)
    def test_cookie_is_HttpOnly_and_strict_and_logout_revokes(self):
        r=self.login();cookie=r.headers['set-cookie'].lower();self.assertIn('httponly',cookie);self.assertIn('samesite=strict',cookie)
        self.assertEqual(self.client.get('/api/session').json()['id'],'TEST_ONLY:alice')
        self.client.post('/api/auth/logout',headers={'Origin':'http://127.0.0.1:8020'});self.assertEqual(self.client.get('/api/session').status_code,401)
    def test_new_account_has_no_GT_and_originals_unchanged(self):
        self.login();loaded=self.client.get('/api/fpue/review').json();self.assertEqual(loaded['revision'],0);self.assertFalse(loaded['package']['gt_annotations'])
        self.assertTrue(all(r['review_status']=='UNREVIEWED' for r in loaded['package']['candidate_reviews'].values()))
    def test_save_is_append_only_and_records_authenticated_owner(self):
        self.login();first=self.save().json();p=self.packet();p['annotation_revision']='TEST_ONLY_revision_2';second=self.save(p,1).json()
        self.assertEqual((first['revision'],second['revision']),(1,2));self.assertEqual(first['saved_by'],'TEST_ONLY:alice')
        self.assertEqual(self.client.get('/api/fpue/history/1').json()['package']['annotation_revision'],'INITIAL_UNREVIEWED');self.assertEqual(len(self.client.get('/api/fpue/history').json()),2)
    def test_conflict_preserves_both_server_and_client_payload(self):
        self.login();self.save();p=self.packet();p['annotation_revision']='UNSAVED_TEST_ONLY';old=copy.deepcopy(p);r=self.save(p,0)
        self.assertEqual(r.status_code,409);self.assertEqual(p,old);self.assertEqual(self.client.get('/api/fpue/review').json()['revision'],1)
    def test_retry_with_same_save_id_does_not_duplicate(self):
        self.login();key=str(uuid.uuid4());a=self.save(key=key).json();b=self.save(key=key).json();self.assertEqual(a['revision'],b['revision']);self.assertEqual(len(self.client.get('/api/fpue/history').json()),1)
    def test_save_id_cannot_be_reused_for_different_content(self):
        self.login();key=str(uuid.uuid4());self.save(key=key);p=self.packet();p['annotation_revision']='different';self.assertEqual(self.save(p,1,key).status_code,409)
    def test_authenticated_users_have_separate_reviews_and_history(self):
        self.login();self.save();bob=TestClient(self.app);self.login(bob,'bob');self.assertEqual(bob.get('/api/fpue/review').json()['revision'],0);self.assertEqual(bob.get('/api/fpue/history/1').status_code,404);bob.close()
    def test_SQLite_restores_after_app_restart(self):
        r=self.login();self.save();token=r.cookies.get('fpue_session');app2=create_app(data_dir=self.folder,auth_config=self.config,catalog=self.catalog,instance_kind='TEST_ONLY')
        with TestClient(app2) as c:
            c.cookies.set('fpue_session',token);self.assertEqual(c.get('/api/fpue/review').json()['revision'],1)
    def test_changed_frame_or_synthetic_dataset_cannot_be_saved(self):
        self.login();p=self.packet();p['bindings']['Target']['extraction_zoom']=1;self.assertEqual(self.save(p).status_code,422)
        p=self.packet();p['dataset_kind']='SYNTHETIC_UI_TEST';self.assertEqual(self.save(p).status_code,422)
        self.assertEqual(self.client.get('/api/fpue/review').json()['revision'],0)
    def test_user_instance_rejects_automatic_test_annotations(self):
        app=create_app(data_dir=self.folder,auth_config=self.config,catalog=self.catalog,instance_kind='USER')
        with TestClient(app) as c:
            self.login(c);p=self.packet();p['review_instance_kind']='TEST_ONLY';self.assertEqual(self.save(p,client=c).status_code,422)
    def test_disk_failure_is_explicit_and_does_not_advance_revision(self):
        self.login();self.save()
        with patch.object(self.app.state.reviews,'save',side_effect=sqlite3.OperationalError('test simulated disk failure')):self.assertEqual(self.save(base=1).status_code,503)
        self.assertEqual(self.client.get('/api/fpue/review').json()['revision'],1)
    def test_evaluation_reuses_existing_GT_engine_without_invented_metrics(self):
        self.login();r=self.client.post('/api/fpue/evaluate',json={'revision':0},headers={'Origin':'http://127.0.0.1:8020'});self.assertEqual(r.status_code,200);self.assertEqual(r.json()['result']['status'],'NOT_EVALUATED');self.assertIsNone(r.json()['result']['global_metrics'])
    def test_stale_evaluation_revision_is_rejected(self):
        self.login();self.save();r=self.client.post('/api/fpue/evaluate',json={'revision':0},headers={'Origin':'http://127.0.0.1:8020'});self.assertEqual(r.status_code,409)
    def test_web_evaluation_rejects_unbounded_sampling(self):
        self.login();config=copy.deepcopy(self.catalog.config);config['sampling_step_px']=1e-12
        self.assertEqual(self.client.post('/api/fpue/evaluate',json={'revision':0,'config':config},headers={'Origin':'http://127.0.0.1:8020'}).status_code,422)
    def test_development_origin_is_explicit_and_loopback_only(self):
        cfg=AuthConfig(user_file=self.user_file,development_origin='http://127.0.0.1:5173')
        app=create_app(data_dir=self.folder,auth_config=cfg,catalog=self.catalog,instance_kind='TEST_ONLY')
        with TestClient(app) as c:self.assertEqual(c.post('/api/auth/login',json={'username':'alice','password':'test-password-alice'},headers={'Origin':'http://127.0.0.1:5173'}).status_code,200)
        with self.assertRaises(ValueError):create_app(data_dir=self.folder,auth_config=AuthConfig(user_file=self.user_file,development_origin='https://external.invalid'),catalog=self.catalog)
    def test_original_page_and_user_ROI_are_bound(self):
        self.login();r=self.client.get('/api/fpue/catalog').json();self.assertEqual(r['roi']['vertices'],self.catalog.roi['vertices']);p=self.client.get('/api/fpue/images/Target');self.assertEqual(p.content,(self.catalog.directory/'Target/original.png').read_bytes())
    def test_advanced_data_does_not_publish_another_users_GT(self):
        self.login();self.save();bob=TestClient(self.app);self.login(bob,'bob');text=bob.get('/advanced/review_data.js').text;data=json.loads(text.removeprefix('window.FPUE_GT_DATA=').removesuffix(';'));self.assertFalse(data['initial']['gt_annotations']);bob.close()
    def test_private_files_and_unknown_API_are_not_static_files(self):
        self.login()
        for p in ['/var/fpue/local_login.txt','/advanced/../models/model.safetensors','/api/unknown-route']:self.assertEqual(self.client.get(p).status_code,404,p)
    def test_legacy_PDF_upload_and_owner_isolation_are_preserved(self):
        self.login();pdf=pymupdf.open();pdf.new_page();raw=pdf.tobytes();r=self.client.post('/api/documents',files={'file':('TEST_ONLY.pdf',raw,'application/pdf')},headers={'Origin':'http://127.0.0.1:8020'});self.assertEqual(r.status_code,201,r.text);doc=r.json()['id']
        self.assertEqual(self.client.get('/api/documents/'+doc+'/pages/1/image?zoom=1').status_code,200)
        bob=TestClient(self.app);self.login(bob,'bob');self.assertEqual(bob.get('/api/documents/'+doc).status_code,404);bob.close()
    def access_client(self,claims=None,key=None):
        config=AuthConfig(mode='cloudflare_access',public_origin='https://fpue.example.invalid',access_issuer='https://test-team.cloudflareaccess.com',access_audience='TEST_AUDIENCE')
        jwks=SimpleNamespace(get_signing_key_from_jwt=lambda _:SimpleNamespace(key=self.private.public_key()))
        app=create_app(data_dir=self.folder,auth_config=config,catalog=self.catalog,jwks_client=jwks,instance_kind='TEST_ONLY')
        payload={'iss':config.access_issuer,'aud':config.access_audience,'sub':'SYNTHETIC_SUBJECT','iat':int(time.time())-1,'exp':int(time.time())+120,'email':'test-only@example.invalid'};payload.update(claims or {})
        token=jwt.encode(payload,key or self.private,algorithm='RS256');return TestClient(app),token
    def test_Access_requires_signature_issuer_audience_and_expiry(self):
        for claims,key in [({},None),({'aud':'WRONG'},None),({'iss':'https://wrong.invalid'},None),({'exp':int(time.time())-20},None),({},self.other)]:
            c,token=self.access_client(claims,key);r=c.get('/api/session',headers={'Cf-Access-Jwt-Assertion':token,'Cf-Access-Authenticated-User-Email':'SPOOFED'})
            self.assertEqual(r.status_code,200 if not claims and key is None else 401);c.close()
    def test_external_origin_cannot_enable_local_password_mode(self):
        with self.assertRaises(ValueError):create_app(data_dir=self.folder,auth_config=AuthConfig(user_file=self.user_file,public_origin='https://public.invalid'),catalog=self.catalog)
    def test_missing_Access_config_fails_closed(self):
        with self.assertRaises(ValueError):create_app(data_dir=self.folder,auth_config=AuthConfig(mode='cloudflare_access',public_origin='https://public.invalid'),catalog=self.catalog)

if __name__=='__main__':unittest.main(verbosity=2)
