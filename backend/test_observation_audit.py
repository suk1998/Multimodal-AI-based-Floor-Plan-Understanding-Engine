"""Synthetic stage 2 regressions; no claims of actual floorplan correctness."""
import copy
import hashlib
import json
from pathlib import Path
import tempfile
import unittest

import cv2
import numpy as np
from backend.observation_audit import CoverageAudit,propose_drawing_regions,reobservation_audit
from backend.observation_ocr import run,recover
from backend.reference_pipeline import analyze_reference
from backend.test_reference_pipeline import input_for,synthetic_ocr,test_validator
from backend.test_reference_restoration import fixture
from backend.architectural_observations import validate_request,analyze


class ObservationAuditTests(unittest.TestCase):
    def setUp(self):
        self.pdf,self.image,_,_=fixture()
        self.binding=validate_request(input_for(self.image),self.image)

    def test_caps_record_omitted_positions_without_claiming_empty_space(self):
        audit=CoverageAudit(self.binding,3,3)
        audit.record('PdfLine',[200,200,240,240],False)
        audit.record('PdfLine',[20,20,40,40],True,True)
        r=audit.export();self.assertEqual(r['counts_by_type']['PdfLine']['encountered'],2)
        self.assertEqual(r['counts_by_type']['PdfLine']['omitted_due_to_limit'],1)
        self.assertEqual(r['omitted_location_samples']['PdfLine'][0]['bbox_native'],[200,200,240,240])
        self.assertFalse(r['empty_cell_is_confirmed_empty'])
        cell=next(c for c in r['cells'] if c['cell_id']=='2:2')
        self.assertEqual(cell['counts_by_type']['PdfLine']['omitted_due_to_limit'],1)

    def test_layout_separates_disconnected_content_without_creating_rooms(self):
        gray=np.full((300,300),255,np.uint8)
        cv2.rectangle(gray,(15,20),(130,250),0,3);cv2.rectangle(gray,(190,20),(280,130),0,3)
        result=propose_drawing_regions(gray,self.binding,[])
        self.assertEqual(len(result['candidates']),2)
        self.assertTrue(all(c['kind'] in ['MIXED_OR_UNKNOWN','FRAME_OR_CONTAINER_CANDIDATE'] and c['status']=='PROPOSED' and not c['is_physical_space_polygon'] for c in result['candidates']))
        self.assertFalse(result['automatic_semantic_partition_verified'])

    def test_reobservation_keeps_before_after_and_overlaps_are_dependent(self):
        result=analyze_reference(self.pdf,self.image.png,self.binding,synthetic_ocr,test_validator)
        audit=result['reobservation_audit'];self.assertTrue(audit['tiles'])
        tile=audit['tiles'][0];self.assertIn('raster_to_native',tile);self.assertEqual(tile['execution_status'],'COMPLETED');self.assertFalse(tile['architectural_truth_verified'])
        self.assertNotEqual(tile['before_candidate_id'],tile['after_candidate_id'])
        self.assertFalse(audit['cache']['reused'])
        repeated=copy.deepcopy(result);repeated['cases'][0]['reobservation']['lines'].append({'id':'TEST_ONLY:reobserved:line','source_primitive_id':'TEST_ONLY_DUPLICATED_SOURCE'});case=copy.deepcopy(repeated['cases'][0]);case['id']+=':TEST_DUPLICATE';case['request_id']+=':TEST_DUPLICATE'
        request=copy.deepcopy(repeated['reobservation_requests'][0]);request['request_id']=case['request_id'];request['subject_id']=case['id']
        repeated['cases'].append(case);repeated['reobservation_requests'].append(request)
        second=reobservation_audit(repeated)
        self.assertTrue(second['duplicate_source_support']);self.assertFalse(second['tiles'][1]['overlaps'][0]['independent_evidence'])
        altered=copy.deepcopy(result);altered['source_binding']['frame_id']+=':other'
        self.assertNotEqual(reobservation_audit(altered)['cache']['identity'],audit['cache']['identity'])

    def test_selected_analysis_scope_clips_observations_without_changing_native_frame(self):
        selected={**self.binding,'drawing_region_bbox_native':[40,40,270,270]}
        r=analyze_reference(self.pdf,self.image.png,selected,synthetic_ocr,test_validator)
        self.assertEqual(r['source_binding']['width'],300);self.assertEqual(r['frame_id'],self.binding['frame_id'])
        self.assertEqual(r['observation_bundle']['analysis_scope']['core_roi']['bbox_native'],[40,40,270,270])
        for request in r['reobservation_requests']:
            x0,y0,x1,y1=request['native_context'];self.assertTrue(40<=x0<x1<=270 and 40<=y0<y1<=270)
        with self.assertRaises(ValueError):analyze_reference(self.pdf,self.image.png,{**self.binding,'drawing_region_bbox_native':[-1,0,20,20]},synthetic_ocr,test_validator)

    def test_failed_recognition_stays_detection_not_fabricated_text(self):
        def detector(_):return {'status':'PARTIAL','regions':[], 'detection_regions':[{'id':'TEST:0','polygon_native':[[90,90],[120,90],[120,110],[90,110]],'recognition_status':'FAILED','failure_reason':'TEST_ONLY'}]}
        r=analyze(self.pdf,self.image.png,self.binding,detector)
        self.assertTrue(any(o['observation_type']=='TextDetection' for o in r['observations']))
        self.assertFalse(any(o['observation_type']=='TextRegion' for o in r['observations']))
        self.assertEqual(r['status'],'PARTIAL')


class OCRCheckpointTests(unittest.TestCase):
    def run_fake(self,runner):
        with tempfile.TemporaryDirectory() as directory:
            image=Path(directory)/'test.png';cv2.imwrite(str(image),np.full((60,100,3),255,np.uint8))
            output=Path(directory)/'result.json';result=run(image,output,runner)
            checkpoint=recover(output,hashlib.sha256(image.read_bytes()).hexdigest(),'TEST_TIMEOUT')
            with self.assertRaises(ValueError):recover(output,'0'*64)
            return result,checkpoint

    def test_detection_failure_does_not_claim_recognition_ran(self):
        def runner(args):args.on_stage('detection');raise RuntimeError('SYNTHETIC')
        r,_=self.run_fake(runner)
        self.assertEqual(r['status'],'FAILED');self.assertEqual(r['stages']['detection']['status'],'FAILED')
        self.assertEqual(r['stages']['recognition']['status'],'NOT_RUN');self.assertEqual(r['regions'],[])

    def test_recognizer_model_failure_is_not_a_detector_execution_failure(self):
        def runner(args):args.on_stage('detection_model');args.on_stage('recognition_model');raise RuntimeError('SYNTHETIC')
        r,_=self.run_fake(runner)
        self.assertEqual(r['model_stages']['recognition_model']['status'],'FAILED')
        self.assertEqual(r['stages']['detection']['status'],'NOT_RUN')

    def test_one_failed_recognition_preserves_raw_text_and_detection_evidence(self):
        p=[[10,10],[40,10],[40,24],[10,24]]
        def runner(args):
            args.on_stage('detection');args.on_detection({'dt_polys':[p,p],'dt_scores':[.8,.9]})
            args.on_stage('recognition',0)
            args.on_recognition({'id':'TEST:text:0000','polygon_native':p,'text':' ＰＷ１ ','recognition_score':.8,
                'native_to_rectified_crop':np.eye(3).tolist(),'crop_rotated_ccw_90':False,'recognition_seconds':.1,
                'raw_recognition':{'rec_text':' ＰＷ１ ','rec_score':.8}}, {'native_to_recognition_crop':np.eye(3).tolist(),'recognition_crop_to_native':np.eye(3).tolist()})
            args.on_stage('recognition',1);args.on_recognition_error(1,'SYNTHETIC_RECOGNITION_FAILURE')
        r,timed=self.run_fake(runner)
        self.assertEqual(r['status'],'PARTIAL');self.assertEqual(r['stages']['detection']['status'],'COMPLETED')
        self.assertEqual(r['stages']['recognition']['failed'],1)
        self.assertEqual(r['regions'][0]['text'],' ＰＷ１ ');self.assertEqual(r['regions'][0]['normalized_text'],'PW1')
        self.assertEqual(len(timed['regions']),1);self.assertEqual(len(r['detection_regions']),2)
        self.assertEqual(r['detection_regions'][1]['recognition_status'],'FAILED')



class PDFAndCropFrameTests(unittest.TestCase):
    def test_pdf_text_native_frame_and_recognition_rotation_inverse(self):
        import pymupdf
        from phase1_pdf import render_page
        from backend.test_observations import request_for
        document=pymupdf.open();page=document.new_page(width=300,height=200);page.insert_text((80,90),'TEST ONLY PDF TEXT');pdf=document.tobytes();document.close()
        image,body=request_for(pdf,roi=[100,90,300,250],zoom=2)
        binding=validate_request(body,image)
        # Synthetic crop rotation with a real nonzero ROI context origin.
        transform=np.array([[0,1,-12],[-1,0,40],[0,0,1]],float)
        def text(_):return {'status':'COMPLETED','regions':[{'id':'TEST:0','polygon_native':[[20,20],[40,20],[40,50],[20,50]],'text':'TEST','recognition_score':.9,
            'native_to_rectified_crop':np.eye(3).tolist(),'crop_rotated_ccw_90':True,
            'native_to_recognition_crop':transform.tolist(),'recognition_crop_to_native':np.linalg.inv(transform).tolist()}]}
        result=analyze(pdf,image.png,binding,text)
        pdf_text=[o for o in result['observations'] if o['observation_type']=='PdfText']
        self.assertTrue(pdf_text);self.assertEqual(pdf_text[0]['source_evidence']['text'],'TEST ONLY PDF TEXT')
        self.assertEqual(pdf_text[0]['frame_id'],binding['frame_id'])
        region=next(o for o in result['observations'] if o['observation_type']=='TextRegion')
        source=region['source_evidence'];a=np.asarray(source['native_to_recognition_crop']);b=np.asarray(source['recognition_crop_to_native'])
        np.testing.assert_allclose(a@b,np.eye(3),atol=1e-8)
        point=np.array([binding['context_bbox_native'][0]+20,binding['context_bbox_native'][1]+20,1.])
        np.testing.assert_allclose(b@a@point,point,atol=1e-8)
        self.assertEqual(result['entities'],[])

    def test_high_resolution_clip_rounding_has_an_explicit_native_inverse(self):
        from phase1_pdf import render_page
        from backend.reference_restoration import reobserve
        pdf,_,_,_=fixture();image=render_page(pdf,1,3)
        binding=validate_request(input_for(image),image)
        window=[111,121,251,279]
        result,png=reobserve(pdf,binding,window,5,'TEST_ONLY:3x-to-4x')
        self.assertEqual(result['status'],'COMPLETED');self.assertTrue(png)
        matrix=np.array(result['rerender_to_native']);inverse=np.linalg.inv(matrix)
        width,height=result['rerender_size'];corners=np.array([[0,0,1],[width,height,1]],float)
        native=corners@matrix.T;step=matrix[0,0]
        self.assertTrue(np.all(np.abs(native[:,:2].reshape(-1)-np.array(window))<=step+1e-6))
        np.testing.assert_allclose((native@inverse.T)[:,:2],corners[:,:2],atol=1e-7)
        self.assertFalse(result['architectural_truth_verified'])

if __name__=='__main__':unittest.main()
