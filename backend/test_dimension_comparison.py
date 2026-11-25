"""Synthetic geometry controls, separate from the real-PDF validation script.

No GT values/coordinates are used to construct these fixtures.
"""
from copy import deepcopy
from dataclasses import replace
import csv
import hashlib
from io import StringIO
import unittest

import cv2
import numpy as np

from phase1_pdf import PageImage
from backend.dimension_comparison import compare_dimensions, extract_dimensions, is_supported_dimension_change
from backend.comparison_review import ComparisonReview, export_review


def scene(before='900', after='940'):
    pages=[]; bundles=[]; vectors=[]
    for role,raw,dx,dy in [('Ref',before,9,6),('Target',after,0,0)]:
        image=np.full((220,300),255,np.uint8); lines=[]; terminals=[]
        def line(key,axis,normal,lo,hi):
            points=[[lo,normal],[hi,normal]] if axis==0 else [[normal,lo],[normal,hi]]
            points=(np.array(points)+[dx,dy]).tolist()
            cv2.line(image,tuple(points[0]),tuple(points[1]),0,1)
            lines.append(dict(id=key,axis=axis,normal=normal+(dy if axis==0 else dx),
                              lo=lo+(dx if axis==0 else dy),hi=hi+(dx if axis==0 else dy),
                              points_native=points,source_evidence={'source_primitive_id':key}))
        line('baseline',0,120,20,260);line('context',0,88,15,270)
        for i,x in enumerate([20,80,140,200,260]):
            line('extension'+str(i),1,x,90,123)
            terminals.append(dict(id='terminal'+str(i),kind='SLASH',center=[x+dx,120+dy]))
        png=cv2.imencode('.png',image)[1].tobytes();digest=hashlib.sha256(png).hexdigest()
        page=PageImage(png=png,width=300,height=220,page_number=1,zoom=1,source_sha256=role+'-pdf')
        binding=dict(source_sha256=page.source_sha256,page=1,extraction_zoom=1,width=300,height=220,
                     image_sha256=digest,image_id=role+'-image',source_role=role)
        frame=role+':'+binding['image_id']
        text=dict(observation_id=role+'-text',observation_type='TextRegion',frame_id=frame,source_role=role,
            geometry_native={'points_native':(np.array([[100,107],[120,107],[120,117],[100,117]])+[dx,dy]).tolist()},
            confidence=.99,source_evidence=dict(normalized_text=raw,source_sha256=page.source_sha256,
              image_sha256=digest,algorithm='synthetic OCR observation'))
        bundles.append(dict(source_role=role,frame_id=frame,source_binding=binding,observations=[text]))
        vectors.append(dict(source_sha256=page.source_sha256,page=1,extraction_zoom=1,image_size=[300,220],
                            lines=lines,terminals=terminals));pages.append(page)
    alignment=dict(status='PARTIAL',mapping={'supported_tile_indices':[0]},matrix=[[1,0,9],[0,1,6],[0,0,1]],
        sources=[dict(frame_id=b['frame_id']) for b in bundles],settings={'normal_tolerance_ref_px':2},
        target_roi={'vertices':[[0,0],[299,0],[299,219],[0,219]]})
    return pages,bundles,alignment,vectors


class DimensionComparisonTests(unittest.TestCase):
    def test_different_readings_match_geometric_interval_in_separate_native_frames(self):
        p,b,a,v=scene();original=deepcopy((b,a,v));rows,objects,audit=compare_dimensions(p,b,a,v)
        row,=rows
        self.assertEqual((row['before'],row['after']),('900','940'))
        self.assertEqual(row['ref_bbox_native'],[109,113,129,123])
        self.assertEqual(row['verified_target_bbox_native'],[100,107,120,117])
        self.assertFalse(row['physical_change_inferred']);self.assertTrue(is_supported_dimension_change(row))
        self.assertGreaterEqual(len(row['dimension_evidence']['context_witness_pairs']),2)
        self.assertEqual(len(objects),2);self.assertEqual(original,(b,a,v))
        self.assertEqual(audit['matches'][0]['evidence']['maximum_endpoint_residual_ref_px'],0)

    def test_equal_numeric_values_and_formatting_only_do_not_emit_change(self):
        for before,after in [('940','940'),('1,900','1900')]:
            with self.subTest(before=before,after=after):
                rows,_,audit=compare_dimensions(*scene(before,after))
                self.assertEqual(rows,[]);self.assertEqual(len(audit['matches']),1)

    def test_nearest_numeric_label_without_dimension_geometry_is_not_used(self):
        p,b,a,v=scene();decoy=deepcopy(b[1]['observations'][0]);decoy['observation_id']='nearest-decoy'
        # Closer to Reference's raw image position, but does not fit any interval.
        decoy['geometry_native']['points_native']=[[109,113],[129,113],[129,123],[109,123]]
        decoy['source_evidence']['normalized_text']='812';b[1]['observations'].append(decoy)
        rows,_,audit=compare_dimensions(p,b,a,v)
        self.assertEqual(rows[0]['target_ids'],['Target-text'])
        self.assertTrue(any(t['id']=='nearest-decoy' for t in audit['sources'][1]['rejected']))

    def test_missing_terminal_architectural_context_or_independent_witnesses_defers(self):
        for defect in ('terminals','context','two_witnesses'):
            with self.subTest(defect=defect):
                p,b,a,v=scene()
                if defect=='terminals':v[1]['terminals']=[]
                elif defect=='context':v[1]['lines']=[s for s in v[1]['lines'] if s['id']!='context']
                else:v[1]['lines']=[s for s in v[1]['lines'] if s['id'] not in ('extension0','extension3','extension4')]
                self.assertEqual(compare_dimensions(p,b,a,v)[0],[])

    def test_duplicate_labels_in_one_slot_are_not_resolved_using_value_or_confidence(self):
        p,b,a,v=scene();other=deepcopy(b[1]['observations'][0]);other['observation_id']='duplicate'
        other['source_evidence']['normalized_text']='900';b[1]['observations'].append(other)
        rows,_,audit=compare_dimensions(p,b,a,v)
        self.assertEqual(rows,[])
        self.assertEqual(sum(t['reason']=='MULTIPLE_LABELS_IN_DIMENSION_INTERVAL' for t in audit['sources'][1]['rejected']),2)

    def test_displaced_context_cannot_be_fixed_by_nearest_label_or_refitting(self):
        p,b,a,v=scene();a['matrix'][1][2]+=7
        self.assertEqual(compare_dimensions(p,b,a,v)[0],[])

    def test_failed_transform_absent_vectors_or_missing_ocr_produce_no_event(self):
        for defect in ('transform','vector','ocr'):
            with self.subTest(defect=defect):
                p,b,a,v=scene()
                if defect=='transform':a['status']='DEFERRED'
                elif defect=='vector':v[1]=None
                else:b[1]['observations']=[]
                rows,_,audit=compare_dimensions(p,b,a,v)
                self.assertEqual(rows,[]);self.assertEqual(audit['status'],'DEFERRED')
                self.assertIn('reason',audit)

    def test_pdf_page_scale_image_and_transform_frame_mismatch_are_rejected(self):
        for key in ('source_sha256','page','extraction_zoom','image_sha256','frame'):
            with self.subTest(key=key):
                p,b,a,v=scene()
                if key=='frame':a['sources'][1]['frame_id']='Target:wrong'
                else:b[1]['source_binding'][key]='wrong'
                with self.assertRaises(ValueError):compare_dimensions(p,b,a,v)

    def test_bad_ocr_confidence_source_and_out_of_frame_coordinates_cannot_confirm(self):
        for defect in ('confidence','source','outside','nonfinite'):
            with self.subTest(defect=defect):
                p,b,a,v=scene();o=b[1]['observations'][0]
                if defect=='confidence':o['confidence']=.4
                elif defect=='source':o['source_evidence']['image_sha256']='other-image'
                elif defect=='outside':o['geometry_native']['points_native']=[[-10,107],[10,107],[10,117],[-10,117]]
                else:o['geometry_native']['points_native'][0][0]=float('nan')
                rows,_,audit=compare_dimensions(p,b,a,v);self.assertEqual(rows,[])
                self.assertTrue(audit['sources'][1]['rejected'])

    def test_existing_csv_png_export_accepts_only_supported_dimension_evidence(self):
        p,b,a,v=scene();rows,objects,_=compare_dimensions(p,b,a,v)
        comparison=dict(run_id='synthetic',input_fingerprint='fixture',alignment_status=a['status'],
            sources=[dict(frame_id=g['frame_id']) for g in b],correspondences=rows,
            source_objects={role:[o for o in objects if o['source_role']==role] for role in ('Ref','Target')})
        review=ComparisonReview(comparison,[]);review.save({})
        record,=list(csv.DictReader(StringIO(export_review(review,p[1],'csv').decode('utf-8-sig'))))
        self.assertEqual((record['before'],record['after']),('900','940'))
        self.assertEqual(record['physical_change_inferred'],'False')
        self.assertEqual(record['target_frame_id'],b[1]['frame_id'])
        output=cv2.imdecode(np.frombuffer(export_review(review,p[1],'png'),np.uint8),1)
        np.testing.assert_array_equal(output[107,110],[220,74,0])
        rows[0]['dimension_evidence']['validation_status']='PROPOSED'
        self.assertFalse(review.confirmable(rows[0]));self.assertFalse(is_supported_dimension_change(rows[0]))
        self.assertEqual(list(csv.DictReader(StringIO(export_review(review,p[1],'csv').decode('utf-8-sig')))),[])
