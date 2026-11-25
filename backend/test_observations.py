"""Synthetic observation contracts, separate from real OCR/browser validation."""
import copy,json,time,unittest
from types import SimpleNamespace
from unittest.mock import patch
import cv2,numpy as np,pymupdf
from fastapi.testclient import TestClient
from phase1_pdf import render_page,inspect_pdf
from backend.architectural_observations import validate_request,analyze,clip_segment
from backend.observation_jobs import ObservationJobs
from backend.public_app import create_app,ROOT

def empty_ocr(_):return {'status':'COMPLETED','regions':[],'models':{},'device':'TEST_ONLY'}
def fixture(rotation=0,crop=False):
    d=pymupdf.open();p=d.new_page(width=260,height=200);p.draw_line((20,60),(230,60),width=2);p.draw_line((20,70),(230,70),width=2);p.draw_line((80,20),(80,175),width=2);p.draw_line((20,30),(220,170),width=2);p.draw_rect((110,90,170,140),width=2)
    p.draw_bezier((175,90),(175,120),(205,140),(230,140),width=2)
    if crop:p.set_cropbox(pymupdf.Rect(10,10,240,185))
    p.set_rotation(rotation);data=d.tobytes();d.close();return data

def request_for(data,page=1,roi=None,zoom=2):
    im=render_page(data,page,zoom);identity=f'{im.source_sha256}:{page}:{zoom:g}:{im.width}x{im.height}'
    b={'source_role':'Target','page':page,'extraction_zoom':zoom,'roi_bbox_native':roi or [30,40,im.width-30,im.height-30],
       'image':{'source_sha256':im.source_sha256,'image_id':identity,'frame_id':'Target:'+identity,'width':im.width,'height':im.height,'extraction_zoom':zoom}}
    return im,b

class ObservationContracts(unittest.TestCase):
    def test_ROI_validation_and_context_rounding_keep_core_floats(self):
        im,b=request_for(fixture(),roi=[2.25,3.5,150.125,140.75]);v=validate_request(b,im);self.assertEqual(v['core_bbox_native'],b['roi_bbox_native']);self.assertEqual(v['context_bbox_native'],[0,0,199,189])
        for roi in [[-1,2,100,100],[1,1,1,100],[2,3,1,100],[0,0,float('nan'),10],[False,0,20,20]]:
            bad=copy.deepcopy(b);bad['roi_bbox_native']=roi
            with self.assertRaises(ValueError):validate_request(bad,im)
    def test_frame_PDF_role_size_and_zoom_mismatch_are_rejected(self):
        im,b=request_for(fixture())
        for key in ['source_sha256','image_id','frame_id','width','height','extraction_zoom']:
            bad=copy.deepcopy(b);bad['image'][key]='wrong'
            with self.assertRaises(ValueError):validate_request(bad,im)
        b['source_role']='Ref'
        with self.assertRaises(ValueError):validate_request(b,im)
    def test_clipped_line_end_is_explicit_and_not_a_physical_wall_end(self):
        data=fixture();im,b=request_for(data,roi=[100,90,210,160]);v=validate_request(b,im);r=analyze(data,im.png,v,empty_ocr)
        lines=[o for o in r['observations'] if o['observation_type']=='PdfLine' and o['boundary_truncated']]
        self.assertTrue(lines);self.assertTrue(any(o['source_evidence']['context_clipped'] for o in lines));self.assertTrue(all(o['boundary_details']['endpoint_semantics']=='OBSERVATION_ENDPOINT_NOT_PHYSICAL_WALL_END' for o in lines));self.assertEqual(r['entities'],[]);self.assertTrue(all(o['claim_status']=='PROPOSED' for o in r['observations']))
    def test_context_only_curve_cut_is_flagged_even_without_core_intersection(self):
        data=fixture();im,b=request_for(data,roi=[300,250,350,300]);r=analyze(data,im.png,validate_request(b,im),empty_ocr)
        outside=[o for o in r['observations'] if o['boundary_details']['context_geometry_extends_beyond_window']]
        self.assertTrue(outside);self.assertTrue(all(o['boundary_truncated'] for o in outside))
    def test_diagonal_segment_core_intersection_is_geometric_not_bbox_only(self):
        self.assertIsNone(clip_segment([[0,0],[5,5]],[6,0,10,4]));self.assertEqual(clip_segment([[0,0],[10,10]],[2,2,8,8]),[[2.,2.],[8.,8.]])
    def test_rotated_cropped_vector_frames_match_native_pixels(self):
        for rotation in [0,90,180,270]:
            data=fixture(rotation,True);im,b=request_for(data);v=validate_request(b,im);r=analyze(data,im.png,v,empty_ocr)
            self.assertEqual(r['components']['PDF_VECTOR']['status'],'COMPLETED');self.assertTrue(r['frame_registry']['rerender_image_sha256_matches'])
            matrix=np.array(r['frame_registry']['pdf_local_to_native']);inverse=np.array(r['frame_registry']['native_to_pdf_local']);np.testing.assert_allclose(matrix@inverse,np.eye(3),atol=1e-8)
            lines=[o for o in r['observations'] if o['observation_type']=='PdfLine'];self.assertTrue(lines)
            for o in lines[:5]:
                p=o['source_evidence']['pdf_points_local'];expected=np.c_[p,np.ones(2)]@matrix.T;np.testing.assert_allclose(expected[:,:2],o['source_evidence']['native_unclipped_points'],atol=1e-7)
    def test_raster_lines_parallels_endpoints_curves_are_actual_observations(self):
        data=fixture();im,b=request_for(data);r=analyze(data,im.png,validate_request(b,im),empty_ocr);types={o['observation_type'] for o in r['observations']}
        self.assertTrue({'RasterLine','PdfLine','PdfCurve','PdfRectangle','PdfPath','Endpoint','ParallelBoundaries'}<=types)
        for o in r['observations']:
            self.assertEqual(o['frame_id'],r['frame_id']);self.assertIn('source_evidence',o);self.assertIn('core_roi_intersection',o)
    def test_OCR_failure_preserves_geometry_and_never_fabricates_text(self):
        data=fixture();im,b=request_for(data);r=analyze(data,im.png,validate_request(b,im),lambda _:{'status':'FAILED','reason':'TEST_ONLY_UNAVAILABLE','regions':[]})
        self.assertEqual(r['status'],'PARTIAL');self.assertEqual(r['components']['OCR']['status'],'FAILED');self.assertFalse(any(o['observation_type']=='TextRegion' for o in r['observations']));self.assertTrue(r['observations'])
    def test_text_crop_coordinates_are_mapped_to_native_and_not_to_Spaces(self):
        data=fixture();im,b=request_for(data,roi=[100,100,300,250]);v=validate_request(b,im)
        def test_only(_):return {'status':'COMPLETED','regions':[{'id':'TEST_ONLY:text:0','polygon_native':[[10,12],[40,12],[40,24],[10,24]],'text':'TEST ONLY','recognition_score':.73,'native_to_rectified_crop':np.eye(3).tolist(),'crop_rotated_ccw_90':False}]}
        r=analyze(data,im.png,v,test_only);text=next(o for o in r['observations'] if o['observation_type']=='TextRegion');self.assertEqual(text['geometry_native']['points_native'][0],[62.,64.]);self.assertEqual(text['confidence'],.73);self.assertEqual(r['entities'],[]);self.assertEqual(text['source_evidence']['text'],'TEST ONLY')
    def test_scanned_PDF_without_vectors_falls_back_to_raster(self):
        image=np.full((160,220,3),255,np.uint8);cv2.line(image,(20,40),(200,40),(0,0,0),3);png=cv2.imencode('.png',image)[1].tobytes();d=pymupdf.open();p=d.new_page(width=220,height=160);p.insert_image(p.rect,stream=png);data=d.tobytes();d.close();im,b=request_for(data);r=analyze(data,im.png,validate_request(b,im),empty_ocr)
        self.assertEqual(r['components']['PDF_VECTOR']['status'],'UNAVAILABLE');self.assertTrue(any(o['observation_type']=='RasterLine' for o in r['observations']))

class ObservationAPI(unittest.TestCase):
    def setUp(self):
        self.jobs=ObservationJobs(runner=lambda pdf,png,b:analyze(pdf,png,b,empty_ocr));self.app=create_app(observation_jobs=self.jobs);self.client=TestClient(self.app);self.origin={'Origin':'http://testserver'};self.client.get('/api/workspace');self.data=fixture();self.doc=self.client.post('/api/documents',files={'file':('synthetic_only.pdf',self.data)},headers=self.origin).json();im,self.body=request_for(self.data);self.body['document_id']=self.doc['id']
    def tearDown(self):self.jobs.close();self.client.close()
    def test_job_uses_uploaded_document_and_owning_workspace(self):
        response=self.client.post('/api/observations',json=self.body,headers=self.origin);self.assertEqual(response.status_code,202,response.text);run=response.json()
        for _ in range(100):
            current=self.client.get('/api/observations/'+run['run_id']).json()
            if current['status'] not in ['QUEUED','RUNNING']:break
            time.sleep(.02)
        self.assertEqual(current['status'],'COMPLETED');self.assertEqual(current['result']['source_binding']['source_sha256'],self.doc['sha256']);self.assertEqual(current['result']['run_id'],run['run_id'])
        with TestClient(self.app) as other:
            other.get('/api/workspace');self.assertEqual(other.get('/api/observations/'+run['run_id']).status_code,404);self.assertEqual(other.post('/api/observations',json=self.body,headers=self.origin).status_code,404)
        self.client.delete('/api/documents/'+self.doc['id'],headers=self.origin);self.assertEqual(self.client.get('/api/observations/'+run['run_id']).status_code,410)
    def test_stale_frame_invalid_ROI_and_Ref_requests_do_not_start_jobs(self):
        for edit in [lambda b:b.update(source_role='Ref'),lambda b:b.update(roi_bbox_native=[0,0,9999,9999]),lambda b:b['image'].update(frame_id='other')]:
            b=copy.deepcopy(self.body);edit(b);self.assertEqual(self.client.post('/api/observations',json=b,headers=self.origin).status_code,422)
        self.assertFalse(self.jobs.jobs)
    def test_analysis_requires_same_origin_and_json_limits(self):
        self.assertEqual(self.client.post('/api/observations',json=self.body).status_code,403);self.assertEqual(self.client.post('/api/observations',content=b'x'*70000,headers=self.origin).status_code,413)

if __name__=='__main__':unittest.main()
