"""TEST ONLY geometric contracts; these are not Page 4 performance measurements."""
import copy
import unittest
from backend.test_architectural_spatial import synthetic
from backend.architectural_spatial import analyze_spatial
from backend.spatial_evaluation import evaluate_connections
from backend.spatial_ontology import SpatialReasoner
from integrations.fpue_ontology.rules import rule_digest, validate_signature


def run_change(edit=None, **kwargs):
    data,image,binding,parent=synthetic(door=True,**kwargs)
    if edit: edit(binding,parent)
    return analyze_spatial(data,image.png,binding,parent)


def connections(result):
    return [r for r in result['relations'] if r['relation_type']=='Connection']


class OntologyGeometryTests(unittest.TestCase):
    def test_actual_analysis_calls_rules_and_derives_supported_door_relation(self):
        result=run_change();relation=connections(result)[0]
        self.assertEqual(relation['geometry_status'],'SUPPORTED')
        self.assertEqual(relation['claim_status'],'PROPOSED');self.assertIsNone(relation['confidence'])
        self.assertEqual(result['ontology']['rule_sha256'],rule_digest());self.assertEqual(result['ontology']['GT_verified_relations'],0)
        evidence={e['evidence_id']:e for e in result['evidence']}
        proof=evidence[relation['evidence_refs'][0]]['geometry_evidence']
        self.assertTrue(all(p['physical_wall_free'] for p in proof['probes']))
        self.assertEqual(len(proof['probes']),3)
        self.assertFalse(result['policy']['semantic_relationships_confirmed'])

    def test_ref_analysis_does_not_inherit_target_space_or_frame(self):
        result=run_change(role='Ref')
        self.assertEqual(len(connections(result)),1)
        for relation in result['relations']:
            self.assertEqual(relation['source_role'],'Ref');self.assertEqual(relation['frame_id'],result['frame_id'])
            self.assertNotIn('Target:',relation['relation_id'])

    def test_unknown_host_or_nonwall_host_cannot_close_room_or_connect(self):
        for identity in ['MISSING', 'TEST_ONLY:door']:
            def edit(binding,parent):parent['entities'][-1]['geometry_properties']['host_wall_candidate_ids']=[identity]
            result=run_change(edit)
            self.assertFalse(connections(result));self.assertEqual(result['summary']['space_region_polygons'],0)
            self.assertTrue(any('BOTH_JAMBS' in reason for a in result['relation_attempts'] for reason in a.get('reasons',[])))

    def test_window_and_untyped_opening_never_become_passage(self):
        for kind in ['Window','Opening']:
            def edit(binding,parent):parent['entities'][-1]['element_type']=kind
            result=run_change(edit);self.assertFalse(connections(result))
            self.assertTrue(any(a['kind']=='Connection' for a in result['relation_attempts']))

    def test_unresolved_source_observation_prevents_door_separator(self):
        def edit(binding,parent):parent['evidence'][-1]['source_observation_refs']=['DOES_NOT_EXIST']
        result=run_change(edit);self.assertFalse(connections(result));self.assertEqual(result['summary']['space_region_polygons'],0)

    def test_missing_jamb_evidence_does_not_connect_even_with_host(self):
        def edit(binding,parent):parent['evidence'][-1]['supporting'][0]['jamb_connections'][0]['connected']=False
        self.assertFalse(connections(run_change(edit)))

    def test_partial_space_owners_keep_connection_uncertain(self):
        def edit(binding,parent):
            binding['core_bbox_native']=[50,40,240,180];parent['source_binding']=copy.deepcopy(binding);parent['analysis_scope']['core_roi']['bbox_native']=binding['core_bbox_native']
        result=run_change(edit);self.assertEqual(len(connections(result)),1)
        self.assertEqual(connections(result)[0]['geometry_status'],'UNCERTAIN')
        self.assertEqual(connections(result)[0]['claim_status'],'PROPOSED')
        self.assertTrue(all(space['polygon_native'] is None for space in result['spaces']))

    def test_truncated_door_is_not_used_as_complete_passage(self):
        def edit(binding,parent):parent['entities'][-1]['boundary_truncated']=True
        result=run_change(edit);self.assertFalse(connections(result))
        self.assertTrue(any('OPENING_TRUNCATED_NO_COMPLETE_PASSAGE_INFERENCE' in a.get('reasons',[]) for a in result['relation_attempts']))

    def test_stale_geometry_not_used_as_a_physical_partition(self):
        def edit(binding,parent):
            parent['entities'][4]['claim_status']='STALE';parent['entities'][5]['claim_status']='STALE'
        result=run_change(edit);self.assertFalse(connections(result))
        stale=[d for d in result['diagnostics']['boundary_input_decisions'] if not d['ontology_eligibility']['eligible']]
        self.assertEqual(len(stale),2);self.assertTrue(all(d['role_in_spatial_analysis']=='NOT_A_PHYSICAL_BARRIER' for d in stale))

    def test_physical_wall_in_threshold_blocks_connection(self):
        def edit(binding,parent):
            entity=copy.deepcopy(parent['entities'][4]);entity['entity_id']='TEST_ONLY:blocking_wall';entity['geometry_native']={'bbox_native':[149,94,151,126],'polygons_native':[[[149,94],[151,94],[151,126],[149,126]]]};entity['evidence_refs']=[parent['evidence'][4]['evidence_id']];parent['entities'].append(entity)
        result=run_change(edit);self.assertFalse(connections(result))
        self.assertTrue(any('PHYSICAL_BAND_OR_CONTEXT_EDGE_BLOCKS_THRESHOLD' in a.get('reasons',[]) or 'OPPOSITE_SIDES_DO_NOT_HAVE_TWO_DISTINCT_UNIQUE_SPACE_OWNERS' in a.get('reasons',[]) for a in result['relation_attempts']))

    def test_cross_frame_evidence_and_duplicate_observations_rejected(self):
        data,image,binding,parent=synthetic(door=True)
        for edit in ['frame','duplicate']:
            bad=copy.deepcopy(parent)
            if edit=='frame':bad['evidence'][0]['frame_id']='wrong'
            else:bad['observations'].append(copy.deepcopy(bad['observations'][0]))
            with self.subTest(edit=edit),self.assertRaises(ValueError):analyze_spatial(data,image.png,binding,bad)

    def test_shared_ontology_signature_blocks_text_boundary_and_window_connection(self):
        for kind,source,target in [('BoundaryAssociation','Space','TextRegion'),('Connection','Space','Window'),('HostingRelation','Door','Wall')]:
            with self.assertRaises(ValueError):validate_signature(kind,source,target)


def gt_fixture():
    result=run_change();binding=result['source_binding'];role=result['source_role']
    stamp={'review_status':'CONFIRMED','reviewer':'SYNTHETIC_TEST_REVIEWER_NOT_REAL_GT','reviewed_at':'2026-11-17T12:00:00+09:00'}
    annotations=[]
    for gid,kind,geometry_type,points in [('left','SpaceBoundary','SpaceBoundary',[[28,28],[146,28],[146,192],[28,192]]),('right','SpaceBoundary','SpaceBoundary',[[154,28],[272,28],[272,192],[154,192]]),('door','Door','DoorThreshold',[[150,94],[150,126]])]:
        annotations.append({'gt_id':gid,'element_type':kind,'geometry_type':geometry_type,'geometry_native':{'coordinate_system':'image_native_pixels','points_native':points,'closed':kind=='SpaceBoundary'},'geometry_origin':'HUMAN_DRAWN','role':role,'frame_id':binding['frame_id'],'source_image_sha256':binding['image_sha256'],'source_binding':copy.deepcopy(binding),**stamp})
    matches=[]
    for space in result['spaces']:
        matches.append({'candidate_id':space['space_id'],'gt_id':'left' if space['space_name']=='침실1' else 'right',**stamp})
    matches.append({'candidate_id':connections(result)[0]['via_element_id'],'gt_id':'door',**stamp})
    gt={'schema_version':'fpue_gt_review/1','dataset_kind':'SYNTHETIC_CONTRACT_TEST_ONLY','bindings':{role:copy.deepcopy(binding)},'gt_annotations':annotations}
    rel={'schema_version':'fpue_spatial_relation_gt/1','dataset_kind':'SYNTHETIC_CONTRACT_TEST_ONLY','source_role':role,'frame_id':binding['frame_id'],'source_image_sha256':binding['image_sha256'],'inventory_complete':True,'missing_relations_reviewed':True,'scope_native':{'coordinate_system':'image_native_pixels','points_native':[[5,5],[295,5],[295,215],[5,215]]},'entity_matches':matches,'relations':[{'gt_relation_id':'GT_RELATION_TEST_ONLY','relation_type':'Connection','source_gt_id':'left','target_gt_id':'right','via_gt_id':'door',**stamp}],**stamp}
    return result,gt,rel


class SpatialGTEvaluationTests(unittest.TestCase):
    def test_missing_or_draft_GT_never_produces_metrics(self):
        bundle,gt,rel=gt_fixture()
        self.assertIsNone(evaluate_connections(bundle,None,None)['metrics'])
        rel['review_status']='DRAFT';self.assertIsNone(evaluate_connections(bundle,gt,rel)['metrics'])

    def test_synthetic_confirmed_scope_counts_one_edge(self):
        bundle,gt,rel=gt_fixture();evaluation=evaluate_connections(bundle,gt,rel)
        self.assertEqual(evaluation['metrics']['TP'],1);self.assertEqual(evaluation['metrics']['FP'],0);self.assertEqual(evaluation['metrics']['FN'],0)
        self.assertFalse(evaluation['representative_of_full_page'])

    def test_duplicate_prediction_cannot_count_two_true_positives(self):
        bundle,gt,rel=gt_fixture();edge=copy.deepcopy(connections(bundle)[0]);edge['relation_id']='TEST_ONLY:duplicate';bundle['relations'].append(edge)
        metrics=evaluate_connections(bundle,gt,rel)['metrics'];self.assertEqual(metrics['TP'],1);self.assertEqual(metrics['FP'],1)

    def test_unmapped_prediction_and_incomplete_inventory_are_pending(self):
        bundle,gt,rel=gt_fixture();rel['entity_matches']=rel['entity_matches'][:-1]
        self.assertIsNone(evaluate_connections(bundle,gt,rel)['metrics'])
        bundle,gt,rel=gt_fixture();rel['inventory_complete']='true'
        self.assertIsNone(evaluate_connections(bundle,gt,rel)['metrics'])

    def test_GT_frame_change_and_predictions_copied_into_GT_rejected(self):
        for edit in ['frame','origin']:
            bundle,gt,rel=gt_fixture()
            if edit=='frame':rel['frame_id']='other'
            else:gt['gt_annotations'][0]['geometry_origin']='COPIED_PREDICTION'
            with self.subTest(edit=edit),self.assertRaises(ValueError):evaluate_connections(bundle,gt,rel)

    def test_holdout_threshold_tuning_rejected(self):
        bundle,gt,rel=gt_fixture()
        with self.assertRaises(ValueError):evaluate_connections(bundle,gt,rel,split='HOLDOUT',thresholds_tuned=True)

    def test_missing_predicted_connection_counts_FN_only_with_complete_GT(self):
        bundle,gt,rel=gt_fixture();bundle['relations']=[r for r in bundle['relations'] if r['relation_type']!='Connection']
        metrics=evaluate_connections(bundle,gt,rel)['metrics'];self.assertEqual(metrics['FN'],1);self.assertIsNone(metrics['precision']);self.assertEqual(metrics['recall'],0)
