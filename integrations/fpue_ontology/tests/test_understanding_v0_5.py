"""Synthetic ontology/contract guards, separate from actual marker export."""
from copy import deepcopy
import json
from pathlib import Path
import subprocess,sys,tempfile,unittest
from rdflib import Graph,Literal
from rdflib.compare import isomorphic
from rdflib.namespace import OWL,RDF,RDFS
from integrations.fpue_ontology import understanding_contract_v0_5 as contract
from integrations.fpue_ontology.schema_v0_5 import U,build_understanding_schema
from integrations.fpue_ontology.schema_v0_4 import build_understanding_schema as old_schema
from integrations.fpue_ontology.schema import BASE_ELEMENT_CODES
from integrations.fpue_ontology.examples_v0_5 import Fixture,printed_types_fixture,assigned_door_fixture
from integrations.fpue_ontology.opening_graph_v0_5 import validate_graph,to_rdf,invalidation_plan
ROOT=Path(__file__).resolve().parents[3]


class OpeningSchemaTests(unittest.TestCase):
    def test_previous_schema_is_preserved_and_namespace_version_is_explicit(self):
        saved=ROOT/'ontology_schema_v04_validation/2026-11-20/examples/fpue_understanding_v0_4.ttl'
        self.assertTrue(isomorphic(old_schema(),Graph().parse(saved,format='turtle')))
        schema=build_understanding_schema()
        self.assertEqual(str(schema.value(U[''],U.applicationContractSHA256)),contract.contract_digest())
        self.assertFalse(any(str(t).startswith('https://example.org/fpue/understanding/0.4#') for triple in schema for t in triple))

    def test_existing_door_window_and_grille_hierarchy_and_glossary_are_reused(self):
        schema=build_understanding_schema()
        contract.validate_relation_signature('Installation','Window','Opening')
        with self.assertRaises(ValueError):contract.validate_relation_signature('Installation','Symbol','Opening')
        for name in ['Door','Window','Grille','Opening','ElementType','CatalogEntry','TypeDesignationHypothesis','TypeAssignmentHypothesis']:
            self.assertIn((U[name],RDF.type,OWL.Class),schema)
        self.assertIn((U.FireDoor,RDFS.subClassOf,U.Door),schema)
        families={c.split('/')[0]:(k,m,f) for c,k,_,m,f in BASE_ELEMENT_CODES}
        self.assertEqual(set(families),{'PD','WD','SD','FSD','PW','AW','AG'})
        for family,(kind,_,_) in families.items():
            self.assertIn((U['CodeFamily_'+family],U.familyEntityType,U['EntityType_'+kind]),schema)
        self.assertNotIn((U['PD/1'],RDF.type,OWL.Class),schema)

    def test_no_code_identity_key_same_as_or_transitive_connection_rule(self):
        schema=build_understanding_schema()
        for p in [OWL.hasKey,OWL.sameAs,OWL.propertyChainAxiom]:self.assertFalse(list(schema.triples((None,p,None))))
        self.assertNotIn((U.connectedTo,RDF.type,OWL.TransitiveProperty),schema)
        self.assertFalse(list(schema.subjects(RDF.type,OWL.InverseFunctionalProperty)))

    def test_rdf_roundtrip_uses_only_declared_schema_terms(self):
        schema=build_understanding_schema()
        declared={s for s,_,o in schema.triples((None,RDF.type,None)) if o in {OWL.Class,OWL.ObjectProperty,OWL.DatatypeProperty,OWL.AnnotationProperty}}
        for data in [schema,to_rdf(printed_types_fixture()),to_rdf(assigned_door_fixture())]:
            self.assertTrue(isomorphic(data,Graph().parse(data=data.serialize(format='turtle'),format='turtle')))
            if data is schema:continue
            for _,p,o in data:
                if str(p).startswith(str(U)):self.assertIn(p,declared)
                if p==RDF.type and str(o).startswith(str(U)):self.assertIn(o,declared)

    def test_domain_range_subclass_inference_does_not_create_physical_elements(self):
        schema=build_understanding_schema();data=to_rdf(assigned_door_fixture())
        inferred=set(data.subject_objects(RDF.type))
        for s,p,o in data:
            inferred.update((s,d) for d in schema.objects(p,RDFS.domain))
            if not isinstance(o,Literal):inferred.update((o,r) for r in schema.objects(p,RDFS.range))
        while True:
            more={(s,p) for s,c in inferred for p in schema.objects(c,RDFS.subClassOf)}-inferred
            if not more:break
            inferred.update(more)
        physical={s for s,c in inferred if c in {U.ArchitecturalEntity,U.Door,U.Window,U.Grille,U.Connection}}
        self.assertFalse(physical)

    def test_cli_generates_new_version_and_refuses_overwrite(self):
        with tempfile.TemporaryDirectory() as folder:
            args=[sys.executable,'-B',str(ROOT/'integrations/fpue_ontology/schema.py'),'--schema-version','0.5','--output-dir',folder]
            done=subprocess.run(args,cwd=ROOT,capture_output=True,text=True);self.assertEqual(done.returncode,0,done.stderr)
            path=Path(folder)/'fpue_understanding_v0_5.ttl';before=path.read_bytes()
            self.assertTrue(isomorphic(build_understanding_schema(),Graph().parse(path,format='turtle')))
            self.assertNotEqual(subprocess.run(args,cwd=ROOT,capture_output=True).returncode,0)
            self.assertEqual(path.read_bytes(),before)


class OpeningContractTests(unittest.TestCase):
    def test_repeated_pd1_is_one_type_and_distinct_symbol_instances(self):
        g=printed_types_fixture();validate_graph(g)
        ds=[d for d in g['type_designations'] if d['canonical_code']=='PD/1']
        self.assertEqual(len(ds),2);self.assertEqual(ds[0]['entry_id'],ds[1]['entry_id'])
        self.assertNotEqual(ds[0]['symbol_id'],ds[1]['symbol_id']);self.assertEqual(g['type_assignments'],[])
        self.assertNotEqual(ds[0]['entry_id'],next(d for d in g['type_designations'] if d['canonical_code']=='PD/2')['entry_id'])

    def test_same_code_in_different_catalogs_remains_distinct(self):
        f=Fixture();f.catalog('A');f.catalog('B');f.entry('PD/1','A');f.entry('PD/1','B')
        f.marker('first','PD/1',entry_id='A:PD/1');f.marker('second','PD/1',x=60,entry_id='B:PD/1')
        data=to_rdf(f.graph);entries=set(data.subjects(U.typeCode,Literal('PD/1')))
        self.assertEqual(len(entries),2)

    def test_unknown_fsd2_does_not_gain_fsd1_specifications(self):
        f=Fixture();f.catalog();f.entry('FSD/2');f.marker('symbol','FSD/2');validate_graph(f.graph)
        e=f.graph['catalog_entries'][0];self.assertEqual(e['definition_status'],'UNRESOLVED')
        e['material']='Steel'
        with self.assertRaisesRegex(ValueError,'invent specifications'):validate_graph(f.graph)

    def test_missing_number_cannot_default_to_one_or_claim_complete(self):
        f=Fixture();f.catalog();f.entry('PD/1');d=f.marker('symbol','PD/1')
        d.update(type_number=None,canonical_code=None,entry_id=None,reading_status='PARTIAL',status='PROPOSED')
        d['reading_observation_ids']=['symbol:letters'];d['source_evidence_refs']=['symbol:letters'];validate_graph(f.graph)
        d['status']='SUPPORTED'
        with self.assertRaisesRegex(ValueError,'partial/ambiguous'):validate_graph(f.graph)

    def test_canonical_pd2_must_match_actual_reading_observations(self):
        g=printed_types_fixture();g['observations'][1]['text']='9'
        with self.assertRaisesRegex(ValueError,'contradicts observed'):validate_graph(g)

    def test_number_from_another_marker_cannot_fill_the_same_family(self):
        g=printed_types_fixture();d=g['type_designations'][2]
        d['reading_observation_ids']=['symbol2:letters','symbol0:number'];d['source_evidence_refs']=list(d['reading_observation_ids'])
        with self.assertRaisesRegex(ValueError,'outside this marker'):validate_graph(g)

    def test_explicit_project_definition_does_not_inherit_initial_family_material(self):
        g=assigned_door_fixture();g['catalog_entries'][0].update(category='Window',material='Aluminum')
        g['entities'][1]['expected_type']='Window';data=to_rdf(g)
        entries=set(data.subjects(U.typeCode,Literal('PD/2')))
        self.assertEqual(len(entries),1)
        self.assertNotIn((next(iter(entries)),U.entryFamily,U.CodeFamily_PD),data)

    def test_supported_assignment_needs_project_definition_and_actual_reference(self):
        g=assigned_door_fixture();validate_graph(g)
        for defect in ['initial','missing_reference','nearest','no_geometry','wrong_pair']:
            copy=deepcopy(g)
            if defect=='initial':
                copy['catalogs'][0]['origin']='USER_INITIAL_GLOSSARY';copy['external_evidence'][0]['evidence_origin']='USER_INITIAL_GLOSSARY'
                copy['catalog_entries'][0]['definition_status']='INITIAL_GLOSSARY'
            elif defect=='missing_reference':copy['type_assignments'][0].pop('reference_relation_id')
            elif defect=='nearest':copy['relations'][0]['reference_method']='NEAREST'
            elif defect=='wrong_pair':copy['relations'][0]['target_id']='wall'
            else:copy['entities'][1]['geometry']=None
            with self.subTest(defect=defect),self.assertRaises(ValueError):validate_graph(copy)

    def test_initial_glossary_cannot_be_disguised_as_project_definition(self):
        g=printed_types_fixture();g['catalog_entries'][0]['definition_status']='PROJECT_DECLARED'
        with self.assertRaisesRegex(ValueError,'initial glossary'):validate_graph(g)

    def test_wrong_window_or_grille_assignment_is_rejected_and_conflict_can_be_retained(self):
        for kind in ['Window','Grille']:
            g=assigned_door_fixture();g['entities'][1]['expected_type']=kind
            with self.subTest(kind=kind),self.assertRaisesRegex(ValueError,'contradicts'):validate_graph(g)
            g['type_assignments'][0]['status']='CONFLICTED';validate_graph(g)

    def test_type_definition_does_not_create_material_or_fire_performance_on_door(self):
        g=assigned_door_fixture();data=to_rdf(g)
        self.assertFalse(list(data.triples((None,U.hasMaterial,None))))
        self.assertFalse(list(data.subjects(RDF.type,U.FireDoor)))
        self.assertFalse(g['claims'])

    def test_window_cannot_supply_a_walkable_connection(self):
        g=assigned_door_fixture();common={'source_role':'Ref','frame_id':g['source_binding']['frame_id']}
        for identity,kind in [('room-a','Space'),('room-b','Space'),('window','Window')]:
            g['entities'].append(dict(common,id=identity,expected_type=kind,grounding_status='UNBOUND',geometry_state='UNRESOLVED'))
        for identity,predicate,value in [('existence','connection_existence','UNKNOWN'),('mode','connection_mode','Unknown')]:
            g['claims'].append(dict(common,id=identity,subject_id='bad',predicate=predicate,value=value,status='PROPOSED',scope='SYNTHETIC_CONTRACT_ONLY'))
        g['relations'].append(dict(common,id='bad',kind='Connection',source_id='room-a',target_id='room-b',via_entity_id='window',mode='Unknown',existence_claim_id='existence',mode_claim_id='mode'))
        with self.assertRaisesRegex(ValueError,'via cannot'):validate_graph(g)

    def test_foreign_frame_source_and_dangling_references_are_rejected(self):
        for group in ['catalogs','catalog_entries','type_designations','type_assignments']:
            g=assigned_door_fixture();g[group][0]['source_role']='Target'
            with self.subTest(group=group),self.assertRaisesRegex(ValueError,'crosses'):validate_graph(g)
        g=assigned_door_fixture();g['catalogs'][0]['document_sha256']='a'*64
        with self.assertRaisesRegex(ValueError,'SHA mismatch'):validate_graph(g)
        g=assigned_door_fixture();g['type_assignments'][0]['reference_relation_id']='missing'
        with self.assertRaises(ValueError):validate_graph(g)

    def test_supported_state_is_not_verified_without_independent_review(self):
        for group in ['type_designations','type_assignments']:
            g=assigned_door_fixture();g[group][0]['status']='VERIFIED'
            with self.subTest(group=group),self.assertRaises(ValueError):validate_graph(g)

    def test_catalog_or_geometry_changes_invalidate_binding_without_mutating_ocr(self):
        g=assigned_door_fixture();before=json.dumps(g,sort_keys=True)
        plan=invalidation_plan(g,['catalog:PD/2'])
        self.assertIn('symbol:designation',plan['stale_ids']);self.assertIn('assignment',plan['stale_ids'])
        self.assertNotIn('symbol:letters',plan['stale_ids']);self.assertFalse(plan['applied'])
        self.assertIn('assignment',invalidation_plan(g,['leader'])['stale_ids'])
        self.assertEqual(json.dumps(g,sort_keys=True),before)


class MarkerAdapterTests(unittest.TestCase):
    def reading(self):
        binding=Fixture().binding
        text={'id':'word','raw':'PD','normalized':'PD','bbox_native':[20,30,30,40]}
        number={'id':'number','raw':'2','normalized':'2','bbox_native':[23,20,27,29]}
        marker={'id':'symbol','text':text,'number':number,'bbox_native':[17,17,33,43],
            'symbol_path':{'id':'observed-circle'},'canonical_code':'PD/2','code_family':'PD','reading_status':'SUPPORTED'}
        return dict(source_binding=binding,source_role='Ref',frame_id=binding['frame_id'],code_reviews=[marker])

    def test_adapter_reuses_existing_glossary_without_inventing_door_or_project_schedule(self):
        from integrations.fpue_ontology.opening_catalog_adapter import from_marker_readings
        raw=self.reading();before=deepcopy(raw);g=from_marker_readings(raw)
        self.assertEqual(g['source_binding']['origin'],'SYNTHETIC_CONTRACT_TEST')
        self.assertEqual(raw,before);self.assertEqual(g['type_assignments'],[])
        self.assertEqual(g['catalogs'][0]['origin'],'USER_INITIAL_GLOSSARY')
        self.assertEqual(g['catalog_entries'][0]['definition_status'],'INITIAL_GLOSSARY')
        self.assertEqual(g['type_designations'][0]['status'],'PROPOSED')
        self.assertEqual({e['expected_type'] for e in g['entities']},{'Symbol'})

    def test_adapter_rejects_foreign_digit_frame_and_wrong_code(self):
        from integrations.fpue_ontology.opening_catalog_adapter import from_marker_readings
        raw=self.reading();raw['code_reviews'][0]['number']['frame_id']='Target:foreign'
        with self.assertRaisesRegex(ValueError,'foreign'):from_marker_readings(raw)
        raw=self.reading();raw['code_reviews'][0]['number']['raw']='1'
        with self.assertRaisesRegex(ValueError,'contradicts observed'):from_marker_readings(raw)


if __name__=='__main__':unittest.main()
