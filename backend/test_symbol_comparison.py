"""Synthetic decision controls; actual PDFs/browser are validated separately."""
from copy import deepcopy
from dataclasses import replace
import csv,hashlib,json,unittest
from pathlib import Path
from io import StringIO
import cv2
import numpy as np
from phase1_pdf import PageImage
from backend.structural_comparison import ComparisonScope
from backend.symbol_comparison import enrich_marker_changes
from backend.change_candidate_audit import attach_candidate_audit
from backend.comparison_review import ComparisonReview,export_review
from integrations.fpue_ontology.detection_contract import is_supported_symbol_change,interpret_code


def scene(before='AW/1',after='PW/9'):
    pages=[];models=[];objects={'Ref':[],'Target':[]};ids=[];sources=[]
    for role,code in [('Ref',before),('Target',after)]:
        family,number=code.split('/');frame=role+':fixture'
        image=np.full((220,220),255,np.uint8);cv2.circle(image,(105,105),18,0,1)
        png=cv2.imencode('.png',image)[1].tobytes();sha=hashlib.sha256(png).hexdigest()
        p=PageImage(png=png,width=220,height=220,page_number=1,zoom=1,source_sha256=role+'-pdf')
        b=dict(source_role=role,source_sha256=p.source_sha256,image_sha256=sha,page=1,extraction_zoom=1,width=220,height=220,frame_id=frame,image_id='fixture')
        observations=[];glyphs=[]
        for key,text,box in [('family',family,[95,106,116,117]),('number',number,[102,91,108,101])]:
            ident=frame+':'+key;points=[[box[0],box[1]],[box[2],box[1]],[box[2],box[3]],[box[0],box[3]]]
            glyphs.append(dict(id=ident,raw=text,normalized=text,bbox_native=box,source_kind='OCR',recognition_score=.99))
            observations.append(dict(observation_id=ident,frame_id=frame,source_role=role,observation_type='TextRegion',
                geometry_native={'points_native':points},confidence=.99,
                source_evidence=dict(text=text,source_sha256=p.source_sha256,image_sha256=sha)))
        box=[87,87,123,123];identity=frame+':symbol';ids.append(identity)
        marker=dict(id=identity,text=glyphs[0],number=glyphs[1],bbox_native=box,
            symbol_path=dict(id='source-circle',bbox=box,center=[105,105]),code=family+number,referent_status='HOLD')
        models.append(dict(run_id=role+'-run',frame_id=frame,source_role=role,source_binding=b,observations=observations,
                           **{('reference_relations' if role=='Ref' else 'target_relations'):{'code_reviews':[marker]}}))
        objects[role].append(dict(id=identity,bbox=box,kind='CodeMarker',name=family+number))
        sources.append(dict(frame_id=frame,geometry_run_id=role+'-run'));pages.append(p)
    roi={'image':{'width':220,'height':220},'vertices':[[1,1],[218,1],[218,218],[1,218]]}
    a=dict(status='PARTIAL',sources=sources,target_roi=roi,matrix=[[1,0,0],[0,1,0],[0,0,1]],inverse_matrix=[[1,0,0],[0,1,0],[0,0,1]],
        settings={'normal_tolerance_ref_px':2},mapping={'supported_tile_indices':[0]},
        coverage={'tiles':[dict(status='SUPPORTED',target_bbox_native=[0,0,220,220])]})
    row=dict(correspondence_id='synthetic-symbol',entity_type='CodeMarker',domain='ANNOTATION',identity_status='SUPPORTED',
        judgment='CHANGED',ref_ids=[ids[0]],target_ids=[ids[1]],cardinality='1:1',alignment_fit_parent=False,
        verified_target_bbox_native=[87,87,123,123],change_types=['MODIFIED'],state='REVIEW',
        evidence_refs=[o['observation_id'] for g in models for o in g['observations']],
        measured_facets=['MARKER_TEXT'],decision_reason='synthetic existing matcher output',
        identity_evidence={'mutually_preferred':True},measurements={'shape_iou':1})
    result=dict(run_id='synthetic-run',input_fingerprint='scope',sources=sources,alignment_status='PARTIAL',target_roi=roi,
        correspondences=[row],source_objects=objects,status='REVIEW_AVAILABLE')
    return pages,models,a,result


def apply(data):
    p,g,a,r=data;enrich_marker_changes(r,p,g,a,ComparisonScope(a));attach_candidate_audit(r);return r


class SymbolComparisonTests(unittest.TestCase):
    def test_backend_matches_shared_frontend_output_policy_cases(self):
        from backend.change_policy import is_final_change_kind
        cases=json.loads((Path(__file__).resolve().parents[1]/'tests/final_change_policy_cases.json').read_text())
        resolved={}
        for item in cases:
            row=deepcopy(item['row'] if 'row' in item else resolved[item['mutate']])
            row.update(item.get('patch',{}))
            if 'proof_patch' in item:row['symbol_evidence'].update(item['proof_patch'])
            resolved[item['id']]=row
            self.assertEqual(is_final_change_kind(row),item['expected'],item['id'])

    def test_existing_correspondence_becomes_scoped_printed_change_without_project_type(self):
        data=scene();models=deepcopy(data[1]);r=apply(data);row=r['correspondences'][0]
        self.assertTrue(is_supported_symbol_change(row));self.assertEqual((row['before'],row['after']),('AW/1','PW/9'))
        self.assertEqual(row['symbol_evidence']['sources']['Target']['semantics']['definition_status'],'UNRESOLVED')
        self.assertEqual(row['symbol_evidence']['physical_assignment_status'],'UNBOUND');self.assertFalse(row['physical_change_inferred'])
        self.assertEqual(models,data[1]);self.assertEqual(row['symbol_evidence']['original_comparison']['change_types'],['MODIFIED'])
        self.assertEqual(r['candidate_audit']['records'][0]['stages']['semantic_interpretation']['status'],'PARTIAL')
        self.assertEqual(r['candidate_audit']['records'][0]['stages']['automatic_output']['status'],'ELIGIBLE')

    def test_incomplete_or_low_quality_reading_is_retained_with_reason(self):
        for defect in ['missing','score','foreign','wrong_text','missing_original','missing_score']:
            data=scene();p,g,a,r=data;relation=g[1]['target_relations'];marker=relation['code_reviews'][0]
            if defect=='missing':marker['number']=None
            elif defect=='score':g[1]['observations'][0]['confidence']=.3
            elif defect=='foreign':marker['number']['frame_id']='Ref:foreign'
            elif defect=='wrong_text':g[1]['observations'][0]['source_evidence']['text']='OTHER'
            elif defect=='missing_original':g[1]['observations']=[]
            else:g[1]['observations'][0]['confidence']=None
            apply(data);row=r['correspondences'][0]
            self.assertFalse(is_supported_symbol_change(row),defect)
            self.assertTrue(row['symbol_decision']['reason_codes'],defect)
            self.assertEqual(len(r['candidate_audit']['records']),1)
            self.assertEqual(row['state'],'REVIEW')

    def test_invalid_source_frame_is_rejected(self):
        for field,value in [('source_sha256','another-pdf'),('frame_id','Target:another')]:
            data=scene();data[1][1]['source_binding'][field]=value
            with self.subTest(field=field),self.assertRaises(ValueError):apply(data)

    def test_failed_registration_or_scope_cannot_confirm(self):
        for defect in ['deferred','fit','scope','uncertain_match','duplicate']:
            data=scene();p,g,a,r=data
            if defect=='deferred':a['status']='DEFERRED'
            elif defect=='fit':r['correspondences'][0]['alignment_fit_parent']=True
            elif defect=='scope':a['coverage']['tiles'][0]['target_bbox_native']=[0,0,50,50]
            elif defect=='uncertain_match':r['correspondences'][0]['identity_evidence']['mutually_preferred']=False
            else:r['correspondences'].append(dict(deepcopy(r['correspondences'][0]),correspondence_id='duplicate'))
            apply(data)
            self.assertTrue(all(not is_supported_symbol_change(row) for row in r['correspondences']),defect)
            self.assertEqual(len(r['candidate_audit']['records']),len(r['correspondences']))

    def test_absent_original_circle_ink_cannot_confirm_vector_geometry_alone(self):
        data=scene();p,g,a,r=data
        png=cv2.imencode('.png',np.full((220,220),255,np.uint8))[1].tobytes();sha=hashlib.sha256(png).hexdigest()
        p[1]=replace(p[1],png=png);g[1]['source_binding']['image_sha256']=sha
        for o in g[1]['observations']:o['source_evidence']['image_sha256']=sha
        row=apply(data)['correspondences'][0]
        self.assertFalse(is_supported_symbol_change(row));self.assertIn('MARKER_LOCUS_OR_ORIGINAL_PERIMETER_UNSUPPORTED',row['symbol_decision']['reason_codes'])

    def test_equal_code_and_physical_specification_are_not_auto_promoted(self):
        for kind in ['equal','specification']:
            data=scene('AW/1','AW/1') if kind=='equal' else scene()
            if kind=='specification':data[3]['correspondences'][0]['domain']='SPECIFICATION'
            row=apply(data)['correspondences'][0];self.assertFalse(is_supported_symbol_change(row))
            self.assertEqual(row['state'],'REVIEW')

    def test_unknown_exact_code_does_not_gain_family_specifications(self):
        meaning=interpret_code('FSD/9')
        self.assertEqual(meaning['definition_status'],'UNRESOLVED');self.assertIsNone(meaning['declared_type'])
        self.assertIsNone(meaning['declared_material']);self.assertEqual(meaning['family_interpretation']['element_category'],'FireDoor')
        self.assertEqual(meaning['physical_assignment_status'],'UNBOUND')

    def test_existing_exports_include_scoped_event_and_both_native_locations(self):
        data=scene();result=apply(data);row=result['correspondences'][0];review=ComparisonReview(result,[]);review.save({})
        record,=list(csv.DictReader(StringIO(export_review(review,data[0][1],'csv').decode('utf-8-sig'))))
        self.assertEqual((record['before'],record['after']),('AW/1','PW/9'))
        self.assertEqual(record['physical_change_inferred'],'False');self.assertEqual(json.loads(record['ref_bbox_native']),[87,87,123,123])
        self.assertEqual(json.loads(record['target_bbox_native']),[87,87,123,123])
        rendered=cv2.imdecode(np.frombuffer(export_review(review,data[0][1],'png'),np.uint8),1)
        np.testing.assert_array_equal(rendered[87,100],[220,74,0])
        row['symbol_evidence']['complete_readings']=False
        self.assertFalse(review.confirmable(row));self.assertEqual(list(csv.DictReader(StringIO(export_review(review,data[0][1],'csv').decode('utf-8-sig')))),[])

    def test_final_policy_does_not_accept_bare_changed_annotation(self):
        row=apply(scene())['correspondences'][0]
        for field in ['symbol_evidence','automatic_confirmation','physical_change_inferred']:
            copy=deepcopy(row);copy.pop(field)
            self.assertFalse(is_supported_symbol_change(copy))
        for field in ['registration_supported','complete_readings','unique_correspondence']:
            copy=deepcopy(row);copy['symbol_evidence'][field]=False;self.assertFalse(is_supported_symbol_change(copy))
