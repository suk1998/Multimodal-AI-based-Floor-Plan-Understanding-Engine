"""Current-contract export and shared ontology policy, separate from detection."""
import copy
import json
from pathlib import Path
import sys
import unittest
ROOT=Path(__file__).resolve().parents[3];sys.path.insert(0,str(ROOT))
from rdflib import Literal,Graph
from rdflib.compare import isomorphic
from rdflib.namespace import RDF
from integrations.fpue_ontology.adapter import normalize_bundle
from integrations.fpue_ontology.bridge import convert_normalized
from integrations.fpue_ontology.rules import rule_digest,rule_spec
from integrations.fpue_ontology.vocabulary import vocabulary_digest
from integrations.fpue_ontology.schema import build_schema,FP


def fixture():
    binding={'source_role':'Target','source_sha256':'a'*64,'image_sha256':'b'*64,'page':4,'extraction_zoom':2,'width':100,'height':80,'frame_id':'Target:native'}
    own={'source_role':'Target','frame_id':'Target:native','claim_status':'PROPOSED'}
    return {'schema_version':'architectural_spatial_bundle/1','bundle_kind':'SPATIAL_MODEL_CANDIDATES','source_role':'Target','frame_id':'Target:native','source_binding':binding,'coordinate_system':{'name':'image_native_pixels'},'spaces':[{'space_id':'s',**own,'space_name':'UNLABELED_SPACE','name_observation':None,'seed_native':[20,20],'polygon_native':None,'bbox_native':None,'boundary_segments_native':[],'boundary_status':'AMBIGUOUS','evidence_refs':[]}], 'geometry_candidates':[{'entity_id':'u',**own,'element_type':'Unknown','geometry_native':{'coordinate_system':'image_native_pixels','bbox_native':[1,1,1,20],'points_native':[[1,1],[1,20]]},'evidence_refs':[]}], 'observations':[{'observation_id':'t',**own,'observation_type':'TextRegion','geometry_native':{'points_native':[[10,10],[20,10],[20,20],[10,20]]},'confidence':.98,'source_evidence':{'text':'새공간'}}], 'evidence':[],'geometry_evidence':[],'relations':[],'claims':[],'ontology':{'rule_sha256':rule_digest()}}


class CurrentSpatialTests(unittest.TestCase):
    def test_integer_decimal_bbox_survives_Turtle_roundtrip(self):
        source=fixture();source['geometry_candidates'][0]['geometry_native']['bbox_native']=[1,2,5,20]
        data,_=normalize_bundle(source,'Target');graph=convert_normalized(data)
        reparsed=Graph().parse(data=graph.serialize(format='turtle'),format='turtle')
        self.assertTrue(isomorphic(graph,reparsed))

    def test_OWL_and_runtime_share_the_same_exact_rule_spec(self):
        graph=build_schema()
        self.assertEqual(str(graph.value(FP[''],FP.applicationRulesSHA256)),rule_digest())
        self.assertEqual(json.loads(graph.value(FP[''],FP.applicationRulesJSON)),rule_spec())
        self.assertEqual(str(graph.value(FP[''],FP.initialSpaceVocabularySHA256)),vocabulary_digest())

    def test_current_original_records_native_frame_and_null_polygon_preserved(self):
        source=fixture();before=copy.deepcopy(source);data,report=normalize_bundle(source,'Target');graph=convert_normalized(data)
        self.assertEqual(source,before);self.assertEqual(data['spaces'][0]['source_record'],source['spaces'][0]);self.assertIsNone(data['spaces'][0]['geometry_native']['polygon_native'])
        self.assertNotIn('raw_label',data['spaces'][0]);self.assertFalse(list(graph.triples((None,FP.labelState,FP.LabelAbsent))))
        self.assertEqual(report['ontology_rule_sha256'],rule_digest())

    def test_degenerate_native_line_extent_never_expanded_to_rectangle(self):
        data,report=normalize_bundle(fixture(),'Target');line=next(e for e in data['observations'] if e['id']=='u')
        self.assertEqual(line['geometry_native']['bbox_native'],[1,1,1,20]);self.assertIsNone(line['bbox']);self.assertTrue(report['warnings']);convert_normalized(data)

    def test_unknown_geometry_not_a_wall_and_OCR_confidence_not_entity_certainty(self):
        data,_=normalize_bundle(fixture(),'Target')
        self.assertEqual(next(e for e in data['observations'] if e['id']=='u')['category'],'UnclassifiedObservation')
        text=next(e for e in data['observations'] if e['id']=='t');self.assertEqual(text['recognition_confidence'],.98);self.assertNotIn('confidence',text)

    def test_wrong_role_and_cross_frame_refuse_mapping(self):
        source=fixture()
        with self.assertRaises(ValueError):normalize_bundle(source,'Ref')
        source['spaces'][0]['frame_id']='Ref:native'
        with self.assertRaises(ValueError):normalize_bundle(source,'Target')

    def test_deferred_relations_do_not_disappear_or_become_verified(self):
        source=fixture();source['relations']=[{'relation_id':'bad','source_role':'Target','frame_id':'Target:native','relation_type':'BoundaryAssociation','source_id':'s','target_id':'t','claim_status':'PROPOSED','evidence_refs':[]}]
        data,_=normalize_bundle(source,'Target');self.assertFalse(data['relations']);self.assertEqual(data['deferred_records'][0]['source_record'],source['relations'][0]);convert_normalized(data)


if __name__=='__main__':unittest.main()
