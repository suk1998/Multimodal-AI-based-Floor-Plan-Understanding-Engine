"""Claim boundaries and export integration; synthetic tests, no GT rules."""
from copy import deepcopy
import csv,json,unittest
from io import StringIO
import cv2,numpy as np
from backend import test_door_correspondence as doors
from backend import test_dimension_comparison as dimensions
from backend import test_symbol_comparison as symbols
from backend.test_structural_comparison import Scene
from backend.element_grounding import ground_source
from backend.door_correspondence import attach_door_correspondence
from backend.door_change_policy import is_supported_door_change
from backend.change_policy import is_final_change_kind
from backend.comparison_claims import attach_claim_assessment,registration_fact
from backend.change_candidate_audit import attach_candidate_audit
from backend.comparison_review import ComparisonReview,export_review
from backend.structural_comparison import compare_registered
from backend.observation_trace import iter_records

def prepare(data):
    p,g,a,s,v,result=data
    result['element_grounding']=dict(sources={role:ground_source(page,model,role) for role,page,model in zip(['Ref','Target'],p,g)})
    for role in ['Ref','Target']:
        for node in result['element_grounding']['sources'][role]['entities']:
            if node['kind']=='Door':result['source_objects'][role].append(dict(id=node['id'],kind='Door',bbox=node['bbox_native'],supported=False))
    attach_door_correspondence(result,p,g,a,s,v,type('Scope',(),{'roi_mask':np.ones((320,320),np.uint8)})())
    attach_claim_assessment(result,g);attach_candidate_audit(result)
    return p,g,a,result

def claim(row,name):return next(c for c in row['claim_assessment']['claims'] if c['claim_type']==name)

class ComparisonClaimTests(unittest.TestCase):
    def test_width_swing_leaf_changes_reach_actual_export_without_installation_promotion(self):
        for changed in [dict(width=80.),dict(flip=True),dict(leaf_width=6.)]:
            data=doors.scene(targets=[changed],ref=dict(leaf_width=2.));original=deepcopy(data[1:3]);p,g,a,result=prepare(data)
            row,=result['correspondences'];self.assertTrue(is_supported_door_change(row),row)
            self.assertTrue(is_final_change_kind(row));review=ComparisonReview(result,[])
            self.assertEqual(result['status'],'REVIEW_AVAILABLE')
            self.assertTrue(review.confirmable(row));self.assertEqual(review.state(row),'CONFIRMED')
            records=list(csv.DictReader(StringIO(export_review(review,p[1],'csv').decode('utf-8-sig'))))
            self.assertEqual(len(records),1);self.assertEqual(records[0]['physical_change_inferred'],'False')
            self.assertTrue(records[0]['ref_bbox_native']);self.assertTrue(records[0]['target_bbox_native'])
            original_image=cv2.imdecode(np.frombuffer(p[1].png,np.uint8),1)
            changed_image=cv2.imdecode(np.frombuffer(export_review(review,p[1],'png'),np.uint8),1)
            self.assertEqual(original_image.shape,changed_image.shape);self.assertFalse(np.array_equal(original_image,changed_image))
            self.assertEqual(claim(row,'DRAWING_GEOMETRY_DIFFERENCE')['status'],'SUPPORTED_CHANGE')
            for name in ['SPACE_CONNECTION_DIFFERENCE','PHYSICAL_GEOMETRY_DIFFERENCE','INSTALLATION_SPECIFICATION_DIFFERENCE']:
                self.assertEqual(claim(row,name)['status'],'DEFERRED')
            self.assertEqual(row['source_opening_admissibility'],{'Ref':'HOLD','Target':'HOLD'})
            self.assertEqual(row['physical_installation_identity_status'],'UNKNOWN')
            self.assertEqual((g,a),tuple(original));json.dumps(result,allow_nan=False)

    def test_no_difference_does_not_become_confirmation_or_zero_physical_change_claim(self):
        p,g,a,result=prepare(doors.scene());row,=result['correspondences']
        self.assertEqual(claim(row,'DRAWING_GEOMETRY_DIFFERENCE')['status'],'NO_DIFFERENCE_WITHIN_MEASURED_SCOPE')
        self.assertEqual(claim(row,'PHYSICAL_GEOMETRY_DIFFERENCE')['status'],'DEFERRED')
        self.assertNotIn('automatic_confirmation',row)
        self.assertFalse(ComparisonReview(result,[]).confirmable(row))

    def test_mutual_match_token_cannot_replace_absolute_or_independent_evidence(self):
        _,_,_,result=prepare(doors.scene(targets=[dict(width=80.)]));original=result['correspondences'][0]
        for defect in ['absolute','competitors','position','source','role','relation','physical','metric','fit','output_bbox']:
            row=deepcopy(original);proof=row['door_geometry_evidence']
            if defect=='absolute':proof['geometry_evidence']['sufficient']=False
            if defect=='competitors':proof['competitor_assessment']['competing_candidates']=[{'candidate_id':'other'}]
            if defect=='position':proof['registration']['status']='UNCONFIRMED'
            if defect=='source':proof['Target']['source_stile_ink_support']=[0.,0.]
            if defect=='role':proof['Target']['frame_id']='Ref:foreign'
            if defect=='relation':proof['relation_assessment']['status']='CONFLICT'
            if defect=='physical':row['physical_change_inferred']=True
            if defect=='metric':proof['measured_facets'][0]['delta_ref_px']=float('nan')
            if defect=='fit':row['alignment_fit_parent']=True
            if defect=='output_bbox':row['verified_target_bbox_native']=None
            self.assertFalse(is_supported_door_change(row),defect)
            self.assertFalse(is_final_change_kind(row),defect)
            self.assertFalse(ComparisonReview(result,[]).confirmable(row),defect)

    def test_held_and_verified_conflicting_relationships_have_different_effects(self):
        for state,expected in [('PROPOSED','SUPPORTED_CHANGE'),('SUPPORTED','SUPPORTED_CHANGE'),('VERIFIED','DEFERRED')]:
            data,space_rows=doors.DoorCorrespondenceTests().verified(state=state)
            # A relationship comparison can be evaluated even when no geometry
            # difference is present. Retain existing supported Space mappings.
            data[5]['correspondences']=space_rows
            for row in space_rows:
                row.update(correspondence_id='TEST_SPACE_'+row['ref_ids'][0],domain='STRUCTURAL',judgment='UNDETERMINED',
                    evidence_refs=[],change_types=[],measured_facets=[],decision_reason='test-only mapping')
            p,g,a,result=prepare(data);row=result['correspondences'][-1]
            if expected=='DEFERRED':
                self.assertEqual(claim(row,'DRAWING_GEOMETRY_DIFFERENCE')['status'],'DEFERRED')
                self.assertEqual(row['claim_assessment']['dependencies']['OBJECT_CORRESPONDENCE']['status'],'CONFLICT')
                self.assertTrue(row['door_geometry_evidence']['absolute_geometry_support'])
                self.assertEqual(row['claim_assessment']['dependencies']['VERIFIED_CONNECTION_COMPARISON']['status'],'CONFLICT')
            else:self.assertEqual(claim(row,'DRAWING_GEOMETRY_DIFFERENCE')['status'],'NO_DIFFERENCE_WITHIN_MEASURED_SCOPE')

    def test_verified_connection_equal_names_without_space_mapping_is_still_unresolved(self):
        data,_=doors.DoorCorrespondenceTests().verified()
        _,_,_,result=prepare(data);row=result['correspondences'][-1]
        self.assertEqual(row['claim_assessment']['dependencies']['VERIFIED_CONNECTION_COMPARISON']['status'],'HOLD')
        self.assertEqual(claim(row,'DRAWING_GEOMETRY_DIFFERENCE')['status'],'NO_DIFFERENCE_WITHIN_MEASURED_SCOPE')

    def test_existing_verified_connection_mapping_can_be_compared_without_new_relations(self):
        data,space_rows=doors.DoorCorrespondenceTests().verified()
        ref_ids=data[1][0]['reference_relations']['relations'][0]
        target=data[1][1]['target_relations']['relations'][0]
        target['target']['id']=data[1][1]['frame_id']+':space:B'
        for row in space_rows:
            row.update(correspondence_id='TEST_'+row['ref_ids'][0],domain='STRUCTURAL',judgment='UNDETERMINED',evidence_refs=[],change_types=[],measured_facets=[],decision_reason='test-only')
        data[5]['correspondences']=space_rows;original=deepcopy(data[1])
        _,g,_,result=prepare(data);row=result['correspondences'][-1]
        self.assertEqual(claim(row,'SPACE_CONNECTION_DIFFERENCE')['status'],'NO_DIFFERENCE_WITHIN_MEASURED_SCOPE')
        self.assertEqual(g,original);self.assertEqual(ref_ids['claim_status'],'VERIFIED')

    def test_dimension_text_scope_survives_unknown_rooms_hosts_and_physical_geometry(self):
        p,g,a,v=dimensions.scene();rows,objects,_=dimensions.compare_dimensions(p,g,a,v)
        result=dict(run_id='TEST',correspondences=rows,source_objects={role:[o for o in objects if o['source_role']==role] for role in ['Ref','Target']})
        attach_claim_assessment(result,g);row=rows[0]
        self.assertEqual(claim(row,'TEXT_DESIGNATION_DIFFERENCE')['status'],'SUPPORTED_CHANGE')
        self.assertEqual(claim(row,'PHYSICAL_GEOMETRY_DIFFERENCE')['status'],'DEFERRED')
        self.assertNotIn('SUPPORTED_SPACE_BOUNDARIES',claim(row,'TEXT_DESIGNATION_DIFFERENCE')['required_dependencies'])

    def test_printed_type_change_does_not_claim_installation_or_material_change(self):
        p,g,a,result=symbols.scene();symbols.apply((p,g,a,result));attach_claim_assessment(result,g)
        row=result['correspondences'][0]
        self.assertEqual(claim(row,'TEXT_DESIGNATION_DIFFERENCE')['status'],'SUPPORTED_CHANGE')
        self.assertEqual(claim(row,'INSTALLATION_SPECIFICATION_DIFFERENCE')['status'],'DEFERRED')
        self.assertFalse(row['physical_change_inferred'])

    def test_bbox_presence_is_not_coordinate_verification(self):
        row={'verified_target_bbox_native':[1,2,3,4]}
        self.assertEqual(registration_fact(row)['status'],'HOLD')

    def test_pair_search_rejection_is_recorded_at_the_actual_gate(self):
        scene=Scene();scene.wall('Ref','one','P',40,100,80);scene.wall('Target','two','Q',40,100,80)
        # Remove room context, keeping matching geometric lines. Existing general
        # search still requires a cue; record that fact rather than infer deletion.
        for g in scene.models:g['space_closure']['regions']=[]
        result=compare_registered(scene.models,scene.alignment)
        rows=result['correspondences'];self.assertTrue(rows)
        self.assertTrue(all(r['judgment']=='UNDETERMINED' for r in rows))
        self.assertTrue(all('NO_ELIGIBLE_CATALOG_COUNTERPART' in r['decision_constraints'] for r in rows))
        audits=result['comparison_search']['objects']
        self.assertTrue(any(o['rejection_counts'].get('NO_HOST_SPACE_OR_LOCAL_CONTEXT_SEARCH_CUE') for o in audits))
        logs=[r for stream in result['_catalog_trace_streams'] for r in iter_records(stream)]
        self.assertTrue(any(r['status']=='PAIR_SEARCH_REJECTED' for r in logs))

    def test_partial_space_and_installation_hold_are_separate_constraints(self):
        from backend.comparison_constraints import unsupported_source_reasons
        self.assertEqual(unsupported_source_reasons([dict(kind='Space',supported=False,closed=False)]),['SPACE_BOUNDARY_INCOMPLETE'])
        self.assertEqual(unsupported_source_reasons([dict(kind='Door',supported=False)]),['TYPE_OPENING_OR_INSTALLATION_ADMISSION_INCOMPLETE'])

if __name__=='__main__':unittest.main()
