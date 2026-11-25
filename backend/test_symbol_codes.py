"""Synthetic marker-reading controls; real Page 4 results live separately."""
from copy import deepcopy
import hashlib
from types import SimpleNamespace
import unittest
import cv2
import numpy as np
from backend.symbol_codes import marker_reading,recover_numbers
from backend.reference_relations import code_attempts
from backend.comparison_objects import catalog

SYMBOL={'id':'circle','bbox':[10,10,50,50],'center':[30,30]}
def text(key,value,box):return dict(id=key,raw=value,normalized=value,bbox_native=box)
def fixture():return [text('family','PD',[22,31,39,45]),text('number','2',[25,18,35,32])]

class SymbolCodeTests(unittest.TestCase):
    def test_overlapping_ocr_boxes_still_form_distinct_family_and_number(self):
        rows=fixture();original=deepcopy(rows);reading=marker_reading(rows,SYMBOL)
        self.assertEqual((reading['canonical_code'],reading['ontology_type_code']),('PD/2','PD/2'))
        self.assertEqual((reading['code_family'],reading['type_number']),('PD','2'))
        self.assertEqual(reading['reading_evidence_refs'],['family','number']);self.assertEqual(rows,original)
        other=deepcopy(rows);other[1]['normalized']='1'
        self.assertEqual(marker_reading(other,SYMBOL)['canonical_code'],'PD/1')

    def test_inline_slash_and_joined_forms_use_same_existing_ontology_key(self):
        for value in ['PD/2','PD2',' PD / 2 ']:
            with self.subTest(value=value):
                reading=marker_reading([text('inline',value,[17,23,43,37])],SYMBOL)
                self.assertEqual(reading['canonical_code'],'PD/2');self.assertEqual(reading['reading_status'],'SUPPORTED')

    def test_missing_number_is_preserved_without_defaulting_to_first_type(self):
        result=marker_reading(fixture()[:1],SYMBOL)
        self.assertEqual(result['code'],'PD');self.assertIsNone(result['canonical_code'])
        self.assertEqual(result['reading_status'],'PARTIAL')

    def test_conflicting_numbers_do_not_become_supported_type(self):
        rows=fixture()+[text('other','3',[25,19,34,28])]
        reading=marker_reading(rows,SYMBOL)
        self.assertEqual(reading['reading_status'],'AMBIGUOUS');self.assertIsNone(reading['type_number'])
        rows=[text('inline','PD/1',[20,31,40,44]),fixture()[1]]
        self.assertEqual(marker_reading(rows,SYMBOL)['reading_status'],'AMBIGUOUS')

    def test_neighbor_marker_number_cannot_fill_missing_type_number(self):
        rows=[fixture()[0],text('nearby','9',[52,18,60,29])]
        self.assertIsNone(marker_reading(rows,SYMBOL)['canonical_code'])

    def test_two_pd_markers_keep_their_own_numbers_and_distinct_instances(self):
        first=fixture();second=deepcopy(first)
        for r in second:
            r['id']='second-'+r['id'];r['bbox_native']=[v+60 if i%2==0 else v for i,v in enumerate(r['bbox_native'])]
        second[1]['normalized']='1'
        symbol={'id':'second-circle','bbox':[70,10,110,50],'center':[90,30]}
        reviews,_=code_attempts(first+second,[SYMBOL,symbol],[],[0,0,130,70],'run')
        self.assertEqual([r['canonical_code'] for r in reviews],['PD/2','PD/1'])
        self.assertNotEqual(reviews[0]['id'],reviews[1]['id'])
        self.assertTrue(set(reviews[0]['reading_evidence_refs']).isdisjoint(reviews[1]['reading_evidence_refs']))

    def test_printed_code_does_not_assign_nearby_opening_or_door(self):
        reviews,links=code_attempts(fixture(),[SYMBOL],[{'opening_id':'near','decision':'HOLD','opening_segment_native':[[55,20],[55,40]]}],[0,0,90,90],'Ref:run')
        self.assertEqual(reviews[0]['canonical_code'],'PD/2');self.assertEqual(reviews[0]['referent_status'],'HOLD')
        self.assertFalse(reviews[0]['physical_referent_verified']);self.assertEqual([l['kind'] for l in links],['LabelAssociation'])

    def test_complete_inline_code_remains_complete_in_comparison_catalog(self):
        review=code_attempts([text('inline','PD/2',[17,23,43,37])],[SYMBOL],[],[0,0,90,90],'run')[0][0]
        g=dict(source_role='Ref',space_closure={'regions':[]},architectural_assessment={'structural_parents':[]},reference_relations={'code_reviews':[review]})
        obj,=catalog(g)[0]
        self.assertTrue(obj['complete_text']);self.assertEqual(obj['ontology_type_code'],'PD/2')

    def test_local_reread_preserves_native_frame_and_does_not_overwrite_old_ocr(self):
        image=np.full((80,80,3),255,np.uint8);png=cv2.imencode('.png',image)[1].tobytes()
        page=SimpleNamespace(get_pixmap=lambda **kw:SimpleNamespace(tobytes=lambda kind:png))
        binding=dict(source_role='Target',frame_id='Target:own',source_sha256='pdf',image_sha256=hashlib.sha256(png).hexdigest(),page=7,extraction_zoom=2)
        rows=fixture()[:1];before=deepcopy(rows)
        def recognizer(items):
            return {'status':'COMPLETED','results':[dict(id=i['id'],text='7',score=.99,crop_sha256=hashlib.sha256(i['png']).hexdigest()) for i in items]}
        added,audit=recover_numbers(page,binding,rows,[SYMBOL],[0,0,80,80],'Target:run',recognizer)
        self.assertEqual(rows,before);self.assertEqual(audit['status'],'COMPLETED')
        self.assertEqual(added[0]['frame_id'],'Target:own');self.assertEqual(added[0]['verification_status'],'PROPOSED')
        self.assertEqual(added[0]['geometry_semantics'],'RECOGNITION_CROP_EXTENT_NOT_DETECTED_TEXT_BOX')
        self.assertEqual(marker_reading(rows+added,SYMBOL)['canonical_code'],'PD/7')

    def test_conflicting_low_score_or_failed_rereads_cannot_fill_number(self):
        image=np.full((80,80,3),255,np.uint8);png=cv2.imencode('.png',image)[1].tobytes()
        page=SimpleNamespace(get_pixmap=lambda **kw:SimpleNamespace(tobytes=lambda kind:png))
        binding=dict(source_role='Ref',frame_id='Ref:own',source_sha256='pdf',image_sha256=hashlib.sha256(png).hexdigest(),page=1,extraction_zoom=1)
        for defect in ['conflict','score','failed','hash']:
            def recognizer(items):
                return {'status':'FAILED' if defect=='failed' else 'COMPLETED','results':[] if defect=='failed' else [dict(id=i['id'],text=str(k+4) if defect=='conflict' else '4',score=.7 if defect=='score' else .99,crop_sha256='foreign' if defect=='hash' else hashlib.sha256(i['png']).hexdigest()) for k,i in enumerate(items)]}
            added,audit=recover_numbers(page,binding,fixture()[:1],[SYMBOL],[0,0,80,80],'run',recognizer)
            self.assertEqual(added,[],defect);self.assertEqual(audit['attempts'][0]['status'],'HOLD')
        binding['image_sha256']='different-frame'
        with self.assertRaises(ValueError):recover_numbers(page,binding,fixture()[:1],[SYMBOL],[0,0,80,80],'run',recognizer)
