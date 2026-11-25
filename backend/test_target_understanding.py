"""Independent Target source, parent revision and workspace isolation."""
import copy
import time
import unittest

from fastapi.testclient import TestClient
from backend.architectural_geometry import analyze_geometry
from backend.architectural_observations import validate_request
from backend.observation_jobs import ObservationJobs
from backend.public_app import create_app
from backend.source_contract import request_frame
from backend.target_pipeline import analyze_target
from backend.test_reference_pipeline import failed_ocr, synthetic_ocr
from backend.test_reference_restoration import fixture


class TargetUnderstandingTests(unittest.TestCase):
    def setUp(self):
        self.pdf,self.image,_,_=fixture()
        self.body={'source_role':'Target','page':self.image.page_number,'extraction_zoom':self.image.zoom,
                   'image':request_frame(self.image,'Target')}
        self.binding=validate_request({**self.body,'roi_bbox_native':[0,0,self.image.width,self.image.height],'context_margin_px':0},self.image)
        self.binding.update(document_id='local-target',analysis_run_id='target-observation')

    def test_target_without_ref_runs_shared_engines_with_own_ids(self):
        t=analyze_target(self.pdf,self.image.png,self.binding,synthetic_ocr)
        result=analyze_geometry(self.pdf,self.image.png,{**self.binding,'analysis_run_id':'target-geometry'},t['observation_bundle'])
        self.assertEqual(t['source_role'],'Target')
        self.assertEqual(result['target_model']['schema_version'],'target_understanding_model/1')
        self.assertNotIn('reference_model',result)
        self.assertEqual(result['target_model']['source_revision']['observation_run_id'],t['run_id'])
        self.assertEqual(result['target_model']['reference_audit']['status'],'RESOLVED')
        for stage in ('space_closure','target_domains','target_relations','target_model'):
            self.assertEqual(result[stage]['source_binding']['source_role'],'Target')
            self.assertEqual(result[stage]['frame_id'],self.binding['frame_id'])
        for row in result['target_model']['inventory']:
            value=result
            for key in row['pointer'].strip('/').split('/'):
                value=value[int(key)] if isinstance(value,list) else value[key]
        self.assertNotIn('Ref:',str(result))
        self.assertIsNone(result['target_model']['evaluation']['metrics'])

    def test_foreign_frame_and_scope_rejected(self):
        with self.assertRaises(ValueError):
            analyze_target(self.pdf,self.image.png,{**self.binding,'source_role':'Ref'},failed_ocr)
        t=analyze_target(self.pdf,self.image.png,self.binding,failed_ocr)
        for field,value in [('source_role','Ref'),('page',999),('extraction_zoom',.5)]:
            binding={**self.binding,field:value}
            with self.assertRaises(ValueError):
                analyze_geometry(self.pdf,self.image.png,binding,t['observation_bundle'])
        with self.assertRaises(ValueError):
            analyze_target(self.pdf,self.image.png,{**self.binding,'drawing_region_bbox_native':[0,0,99999,99999]},failed_ocr)

    def test_target_API_rejects_peer_inputs_and_cross_workspace_jobs(self):
        jobs=ObservationJobs(target_runner=lambda p,i,b:analyze_target(p,i,b,synthetic_ocr))
        app=create_app(observation_jobs=jobs)
        try:
            with TestClient(app) as owner,TestClient(app) as stranger:
                headers={'Origin':'http://testserver'}
                owner.get('/api/workspace');stranger.get('/api/workspace')
                doc=owner.post('/api/documents',files={'file':('target.pdf',self.pdf,'application/pdf')},headers=headers).json()
                body={**self.body,'document_id':doc['id']}
                self.assertEqual(owner.post('/api/target-understandings',json={**body,'reference_run_id':'peer'},headers=headers).status_code,422)
                accepted=owner.post('/api/target-understandings',json=body,headers=headers)
                self.assertEqual(accepted.status_code,202,accepted.text)
                identity=accepted.json()['run_id']
                for _ in range(200):
                    job=owner.get('/api/target-understandings/'+identity).json()
                    if job['status'] not in ('QUEUED','RUNNING'):break
                    time.sleep(.01)
                self.assertEqual(job['status'],'PARTIAL',job)
                self.assertEqual(stranger.get('/api/target-understandings/'+identity).status_code,404)
                self.assertEqual(owner.get('/api/reference-understandings/'+identity).status_code,404)
                self.assertEqual(owner.post('/api/geometries',json={**body,'reference_run_id':identity,'source_role':'Ref'},headers=headers).status_code,409)
        finally:
            jobs.close()
