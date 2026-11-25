from __future__ import annotations

import copy
import json
import unittest
from pathlib import Path
from rdflib import Literal
from rdflib.namespace import RDF
import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from bridge import convert_normalized
from schema import (
    BASE_ELEMENT_CODES, BASE_SPACE_TERMS, FP, build_base_vocabulary,
    build_schema, space_term_for_label, space_term_uri, element_type_uri, register_space_term
)

BASE = Path(__file__).resolve().parents[1]


class OntologyTests(unittest.TestCase):
    def setUp(self):
        self.data = json.loads((BASE / 'examples' / 'manual_84a_ref_subset.json').read_text(encoding='utf-8'))

    def test_exactly_14_base_space_terms_and_element_codes(self):
        self.assertEqual(len(BASE_SPACE_TERMS), 14)
        self.assertEqual(len(BASE_ELEMENT_CODES), 14)
        vocab = build_base_vocabulary()
        self.assertIn((space_term_uri('팬트리'), RDF.type, FP.SpaceTerm), vocab)
        self.assertIn((element_type_uri('INITIAL_USER_VOCAB','WD/1'), FP.typeCode, Literal('WD/1')), vocab)
        self.assertIn((element_type_uri('INITIAL_USER_VOCAB','WD/2'), FP.typeCode, Literal('WD/2')), vocab)
        self.assertNotEqual(element_type_uri('catalog-A','WD/1'), element_type_uri('catalog-B','WD/1'))
        self.assertEqual(space_term_for_label('침실 1')[0], '침실1')
        self.assertIsNone(space_term_for_label('서재'))  # extensible new label, not forced into 14

    def test_schema_no_fixed_plan_variant_and_basic_relation_classes(self):
        schema = build_schema()
        self.assertIn((FP.Connection, RDF.type, __import__('rdflib').namespace.OWL.Class), schema)
        self.assertNotIn((FP.planVariant, RDF.type, __import__('rdflib').namespace.OWL.Class), schema)
        self.assertEqual(len(list(schema.triples((None, FP.planVariant, None)))), 0)

    def test_manual_instance_preserves_typed_relations_without_geometry(self):
        g = convert_normalized(self.data)
        self.assertEqual(len(list(g.subjects(RDF.type, FP.Connection))), 2)
        self.assertEqual(len(list(g.subjects(RDF.type, FP.ElementAssociation))), 1)
        self.assertEqual(len(list(g.subjects(RDF.type, FP.Geometry))), 0)
        self.assertEqual(len(list(g.triples((None, FP.openingSide, None)))), 0)
        self.assertEqual(len(list(g.triples((None, FP.hasSpaceTerm, space_term_uri('침실1'))))), 1)
        self.assertEqual(len(list(g.triples((None, FP.viaElement, None)))), 1)

    def test_unseen_space_label_stays_unmapped(self):
        data = copy.deepcopy(self.data)
        data['spaces'].append({'id':'new1', 'raw_label':'서재', 'annotation_source':'FPUE_PROPOSAL'})
        g = convert_normalized(data)
        new = next(s for s in g.subjects(FP.sourceId, Literal('new1')))
        self.assertIn((new, FP.visibleLabel, Literal('서재')), g)
        self.assertEqual(len(list(g.objects(new, FP.hasSpaceTerm))), 0)
        self.assertEqual(len(list(g.objects(new, FP.hasSpaceType))), 0)
        self.assertIn((new, FP.reviewStatus, FP.Proposed), g)

    def test_extended_space_vocabulary_mapping(self):
        data = copy.deepcopy(self.data)
        data['spaces'].append({'id':'study', 'raw_label':'서재', 'status':'PROPOSED'})
        vocab = build_base_vocabulary()
        register_space_term(vocab, '서재', 'StudyRoom')
        g = convert_normalized(data, vocabulary=vocab)
        study = next(s for s in g.subjects(FP.sourceId, Literal('study')))
        self.assertIn((study, FP.hasSpaceTerm, space_term_uri('서재')), g)

    def test_fpue_observation_relations_stay_proposals(self):
        data = json.loads((BASE / 'examples' / 'fpue_candidate_contract_example.json').read_text(encoding='utf-8'))
        g = convert_normalized(data)
        self.assertEqual(len(list(g.subjects(RDF.type, FP.LabelAssociation))), 1)
        self.assertEqual(len(list(g.subjects(RDF.type, FP.BoundaryAssociation))), 1)
        self.assertEqual(len(list(g.subjects(RDF.type, FP.HostingRelation))), 1)
        self.assertEqual(len(list(g.subjects(RDF.type, FP.Connection))), 0)

    def test_missing_label_does_not_mean_label_absent(self):
        data = copy.deepcopy(self.data)
        data['spaces'].append({'id':'no_ocr'})
        g = convert_normalized(data)
        new = next(s for s in g.subjects(FP.sourceId, Literal('no_ocr')))
        self.assertIn((new, FP.labelState, FP.LabelUnknown), g)
        self.assertNotIn((new, FP.labelState, FP.LabelAbsent), g)

    def test_relation_missing_is_not_removed_event(self):
        data = copy.deepcopy(self.data)
        data['relations'] = []
        g = convert_normalized(data)
        self.assertEqual(len(list(g.subjects(RDF.type, FP.ChangeEvent))), 0)

    def test_bad_connection_is_rejected_not_inferred(self):
        data = copy.deepcopy(self.data)
        data['relations'][0]['target_id'] = 'not_existing'
        with self.assertRaisesRegex(ValueError, 'unknown relation endpoint'):
            convert_normalized(data)

    def test_bbox_and_north_not_invented(self):
        data = copy.deepcopy(self.data)
        data['document']['coordinate_frame'].update({'width': 100, 'height': 100})
        data['elements'][0]['bbox'] = [10, 20, 30, 40]
        g = convert_normalized(data)
        self.assertEqual(len(list(g.subjects(RDF.type, FP.Geometry))), 1)
        self.assertEqual(len(list(g.triples((None, FP.northRotationDeg, None)))), 0)
        data['elements'][0]['bbox'] = [10, 20, 110, 40]
        with self.assertRaisesRegex(ValueError, 'outside frame width'):
            convert_normalized(data)


if __name__ == '__main__':
    unittest.main()
