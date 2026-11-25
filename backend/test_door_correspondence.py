"""Absolute geometry, distinguishability, semantic conflict and change controls.
Synthetic sources; real Page 4 and GT review are separate validation artifacts.
"""
from copy import deepcopy
from dataclasses import replace
import hashlib,unittest
from unittest.mock import patch
import cv2,numpy as np
from backend.test_opening_comparison import scene as registration_scene
from backend.element_grounding import ground_source
from backend.door_correspondence import compare_doors,attach_door_correspondence

def scene(targets=None,ref=None):
    pages,strokes,_,models,alignment,result=registration_scene()
    vectors=[]
    for i,role in enumerate(['Ref','Target']):
        page=pages[i];frame=models[i]['frame_id'];image=cv2.imdecode(np.frombuffer(page.png,np.uint8),0)
        g=models[i];g.update(run_id='TEST_ONLY_'+role,observations=[],evidence=[])
        g['architectural_assessment']=dict(openings=[],installed_elements=[],structural_parents=[])
        g['reference_relations' if i==0 else 'target_relations']=dict(code_reviews=[],door_reviews=[],relations=[])
        vector=dict(source_sha256=page.source_sha256,page=1,extraction_zoom=2,image_size=[320,320],lines=[])
        descriptions=[ref or {}] if i==0 else targets or [{}]
        for number,kw in enumerate(descriptions):
            left=kw.get('left',100.);width=kw.get('width',60.);plane=kw.get('plane',190.);flip=kw.get('flip',False)
            thickness=kw.get('leaf_width',2.);right=left+width
            hinge=np.array([left if flip else right,plane]);radius=width;direction=-1 if flip else 1
            t=np.linspace(0,np.pi/2,25)
            points=(np.c_[hinge[0]+(radius*np.cos(t) if flip else -radius*np.cos(t)),plane+direction*radius*np.sin(t)]).tolist()
            caps=[[[left,plane-8],[left,plane+2]],[[right,plane-8],[right,plane+2]]]
            for points_to_draw in [*caps,points]:cv2.polylines(image,[np.rint(points_to_draw).astype(np.int32)],False,0,1)
            for k,x in enumerate([hinge[0],hinge[0]+thickness if flip else hinge[0]-thickness]):
                line=[[x,plane],[x,plane+direction*radius]];cv2.polylines(image,[np.rint(line).astype(np.int32)],False,0,1)
                vector['lines'].append(dict(id=f'pdf_path:leaf:{number}:{k}',axis=1,normal=x,lo=min(p[1] for p in line),hi=max(p[1] for p in line),
                    points_native=line,source_evidence=dict(stroke_width_native=.72,origin='SYNTHETIC_TEST_ONLY',layer='')))
            xs=sorted([hinge[0],hinge[0]+thickness if flip else hinge[0]-thickness])
            for k,y in enumerate([plane,plane+direction*radius]):
                line=[[xs[0],y],[xs[1],y]];cv2.polylines(image,[np.rint(line).astype(np.int32)],False,0,1)
                vector['lines'].append(dict(id=f'pdf_path:leafcap:{number}:{k}',axis=0,normal=y,lo=xs[0],hi=xs[1],
                    points_native=line,source_evidence=dict(stroke_width_native=.72,origin='SYNTHETIC_TEST_ONLY',layer='')))
            if kw.get('wall_side_extra'):
                line=[[hinge[0]+3,plane+15],[hinge[0]+3,plane+radius-5]]
                cv2.polylines(image,[np.rint(line).astype(np.int32)],False,0,1)
                vector['lines'].append(dict(id=f'pdf_path:unrelated-wall:{number}',axis=1,normal=hinge[0]+3,lo=plane+15,hi=plane+radius-5,
                    points_native=line,source_evidence=dict(stroke_width_native=.96,origin='SYNTHETIC_TEST_ONLY',layer='')))
            arc_id=frame+f':arc:{number}';ev=frame+f':evidence:{number}';oid=frame+f':opening:{number}';did=frame+f':door:{number}'
            g['observations'].append(dict(observation_id=arc_id,observation_type='PdfCurve',geometry_native=dict(points_native=points)))
            jamb_ids=[]
            for k,c in enumerate(caps):
                jid=frame+f':jamb:{number}:{k}';jamb_ids.append(jid)
                g['observations'].append(dict(observation_id=jid,observation_type='PdfLine',geometry_native=dict(points_native=c)))
            leaf_end=[hinge[0],plane+direction*radius]
            support=[dict(kind='SWING_LEAF_AND_BOTH_JAMBS',arc_observation_ref=arc_id,hinge_candidate_native=hinge.tolist(),
                swing_polyline_native=points,swing_radius_px=radius,curve_visibility=0 if kw.get('weak') else 1.,
                leaf_candidates=[dict(segment_native=[hinge.tolist(),leaf_end],visible_fraction=1.)],
                jamb_connections=[dict(connected=True,observation_refs=[j]) for j in jamb_ids]),
                dict(kind='GAP_BRACKETS_TRACKS',jamb_brackets_native=caps,cap_visibility=[1.,1.],tracks_native=[]),
                dict(kind='INSTALLATION_SHAPE_RECOVERY',claim_scope='DRAWING_ONLY')]
            g['evidence'].append(dict(evidence_id=ev,source_observation_refs=[arc_id,*jamb_ids],supporting=support))
            box=[left-3,min(plane-8,plane+direction*radius),right+3,max(plane+2,plane+direction*radius)]
            geo=dict(bbox_native=box,polylines_native=[[[left,plane-3],[right,plane-3]],points])
            g['architectural_assessment']['openings'].append(dict(opening_id=oid,decision='HOLD',host_parent_ids=[],reasons=['HOST_UNRESOLVED'],
                geometry_native=geo,opening_segment_native=[[left,plane-3],[right,plane-3]],evidence_refs=[ev]))
            g['architectural_assessment']['installed_elements'].append(dict(installation_id=did,opening_id=oid,type_candidates=['Door'],
                geometry_native=geo,evidence_refs=[ev],claim_status='PROPOSED',decision='HOLD',installation_shape_only=True))
        png=cv2.imencode('.png',image)[1].tobytes();pages[i]=replace(page,png=png)
        g['source_binding']=dict(source_role=role,frame_id=frame,source_sha256=page.source_sha256,page=1,extraction_zoom=2,
            width=320,height=320,image_sha256=hashlib.sha256(png).hexdigest())
        alignment['sources'][i]['image_sha256']=hashlib.sha256(png).hexdigest();vectors.append(vector)
    alignment['settings']=dict(normal_tolerance_ref_px=3.,angle_tolerance_degrees=1.)
    for pair in alignment['pairs']:
        axis=0 if pair['id']=='H' else 1
        a,b=([[20,100],[60,100]],[[20,120],[60,120]]) if axis==0 else ([[260,150],[260,220]],[[280,150],[280,220]])
        pair.update(axis=axis,ref_segment=a,target_segment=a,ref_other_face=b,target_other_face=b,
                    evidence={'Ref':['TEST_ONLY_HOLDOUT'],'Target':['TEST_ONLY_HOLDOUT']})
    return pages,models,alignment,strokes,vectors,result

def run(data,rows=()):
    p,g,a,s,v,_=data;graphs={role:ground_source(page,model,role) for role,page,model in zip(['Ref','Target'],p,g)}
    return compare_doors(p,g,graphs,a,s,v,np.ones((320,320),np.uint8),list(rows))

def supported(result):return [e for e in result['candidates'] if e['status']=='SUPPORTED_GEOMETRIC_CORRESPONDENCE']

class DoorCorrespondenceTests(unittest.TestCase):
    def test_held_hosts_and_no_room_context_do_not_block_absolute_geometry(self):
        data=scene();original=deepcopy(data);result=run(data)
        self.assertEqual(len(supported(result)),1)
        self.assertEqual(data,original)
        e=supported(result)[0];self.assertEqual(e['relation_assessment']['status'],'HOLD')
        self.assertTrue(e['competitor_assessment']['reciprocal_best'])
        self.assertTrue(e['absolute_geometry_support'])

    def test_mutual_singleton_with_large_absolute_error_cannot_be_supported(self):
        result=run(scene(targets=[dict(plane=198.)]));self.assertEqual(len(result['candidates']),1)
        self.assertFalse(supported(result));self.assertFalse(result['candidates'][0]['absolute_geometry_support'])

    def test_weak_singleton_and_missing_pixels_cannot_pass(self):
        self.assertFalse(supported(run(scene(targets=[dict(weak=True)]))))
        data=scene();page=data[0][1];blank=np.full((320,320),255,np.uint8)
        data[0][1]=replace(page,png=cv2.imencode('.png',blank)[1].tobytes())
        self.assertFalse(supported(run(data)))

    def test_duplicate_geometries_and_close_weaker_competitor_remain_ambiguous(self):
        for second in [{},dict(plane=192.),dict(plane=192.,weak=True)]:
            result=run(scene(targets=[{},second]));self.assertFalse(supported(result))
            self.assertTrue(any('COMPETING_CANDIDATE_UNCERTAINTY_INTERVALS_OVERLAP' in e['reason_codes'] for e in result['candidates']))

    def test_clearly_distinguishable_competitor_does_not_block(self):
        result=run(scene(targets=[{},dict(plane=204.)]));self.assertEqual(len(supported(result)),1)
        self.assertTrue(supported(result)[0]['competitor_assessment']['reciprocal_best'])

    def test_width_swing_and_leaf_changes_are_not_identity_filters(self):
        for change,facet in [(dict(width=80.),'OBSERVED_FRAME_INTERVAL'),(dict(flip=True),'OBSERVED_SWING_DIRECTION'),
                             (dict(leaf_width=6.),'OBSERVED_LEAF_PROFILE')]:
            result=run(scene(targets=[change],ref=dict(leaf_width=2.)))
            self.assertEqual(len(supported(result)),1,(change,result))
            measured=next(f for f in supported(result)[0]['measured_facets'] if f['facet']==facet)
            self.assertEqual(measured['status'],'SUPPORTED');self.assertTrue(measured['changed'])

    def test_changed_both_stiles_remains_candidate_without_false_identity(self):
        result=run(scene(targets=[dict(left=112.)]));self.assertTrue(result['candidates']);self.assertFalse(supported(result))
        self.assertIn('NO_SHARED_OBSERVED_STILE_LOCUS',result['candidates'][0]['reason_codes'])

    def test_large_width_change_does_not_disappear_due_to_centroid_distance(self):
        result=run(scene(targets=[dict(width=500.)]))
        self.assertEqual(len(result['candidates']),1)
        self.assertIn('DOOR_CORE_OUTSIDE_USER_ROI',result['candidates'][0]['reason_codes'])

    def test_fragmented_nearby_wall_is_not_a_leaf_profile_change(self):
        edge=supported(run(scene(targets=[dict(wall_side_extra=True)])))[0]
        measured=next(f for f in edge['measured_facets'] if f['facet']=='OBSERVED_LEAF_PROFILE')
        self.assertEqual(measured['status'],'SUPPORTED');self.assertFalse(measured['changed'])

    def test_numpy_stroke_widths_produce_json_serializable_results(self):
        import json
        data=scene()
        for vector in data[4]:
            for line in vector['lines']:line['source_evidence']['stroke_width_native']=np.float64(.72)
        json.dumps(run(data),allow_nan=False)

    def test_holdout_failure_core_fit_and_hull_extrapolation_do_not_fallback(self):
        for failure in ['holdout','fit','hull','matrix']:
            data=scene();a=data[2]
            if failure=='holdout':a['rows'][0]['passed']=False
            if failure=='fit':a['pairs'].append(dict(id='fit',usage='FIT',target_segment=[[100,190],[160,190]],target_other_face=[[100,195],[160,195]]))
            if failure=='hull':a['coverage']['directional_validation_hulls_target']=[[[1,1],[80,1],[80,80],[1,80]]]
            if failure=='matrix':a['status']='DEFERRED';a['matrix']=None
            self.assertFalse(supported(run(data)),failure)

    def test_fit_parent_cannot_also_supply_independent_validation(self):
        data=scene();a=data[2]
        a['pairs'].append(dict(id='FIT',parent_structure_id='parent-h',usage='FIT',
                              target_segment=[[20,40],[60,40]],target_other_face=[[20,60],[60,60]]))
        self.assertFalse(supported(run(data)))
        self.assertIn('FIT_AND_VALIDATION_PARENT_NOT_INDEPENDENT',run(data)['candidates'][0]['reason_codes'])

    def test_reference_only_fit_intersection_is_not_ignored(self):
        data=scene();data[2]['pairs'].append(dict(id='fit',usage='FIT',parent_structure_id='fit-only',
            ref_segment=[[100,190],[160,190]],ref_other_face=[[100,195],[160,195]],
            target_segment=[[20,40],[60,40]],target_other_face=[[20,60],[60,60]]))
        result=run(data);self.assertFalse(supported(result))
        self.assertIn('CHANGED_LOCUS_INTERSECTS_ALIGNMENT_FIT_INTERVAL',result['candidates'][0]['reason_codes'])

    def test_nonfinite_transform_is_deferred_without_identity_fallback(self):
        data=scene();data[2]['matrix'][0][0]=float('nan')
        result=run(data);self.assertEqual(result['reason'],'INVALID_FIXED_TRANSFORM');self.assertEqual(result['candidates'],[])

    def test_reread_holdout_fallback_and_changed_wall_absorption_counterexample(self):
        from backend.door_correspondence import local_registration
        reason=dict(status='UNCONFIRMED',reason='MISSING_TWO_DIRECTIONAL_EXTERNAL_WALL_WITNESSES')
        with patch('backend.door_correspondence.local_registration',return_value=reason):
            self.assertEqual(len(supported(run(scene()))),1)
            data=scene();data[2]['pairs'][1]['target_segment']=[[295.623,150],[295.623,220]]
            self.assertFalse(supported(run(data)))

    def verified(self,state='VERIFIED',integrity=True):
        data=scene();rows=[]
        for role,g in zip(['Ref','Target'],data[1]):
            frame=g['frame_id']
            for name in ['A','B','C']:
                g['space_closure']['regions'].append(dict(region_id=frame+':space:'+name,name=name,claim_status='VERIFIED'))
            door=g['architectural_assessment']['installed_elements'][0]['installation_id']
            ids=[frame+':space:A',frame+':space:'+('B' if role=='Ref' else 'C')]
            relation=dict(id=frame+':connection',kind='Connection',source={'id':ids[0],'type':'Space'},target={'id':ids[1],'type':'Space'},
                via_entity_id=door,claim_status=state,evidence_refs=[g['evidence'][0]['evidence_id']] if integrity else ['missing'])
            g['reference_relations' if role=='Ref' else 'target_relations']['relations']=[relation]
        for name in ['A','B','C']:
            rows.append(dict(entity_type='Space',identity_status='SUPPORTED',ref_ids=[data[1][0]['frame_id']+':space:'+name],
                             target_ids=[data[1][1]['frame_id']+':space:'+name]))
        return data,rows

    def test_verified_relation_conflict_is_not_treated_as_hold(self):
        data,rows=self.verified();before=deepcopy(data[1]);result=run(data,rows)
        self.assertFalse(supported(result));edge=result['candidates'][0]
        self.assertTrue(edge['absolute_geometry_support'])
        self.assertEqual(edge['relation_assessment']['status'],'CONFLICT')
        self.assertIn('VERIFIED_CONNECTION_CONTRADICTION',edge['reason_codes'])
        self.assertEqual(data[1],before)

    def test_partial_relation_differences_are_not_verified_contradictions(self):
        for state in ['PROPOSED','SUPPORTED']:
            data,rows=self.verified(state);self.assertEqual(len(supported(run(data,rows))),1)

    def test_verified_symmetric_connection_order_is_not_a_conflict(self):
        data,rows=self.verified();rel=data[1][1]['target_relations']['relations'][0]
        rel['target']['id']=data[1][1]['frame_id']+':space:B'
        rel['source'],rel['target']=rel['target'],rel['source']
        self.assertEqual(len(supported(run(data,rows))),1)

    def test_unresolved_asserted_verified_provenance_is_explicit_review(self):
        data,rows=self.verified(integrity=False);result=run(data,rows)
        self.assertFalse(supported(result));self.assertEqual(result['candidates'][0]['relation_assessment']['status'],'REVIEW')

    def test_geometry_contradiction_or_truncation_is_distinct_from_host_hold(self):
        for kind,reason in [('CONTRADICTED','SOURCE_GEOMETRY_CONTRADICTION'),('TRUNCATED','SOURCE_GEOMETRY_TRUNCATED_OR_UNSUPPORTED')]:
            data=scene();o=data[1][1]['architectural_assessment']['openings'][0]
            if kind=='CONTRADICTED':o['decision']='CONTRADICTED'
            else:o['reasons'].append('CONTEXT_CUT_OPENING_NOT_COMPLETE')
            result=run(data);self.assertFalse(supported(result));self.assertIn(reason,result['candidates'][0]['reason_codes'])

    def test_same_names_without_verified_space_mapping_are_not_a_proven_conflict(self):
        data,_=self.verified();result=run(data,[])
        self.assertEqual(len(supported(result)),1)
        self.assertFalse(result['candidates'][0]['relation_assessment']['conflicts'])

    def test_roi_exclusion_and_search_budget_fail_explicitly(self):
        from backend.door_correspondence import Settings
        p,g,a,s,v,_=scene();graphs={role:ground_source(page,model,role) for role,page,model in zip(['Ref','Target'],p,g)}
        result=compare_doors(p,g,graphs,a,s,v,np.zeros((320,320),np.uint8),[])
        self.assertFalse(supported(result));self.assertIn('DOOR_CORE_OUTSIDE_USER_ROI',result['candidates'][0]['reason_codes'])
        result=compare_doors(p,g,graphs,a,s,v,np.ones((320,320),np.uint8),[],Settings(maximum_combinations=0))
        self.assertEqual(result['reason'],'CANDIDATE_SEARCH_BUDGET_EXCEEDED')

    def test_missing_vector_and_changed_code_do_not_fabricate_leaf_comparison(self):
        data=scene();data[4][1]['source_sha256']='foreign'
        edge=supported(run(data))[0]
        self.assertEqual(next(f for f in edge['measured_facets'] if f['facet']=='OBSERVED_LEAF_PROFILE')['status'],'HOLD')

    def test_legacy_rows_are_preserved_and_no_automatic_physical_change(self):
        data=scene();p,g,a,s,v,result=data;result['correspondences']=[dict(correspondence_id='old',entity_type='Door',
            ref_ids=[g[0]['architectural_assessment']['installed_elements'][0]['installation_id']],target_ids=[],judgment='UNDETERMINED')]
        result['element_grounding']=dict(sources={role:ground_source(page,model,role) for role,page,model in zip(['Ref','Target'],p,g)})
        scope=type('Scope',(),{'roi_mask':np.ones((320,320),np.uint8)})()
        attach_door_correspondence(result,p,g,a,s,v,scope)
        self.assertEqual(result['correspondences'][0]['correspondence_id'],'old')
        self.assertEqual(result['correspondences'][0]['output_disposition'],'REASSOCIATED_GEOMETRY_EVIDENCE')
        row=result['correspondences'][-1];self.assertEqual(row['identity_status'],'SUPPORTED')
        self.assertFalse(row['physical_change_inferred']);self.assertEqual(row['source_opening_admissibility'],{'Ref':'HOLD','Target':'HOLD'})
        self.assertNotIn('automatic_confirmation',row)

    def test_existing_supported_installation_and_host_claims_are_not_reinterpreted(self):
        data=scene();p,g,a,s,v,result=data
        for model in g:
            door=model['architectural_assessment']['installed_elements'][0];opening=door['opening_id'];door['claim_status']='SUPPORTED'
            model['architectural_assessment']['relations']=[dict(relation_id=model['frame_id']+':installed',relation_type='installedIn',
                source_id=door['installation_id'],target_id=opening,claim_status='SUPPORTED',admissibility='HOLD',evidence_refs=door['evidence_refs'])]
        result['element_grounding']=dict(sources={role:ground_source(page,model,role) for role,page,model in zip(['Ref','Target'],p,g)})
        attach_door_correspondence(result,p,g,a,s,v,type('Scope',(),{'roi_mask':np.ones((320,320),np.uint8)})())
        row=result['correspondences'][-1]
        self.assertEqual(row['source_installation_entity_claim_status'],{'Ref':'SUPPORTED','Target':'SUPPORTED'})
        self.assertEqual(row['source_installation_relation_claims']['Target'][0]['claim_status'],'SUPPORTED')
        self.assertEqual(row['source_opening_admissibility']['Target'],'HOLD')
        self.assertEqual(row['physical_installation_identity_status'],'UNKNOWN')

if __name__=='__main__':unittest.main()
