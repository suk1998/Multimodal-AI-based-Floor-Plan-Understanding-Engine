"""Public upload workflow contracts using the existing real PDFs/API."""
import hashlib,json,unittest
from pathlib import Path
from fastapi.testclient import TestClient
from backend.public_app import COOKIE,ROOT,Workspaces,create_app
from phase1_pdf import render_page

class PublicSiteTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.ref=(ROOT/'source_pdfs/자이사업승인도서.pdf').read_bytes();cls.target=(ROOT/'source_pdfs/자이실시도서.pdf').read_bytes()
    def setUp(self):
        self.app=create_app();self.client=TestClient(self.app);self.origin={'Origin':'http://testserver'}
    def tearDown(self):self.client.close()
    def start(self,client=None):
        c=client or self.client;r=c.get('/api/workspace');self.assertEqual(r.status_code,200);return r
    def upload(self,data=None,name='drawing.pdf',client=None):
        return (client or self.client).post('/api/documents',files={'file':(name,self.ref if data is None else data,'application/pdf')},headers=self.origin)
    def test_initial_page_is_blank_no_auth_no_fixed_dataset(self):
        self.assertEqual(self.client.get('/').status_code,200);health=self.client.get('/api/health').json()
        self.assertEqual(health['mode'],'public_pdf_workspace');self.assertFalse(health['fixed_dataset']);self.assertFalse(self.app.state.workspaces.items)
        self.assertTrue(health['capabilities']['architectural_observations'])
        self.assertTrue(health['capabilities']['architectural_geometry'])
        self.assertTrue(health['capabilities']['scoped_spatial_alignment'])
        for key in ['structural_comparison_review','change_detection','confirmed_change_export']:self.assertTrue(health['capabilities'][key])
        self.assertFalse(health['capabilities']['VLM'])
        for path in ['/api/fpue/catalog','/api/fpue/review','/api/fpue/images/Ref','/api/fpue/images/Target','/api/session','/api/auth/config']:self.assertEqual(self.client.get(path).status_code,404)
    def test_workspace_is_anonymous_HttpOnly_cookie_not_an_account(self):
        r=self.start();self.assertEqual(set(r.json()),{'status','storage','idle_expiry_seconds'});cookie=r.headers['set-cookie'].lower()
        self.assertIn('httponly',cookie);self.assertIn('samesite=strict',cookie);self.assertNotIn('secure;',cookie)
        self.assertEqual(len(self.app.state.workspaces.items),1);self.start();self.assertEqual(len(self.app.state.workspaces.items),1)
    def test_library_lists_only_owned_documents_and_reuses_content(self):
        self.start()
        first=self.upload().json()
        for _ in range(9):
            self.assertEqual(self.upload().json()['id'],first['id'])
        self.assertEqual(self.client.get('/api/documents').json()['usage']['documents'],1)
        with TestClient(self.app) as other:
            self.start(other)
            self.assertEqual(other.get('/api/documents').json()['documents'],[])
            response=self.upload(client=other).json()
            self.assertNotEqual(response['id'],first['id'])
        self.assertEqual(self.client.get('/api/documents').json()['documents'][0]['id'],first['id'])

    def test_HTTPS_cookie_is_secure(self):
        with TestClient(self.app,base_url='https://fpue-test.trycloudflare.com') as c:self.assertIn('secure',self.start(c).headers['set-cookie'].lower())
    def test_real_upload_metadata_and_independent_document_ids(self):
        self.start();a=self.upload().json();b=self.upload(self.target).json();self.assertNotEqual(a['id'],b['id']);self.assertEqual((a['page_count'],b['page_count']),(6,6))
        self.assertEqual(a['sha256'],hashlib.sha256(self.ref).hexdigest());self.assertEqual(b['sha256'],hashlib.sha256(self.target).hexdigest())
        self.assertEqual(self.client.get('/api/documents/'+a['id']).json(),a)
    def test_rendering_uses_uploaded_PDF_page_and_native_headers(self):
        self.start()
        for data,page in [(self.ref,4),(self.target,5)]:
            a=self.upload(data).json();r=self.client.get(f"/api/documents/{a['id']}/pages/{page}/image?zoom=1")
            self.assertEqual(r.status_code,200);self.assertEqual(r.content,render_page(data,page,1).png)
            self.assertEqual(r.headers['x-source-sha256'],a['sha256']);self.assertEqual(r.headers['x-page-number'],str(page));self.assertEqual(r.headers['x-coordinate-system'],'image_native_pixels')
    def test_other_browser_cannot_read_or_delete_uploaded_document(self):
        self.start();a=self.upload().json();url='/api/documents/'+a['id']
        with TestClient(self.app) as other:
            self.start(other);self.assertEqual(other.get(url).status_code,404);self.assertEqual(other.get(url+'/pages/4/image').status_code,404)
            self.assertEqual(other.delete(url,headers=self.origin).status_code,204)
        self.assertEqual(self.client.get(url).status_code,200)
    def test_missing_or_forged_workspace_never_sees_existing_files(self):
        self.start();a=self.upload().json();self.client.cookies.clear()
        self.assertEqual(self.client.get('/api/documents/'+a['id']).status_code,404);self.assertEqual(self.upload().status_code,409)
        self.client.cookies.set(COOKIE,'forged');self.assertEqual(self.client.get('/api/documents/'+a['id']).status_code,404)
        self.start();self.assertEqual(self.client.get('/api/documents/'+a['id']).status_code,404)
    def test_expired_workspace_is_not_reassigned_to_another_browser(self):
        now=[0.];spaces=Workspaces(ttl=2,clock=lambda:now[0]);app=create_app(workspaces=spaces)
        with TestClient(app) as c:
            self.start(c);a=self.upload(client=c).json();now[0]=3
            self.assertEqual(c.get('/api/documents/'+a['id']).status_code,404);self.start(c);self.assertEqual(c.get('/api/documents/'+a['id']).status_code,404)
    def test_full_workspace_registry_does_not_evict_active_work(self):
        app=create_app(workspaces=Workspaces(maximum=1))
        with TestClient(app) as a,TestClient(app) as b:
            self.start(a);d=self.upload(client=a).json();self.assertEqual(b.get('/api/workspace').status_code,503);self.assertEqual(a.get('/api/documents/'+d['id']).status_code,200)
    def test_write_requests_require_same_origin(self):
        self.start();self.assertEqual(self.client.post('/api/documents',files={'file':('x.pdf',self.ref)}).status_code,403)
        self.assertEqual(self.client.post('/api/documents',files={'file':('x.pdf',self.ref)},headers={'Origin':'https://evil.invalid'}).status_code,403)
    def test_large_or_invalid_uploads_preserve_existing_documents(self):
        self.start();a=self.upload().json();self.assertEqual(self.upload(b'not a PDF').status_code,422)
        r=self.client.post('/api/documents',content=b'x',headers={**self.origin,'Content-Length':str(52*1024*1024)});self.assertEqual(r.status_code,413)
        self.assertEqual(self.client.get('/api/documents/'+a['id']).status_code,200)
    def test_page_and_zoom_errors_reuse_existing_API_validation(self):
        self.start();a=self.upload().json();base='/api/documents/'+a['id']+'/pages/'
        for path in ['0/image','-1/image','7/image','4/image?zoom=nan','4/image?zoom=0','4/image?zoom=5']:self.assertEqual(self.client.get(base+path).status_code,422,path)
    def test_only_filename_metadata_is_used_not_server_filesystem_paths(self):
        self.start();a=self.upload(name='../../private/source.pdf').json();self.assertEqual(a['filename'],'source.pdf');self.assertNotIn('/home/',json.dumps(a))
    def test_private_files_and_execution_APIs_are_not_routed(self):
        self.start()
        for path in ['/var/fpue/local_login.txt','/var/fpue/local_auth.json','/var/fpue/reviews.sqlite3','/.env','/backend/app.py','/source_pdfs/자이사업승인도서.pdf','/poc/models/anything','/public_site/page4/Ref.png','/api/alignments','/api/structural-anchors','/api/fpue/history','/api/fpue/export','/advanced/review_data.js','/docs','/openapi.json']:
            r=self.client.get(path);self.assertEqual(r.status_code,404,path);self.assertNotIn('/home/',r.text)
        for method in ['POST','PUT','DELETE']:
            for path in ['/api/alignments','/api/fpue/review','/api/auth/login']:self.assertEqual(self.client.request(method,path,json={},headers=self.origin).status_code,404)
    def test_expected_tunnel_host_and_no_static_project_mount(self):
        self.assertEqual(self.client.get('/api/health',headers={'Host':'fpue-example.trycloudflare.com'}).status_code,200);self.assertEqual(self.client.get('/api/health',headers={'Host':'unapproved.invalid'}).status_code,400)
        self.assertEqual(self.client.get('/public_site/page4/release_manifest.json').status_code,404)
        self.assertIn("frame-src 'none'",self.client.get('/api/health').headers['content-security-policy'])
    def test_document_deletion_releases_only_its_workspace(self):
        self.start();a=self.upload().json();path='/api/documents/'+a['id'];self.client.get(path+'/pages/4/image');self.assertEqual(self.client.delete(path,headers=self.origin).status_code,204);self.assertEqual(self.client.get(path).status_code,404)

if __name__=='__main__':unittest.main()
