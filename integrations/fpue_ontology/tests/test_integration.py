"""Synthetic contract checks and separate real-artifact mapping regressions."""
from __future__ import annotations

import copy
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))

from rdflib import Graph, Literal
from rdflib.compare import isomorphic
from rdflib.namespace import OWL, RDF, RDFS, XSD

from integrations.fpue_ontology.adapter import inspect_directory, normalize_bundle, write_conversion
from integrations.fpue_ontology.bridge import convert_normalized
from integrations.fpue_ontology.schema import FP, build_schema, build_base_vocabulary, element_type_uri

PACKAGE = Path(__file__).resolve().parents[1]
REAL = ROOT / 'poc/results/space_grounding_page4'


def raw_fixture():
    bundle = {'schema_version': 'space_grounding/1', 'coordinate_system': 'image_native_pixels', 'source_bindings': {}, 'frame_registry': {}, 'entities': [], 'relations': [], 'claims': []}
    for role, ch in [('Ref', 'a'), ('Target', 'b')]:
        frame = role + ':native'
        binding = {'source_sha256': ch * 64, 'image_sha256': ch * 64, 'page': 4, 'extraction_zoom': 2, 'width': 100, 'height': 80, 'frame_id': frame, 'coordinate_system': 'image_native_pixels', 'role': role}
        bundle['source_bindings'][role] = binding
        bundle['frame_registry'][role] = {'pdf_user_to_native': [[2, 0, 0], [0, -2, 80], [0, 0, 1]]}
        for suffix, typ in [('s1', 'Space'), ('s2', 'Space'), ('w', 'Wall'), ('d', 'Door'), ('win', 'Window'), ('t', 'TextRegion'), ('sym', 'Symbol')]:
            record = {'id': role + ':' + suffix, 'type': typ, 'role': role, 'frame_id': frame, 'source_binding': copy.deepcopy(binding), 'geometry': {'coordinate_system': 'image_native_pixels'}, 'claim_status': 'PROPOSED', 'verification_status': 'PROPOSED', 'geometry_evidence_ids': [role + ':evidence:' + suffix], 'claims': []}
            if typ == 'Space':
                record['name_hypotheses'] = [{'text': '서재', 'status': 'PROPOSED'}] if suffix == 's1' else []
                record['geometry'].update(seed_native=[15, 25], polygon_native=None, bbox_xyxy=None)
            if typ == 'TextRegion':
                record.update(text='서재', recognition_score=.99)
                record['geometry'].update(bbox_xyxy=[10, 10, 20, 20], polygon_native=[[10, 10], [20, 10], [20, 20], [10, 20]])
            bundle['entities'].append(record)
    return bundle


def raw_relation(predicate='labels', src='Ref:t', dst='Ref:s1', **extra):
    result = {'id': 'Ref:r', 'role': 'Ref', 'frame_id': 'Ref:native', 'subject_id': src, 'predicate': predicate, 'object_ids': [dst], 'status': 'SUPPORTED', 'verification_status': 'SUPPORTED', 'evidence_ids': ['Ref:rel-evidence']}
    result.update(extra)
    return result


def normalized_fixture():
    return {'document': {'id': 'doc', 'role': 'REF', 'coordinate_frame': {'id': 'frame', 'kind': 'image_native_pixels', 'width': 100, 'height': 80}}, 'spaces': [{'id': 's1'}, {'id': 's2'}], 'elements': [{'id': 'wall', 'category': 'Wall'}, {'id': 'door', 'category': 'Door'}, {'id': 'window', 'category': 'Window'}, {'id': 'ps', 'category': 'PipeShaft'}], 'observations': [{'id': 'text', 'category': 'TextRegion'}, {'id': 'symbol', 'category': 'Symbol'}]}


class BridgeRegressionTests(unittest.TestCase):
    def test_all_claim_statuses_preserved_without_confirmation(self):
        data = normalized_fixture()
        statuses = ['PROPOSED', 'SUPPORTED', 'VERIFIED', 'CONFLICTED', 'STALE', 'UNKNOWN', 'UNCERTAIN', 'REJECTED']
        for index, status in enumerate(statuses):
            data['spaces'].append({'id': f'status{index}', 'status': status})
        graph = convert_normalized(data)
        self.assertFalse(list(graph.subjects(FP.reviewStatus, FP.Confirmed)))
        for status in (FP.Proposed, FP.Supported, FP.Verified, FP.Conflicted, FP.Stale, FP.Unknown, FP.Uncertain, FP.Rejected):
            self.assertTrue(list(graph.subjects(FP.reviewStatus, status)))

    def test_invalid_relation_types_rejected(self):
        cases = [('Adjacency', 'text', 'window'), ('BoundaryAssociation', 's1', 'ps'), ('ReferenceAssociation', 'text', 'window'), ('HostingRelation', 'door', 'wall'), ('LabelAssociation', 'wall', 's1'), ('Containment', 'text', 'window'), ('Adjacency', 's1', 's1')]
        for kind, src, dst in cases:
            with self.subTest(kind=kind):
                data = normalized_fixture(); data['relations'] = [{'id': 'bad', 'kind': kind, 'source_id': src, 'target_id': dst}]
                with self.assertRaises(ValueError): convert_normalized(data)

    def test_text_can_label_symbol_without_labeling_space(self):
        data = normalized_fixture(); data['relations'] = [{'id': 'label', 'kind': 'LabelAssociation', 'source_id': 'text', 'target_id': 'symbol'}]
        graph = convert_normalized(data)
        relation = next(graph.subjects(RDF.type, FP.LabelAssociation))
        target = graph.value(relation, FP.relationTarget)
        self.assertIn((target, RDF.type, FP.Symbol), graph)
        self.assertNotIn((target, RDF.type, FP.Space), graph)

    def test_window_or_unobserved_door_cannot_be_connection(self):
        for via in ('window', None, 'wall'):
            data = normalized_fixture(); data['relations'] = [{'id': 'c', 'kind': 'Connection', 'source_id': 's1', 'target_id': 's2', 'mode': 'Door', 'via_element_id': via, 'evidence_ids': ['observed']}]
            with self.subTest(via=via), self.assertRaises(ValueError): convert_normalized(data)

    def test_door_connection_needs_observation_evidence(self):
        data = normalized_fixture(); data['relations'] = [{'id': 'c', 'kind': 'Connection', 'source_id': 's1', 'target_id': 's2', 'mode': 'Door', 'via_element_id': 'door'}]
        with self.assertRaises(ValueError): convert_normalized(data)
        data['relations'][0]['evidence_ids'] = ['actual-candidate-evidence']
        self.assertEqual(len(list(convert_normalized(data).subjects(RDF.type, FP.Connection))), 1)

    def test_no_automatic_direct_spatial_facts(self):
        data = normalized_fixture(); data['relations'] = [{'id': 'r', 'kind': 'Adjacency', 'source_id': 's1', 'target_id': 's2', 'status': 'PROPOSED'}]
        graph = convert_normalized(data)
        self.assertFalse(list(graph.triples((None, FP.connectedTo, None))))
        self.assertFalse(list(graph.subjects(RDF.type, FP.ChangeEvent)))

    def test_plan_role_and_frame_partition_uris_including_geometry(self):
        base = normalized_fixture(); base['spaces'][0]['bbox'] = [1, 2, 5, 6]
        other = copy.deepcopy(base); other['document']['role'] = 'TARGET'
        another_frame = copy.deepcopy(base); another_frame['document']['coordinate_frame']['id'] = 'other-frame'
        graphs = [convert_normalized(d) for d in (base, other, another_frame)]
        for typ in (FP.Space, FP.Geometry, FP.FloorPlan, FP.CoordinateFrame):
            sets = [set(g.subjects(RDF.type, typ)) for g in graphs]
            self.assertFalse(sets[0] & sets[1]); self.assertFalse(sets[0] & sets[2])

    def test_old_slash_geometry_identifier_collision_is_removed(self):
        a = normalized_fixture(); a['document']['id'] = 'a/space/b'; a['spaces'] = [{'id': 'c', 'bbox': [1, 2, 5, 6]}]
        b = normalized_fixture(); b['document']['id'] = 'a'; b['spaces'] = [{'id': 'b/space/c', 'bbox': [1, 2, 5, 6]}]
        self.assertFalse(set(convert_normalized(a).subjects(RDF.type, FP.Geometry)) & set(convert_normalized(b).subjects(RDF.type, FP.Geometry)))

    def test_geometry_json_preserves_holes_partial_boundaries_and_endpoint_meaning(self):
        data = normalized_fixture()
        geometry = {'coordinate_system': 'image_native_pixels', 'polygon_native': {'outer_rings': [[[1, 1], [40, 1], [40, 40], [1, 40]]], 'holes': [[[10, 10], [20, 10], [20, 20], [10, 20]]]}, 'partial_physical_boundary_segments': [{'points_native': [[1.25, 1.5], [5.75, 1.5]], 'coverage': 'analysis interval, not physical endpoint'}]}
        data['spaces'][0]['geometry_native'] = geometry
        graph = convert_normalized(data)
        self.assertEqual(json.loads(next(graph.objects(None, FP.geometryJSON))), geometry)
        self.assertFalse(list(graph.objects(None, FP.minX)))

    def test_scoped_claim_not_upgraded_to_entity_verification(self):
        data = normalized_fixture(); data['claims'] = [{'id': 'cl', 'subject': 's1', 'predicate': 'enclosure', 'value': True, 'scope': 'geometry only', 'status': 'SUPPORTED', 'evidence_ids': ['ev']}]
        graph = convert_normalized(data); claim = next(graph.subjects(RDF.type, FP.Claim)); subject = graph.value(claim, FP.claimSubject)
        self.assertIn((claim, FP.reviewStatus, FP.Supported), graph)
        self.assertIn((subject, FP.reviewStatus, FP.Proposed), graph)
        self.assertEqual(str(graph.value(claim, FP.claimScope)), 'geometry only')

    def test_finite_coordinates_and_strict_dimensions(self):
        for bbox in ([1, 1, float('inf'), 5], [1, 1, float('nan'), 5], [True, 1, 5, 5], [-1, 1, 5, 5], [1, 1, 101, 5]):
            data = normalized_fixture(); data['spaces'][0]['bbox'] = bbox
            with self.subTest(bbox=bbox), self.assertRaises(ValueError): convert_normalized(data)
        data = normalized_fixture(); data['document']['coordinate_frame']['width'] = True
        with self.assertRaises(ValueError): convert_normalized(data)

    def test_native_points_outside_frame_rejected_without_clipping(self):
        data = normalized_fixture(); data['spaces'][0]['geometry_native'] = {'seed_native': [101, 12]}
        with self.assertRaises(ValueError): convert_normalized(data)

    def test_native_coordinate_kind_required(self):
        data = normalized_fixture(); data['document']['coordinate_frame']['kind'] = 'pdf_points'
        with self.assertRaises(ValueError): convert_normalized(data)
        data = normalized_fixture(); data['spaces'][0]['geometry_native'] = {'coordinate_system': 'pdf_points', 'seed_native': [5, 5]}
        with self.assertRaises(ValueError): convert_normalized(data)

    def test_candidate_record_frame_mismatch_rejected(self):
        data = normalized_fixture(); data['spaces'][0]['frame_id'] = 'other'
        with self.assertRaises(ValueError): convert_normalized(data)

    def test_confidence_range_not_guessed(self):
        for confidence in (float('nan'), -1, 1.1, True):
            data = normalized_fixture(); data['spaces'][0]['confidence'] = confidence
            with self.subTest(confidence=confidence), self.assertRaises(ValueError): convert_normalized(data)

    def test_unregistered_code_scoped_without_material_inference(self):
        data = normalized_fixture(); data['elements'][0]['type_code'] = 'NEW/97'
        graph = convert_normalized(data)
        self.assertIn((element_type_uri('INITIAL_USER_VOCAB', 'NEW/97'), FP.typeCode, Literal('NEW/97')), graph)
        self.assertFalse(list(graph.triples((None, FP.hasMaterial, None))))

    def test_language_tagged_vocabulary_range_and_original_ttl_parse(self):
        self.assertEqual(build_schema().value(FP.canonicalLabel, RDFS.range), RDFS.Literal)
        vocab = build_base_vocabulary()
        self.assertTrue(any(o.language == 'ko' for o in vocab.objects(None, FP.canonicalLabel)))
        for path in (PACKAGE / 'output').glob('*.ttl'):
            with self.subTest(path=path): self.assertGreater(len(Graph().parse(path, format='turtle')), 0)
        self.assertTrue(isomorphic(vocab, Graph().parse(PACKAGE / 'output/fpue_initial_vocabulary.ttl', format='turtle')))

    def test_cli_refuses_overwriting_existing_ttl(self):
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / 'sentinel.ttl'; target.write_text('PRESERVE')
            result = subprocess.run([sys.executable, '-B', '-m', 'integrations.fpue_ontology.bridge', '--input', str(PACKAGE / 'examples/manual_84a_ref_subset.json'), '--output', str(target)], cwd=ROOT, capture_output=True)
            self.assertNotEqual(result.returncode, 0); self.assertEqual(target.read_text(), 'PRESERVE')


class AdapterContractTests(unittest.TestCase):
    def setUp(self): self.bundle = raw_fixture()

    def test_no_source_mutation_and_stable_identity(self):
        before = copy.deepcopy(self.bundle)
        a, _ = normalize_bundle(self.bundle, 'Ref'); b, _ = normalize_bundle(self.bundle, 'Ref')
        self.assertEqual(a, b); self.assertEqual(self.bundle, before)
        self.assertTrue(isomorphic(convert_normalized(a), convert_normalized(b)))

    def test_target_changes_do_not_modify_reference_normalization(self):
        before, _ = normalize_bundle(self.bundle, 'Ref')
        for entity in self.bundle['entities']:
            if entity['role'] == 'Target': entity['extra_target_result'] = 'changed'
        after, _ = normalize_bundle(self.bundle, 'Ref')
        self.assertEqual(before, after)
        self.assertNotIn('Target:', json.dumps(after, ensure_ascii=False))

    def test_unknown_status_preserved_without_confirmation(self):
        self.bundle['entities'][0]['claim_status'] = 'FUTURE_STATE'
        data, report = normalize_bundle(self.bundle, 'Ref')
        self.assertEqual(data['spaces'][0]['status'], 'UNKNOWN')
        self.assertEqual(data['spaces'][0]['source_record']['claim_status'], 'FUTURE_STATE')
        self.assertTrue(report['warnings'])

    def test_unlabeled_space_and_unknown_name_not_discarded(self):
        data, _ = normalize_bundle(self.bundle, 'Ref'); graph = convert_normalized(data)
        self.assertEqual(len(data['spaces']), 2)
        self.assertEqual(data['spaces'][0]['raw_label'], '서재')
        self.assertFalse(list(graph.triples((None, FP.hasSpaceType, None))))
        self.assertFalse(list(graph.triples((None, FP.labelState, FP.LabelAbsent))))
        self.assertIsNone(data['spaces'][0]['bbox'])

    def test_ocr_confidence_not_space_or_entity_confidence(self):
        data, _ = normalize_bundle(self.bundle, 'Ref')
        text = next(r for r in data['observations'] if r['category'] == 'TextRegion')
        self.assertEqual(text['recognition_confidence'], .99)
        self.assertNotIn('confidence', text)
        self.assertNotIn('confidence', data['spaces'][0])

    def test_multiple_names_not_resolved_by_first_match(self):
        self.bundle['entities'][0]['name_hypotheses'].append({'text': '침실1'})
        data, report = normalize_bundle(self.bundle, 'Ref')
        self.assertNotIn('raw_label', data['spaces'][0]); self.assertEqual(len(data['spaces'][0]['name_hypotheses']), 2)
        self.assertTrue(report['warnings'])

    def test_invalid_and_nonbinary_relations_are_retained_as_deferred(self):
        self.bundle['relations'] = [raw_relation('boundedBy', src='Ref:s1', dst='Ref:t')]
        data, _ = normalize_bundle(self.bundle, 'Ref')
        self.assertFalse(data['relations']); self.assertEqual(data['deferred_records'][0]['source_record'], self.bundle['relations'][0])
        self.assertEqual(len(list(convert_normalized(data).subjects(RDF.type, FP.DeferredRecord))), 1)
        self.bundle['relations'][0]['object_ids'].append('Ref:s2')
        data, _ = normalize_bundle(self.bundle, 'Ref'); self.assertFalse(data['relations']); self.assertIn('non-binary', data['deferred_records'][0]['reason'])

    def test_missing_endpoint_not_fabricated(self):
        self.bundle['relations'] = [raw_relation(dst='Ref:not-existing')]
        data, report = normalize_bundle(self.bundle, 'Ref')
        self.assertEqual(len(data['spaces']), 2); self.assertFalse(data['relations']); self.assertEqual(len(report['deferred']), 1)

    def test_adjacent_is_never_connection(self):
        self.bundle['relations'] = [raw_relation('adjacentTo', 'Ref:s1', 'Ref:s2')]
        data, _ = normalize_bundle(self.bundle, 'Ref'); graph = convert_normalized(data)
        self.assertEqual(len(list(graph.subjects(RDF.type, FP.Adjacency))), 1)
        self.assertFalse(list(graph.subjects(RDF.type, FP.Connection)))

    def test_connection_requires_explicit_opening_and_evidence(self):
        self.bundle['relations'] = [raw_relation('connects', 'Ref:s1', 'Ref:s2')]
        data, _ = normalize_bundle(self.bundle, 'Ref'); self.assertFalse(data['relations'])
        self.bundle['relations'][0].update(via_element_id='Ref:d', mode='Door')
        data, _ = normalize_bundle(self.bundle, 'Ref')
        self.assertEqual(len(list(convert_normalized(data).subjects(RDF.type, FP.Connection))), 1)
        self.assertEqual(data['relations'][0]['status'], 'SUPPORTED')

    def test_cross_role_relation_is_deferred_not_remapped(self):
        self.bundle['relations'] = [raw_relation(dst='Target:s1')]
        data, _ = normalize_bundle(self.bundle, 'Ref'); self.assertFalse(data['relations']); self.assertEqual(len(data['deferred_records']), 1)

    def test_cross_frame_and_source_binding_mismatch_are_errors(self):
        for key, value in [('frame_id', 'Target:native'), ('source_binding', self.bundle['source_bindings']['Target'])]:
            changed = copy.deepcopy(self.bundle); changed['entities'][0][key] = value
            with self.subTest(key=key), self.assertRaises(ValueError): normalize_bundle(changed, 'Ref')

    def test_duplicate_source_ids_rejected_semantic_duplicates_retained(self):
        self.bundle['entities'].append(copy.deepcopy(self.bundle['entities'][0]))
        with self.assertRaisesRegex(ValueError, 'duplicate id'): normalize_bundle(self.bundle, 'Ref')
        self.bundle = raw_fixture(); a = raw_relation(); b = copy.deepcopy(a); b['id'] = 'Ref:r2'; self.bundle['relations'] = [a, b]
        data, report = normalize_bundle(self.bundle, 'Ref'); self.assertEqual(len(data['relations']), 2)
        self.assertEqual(report['duplicate_semantic_relations_retained_by_distinct_source_ID'], 1)

    def test_top_level_claim_copy_deduplicated_not_strengthened(self):
        claim = {'id': 'Ref:cl', 'subject': 'Ref:s1', 'predicate': 'enclosure', 'value': True, 'status': 'SUPPORTED', 'verification_status': 'SUPPORTED', 'scope': 'geometry only'}
        self.bundle['entities'][0]['claims'] = [claim]; self.bundle['claims'] = [copy.deepcopy(claim)]
        data, _ = normalize_bundle(self.bundle, 'Ref'); self.assertEqual(len(data['claims']), 1)
        self.assertEqual(data['spaces'][0]['status'], 'PROPOSED'); self.assertEqual(data['claims'][0]['status'], 'SUPPORTED')
        self.bundle['claims'][0]['status'] = 'VERIFIED'
        with self.assertRaisesRegex(ValueError, 'conflicting duplicate'): normalize_bundle(self.bundle, 'Ref')

    def test_unregistered_entity_not_discarded_or_called_wall(self):
        self.bundle['entities'][2]['type'] = 'UnexpectedCandidate'
        data, _ = normalize_bundle(self.bundle, 'Ref')
        unknown = next(o for o in data['observations'] if o['id'] == 'Ref:w')
        self.assertEqual(unknown['category'], 'UnclassifiedObservation'); self.assertEqual(unknown['source_record']['type'], 'UnexpectedCandidate')

    def test_current_web_contract_requires_current_fields_not_legacy_coercion(self):
        self.bundle['schema_version'] = 'architectural_spatial_bundle/1'
        with self.assertRaisesRegex(ValueError, 'Spatial bundle role'): normalize_bundle(self.bundle, 'Ref')

    def test_no_overwrite_or_partial_output_on_invalid_other_role(self):
        with tempfile.TemporaryDirectory() as tmp:
            output = Path(tmp) / 'result'
            self.bundle['source_bindings']['Target']['width'] = True
            with self.assertRaises(ValueError): write_conversion(self.bundle, output)
            self.assertFalse(output.exists())
            output.mkdir(); (output / 'sentinel').write_text('unchanged')
            with self.assertRaises(FileExistsError): write_conversion(raw_fixture(), output)
            self.assertEqual((output / 'sentinel').read_text(), 'unchanged')

    def test_missing_files_and_inconsistent_siblings_fail(self):
        with tempfile.TemporaryDirectory() as tmp:
            directory = Path(tmp)
            with self.assertRaisesRegex(ValueError, 'missing'): inspect_directory(directory)
            for name in ('understanding_bundle', 'space_candidates', 'element_candidates', 'relation_candidates'):
                if name == 'understanding_bundle': value = self.bundle
                else:
                    drawings = {}
                    for role in ('Ref', 'Target'):
                        if name == 'relation_candidates': drawings[role] = {'candidates': [], 'attempts': []}
                        else: drawings[role] = [r for r in self.bundle['entities'] if r['role'] == role and ((r['type'] == 'Space') == (name == 'space_candidates'))]
                    value = {'schema_version': 'space_grounding/1', 'source_bindings': self.bundle['source_bindings'], 'coordinate_system': 'image_native_pixels', 'drawings': drawings}
                (directory / (name + '.json')).write_text(json.dumps(value))
            inspect_directory(directory)
            changed = json.loads((directory / 'space_candidates.json').read_text()); changed['drawings']['Ref'][0]['id'] = 'WRONG'
            (directory / 'space_candidates.json').write_text(json.dumps(changed))
            with self.assertRaisesRegex(ValueError, 'differ'): inspect_directory(directory)


class RealArtifactMappingTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if not REAL.is_dir():
            raise unittest.SkipTest('real FPUE result directory is required for artifact regressions')
        cls.bundle, cls.context = inspect_directory(REAL)

    def test_four_real_files_match_exactly(self):
        self.assertEqual(self.context['four_file_consistency'], 'MATCHED')
        self.assertEqual(len(self.bundle['entities']), 928)

    def test_page4_source_records_geometry_ids_and_claim_counts_preserved(self):
        for role, entity_count, relation_count in [('Ref', 429, 124), ('Target', 499, 155)]:
            data, report = normalize_bundle(self.bundle, role, context=self.context)
            source = {e['id']: e for e in self.bundle['entities'] if e['role'] == role}
            normalized = {r['id']: r for key in ('spaces', 'elements', 'observations') for r in data[key]}
            self.assertEqual(set(source), set(normalized)); self.assertEqual(len(normalized), entity_count)
            self.assertEqual(len(data['relations']), relation_count); self.assertFalse(data['deferred_records'])
            for eid, original in source.items():
                self.assertEqual(normalized[eid]['source_record'], original)
                self.assertEqual(normalized[eid].get('geometry_native'), original.get('geometry'))
                self.assertEqual(normalized[eid]['status'], original['claim_status'])
            self.assertEqual(len(data['claims']), sum(len(e.get('claims', [])) for e in source.values()))
            self.assertFalse(report['verified_by_adapter']); self.assertIsNone(report['GT_metrics'])

    def test_real_text_to_symbol_labels_preserved_and_connects_stay_zero(self):
        for role in ('Ref', 'Target'):
            data, _ = normalize_bundle(self.bundle, role)
            graph = convert_normalized(data)
            symbols = {r['id'] for r in data['observations'] if r['category'] == 'Symbol'}
            self.assertGreater(sum(r['target_id'] in symbols for r in data['relations'] if r['kind'] == 'LabelAssociation'), 0)
            self.assertFalse(list(graph.subjects(RDF.type, FP.Connection)))
            self.assertFalse(list(graph.subjects(FP.reviewStatus, FP.Confirmed)))

    def test_poc2_refinement_source_is_supported_without_legacy_replacement(self):
        path = ROOT / 'poc/results/geometry_refinement_page4/understanding_bundle.json'
        bundle = json.loads(path.read_text())
        for role, count in [('Ref', 475), ('Target', 666)]:
            data, report = normalize_bundle(bundle, role)
            self.assertEqual(sum(len(data[k]) for k in ('spaces', 'elements', 'observations')), count)
            self.assertFalse(data['deferred_records']); self.assertIsNone(data['document']['frame_registry'])
            self.assertFalse(report['geometry']['pdf_transform_available'])

    def test_real_native_geometry_turtle_roundtrip_and_entity_uris_disjoint(self):
        graphs = []
        for role in ('Ref', 'Target'):
            data, _ = normalize_bundle(self.bundle, role)
            graph = convert_normalized(data)
            reparsed = Graph().parse(data=graph.serialize(format='turtle'), format='turtle')
            self.assertTrue(isomorphic(graph, reparsed))
            native_by_id = {r['id']: r['geometry_native'] for k in ('spaces', 'elements', 'observations') for r in data[k]}
            for node in graph.subjects(RDF.type, FP.CandidateRecord):
                eid = str(graph.value(node, FP.sourceId))
                if eid in native_by_id:
                    geometry = graph.value(node, FP.hasGeometry)
                    self.assertEqual(json.loads(graph.value(geometry, FP.geometryJSON)), native_by_id[eid])
            graphs.append(graph)
        self.assertFalse(set(graphs[0].subjects(RDF.type, FP.CandidateRecord)) & set(graphs[1].subjects(RDF.type, FP.CandidateRecord)))


if __name__ == '__main__': unittest.main()
