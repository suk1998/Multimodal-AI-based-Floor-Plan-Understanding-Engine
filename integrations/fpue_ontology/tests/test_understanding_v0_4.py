"""Synthetic schema/contract regressions, not real plan understanding metrics."""
from copy import deepcopy
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

from rdflib import Graph, Literal
from rdflib.compare import isomorphic
from rdflib.namespace import OWL, RDF, RDFS

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))
from integrations.fpue_ontology import understanding_contract_v0_4 as contract
from integrations.fpue_ontology.schema_v0_4 import U, build_understanding_schema
from integrations.fpue_ontology.schema_v0_3 import build_understanding_schema as previous_schema
from integrations.fpue_ontology.examples_v0_4 import closure_fixture, alternatives_fixture, shared_fixture, residual_fixture, generate_examples
from integrations.fpue_ontology.region_graph_v0_4 import validate_graph, to_rdf, invalidation_plan, plan_review_requests


class RegionSchemaTests(unittest.TestCase):
    def test_v03_snapshot_and_namespaces_are_preserved(self):
        saved = ROOT/'ontology_schema_v03_validation/2026-11-18/reviewed_examples/fpue_understanding_v0_3.ttl'
        self.assertTrue(isomorphic(previous_schema(), Graph().parse(saved, format='turtle')))
        new = build_understanding_schema()
        self.assertFalse(any(str(term).startswith('https://example.org/fpue/understanding/0.3#') for triple in new for term in triple))
        self.assertFalse(list(new.triples((None, OWL.equivalentClass, None))))

    def test_inferred_boundary_is_not_wall_and_region_is_not_space(self):
        schema = build_understanding_schema()
        self.assertIn((U.InferredBoundary, OWL.disjointWith, U.BuildingElement), schema)
        self.assertIn((U.SpatialRegion, OWL.disjointWith, U.Space), schema)
        self.assertFalse(contract.is_a('ClosureBoundary', 'Wall'))
        self.assertTrue(contract.is_a('FunctionalSpace', 'Space'))
        self.assertFalse(contract.is_a('PartitionRegion', 'Space'))

    def test_contract_digest_and_four_function_mapping(self):
        schema = build_understanding_schema()
        self.assertEqual(str(schema.value(U[''], U.applicationContractSHA256)), contract.contract_digest())
        self.assertEqual(len(contract.FUNCTION_PROFILE_MAPPING), 4)
        self.assertEqual(set(contract.ENGINE_PROFILES), {'GEOMETRIC', 'SPATIAL', 'SEMANTIC', 'RELATIONAL'})
        self.assertIn('SEMANTIC', contract.FUNCTION_PROFILE_MAPPING['SpatialUnderstanding']['profiles'])
        self.assertFalse(contract.OPERATION_CONTRACTS['FRAGMENT_CLOSURE']['requires_confirmed_door'])
        self.assertFalse(contract.OPERATION_CONTRACTS['FRAGMENT_CLOSURE']['requires_four_observed_walls'])

    def test_schema_has_no_apartment_layout_rules_or_direct_transitive_connection(self):
        schema = build_understanding_schema()
        self.assertNotIn((U.connectedTo, RDF.type, OWL.TransitiveProperty), schema)
        for text in ['84A', '84B', '84C']:
            self.assertFalse(any(text in str(term) for triple in schema for term in triple))

    def test_schema_and_examples_roundtrip_and_declarations(self):
        schema = build_understanding_schema()
        declared = {s for s, p, o in schema.triples((None, RDF.type, None)) if o in {OWL.Class, OWL.ObjectProperty, OWL.DatatypeProperty, OWL.AnnotationProperty}}
        for graph in [schema] + [to_rdf(factory()) for factory in [closure_fixture, alternatives_fixture, shared_fixture, residual_fixture]]:
            self.assertTrue(isomorphic(graph, Graph().parse(data=graph.serialize(format='turtle'), format='turtle')))
            if graph is schema:
                continue
            for subject, predicate, obj in graph:
                if str(predicate).startswith(str(U)):
                    self.assertIn(predicate, declared)
                if predicate == RDF.type and str(obj).startswith(str(U)):
                    self.assertIn(obj, declared)

    def test_restricted_type_inference_keeps_hypotheses_separate(self):
        schema = build_understanding_schema()
        for factory in [closure_fixture, alternatives_fixture, shared_fixture, residual_fixture]:
            graph = to_rdf(factory())
            inferred = set(graph.subject_objects(RDF.type))
            for subject, predicate, obj in graph:
                inferred.update((subject, domain) for domain in schema.objects(predicate, RDFS.domain))
                if not isinstance(obj, Literal):
                    inferred.update((obj, range_) for range_ in schema.objects(predicate, RDFS.range))
            while True:
                extra = {(node, parent) for node, kind in inferred for parent in schema.objects(kind, RDFS.subClassOf)} - inferred
                if not extra:
                    break
                inferred.update(extra)
            proposed = {node for node, kind in inferred if kind == U.Hypothesis}
            physical = {node for node, kind in inferred if kind in {U.ArchitecturalEntity, U.Connection}}
            self.assertTrue(proposed)
            self.assertFalse(proposed & physical)

    def test_cli_explicit_v04_selection_and_no_overwrite(self):
        with tempfile.TemporaryDirectory() as directory:
            args = [sys.executable, '-B', str(ROOT/'integrations/fpue_ontology/schema.py'), '--schema-version', '0.4', '--output-dir', directory]
            result = subprocess.run(args, cwd=ROOT, capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stderr)
            path = Path(directory)/'fpue_understanding_v0_4.ttl'
            before = path.read_bytes()
            self.assertTrue(isomorphic(build_understanding_schema(), Graph().parse(path, format='turtle')))
            self.assertNotEqual(subprocess.run(args, cwd=ROOT, capture_output=True).returncode, 0)
            self.assertEqual(path.read_bytes(), before)


class RegionContractTests(unittest.TestCase):
    def test_three_fragments_create_region_hypothesis_without_door_or_four_walls(self):
        graph = closure_fixture()
        self.assertEqual(len(graph['observations']), 3)
        self.assertEqual({entity['expected_type'] for entity in graph['entities']}, {'ClosureBoundary', 'PartitionRegion'})
        self.assertTrue(all(c['status'] == 'PROPOSED' for c in graph['claims']))
        self.assertEqual(graph['source_binding']['origin'], 'SYNTHETIC_CONTRACT_TEST')

    def test_unknown_gap_stays_unknown_and_review_is_not_executed(self):
        graph = closure_fixture()
        before = json.dumps(graph, sort_keys=True)
        requests = plan_review_requests(graph)
        self.assertEqual(len(requests), 1)
        self.assertEqual(requests[0]['question'], 'INTERPRET_CLOSURE_GAP')
        self.assertFalse(requests[0]['executable'])
        self.assertEqual(before, json.dumps(graph, sort_keys=True))

    def test_closure_cannot_output_physical_wall(self):
        graph = closure_fixture()
        graph['entities'][0]['expected_type'] = 'Wall'
        graph['relations'] = []  # Isolate operation typing from earlier relation typing.
        with self.assertRaisesRegex(ValueError, 'operation output'):
            validate_graph(graph)

    def test_closure_cannot_assert_physical_wall_existence(self):
        graph = closure_fixture()
        graph['claims'][1].update(predicate='wall_exists', value=True)
        with self.assertRaisesRegex(ValueError, 'physical wall'):
            validate_graph(graph)

    def test_text_box_cannot_replace_boundary_fragment(self):
        graph = closure_fixture()
        graph['observations'][0]['observation_type'] = 'TextRegion'
        with self.assertRaisesRegex(ValueError, 'text boxes'):
            validate_graph(graph)

    def test_frame_and_ref_target_mixing_are_rejected(self):
        for field, value in [('source_role', 'Target'), ('frame_id', 'other-frame')]:
            graph = closure_fixture()
            graph['entities'][0][field] = value
            with self.assertRaisesRegex(ValueError, 'mixed'):
                validate_graph(graph)

    def test_geometry_outside_image_and_nonfinite_coordinates_are_rejected(self):
        for value in [-1, 101, float('nan'), float('inf')]:
            graph = closure_fixture()
            graph['entities'][0]['geometry']['points_native'][0][0] = value
            with self.assertRaises(ValueError):
                validate_graph(graph)

    def test_geometry_frame_and_polygon_state_mismatch_are_rejected(self):
        graph = closure_fixture()
        graph['entities'][0]['geometry']['frame_id'] = 'wrong'
        with self.assertRaises(ValueError):
            validate_graph(graph)
        graph = closure_fixture()
        graph['entities'][1]['geometry'] = None
        with self.assertRaisesRegex(ValueError, 'available polygon'):
            validate_graph(graph)

    def test_degenerate_or_crossing_polygon_is_rejected(self):
        for ring in [[[20, 20], [40, 40], [60, 60]], [[20, 20], [80, 80], [80, 20], [20, 80]]]:
            graph = closure_fixture()
            graph['entities'][1]['geometry']['outer_rings'] = [ring]
            with self.assertRaises(ValueError):
                validate_graph(graph)

    def test_self_supporting_closure_cycle_is_rejected(self):
        graph = closure_fixture()
        graph['derivations'][0]['input_ids'].append('closing-edge')
        with self.assertRaisesRegex(ValueError, 'cyclic'):
            validate_graph(graph)

    def test_shared_space_does_not_require_individual_polygons_or_opening(self):
        graph = shared_fixture()
        spaces = [e for e in graph['entities'] if e['expected_type'] == 'FunctionalSpace']
        self.assertEqual(len(spaces), 3)
        self.assertTrue(all(space['geometry'] is None for space in spaces))
        self.assertFalse(any(e['expected_type'] in {'Opening', 'Door'} for e in graph['entities']))
        self.assertEqual(len([r for r in graph['relations'] if r['kind'] == 'Connection']), 1)

    def test_connection_existence_is_independent_of_mode(self):
        graph = shared_fixture()
        self.assertEqual(graph['claims'][0]['value'], 'UNKNOWN')
        self.assertEqual(graph['claims'][1]['value'], 'OpenPlan')
        graph['claims'][1]['value'] = 'Unknown'
        graph['relations'][-1]['mode'] = 'Unknown'
        validate_graph(graph)

    def test_connection_mode_and_claim_cannot_disagree(self):
        graph = shared_fixture()
        graph['claims'][1]['value'] = 'Door'
        with self.assertRaisesRegex(ValueError, 'mismatch'):
            validate_graph(graph)

    def test_window_cannot_be_a_walkable_connection_via(self):
        graph = shared_fixture()
        window = deepcopy(graph['entities'][1])
        window.update(id='window', expected_type='Window')
        graph['entities'].append(window)
        graph['relations'][-1]['via_entity_id'] = 'window'
        with self.assertRaisesRegex(ValueError, 'via cannot'):
            validate_graph(graph)

    def test_door_mode_requires_separate_door_hypothesis(self):
        graph = shared_fixture()
        graph['claims'][1]['value'] = 'Door'
        graph['relations'][-1]['mode'] = 'Door'
        with self.assertRaisesRegex(ValueError, 'separate Door'):
            validate_graph(graph)

    def test_shared_region_and_partial_geometry_states_can_coexist(self):
        graph = shared_fixture()
        graph['entities'][0].update(geometry=None, geometry_state='PARTIAL_BOUNDARY')
        validate_graph(graph)
        self.assertEqual(graph['entities'][0]['region_kind'], 'SHARED')
        self.assertEqual(graph['claims'][-1]['status'], 'PROPOSED')

    def test_residual_is_unassigned_and_preserves_unknown_room_coverage(self):
        graph = residual_fixture()
        residual = next(e for e in graph['entities'] if e['id'] == 'residual')
        self.assertEqual(residual['expected_type'], 'UnassignedRegion')
        self.assertEqual(residual['region_kind'], 'UNASSIGNED')
        self.assertFalse(graph['derivations'][-1]['inventory_complete'])
        self.assertIn('unresolved-label', graph['derivations'][-1]['unknown_coverage'][0]['observation_ids'])

    def test_difference_cannot_output_a_named_functional_space(self):
        graph = residual_fixture()
        graph['entities'][-1]['expected_type'] = 'FunctionalSpace'
        with self.assertRaisesRegex(ValueError, 'operation output'):
            validate_graph(graph)

    def test_difference_cannot_claim_complete_inventory(self):
        graph = residual_fixture()
        graph['derivations'][-1]['inventory_complete'] = True
        with self.assertRaisesRegex(ValueError, 'partial inventory'):
            validate_graph(graph)

    def test_operation_requires_scalar_domain_and_matching_typed_operands(self):
        graph = residual_fixture()
        graph['derivations'][0]['operands']['domain'] = []
        with self.assertRaisesRegex(ValueError, 'scalar domain'):
            validate_graph(graph)
        graph = residual_fixture()
        graph['derivations'][-1]['operands']['partitions'] = ['occupied']
        with self.assertRaisesRegex(ValueError, 'typed operation'):
            validate_graph(graph)

    def test_occupancy_change_propagates_stale_without_overwriting_graph(self):
        graph = residual_fixture()
        before = json.dumps(graph, sort_keys=True)
        result = invalidation_plan(graph, ['occupied'])
        self.assertTrue({'free-domain', 'free', 'difference', 'residual', 'residual-geometry'} <= set(result['stale_ids']))
        self.assertNotIn('unresolved-label', result['stale_ids'])
        self.assertFalse(result['applied'])
        self.assertEqual(before, json.dumps(graph, sort_keys=True))

    def test_verification_cannot_use_its_own_region_as_independent_gt(self):
        graph = closure_fixture()
        claim = graph['claims'][0]
        claim.update(status='VERIFIED', verification_record={'criteria_version': 'test', 'verified_scope': 'test region', 'independent_evidence_ids': ['room-region']})
        with self.assertRaisesRegex(ValueError, 'separate external evidence'):
            validate_graph(graph)

    def test_supported_claim_requires_source_evidence(self):
        graph = shared_fixture()
        graph['claims'][0]['status'] = 'SUPPORTED'
        with self.assertRaisesRegex(ValueError, 'source evidence'):
            validate_graph(graph)

    def test_verified_claim_needs_separate_review_provenance(self):
        graph = closure_fixture()
        binding = graph['source_binding']
        graph['external_evidence'].append({'id': 'independent-review', 'source_role': binding['source_role'],
            'frame_id': binding['frame_id'], 'evidence_origin': 'INDEPENDENT_REVIEW',
            'notice': 'Synthetic metadata for verifier testing, not actual GT.'})
        graph['claims'][0].update(status='VERIFIED', verification_record={'criteria_version': 'synthetic-check/1',
            'verified_scope': 'synthetic polygon coordinates only', 'independent_evidence_ids': ['independent-review']})
        validate_graph(graph)
        to_rdf(graph)
        graph['external_evidence'][0]['source_evidence_refs'] = ['top']
        with self.assertRaisesRegex(ValueError, 'independent review provenance'):
            validate_graph(graph)

    def test_changed_claim_invalidates_its_subject_and_derivatives(self):
        graph = residual_fixture()
        plan = invalidation_plan(graph, ['occupied-geometry'])
        self.assertTrue({'occupied', 'free', 'residual'} <= set(plan['stale_ids']))

    def test_competing_regions_are_not_automatically_selected(self):
        graph = alternatives_fixture()
        self.assertEqual(len(graph['alternatives'][0]['member_ids']), 2)
        self.assertEqual(graph['alternatives'][0]['selection'], 'UNRESOLVED')
        rdf = to_rdf(graph)
        self.assertFalse(list(rdf.subjects(RDF.type, U.Wall)))
        self.assertFalse(list(rdf.subjects(RDF.type, U.Space)))

    def test_rdf_ids_do_not_collide_with_frame_or_geometry_helpers(self):
        graph = shared_fixture()
        extra = deepcopy(graph['entities'][1])
        extra.update(id='snapshot', label='synthetic collision check')
        graph['entities'].append(extra)
        rdf = to_rdf(graph)
        snapshots = set(rdf.subjects(RDF.type, U.GraphSnapshot))
        hypotheses = set(rdf.subjects(RDF.type, U.SpaceHypothesis))
        self.assertFalse(snapshots & hypotheses)

    def test_generation_marks_synthetic_origin_and_preserves_existing_outputs(self):
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory)/'new'
            audit = generate_examples(output)
            self.assertEqual(len(audit), 4)
            self.assertTrue(all(row['origin'] == 'SYNTHETIC_CONTRACT_TEST' and not row['actual_image_analysis_performed'] for row in audit))
            before = (output/'three_fragment_closure.json').read_bytes()
            with self.assertRaises(FileExistsError):
                generate_examples(output)
            self.assertEqual((output/'three_fragment_closure.json').read_bytes(), before)


if __name__ == '__main__':
    unittest.main()
