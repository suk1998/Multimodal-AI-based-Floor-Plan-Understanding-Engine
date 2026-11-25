"""New upload adapter contracts; preserved PoC synthetic tests run separately."""
import copy,json,time,unittest
from unittest.mock import patch
from fastapi.testclient import TestClient
from phase1_pdf import render_page
from backend.test_observations import fixture,request_for,empty_ocr
from backend.architectural_observations import analyze,validate_request
from backend.architectural_geometry import analyze_geometry,validate_parent
from backend.observation_jobs import ObservationJobs
from backend.public_app import create_app


def prepare(data=None,roi=None,rotation=0):
    data=data or fixture(rotation=rotation);im,request=request_for(data,roi=roi);binding=validate_request(request,im);binding['document_id']='TEST_ONLY_document';parent=analyze(data,im.png,binding,empty_ocr)
    return data,im,binding,parent

class GeometryAdapterTests(unittest.TestCase):
    def test_original_observations_are_not_mutated_and_all_claims_remain_proposed(self):
        data,im,b,p=prepare();original=json.dumps(p,sort_keys=True);r=analyze_geometry(data,im.png,b,p)
        self.assertEqual(original,json.dumps(p,sort_keys=True));self.assertTrue(r['entities']);self.assertTrue(all(e['claim_status']=='PROPOSED' for e in r['entities']));self.assertEqual(r['claims'],[])
        self.assertFalse(r['policy']['alignment_performed']);self.assertFalse(r['policy']['code_mapping_performed']);self.assertTrue(all(e['geometry_properties']['physical_endpoints_status']=='UNKNOWN' for e in r['entities']))
    def test_capped_display_lines_do_not_remove_geometry_input(self):
        data,im,b,p=prepare();r1=analyze_geometry(data,im.png,b,p);p['observations']=[o for o in p['observations'] if o['observation_type']=='TextRegion'];p['coverage']['omitted_due_to_limits']['PdfLine']=999
        r2=analyze_geometry(data,im.png,b,p)
        self.assertEqual(r1['audit']['candidate_counts'],r2['audit']['candidate_counts']);self.assertEqual(r1['coverage']['source_counts'],r2['coverage']['source_counts']);self.assertFalse(r2['coverage']['parent_display_caps_used_for_geometry'])
    def test_new_PDF_or_ROI_or_frame_cannot_reuse_previous_observations(self):
        data,im,b,p=prepare()
        for key in ['source_sha256','frame_id','document_id','core_bbox_native','context_bbox_native']:
            bad=copy.deepcopy(b);bad[key]='other'
            with self.assertRaises(ValueError):validate_parent(bad,p)
        with self.assertRaises(ValueError):analyze_geometry(b'not the upload',im.png,b,p)
    def test_every_entity_and_derived_observation_evidence_reference_resolves(self):
        data,im,b,p=prepare();r=analyze_geometry(data,im.png,b,p);obs={o['observation_id'] for o in r['observations']};ev={e['evidence_id'] for e in r['evidence']}
        for e in r['entities']:self.assertTrue(set(e['observation_refs'])<=obs);self.assertTrue(set(e['evidence_refs'])<=ev);self.assertTrue(e['observation_refs'])
        for evidence in r['evidence']:self.assertFalse(evidence['unresolved_source_ids'])
        for o in r['observations']:self.assertTrue(set(o['source_evidence'].get('observation_refs',[]))<=obs)
    def test_rotated_native_geometry_uses_same_coordinate_contract(self):
        for rotation in [0,90,180,270]:
            data,im,b,p=prepare(rotation=rotation);r=analyze_geometry(data,im.png,b,p);self.assertTrue(r['frame_registry']['rerender_image_sha256_matches']);self.assertTrue(r['entities'])
            for e in r['entities']:
                x0,y0,x1,y1=e['geometry_native']['bbox_native'];self.assertGreaterEqual(x0,0);self.assertLessEqual(x1,im.width);self.assertGreaterEqual(y0,0);self.assertLessEqual(y1,im.height)
    def test_context_cut_is_not_physical_wall_end_and_gap_filling_is_forbidden(self):
        data,im,b,p=prepare(roi=[100,90,210,160]);r=analyze_geometry(data,im.png,b,p)
        self.assertTrue(any(e['boundary_truncated'] for e in r['entities']))
        for e in r['entities']:
            self.assertEqual(e['boundary_details']['physical_endpoints_status'],'UNKNOWN')
            if e['element_type']=='Wall':self.assertFalse(e['geometry_properties']['physical_gaps_filled'])
    def test_no_text_model_reexecution_and_OCR_failure_remains_incomplete(self):
        data,im,b,p=prepare();p['components']['OCR']['status']='FAILED'
        with patch('backend.architectural_observations.existing_ocr',side_effect=AssertionError('No rerun')):r=analyze_geometry(data,im.png,b,p)
        self.assertEqual(r['status'],'PARTIAL');self.assertIn('TEXT_COVERAGE_INCOMPLETE',r['coverage']['reasons'])
    def test_roles_are_independent_and_no_other_frame_is_injected(self):
        data,im,b,p=prepare();b['source_role']='Ref';b['frame_id']=b['frame_id'].replace('Target:','Ref:');p['source_role']='Ref';p['frame_id']=b['frame_id'];p['source_binding']=copy.deepcopy(b)
        for o in p['observations']:o['source_role']='Ref';o['frame_id']=b['frame_id']
        r=analyze_geometry(data,im.png,b,p);self.assertTrue(all(e['source_role']=='Ref' and e['frame_id']==b['frame_id'] for e in r['entities']));self.assertNotIn('Target:',json.dumps(r['entities']))
    def test_context_only_candidate_is_not_reported_as_crossing_core_boundary(self):
        data,im,b,p=prepare(roi=[200,100,340,260]);r=analyze_geometry(data,im.png,b,p)
        outside=[e for e in r['entities'] if not e['roi_intersection']];self.assertTrue(outside)
        self.assertTrue(all(not e['boundary_details']['core_boundary_crossed'] for e in outside))
    def test_compound_pdf_curves_omitted_by_legacy_fitter_are_explicit_in_coverage(self):
        import pymupdf
        doc=pymupdf.open(stream=fixture(),filetype='pdf');doc[0].draw_circle((130,110),15);data=doc.tobytes();doc.close()
        data,im,b,p=prepare(data=data);r=analyze_geometry(data,im.png,b,p)
        self.assertGreater(r['coverage']['curve_inspection_scope']['compound_curve_primitives_not_interpreted'],0)
        self.assertFalse(r['coverage']['context_geometry_input_complete']);self.assertEqual(r['status'],'PARTIAL')
    def test_scanned_pdf_uses_real_raster_geometry_without_vector_or_fake_arcs(self):
        import cv2,numpy as np,pymupdf
        gray=np.full((190,230,3),255,np.uint8)
        for y in [35,47,130,142]:cv2.line(gray,(20,y),(210,y),(0,0,0),2)
        for x in [80,86]:cv2.line(gray,(x,47),(x,130),(0,0,0),1)
        doc=pymupdf.open();page=doc.new_page(width=230,height=190);page.insert_image(page.rect,stream=cv2.imencode('.png',gray)[1].tobytes());data=doc.tobytes();doc.close()
        data,im,b,p=prepare(data=data);r=analyze_geometry(data,im.png,b,p)
        self.assertEqual(r['coverage']['source_counts']['pdf_segments_context'],0);self.assertGreater(r['coverage']['source_counts']['raster_segments_context'],0);self.assertTrue(r['entities']);self.assertIn('RASTER_ONLY_INPUT_NO_SWING_ARC_ESTIMATOR_IN_PRESERVED_ENGINE',r['coverage']['reasons'])
    def test_work_budget_failure_never_silently_runs_on_first_n_lines(self):
        data,im,b,p=prepare()
        # Existing source merging remains unchanged; this tests adapter coverage.
        with patch('backend.architectural_geometry.pair_boundaries',return_value=([{}]*12001,{})):
            r=analyze_geometry(data,im.png,b,p)
        self.assertTrue(all(e['element_type']=='Unknown' and e['geometry_properties']['classification']=='UNKNOWN_DIAGONAL_STRUCTURE' for e in r['entities']));self.assertFalse(r['coverage']['context_geometry_input_complete']);self.assertIn('ENGINE_WORK_BUDGET_EXCEEDED_NO_TRUNCATED_INFERENCE_USED',r['coverage']['reasons'])

class GeometryAPIContracts(unittest.TestCase):
    def setUp(self):
        self.jobs=ObservationJobs(runner=lambda pdf,png,b:analyze(pdf,png,b,empty_ocr),geometry_runner=analyze_geometry);self.app=create_app(observation_jobs=self.jobs);self.client=TestClient(self.app);self.client.get('/api/workspace');self.headers={'Origin':'http://testserver'};self.data=fixture();doc=self.client.post('/api/documents',files={'file':('TEST_ONLY_new_upload.pdf',self.data)},headers=self.headers).json();im,self.body=request_for(self.data);self.body['document_id']=doc['id'];self.doc=doc
        self.parent=self.wait('/api/observations',self.client.post('/api/observations',json=self.body,headers=self.headers).json()['run_id']);self.body['observation_run_id']=self.parent['run_id']
    def wait(self,path,identity):
        for _ in range(200):
            current=self.client.get(path+'/'+identity).json()
            if current['status'] not in ['QUEUED','RUNNING']:return current
            time.sleep(.01)
        self.fail('job did not finish')
    def tearDown(self):self.jobs.close();self.client.close()
    def test_real_uploaded_bytes_flow_through_observation_to_geometry_and_download(self):
        r=self.client.post('/api/geometries',json=self.body,headers=self.headers);self.assertEqual(r.status_code,202,r.text);job=self.wait('/api/geometries',r.json()['run_id']);self.assertIn(job['status'],['COMPLETED','PARTIAL']);self.assertEqual(job['job_kind'],'geometry');self.assertEqual(job['result']['parent_observation_run_id'],self.parent['run_id']);self.assertEqual(job['result']['source_binding']['source_sha256'],self.doc['sha256']);self.assertTrue(job['result']['entities'])
    def test_stale_roi_missing_observations_other_visitor_and_Ref_are_rejected(self):
        for field,value in [('source_role','Ref'),('roi_bbox_native',[2,2,55,55]),('observation_run_id','missing')]:
            b=copy.deepcopy(self.body);b[field]=value;r=self.client.post('/api/geometries',json=b,headers=self.headers);self.assertIn(r.status_code,[422,404])
        with TestClient(self.app) as other:
            other.get('/api/workspace');self.assertEqual(other.post('/api/geometries',json=self.body,headers=self.headers).status_code,404)
    def test_geometry_worker_failure_returns_no_fake_candidates_or_private_trace(self):
        def failure(*args):raise RuntimeError('TEST_ONLY_PRIVATE /home/internal')
        self.jobs.geometry_runner=failure
        r=self.client.post('/api/geometries',json=self.body,headers=self.headers);job=self.wait('/api/geometries',r.json()['run_id'])
        self.assertEqual(job['status'],'FAILED');self.assertIsNone(job['result']);self.assertNotIn('/home/',json.dumps(job));self.assertEqual(job['failure']['code'],'GEOMETRY_WORKER_FAILED')
    def test_origin_size_and_wrong_job_type_are_rejected(self):
        self.assertEqual(self.client.post('/api/geometries',json=self.body).status_code,403);self.assertEqual(self.client.post('/api/geometries',content=b'x'*70000,headers=self.headers).status_code,413);self.assertEqual(self.client.get('/api/geometries/'+self.parent['run_id']).status_code,404)

if __name__=='__main__':unittest.main()
