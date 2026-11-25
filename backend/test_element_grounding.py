"""Synthetic controls; real Page 4 is a separate evidence replay, not GT."""
from copy import deepcopy
import hashlib
import unittest
from backend.test_opening_comparison import scene
from backend.opening_comparison import attach_opening_comparison
from backend.element_grounding import ground_source, attach_element_grounding


def source():
    p, s, v, g, a, r = scene()
    for i, role in enumerate(['Ref', 'Target']):
        frame = g[i]['frame_id']
        for row in v[i]['lines']:
            row['id'] = frame+':primitive:'+row['id'].split(':')[-1]
        g[i]['source_binding'] = dict(source_role=role, frame_id=frame, source_sha256=p[i].source_sha256,
            page=1, extraction_zoom=2, width=320, height=320, image_sha256=hashlib.sha256(p[i].png).hexdigest())
        g[i]['reference_relations' if i == 0 else 'target_relations'] = dict(code_reviews=[], door_reviews=[])
    attach_opening_comparison(r, p, g, a, s, v)
    return p, g, r


def marker(g, code='PW/1', box=(112, 66, 144, 98), suffix='a'):
    row = dict(id=g['frame_id']+':symbol:'+suffix, canonical_code=code, reading_status='SUPPORTED' if code else 'PARTIAL',
               bbox_native=list(box), reading_evidence_refs=[])
    g['reference_relations' if g['source_role'] == 'Ref' else 'target_relations']['code_reviews'].append(row)
    return row


def run_source(data, side=0):
    p, g, r = data; audit = r['opening_geometry_comparison']
    return ground_source(p[side], g[side], ['Ref', 'Target'][side], audit['source_candidates'][side],
                         audit['source_assembly_features'][side])


class ElementGroundingTests(unittest.TestCase):
    def test_geometry_hosts_do_not_assert_filled_wall_or_catalog_assignment(self):
        data = source(); marker(data[1][0]); original = deepcopy(data)
        graph = run_source(data)
        self.assertEqual(data, original)
        hosts = [r for r in graph['relations'] if r['kind'] == 'Hosting']
        self.assertEqual(len(hosts), 2)
        self.assertTrue(all(r['claim_status'] == 'SUPPORTED' and r['claim_scope'] == 'OWN_DRAWN_GEOMETRY' for r in hosts))
        self.assertTrue(all(not r['physical_truth_verified'] for r in hosts))
        self.assertTrue(all(not n['fills_opening'] for n in graph['entities'] if n['kind'] == 'Wall'))
        review, = graph['marker_reviews']
        self.assertEqual(review['physical_assignment_status'], 'UNBOUND')
        self.assertEqual(len(review['candidate_relation_ids']), 1)
        self.assertEqual(graph['summary']['supported_physical_type_assignments'], 0)

    def test_marker_inside_or_projecting_to_gap_is_not_reference_proof(self):
        data = source(); marker(data[1][0])
        graph = run_source(data)
        ref, = [r for r in graph['relations'] if r['kind'] == 'ReferenceAssociation']
        self.assertEqual(ref['claim_status'], 'PROPOSED')
        self.assertEqual(ref['reference_status'], 'HOLD')
        self.assertIn('NO_TRACED_INSTANCE_REFERENCE', graph['marker_reviews'][0]['reason_codes'])

    def test_nearby_but_outside_gap_projection_does_not_bind(self):
        data = source(); marker(data[1][0], box=(160, 66, 192, 98))
        self.assertEqual(run_source(data)['marker_reviews'][0]['candidate_relation_ids'], [])

    def test_same_type_codes_remain_distinct_and_competitors_are_retained(self):
        data = source(); g = data[1][0]; marker(g, suffix='a'); marker(g, suffix='b')
        audit = data[2]['opening_geometry_comparison']
        opening = deepcopy(audit['source_candidates'][0][0]); opening['id'] += '-competing'
        audit['source_candidates'][0].append(opening)
        audit['source_assembly_features'][0][opening['id']] = deepcopy(next(iter(audit['source_assembly_features'][0].values())))
        graph = run_source(data)
        self.assertEqual(len(graph['marker_reviews']), 2)
        self.assertTrue(all(len(r['candidate_relation_ids']) == 2 for r in graph['marker_reviews']))
        self.assertTrue(all('COMPETING_GEOMETRY_CANDIDATES' in r['reason_codes'] for r in graph['marker_reviews']))
        self.assertNotEqual(*[r['id'] for r in graph['marker_reviews']])

    def test_boundary_lines_and_duplicate_normals_are_not_parallel_assembly(self):
        for case in ['boundary', 'duplicate']:
            data = source(); marker(data[1][0]); audit = data[2]['opening_geometry_comparison']
            opening = audit['source_candidates'][0][0]; features = audit['source_assembly_features'][0][opening['id']]
            for f in features:
                f['normal_native'] = opening['normal_lo'] if case == 'boundary' else opening['normal_lo']+2
            graph = run_source(data)
            self.assertFalse(any(n.get('type_candidates') for n in graph['entities']))
            self.assertEqual(graph['marker_reviews'][0]['candidate_relation_ids'], [])

    def test_wrong_pdf_frame_zoom_page_and_png_are_rejected(self):
        for key, bad in [('frame_id', 'Target:foreign'), ('page', 2), ('extraction_zoom', 1),
                         ('source_sha256', 'wrong'), ('image_sha256', 'wrong')]:
            data = source(); data[1][0]['source_binding'][key] = bad
            self.assertEqual(run_source(data)['status'], 'UNAVAILABLE')

    def test_foreign_nested_marker_or_opening_is_quarantined(self):
        for case in ['opening', 'glyph', 'relations', 'glyph_sha', 'symbol_id']:
            data = source(); g = data[1][0]; m = marker(g)
            if case == 'opening':
                data[2]['opening_geometry_comparison']['source_candidates'][0][0]['source_binding']['frame_id'] = 'Target:foreign'
            elif case == 'glyph':
                m['text'] = dict(frame_id='Target:foreign')
            elif case == 'glyph_sha':
                m['text'] = dict(source_sha256='foreign')
            elif case == 'symbol_id':
                m['id'] = 'Target:foreign:symbol'
            else:
                g['reference_relations']['source_binding'] = dict(source_sha256='foreign')
            result = run_source(data)
            self.assertTrue(result['rejected_inputs'])
            self.assertFalse(any(r['kind'] == 'ReferenceAssociation' for r in result['relations']))

    def test_incomplete_reading_remains_unknown_and_never_defaults_number(self):
        data = source(); marker(data[1][0], code=None)
        review, = run_source(data)['marker_reviews']
        self.assertIsNone(review['canonical_code'])
        self.assertIsNone(review['interpretation'])
        self.assertIn('INCOMPLETE_OR_AMBIGUOUS_READING', review['reason_codes'])

    def test_legacy_marker_without_reading_state_does_not_block_comparison(self):
        data = source(); row = marker(data[1][0], code=None); row.pop('reading_status')
        graph = run_source(data)
        self.assertEqual(graph['status'], 'REVIEW_AVAILABLE')
        self.assertEqual(graph['marker_reviews'][0]['physical_assignment_status'], 'UNBOUND')
        symbol = next(e for e in graph['entities'] if e['kind'] == 'Symbol')
        self.assertEqual(symbol['reading_status'], 'LEGACY_UNSPECIFIED')

    def test_reference_independent_of_target_changes(self):
        data = source(); marker(data[1][0]); before = run_source(data)
        data[1][1].clear(); data[2]['opening_geometry_comparison']['source_candidates'][1] = []
        self.assertEqual(run_source(data), before)

    def test_comparison_judgments_and_geometry_output_are_not_reclassified(self):
        data = source(); p, g, r = data; m = marker(g[0]); before = deepcopy(r['correspondences'])
        attach_element_grounding(r, p, g)
        after = deepcopy(r['correspondences'])
        for row in after:
            row.pop('element_grounding_entity_ids', None)
            row.pop('element_grounding_review_ids', None)
        self.assertEqual(before, after)
        self.assertFalse(r['element_grounding']['detection_decision_changed'])
        self.assertEqual(r['opening_geometry_comparison']['physical_assignment_status'], 'UNBOUND')

    def door(self, leaf=1, arc=1):
        data = source(); p, gs, r = data; g = gs[0]; frame = g['frame_id']; marker(g, code='PD/1', box=(204, 204, 220, 220))
        evidence = frame+':evidence:door'; opening = frame+':opening:door'; door = frame+':installation:door'; arc_id = frame+':arc'
        geometry = dict(bbox_native=[200, 200, 260, 260], polylines_native=[[[200, 200], [260, 200]]])
        g['architectural_assessment'] = dict(openings=[dict(opening_id=opening, geometry_native=geometry, evidence_refs=[evidence],
            decision='HOLD', reasons=['HOST_WALL_GEOMETRY_NOT_ADMISSIBLE'])], installed_elements=[dict(installation_id=door,
            opening_id=opening, geometry_native=geometry, type_candidates=['Door'], evidence_refs=[evidence])])
        g['evidence'] = [dict(evidence_id=evidence, supporting=[dict(kind='SWING_LEAF_AND_BOTH_JAMBS', arc_observation_ref=arc_id,
            leaf_candidates=[dict(visible_fraction=leaf)])])]
        g['reference_relations']['door_reviews'] = [dict(id=frame+':review:door', door_id=door,
            reobservation=dict(arc_checks=[dict(arc_observation_ref=arc_id, visible_fraction=arc, both_jambs_supported=True)]))]
        return data

    def test_swing_leaf_jamb_evidence_usable_when_host_and_space_are_held(self):
        graph = run_source(self.door()); relation, = [r for r in graph['relations'] if r['kind'] == 'Installation' and r['claim_status'] == 'SUPPORTED']
        self.assertEqual(relation['host_status'], 'HOLD')
        self.assertEqual(relation['passage_status'], 'UNKNOWN')
        review = graph['marker_reviews'][0]
        self.assertEqual(len(review['candidate_relation_ids']), 1)
        self.assertEqual(review['physical_assignment_status'], 'UNBOUND')

    def test_original_host_hold_is_retained_alongside_supported_door_drawing(self):
        data = self.door(); g = data[1][0]; parent = g['frame_id']+':host:held'
        opening = g['architectural_assessment']['openings'][0]; opening['host_parent_ids'] = [parent]
        g['architectural_assessment']['structural_parents'] = [dict(parent_structure_id=parent, decision='HOLD', fragments=[
            dict(fragment_id=parent+':fragment:0', boundary_faces_native=[[[180,200],[200,200]],[[180,210],[200,210]]],
                 source_observation_refs=[g['frame_id']+':observed:face'], decision='HOLD', reasons=['NO_OBSERVED_JUNCTION_SUPPORT'])])]
        graph = run_source(data)
        host = next(r for r in graph['relations'] if r['kind'] == 'Hosting' and r['source_id'] == parent)
        self.assertEqual(host['claim_status'], 'PROPOSED')
        wall = next(e for e in graph['entities'] if e['id'] == parent)
        self.assertEqual(wall['source_parent_decision'], 'HOLD')
        self.assertTrue(any(r['kind'] == 'Installation' and r['claim_status'] == 'SUPPORTED' for r in graph['relations']))

    def test_missing_visible_leaf_or_swing_prevents_supported_door_geometry(self):
        for leaf, arc in [(0, 1), (1, 0)]:
            graph = run_source(self.door(leaf, arc))
            self.assertFalse(any(r['kind'] == 'Installation' and r['claim_status'] == 'SUPPORTED' for r in graph['relations']))

    def test_grille_code_cannot_type_a_door_or_infer_fire_rating(self):
        data = self.door(); data[1][0]['reference_relations']['code_reviews'][0]['canonical_code'] = 'AG/1'
        review = run_source(data)['marker_reviews'][0]
        self.assertEqual(review['candidate_relation_ids'], [])
        self.assertEqual(len(review['incompatible_candidates']), 1)
        self.assertEqual(review['fire_rating_status'], 'UNKNOWN')

    def test_element_graph_is_linked_to_source_objects_and_upstream_trace(self):
        from backend.change_candidate_audit import attach_candidate_audit
        from backend.upstream_lineage import attach_upstream_lineage
        from backend.observation_trace import iter_records
        data = source(); p, gs, result = data; marker(gs[0])
        for g in gs:g['run_id'] = 'SYNTHETIC_ONLY'
        attach_element_grounding(result, p, gs)
        self.assertTrue(all(o.get('element_grounding_entity_ids') for o in result['source_objects']['Ref']))
        attach_candidate_audit(result); attach_upstream_lineage(result, gs)
        events = [e for s in result['upstream_lineage']['streams'] for e in iter_records(s)]
        records = [e for e in events if e['stage'] == 'ELEMENT_GROUNDING']
        self.assertTrue(records)
        relation = next(r for r in result['element_grounding']['sources']['Ref']['relations'] if r['kind'] == 'ReferenceAssociation')
        record = next(e for e in records if e['subject_id'] == relation['id'])
        self.assertIn(relation['source_id'], record['input_ids'])
        self.assertIn(relation['target_id'], record['input_ids'])


if __name__ == '__main__':
    unittest.main()
