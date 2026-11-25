"""Live adapter contracts using explicitly synthetic OCR; no architectural GT."""
import copy
import time
import unittest
from fastapi.testclient import TestClient
from backend.architectural_observations import validate_request
from backend.observation_jobs import ObservationJobs
from backend.public_app import create_app
from backend.reference_pipeline import analyze_reference, validate_ontology
from backend.source_contract import request_frame
from backend.test_reference_restoration import fixture


def input_for(image):
    return {'source_role': 'Ref', 'page': image.page_number, 'extraction_zoom': image.zoom,
        'image': request_frame(image, 'Ref')}


def failed_ocr(_):
    return {'status': 'FAILED', 'reason': 'SYNTHETIC_FAILURE', 'regions': []}


def synthetic_ocr(_):
    return {'status': 'COMPLETED', 'device': 'SYNTHETIC_TEST_ONLY', 'regions': [{
        'id': 'TEST_ONLY', 'text': '침실1', 'polygon_native': [[142,142],[158,142],[158,158],[142,158]],
        'recognition_score': .9, 'native_to_rectified_crop': [[1,0,0],[0,1,0],[0,0,1]], 'crop_rotated_ccw_90': False}]}


def test_validator(before, after):
    return {'status': 'COMPLETED', 'test_only': True, 'architectural_truth_verified': False}


class ReferencePipelineTests(unittest.TestCase):
    def setUp(self):
        self.pdf, self.image, _, _ = fixture()
        self.body = input_for(self.image)
        self.binding = validate_request(self.body, self.image)
        self.binding['analysis_run_id'] = 'synthetic-live-run'

    def test_ref_without_target_has_own_full_page_scope(self):
        self.assertEqual(self.binding['core_bbox_native'], [0,0,300,300])
        self.assertEqual(self.binding['context_margin_px'], 0)
        bundle = analyze_reference(self.pdf, self.image.png, self.binding, synthetic_ocr, test_validator)
        self.assertEqual(bundle['run_id'], 'synthetic-live-run')
        self.assertEqual(len(bundle['cases']), 1)
        self.assertEqual(bundle['cases'][0]['label']['name'], '침실1')
        self.assertTrue(bundle['coverage']['new_OCR_run'])
        self.assertFalse(bundle['architectural_gate_passed'])
        self.assertEqual(bundle['status'], 'PARTIAL')
        for observation in bundle['observation_bundle']['observations']:
            self.assertEqual(observation['source_role'], 'Ref')
            self.assertEqual(observation['frame_id'], self.binding['frame_id'])
        self.assertTrue(all(c['status'] != 'VERIFIED' for c in bundle['region_graph_after']['claims']))
        self.assertEqual(bundle['region_graph_after']['run_id'], bundle['run_id'])

    def test_ocr_failure_keeps_real_geometry_without_invented_rooms(self):
        bundle = analyze_reference(self.pdf, self.image.png, self.binding, failed_ocr, test_validator)
        self.assertEqual(bundle['cases'], [])
        self.assertFalse(bundle['coverage']['new_OCR_run'])
        self.assertTrue(bundle['coverage']['OCR_requested'])
        self.assertEqual(bundle['components']['OCR']['status'], 'FAILED')
        self.assertEqual(bundle['components']['RESTORATION']['status'], 'UNAVAILABLE')
        self.assertTrue(bundle['observation_bundle']['observations'])
        self.assertEqual(bundle['summary']['verified_architectural_spaces'], 0)

    def test_wrong_role_partial_scope_and_old_frame_rejected(self):
        for change in [{'source_role':'Target'}, {'core_bbox_native':[10,10,100,100]}]:
            with self.assertRaises(ValueError):analyze_reference(self.pdf, self.image.png, {**self.binding, **change}, failed_ocr, test_validator)
        bad=copy.deepcopy(self.body);bad['image']['frame_id']='Target:'+bad['image']['image_id']
        with self.assertRaises(ValueError):validate_request(bad,self.image)

    def test_API_ownership_job_kind_and_document_removal(self):
        jobs=ObservationJobs(reference_runner=lambda p,i,b:analyze_reference(p,i,b,failed_ocr,test_validator))
        app=create_app(observation_jobs=jobs)
        try:
            with TestClient(app) as client, TestClient(app) as stranger:
                headers={'Origin':'http://testserver'}
                client.get('/api/workspace');stranger.get('/api/workspace')
                uploaded=client.post('/api/documents',files={'file':('test.pdf',self.pdf,'application/pdf')},headers=headers)
                self.assertEqual(uploaded.status_code,201,uploaded.text)
                body={**self.body,'document_id':uploaded.json()['id']}
                wrong=client.post('/api/reference-understandings',json={**body,'source_role':'Target'},headers=headers)
                self.assertEqual(wrong.status_code,422)
                self.assertEqual(stranger.post('/api/reference-understandings',json=body,headers=headers).status_code,404)
                submitted=client.post('/api/reference-understandings',json=body,headers=headers)
                self.assertEqual(submitted.status_code,202,submitted.text)
                identity=submitted.json()['run_id'];url='/api/reference-understandings/'+identity
                for _ in range(200):
                    job=client.get(url).json()
                    if job['status'] not in ['QUEUED','RUNNING']:break
                    time.sleep(.01)
                self.assertEqual(job['status'],'PARTIAL',job)
                self.assertEqual(job['result']['source_binding']['document_id'],body['document_id'])
                self.assertEqual(stranger.get(url).status_code,404)
                self.assertEqual(client.get('/api/observations/'+identity).status_code,404)
                self.assertEqual(client.delete('/api/documents/'+body['document_id'],headers=headers).status_code,204)
                self.assertEqual(client.get(url).status_code,410)
        finally:jobs.close()


if __name__=='__main__':unittest.main()
