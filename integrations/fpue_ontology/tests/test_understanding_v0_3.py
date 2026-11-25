"""v0.3 modeling/source-preservation checks, not native drawing performance."""
import copy
import hashlib
import json
from pathlib import Path
import sys
import tempfile
import unittest
import subprocess

ROOT=Path(__file__).resolve().parents[3];sys.path.insert(0,str(ROOT))
from rdflib import Graph,Literal
from rdflib.compare import isomorphic
from rdflib.namespace import RDF,RDFS,OWL
from integrations.fpue_ontology.schema import build_schema
from integrations.fpue_ontology.schema_v0_3 import U,build_understanding_schema
from integrations.fpue_ontology.apartment_examples_v0_3 import read_apartment_document,generate_examples,Builder
from integrations.fpue_ontology.graph_v0_3 import validate_graph,to_rdf
from integrations.fpue_ontology.understanding_contract import (VERSION,ENGINE_PROFILES,contract_digest,is_a,plan_evidence_requests,validate_relation_signature)

SOURCE=ROOT/'docs/아파트 설계도 그래프 구조.md'


class UnderstandingSchemaTests(unittest.TestCase):
    def test_service_shaft_volume_is_distinct_from_solid_enclosure(self):
        schema=build_understanding_schema()
        self.assertIn((U.PipeShaft,RDFS.subClassOf,U.ServiceSpace),schema)
        self.assertIn((U.ShaftEnclosure,RDFS.subClassOf,U.BuildingElement),schema)
        self.assertTrue(is_a('PipeShaft','Space'));self.assertFalse(is_a('PipeShaft','BuildingElement'))
        validate_relation_signature('Adjacency','Space','PipeShaft')

    def test_opening_is_not_door_window_or_solid_wall(self):
        schema=build_understanding_schema()
        self.assertIn((U.Opening,OWL.disjointWith,U.BuildingElement),schema)
        validate_relation_signature('Hosting','Wall','Opening')
        validate_relation_signature('Installation','Window','Opening')
        with self.assertRaises(ValueError):validate_relation_signature('Hosting','Wall','Door')

    def test_has_does_not_entail_containment_or_passage(self):
        schema=build_understanding_schema()
        self.assertNotIn((U.ElementAssociationHypothesis,RDFS.subClassOf,U.ContainmentHypothesis),schema)
        self.assertNotIn((U.AdjacencyHypothesis,RDFS.subClassOf,U.ConnectionHypothesis),schema)
        self.assertNotIn((U.connectedTo,RDF.type,OWL.TransitiveProperty),schema)

    def test_candidates_are_not_subclasses_of_physical_space(self):
        schema=build_understanding_schema()
        self.assertNotIn((U.SpaceHypothesis,RDFS.subClassOf,U.Space),schema)
        self.assertIn((U.ArchitecturalEntity,OWL.disjointWith,U.Hypothesis),schema)
        self.assertEqual(schema.value(U.proposedEntityType,RDFS.range),U.EntityType)

    def test_four_engines_share_generic_contract_not_apartment_rules(self):
        schema=build_understanding_schema()
        self.assertEqual(set(ENGINE_PROFILES),{'GEOMETRIC','SPATIAL','SEMANTIC','RELATIONAL'})
        self.assertEqual(str(schema.value(U[''],U.applicationContractSHA256)),contract_digest())
        for variant in ('84A','84B','84C'):
            self.assertFalse(any(variant in str(s) or variant in str(o) for s,p,o in schema))

    def test_schema_turtle_roundtrip(self):
        schema=build_understanding_schema()
        self.assertTrue(isomorphic(schema,Graph().parse(data=schema.serialize(format='turtle'),format='turtle')))

    def test_old_operational_schema_is_not_silently_switched(self):
        schema=build_schema()
        self.assertNotIn((U[''],RDF.type,OWL.Ontology),schema)
        path=ROOT/'ontology_spatial_validation/2026-11-17/latest_schema/fpue_schema_v0_2.ttl'
        if path.is_file():self.assertTrue(isomorphic(schema,Graph().parse(path,format='turtle')))

    def test_schema_CLI_dispatch_and_existing_output_protection(self):
        with tempfile.TemporaryDirectory() as temp:
            legacy=Path(temp)/'legacy';new=Path(temp)/'new'
            for version,out in [('0.2',legacy),('0.3',new)]:
                result=subprocess.run([sys.executable,'-B',str(ROOT/'integrations/fpue_ontology/schema.py'),'--schema-version',version,'--output-dir',str(out)],cwd=ROOT,capture_output=True,text=True)
                self.assertEqual(result.returncode,0,result.stderr)
            self.assertTrue(isomorphic(build_schema(),Graph().parse(legacy/'fpue_schema_v0_2.ttl',format='turtle')))
            path=new/'fpue_understanding_v0_3.ttl';before=path.read_bytes()
            result=subprocess.run([sys.executable,'-B',str(ROOT/'integrations/fpue_ontology/schema.py'),'--schema-version','0.3','--output-dir',str(new)],cwd=ROOT,capture_output=True)
            self.assertNotEqual(result.returncode,0);self.assertEqual(path.read_bytes(),before)


class ApartmentGraphExamplesTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if not SOURCE.is_file():raise unittest.SkipTest('user apartment source document is required')
        cls.graphs,cls.standalone,cls.embedded=read_apartment_document(SOURCE)
        cls.by={(g['context']['variant'],g['context']['source_role']):g for g in cls.graphs}

    def test_all_six_sections_and_157_source_statements_are_preserved(self):
        self.assertEqual(len(self.graphs),6)
        self.assertEqual(sum(len(g['source_statements']) for g in self.graphs),157)
        counts={key:len(g['source_statements']) for key,g in self.by.items()}
        self.assertEqual(counts,{('84A','Ref'):28,('84A','Target'):28,('84B','Ref'):25,('84B','Target'):25,('84C','Ref'):26,('84C','Target'):25})
        raw_lines=SOURCE.read_text().splitlines()
        for graph in self.graphs:
            for source in graph['source_statements']:
                self.assertEqual(source['raw_text'],raw_lines[source['line']-1].strip())

    def test_no_native_geometry_or_verified_architectural_facts_are_invented(self):
        for graph in self.graphs:
            self.assertIsNone(graph['context']['source_binding'])
            self.assertFalse(graph['context']['inventory_complete'])
            self.assertTrue(all(e['geometry'] is None and e['grounding_status']=='UNBOUND' for e in graph['entities']))
            self.assertTrue(all(c['status']=='PROPOSED' for c in graph['claims']))
            rdf=to_rdf(graph)
            for physical in [U.Space,U.Wall,U.Door,U.Opening,U.Connection]:self.assertFalse(list(rdf.subjects(RDF.type,physical)))
            self.assertFalse(list(rdf.triples((None,OWL.sameAs,None))))

    def test_each_PS_reference_remains_unresolved_not_one_global_shaft(self):
        graph=self.by['84A','Ref'];shafts=[e for e in graph['entities'] if e['expected_type']=='PipeShaft']
        self.assertEqual(len(shafts),5)
        self.assertEqual(len({s['id'] for s in shafts}),5)
        self.assertTrue(all(s['referent_status']=='UNRESOLVED' for s in shafts))
        rdf=to_rdf(graph);self.assertFalse(list(rdf.triples((None,OWL.differentFrom,None))))

    def test_domain_range_and_subclass_inference_does_not_promote_hypotheses(self):
        # Restricted RDFS type closure only; this is not a complete OWL reasoner.
        schema=build_understanding_schema()
        for graph in self.graphs+[self.standalone]:
            data=to_rdf(graph)
            inferred=set(data.subject_objects(RDF.type))
            for subject,predicate,obj in data:
                inferred.update((subject,domain) for domain in schema.objects(predicate,RDFS.domain))
                if not isinstance(obj,Literal):
                    inferred.update((obj,range_) for range_ in schema.objects(predicate,RDFS.range))
            while True:
                additions={(node,parent) for node,kind in inferred for parent in schema.objects(kind,RDFS.subClassOf)}-inferred
                if not additions:break
                inferred.update(additions)
            hypotheses={node for node,kind in inferred if kind==U.Hypothesis}
            physical={node for node,kind in inferred if kind in {U.ArchitecturalEntity,U.Connection,U.Space,U.BuildingElement,U.Opening}}
            self.assertTrue(hypotheses)
            self.assertFalse(hypotheses & physical)

    def test_same_type_code_does_not_merge_bedroom_doors(self):
        graph=self.by['84A','Ref'];entities={e['id']:e for e in graph['entities']}
        bedroom_doors=[r for r in graph['relations'] if r['kind']=='Connection' and entities[r['source_id']].get('label') in ['침실2','침실3']]
        self.assertEqual(len(bedroom_doors),2)
        self.assertEqual({entities[r['via_entity_id']]['catalog_code'] for r in bedroom_doors},{'WD/2'})
        self.assertEqual(len({r['via_entity_id'] for r in bedroom_doors}),2)

    def test_code_alias_is_preserved_with_scoped_canonical_candidate(self):
        graph=self.by['84C','Ref'];aliases=[e for e in graph['entities'] if 'PW3' in e.get('raw_type_codes',[])]
        self.assertEqual(len(aliases),1);self.assertEqual(aliases[0]['catalog_code'],'PW/3')
        self.assertTrue(graph['catalog']['scope'].startswith('USER_DOCUMENT'))

    def test_unregistered_FSD2_keeps_unknown_material_and_subtype(self):
        graph=self.by['84A','Target'];entry=next(e for e in graph['catalog']['entries'] if e['code']=='FSD/2')
        self.assertEqual(entry['definition_status'],'UNREGISTERED');self.assertIsNone(entry['material']);self.assertIsNone(entry['category'])
        entity=next(e for e in graph['entities'] if e.get('catalog_code')=='FSD/2')
        self.assertEqual(entity['expected_type'],'Door')
        self.assertNotEqual(entity['expected_type'],'FireDoor')

    def test_duplicate_AG_assertion_preserves_both_sources_without_two_physical_grilles(self):
        graph=self.by['84A','Target'];grilles=[e for e in graph['entities'] if e.get('catalog_code')=='AG/1']
        self.assertEqual(len(grilles),1);self.assertEqual(len(grilles[0]['statement_ids']),2)
        self.assertIsNone(grilles[0]['physical_instance_count'])
        relation=next(r for r in graph['relations'] if r['target_id']==grilles[0]['id'])
        self.assertEqual(relation['kind'],'ElementAssociation');self.assertEqual(len(relation['statement_ids']),2)
        self.assertEqual(relation['association_semantics'],'HAS_OR_CONTAINS_UNRESOLVED')

    def test_pantry_narrative_never_removes_space_or_invents_direction_frame(self):
        graph=self.by['84A','Target'];narrative=next(s for s in graph['source_statements'] if s.get('interpretation')=='AMBIGUOUS_CHANGE_NARRATIVE')
        self.assertEqual(narrative['direction_reference_frame'],'UNKNOWN')
        self.assertTrue(any(c['predicate']=='author_stated_space_preserved' and c['value'] is True for c in graph['claims']))
        self.assertFalse(any(c['predicate'] in ['space_removed','label_removed'] for c in graph['claims']))
        self.assertTrue(any(q['question']=='RESOLVE_ACCESS_LOCATION' for q in graph['evidence_requests']))

    def test_missing_84C_last_connection_is_not_deleted_or_completed(self):
        graph=self.by['84C','Target'];entities={e['id']:e for e in graph['entities']}
        self.assertTrue(any(e.get('label')=='침실3' for e in graph['entities']))
        self.assertFalse(any(r['kind']=='Connection' and any(entities[i].get('label')=='침실3' for i in [r['source_id'],r['target_id']]) for r in graph['relations']))
        self.assertFalse(any('removed' in c['predicate'] for c in graph['claims']))
        self.assertFalse(graph['context']['inventory_complete'])

    def test_84B_equal_written_topology_does_not_conclude_geometry_unchanged(self):
        def semantics(graph):
            entities={e['id']:e for e in graph['entities']}
            return sorted((r['kind'],entities[r['source_id']].get('label'),entities[r['target_id']].get('label') or entities[r['target_id']].get('catalog_code'),r.get('mode'),entities[r['via_entity_id']].get('catalog_code') if r.get('via_entity_id') else '') for r in graph['relations'])
        self.assertEqual(semantics(self.by['84B','Ref']),semantics(self.by['84B','Target']))
        self.assertFalse(any(c['predicate'] in ['geometry_unchanged','unchanged'] for g in [self.by['84B','Ref'],self.by['84B','Target']] for c in g['claims']))

    def test_open_passage_code_example_is_separate_from_unknown_mode_plan_edges(self):
        self.assertEqual(self.standalone['context']['source_role'],'SINGLE')
        self.assertIsNone(self.standalone['context']['variant'])
        self.assertEqual(self.standalone['relations'][0]['mode'],'OpenPassage')
        self.assertIsNone(self.standalone['relations'][0]['via_entity_id'])
        second=self.standalone['relations'][1];self.assertEqual(second['mode'],'Door');self.assertIsNone(second['via_opening_id'])
        entities={e['id']:e for e in self.standalone['entities']}
        self.assertEqual(entities[second['via_entity_id']]['catalog_code'],'PD/2')
        self.assertFalse(any(e['expected_type']=='Opening' for e in self.standalone['entities']))
        for graph in self.graphs:
            for r in graph['relations']:
                if r['kind']=='Connection' and r['via_entity_id'] is None:self.assertEqual(r['mode'],'Unknown')

    def test_ref_target_ids_are_independent_and_requests_are_not_facts(self):
        for variant in ['84A','84B','84C']:
            ref=self.by[variant,'Ref'];target=self.by[variant,'Target']
            self.assertFalse({e['id'] for e in ref['entities']}&{e['id'] for e in target['entities']})
        engines={q['engine'] for g in self.graphs for q in g['evidence_requests']}
        self.assertEqual(engines,set(ENGINE_PROFILES))
        for graph in self.graphs:
            self.assertTrue(all(q['executable'] is False and q['scope']=='SOURCE_DRAWING_ONLY' for q in graph['evidence_requests']))
            signatures=[(q['engine'],q['question'],tuple(q['subject_ids'])) for q in graph['evidence_requests']]
            self.assertEqual(len(signatures),len(set(signatures)))

    def test_catalog_attributes_survive_rdf_without_becoming_physical_material(self):
        graph=self.by['84A','Ref'];rdf=to_rdf(graph)
        self.assertTrue(list(rdf.triples((None,U.declaredMaterial,None))))
        entries=[json.loads(value) for value in rdf.objects(None,U.catalogDefinitionJSON)]
        self.assertEqual(len(entries),14)
        self.assertTrue(any(e['code']=='AG/1' and e['function']=='Ventilation' for e in entries))

    def test_graph_roundtrip_and_source_document_non_destruction(self):
        before=hashlib.sha256(SOURCE.read_bytes()).hexdigest()
        for graph in self.graphs:
            rdf=to_rdf(graph)
            self.assertTrue(isomorphic(rdf,Graph().parse(data=rdf.serialize(format='turtle'),format='turtle')))
        self.assertEqual(hashlib.sha256(SOURCE.read_bytes()).hexdigest(),before)

    def test_output_directory_never_overwrites_existing_data(self):
        with tempfile.TemporaryDirectory() as temp:
            p=Path(temp)/'sentinel';p.write_text('KEEP')
            with self.assertRaises(FileExistsError):generate_examples(SOURCE,temp)
            self.assertEqual(p.read_text(),'KEEP')

    def test_invalid_cross_snapshot_connection_or_window_via_is_rejected(self):
        for change in ['cross','window']:
            graph=copy.deepcopy(self.by['84A','Ref']);relation=next(r for r in graph['relations'] if r['kind']=='Connection' and r['mode']=='Door')
            if change=='cross':relation['target_id']=self.by['84A','Target']['entities'][0]['id']
            else:relation['via_entity_id']=next(e['id'] for e in graph['entities'] if e['expected_type']=='Window')
            with self.subTest(change=change),self.assertRaises(ValueError):validate_graph(graph)

    def test_VERIFIED_or_complete_inventory_requires_independent_scoped_evidence(self):
        graph=copy.deepcopy(self.by['84A','Ref']);graph['claims'][0]['status']='VERIFIED'
        with self.assertRaises(ValueError):validate_graph(graph)
        graph=copy.deepcopy(self.by['84A','Ref']);graph['context']['inventory_complete']=True
        with self.assertRaises(ValueError):validate_graph(graph)

    def test_unbound_native_geometry_is_not_accepted(self):
        graph=copy.deepcopy(self.by['84A','Ref']);graph['entities'][0]['geometry']={'frame_id':'invented','coordinate_system':'image_native_pixels','bbox':[0,0,1,1]}
        with self.assertRaises(ValueError):validate_graph(graph)

    def test_unknown_space_labels_are_not_forced_into_fourteen_room_names(self):
        graph=self.by['84A','Ref'];builder=Builder(SOURCE,graph['context']['source_document']['sha256'],'CUSTOM','Ref')
        builder.add_line(1,'서재 is connected to 현관')
        custom=builder.finish();self.assertTrue(any(e['label']=='서재' for e in custom['entities']))
        self.assertTrue(all(e['expected_type']=='Space' for e in custom['entities']))


if __name__=='__main__':unittest.main()
