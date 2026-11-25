"""DrawingRegion requests must resolve an owned, same-frame proposal."""
import time
import unittest
import pymupdf
from fastapi.testclient import TestClient
from backend.observation_jobs import ObservationJobs
from backend.public_app import create_app
from backend.reference_pipeline import analyze_reference
from backend.test_reference_pipeline import synthetic_ocr,test_validator,input_for
from backend.test_reference_restoration import fixture


class DrawingRegionAPITests(unittest.TestCase):
    def test_owned_scope_uses_server_box_and_rejects_other_uploads(self):
        pdf,image,_,_=fixture()
        jobs=ObservationJobs(reference_runner=lambda p,i,b:analyze_reference(p,i,b,synthetic_ocr,test_validator))
        try:
            with TestClient(create_app(observation_jobs=jobs)) as client:
                headers={'Origin':'http://testserver'};client.get('/api/workspace')
                doc=client.post('/api/documents',files={'file':('TEST_ONLY.pdf',pdf)},headers=headers).json()
                body={**input_for(image),'document_id':doc['id']}
                def submit(value):
                    response=client.post('/api/reference-understandings',json=value,headers=headers)
                    self.assertEqual(response.status_code,202,response.text);identity=response.json()['run_id']
                    for _ in range(200):
                        job=client.get('/api/reference-understandings/'+identity).json()
                        if job['status'] not in ['RUNNING','QUEUED']:return job
                        time.sleep(.01)
                    self.fail('Synthetic scope worker did not finish')
                parent=submit(body);self.assertEqual(parent['status'],'PARTIAL',parent.get('failure'))
                region=parent['result']['drawing_regions']['candidates'][0]
                selection={'run_id':parent['run_id'],'region_id':region['region_id'],'bbox_native':[-1,-1,9999,9999]}
                result=submit({**body,'drawing_region_selection':selection})
                self.assertEqual(result['result']['source_binding']['core_bbox_native'],region['bbox_native'])
                self.assertEqual(result['result']['frame_id'],parent['result']['frame_id'])
                self.assertEqual(parent['result']['source_binding']['core_bbox_native'],[0,0,300,300])
                unknown=client.post('/api/reference-understandings',json={**body,'drawing_region_selection':{**selection,'region_id':'invented'}},headers=headers)
                self.assertEqual(unknown.status_code,422)
                # Different content is required now that repeated uploads reuse an identity.
                with pymupdf.open(stream=pdf,filetype='pdf') as changed:
                    changed.set_metadata({'subject':'different-upload'})
                    different_pdf=changed.tobytes()
                another=client.post('/api/documents',files={'file':('SECOND_UPLOAD.pdf',different_pdf)},headers=headers).json()
                self.assertNotEqual(another['id'],doc['id'])
                mismatch=client.post('/api/reference-understandings',json={**body,'document_id':another['id'],'drawing_region_selection':selection},headers=headers)
                self.assertEqual(mismatch.status_code,422)
        finally:jobs.close()


if __name__=='__main__':unittest.main()
