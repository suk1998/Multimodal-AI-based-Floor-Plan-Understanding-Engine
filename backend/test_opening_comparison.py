"""Synthetic integration/negative controls. Actual PDFs are evaluated separately."""
from copy import deepcopy
from dataclasses import replace
import csv,hashlib,unittest
from io import StringIO
import cv2,numpy as np
from phase1_pdf import PageImage
from backend.wall_sections import Stroke
from backend.structural_alignment import image_binding
from backend.opening_geometry import compare_openings,find_openings,local_registration
from backend.opening_comparison import attach_opening_comparison
from backend.comparison_review import ComparisonReview,export_review
from backend.change_candidate_audit import attach_candidate_audit
from integrations.fpue_ontology.opening_change_contract import is_supported_opening_change

def scene():
    pages=[];strokes=[];vectors=[];bundles=[];sources=[]
    for role,shift in [('Ref',0),('Target',-4)]:
        left,right=100+shift,160+shift
        raw=[(1,left,100,120),(1,right,100,120),(0,100,20,left),(0,120,20,left),
             (0,100,right,240),(0,120,right,240),(1,260,120,260),(1,280,120,260)]
        lines=[Stroke(axis,n,a,b,(f'pdf_path:{i}:0:0',),True) for i,(axis,n,a,b) in enumerate(raw)]
        g=np.full((320,320),255,np.uint8)
        for s in lines:cv2.line(g,*[tuple(map(round,p)) for p in s.points()],0,1)
        features=[]
        for n in ([104,108] if role=='Ref' else [114,118]):
            points=[[left+4,n],[right-4,n]];cv2.line(g,*[tuple(p) for p in points],0,1)
            features.append(dict(id=role+':frame:'+str(n),axis=0,normal=n,lo=left+4,hi=right-4,
                                 source_evidence={'stroke_width_native':.72,'origin':'SYNTHETIC_TEST_ONLY'}))
        png=cv2.imencode('.png',g)[1].tobytes()
        page=PageImage(png,320,320,1,2,hashlib.sha256((role+'-test-pdf').encode()).hexdigest())
        binding=image_binding(page);frame=role+':'+binding['image_id']
        pages.append(page);strokes.append(lines)
        vectors.append(dict(source_sha256=page.source_sha256,page=1,extraction_zoom=2,image_size=[320,320],lines=features))
        bundles.append(dict(frame_id=frame,source_role=role,space_closure={'regions':[]}))
        sources.append(dict(frame_id=frame,image=binding,image_sha256=hashlib.sha256(png).hexdigest()))
    alignment=dict(status='PARTIAL',matrix=np.eye(3).tolist(),inverse_matrix=np.eye(3).tolist(),sources=sources,
        target_roi={'image':image_binding(pages[1]),'vertices':[[1,1],[318,1],[318,318],[1,318]]},
        coverage={'directional_validation_hulls_target':[[[1,1],[318,1],[318,318],[1,318]]],'tiles':[]},
        wall_registration={'independent_max_normal_ref_px':.2},
        pairs=[dict(id='H',usage='VALIDATION',parent_structure_id='parent-h'),dict(id='V',usage='VALIDATION',parent_structure_id='parent-v')],
        rows=[dict(id='H',usage='VALIDATION',passed=True),dict(id='V',usage='VALIDATION',passed=True)])
    result=dict(run_id='synthetic',input_fingerprint='test-only',alignment_status='PARTIAL',sources=sources,
        target_roi=alignment['target_roi'],source_objects={'Ref':[],'Target':[]},correspondences=[],status='UNDETERMINED')
    return pages,strokes,vectors,bundles,alignment,result

def apply(data):
    p,s,v,g,a,r=data;attach_opening_comparison(r,p,g,a,s,v);attach_candidate_audit(r);return r

class OpeningComparisonTests(unittest.TestCase):
    def test_observed_difference_survives_without_core_ncc_or_complete_graph(self):
        data=scene();p,s,v,g,a,r=data;before=deepcopy((g,a,v));apply(data)
        row,=r['correspondences'];self.assertTrue(is_supported_opening_change(row))
        self.assertEqual(row['change_types'],['OPENING_DRAWN_JAMB_POSITION','OPENING_DRAWN_PROFILE'])
        self.assertEqual(row['measurements']['jamb_displacements_ref_px'],[-4,-4])
        self.assertEqual((g,a,v),before);self.assertEqual(a['coverage']['tiles'],[])
        self.assertEqual(row['opening_geometry_evidence']['physical_assignment_status'],'UNBOUND')
        self.assertEqual(r['candidate_audit']['records'][0]['claim_scope'],'OBSERVED_OPENING_DRAWING_GEOMETRY')

    def test_same_scene_retains_no_difference(self):
        data=scene();p,s,v,g,a,r=data
        p[1]=replace(p[1],png=p[0].png);s[1]=deepcopy(s[0]);v[1]['lines']=deepcopy(v[0]['lines'])
        a['sources'][1]['image_sha256']=hashlib.sha256(p[1].png).hexdigest()
        apply(data);row,=r['correspondences']
        self.assertEqual(row['judgment'],'UNCHANGED_WITHIN_SCOPE');self.assertNotIn('automatic_confirmation',row)

    def test_wrong_vector_source_page_zoom_size_rejected(self):
        for key,value in [('source_sha256','wrong'),('page',2),('extraction_zoom',1),('image_size',[640,640])]:
            data=scene();data[2][1][key]=value
            with self.subTest(key=key),self.assertRaises(ValueError):apply(data)

    def test_missing_vectors_explicitly_unavailable(self):
        data=scene();data[2][1]=None;r=apply(data)
        self.assertEqual(r['opening_geometry_comparison']['reason'],'PDF_VECTOR_INPUT_UNAVAILABLE')
        self.assertEqual(r['correspondences'],[])

    def test_failed_global_or_independent_validation_cannot_confirm(self):
        for defect in ['status','holdout','one_parent','fit','hull','rotation']:
            data=scene();a=data[4]
            if defect=='status':a['status']='DEFERRED'
            elif defect=='holdout':a['rows'][1]['passed']=False
            elif defect=='one_parent':a['pairs'][1]['parent_structure_id']='parent-h'
            elif defect=='fit':a['pairs'].append(dict(id='fit-core',usage='FIT',target_segment=[[100,100],[160,100]],target_other_face=[[100,120],[160,120]]))
            elif defect=='hull':a['coverage']['directional_validation_hulls_target']=[]
            else:a['matrix'][0][1]=.05
            with self.subTest(defect=defect):
                r=apply(data);self.assertTrue(r['correspondences']);review=ComparisonReview(r,[])
                for row in r['correspondences']:
                    self.assertFalse(is_supported_opening_change(row));self.assertIsNone(row['verified_target_bbox_native'])
                    with self.assertRaises(ValueError):review.save({row['correspondence_id']:'CONFIRMED'})

    def test_unobserved_target_not_borrowed_from_ref(self):
        data=scene();data[1][1]=[];r=apply(data)
        self.assertGreater(len(r['source_objects']['Ref']),0);self.assertEqual(r['source_objects']['Target'],[])
        self.assertEqual(r['correspondences'],[])

    def test_plain_line_or_rectangle_not_assumed_opening(self):
        data=scene();s=data[1][0];gray=np.full((320,320),255,np.uint8);gray[:]=0
        for lines in [s[:2],s[:2]+[Stroke(0,110,20,240,('dim',),False)],
                      s[:2]+[Stroke(0,n,100,160,('furniture',),False) for n in [100,120]]]:
            self.assertEqual(find_openings(lines,2,data[4]['target_roi']['vertices'],gray,'Ref')[0],[])

    def test_export_native_frames_coordinates_and_no_physical_claim(self):
        data=scene();r=apply(data);row,=r['correspondences'];review=ComparisonReview(r,[])
        records=list(csv.DictReader(StringIO(export_review(review,data[0][1],'csv').decode('utf-8-sig'))))
        self.assertEqual(len(records),1);record=records[0]
        self.assertEqual(record['ref_frame_id'],data[3][0]['frame_id']);self.assertEqual(record['target_frame_id'],data[3][1]['frame_id'])
        self.assertEqual(record['physical_change_inferred'],'False');self.assertIn('미확인',record['change_description'])
        png=cv2.imdecode(np.frombuffer(export_review(review,data[0][1],'png'),np.uint8),1)
        self.assertTrue(np.any(np.all(png[98:122,94:162]==[220,74,0],axis=-1)))

    def test_no_deleting_uncertain_geometry_or_claiming_gt(self):
        data=scene();data[4]['coverage']['directional_validation_hulls_target']=[];r=apply(data)
        self.assertEqual(len(r['correspondences']),1);row=r['correspondences'][0]
        self.assertEqual(row['judgment'],'UNDETERMINED');self.assertTrue(row['evidence_refs'])
        self.assertEqual(row['opening_geometry_evidence']['observations']['semantic_status'],'PROPOSED')
        self.assertFalse(row['opening_geometry_evidence']['observations']['GT_used'])

    def test_same_native_jamb_evidence_supplements_without_duplicate_export(self):
        data=scene();p,s,v,g,a,r=data
        for vector in v:vector['lines']=[]
        ids=[]
        for i,role in enumerate(['Ref','Target']):
            cap=s[i][0];points=np.asarray(cap.points());ident=role+':existing-cap';ids.append(ident)
            r['source_objects'][role].append(dict(id=ident,kind='Wall',name=None,
                bbox=[*points.min(0).tolist(),*points.max(0).tolist()],refs=[role+':'+ref for ref in cap.refs]))
        existing=dict(correspondence_id='existing-wall',entity_type='Wall',domain='STRUCTURAL',
            ref_ids=ids[:1],target_ids=ids[1:],identity_status='SUPPORTED',judgment='CHANGED',
            evidence_refs=['Ref:pdf_path:0:0:0','Target:pdf_path:0:0:0'],
            change_types=['WALL_END_SHIFT'],decision_reason='existing wall evidence',alignment_fit_parent=False,
            verified_target_bbox_native=[95,99,101,121],automatic_confirmation='VECTOR_WALL_END_WITH_INDEPENDENT_RASTER_SUPPORT')
        r['correspondences'].append(existing);apply(data)
        supplemental=r['correspondences'][1]
        self.assertEqual(supplemental['represented_by_correspondence_ids'],['existing-wall'])
        self.assertNotIn('automatic_confirmation',supplemental)
        self.assertEqual(r['candidate_audit']['records'][1]['stages']['automatic_output']['status'],'SUPPLEMENTARY_EVIDENCE')
        records=list(csv.DictReader(StringIO(export_review(ComparisonReview(r,[]),p[1],'csv').decode('utf-8-sig'))))
        self.assertEqual([x['change_id'] for x in records],['existing-wall'])

if __name__=='__main__':unittest.main()
