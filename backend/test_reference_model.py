"""Model provenance and review states require explicit source/revision support."""
import copy
import unittest

from backend.architectural_geometry import analyze_geometry
from backend.reference_model import build_reference_model
from backend.test_reference_pipeline import failed_ocr
from backend.test_reference_restoration import fixture
from backend.architectural_observations import analyze, validate_request
from backend.source_contract import request_frame


class ReferenceModelTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        pdf,image,_,_=fixture()
        binding=validate_request({'source_role':'Ref','page':image.page_number,'image':request_frame(image,'Ref')},image)
        binding['analysis_run_id']='observed'
        cls.observation=analyze(pdf,image.png,binding,failed_ocr)
        cls.geometry=analyze_geometry(pdf,image.png,{**binding,'analysis_run_id':'geometry'},cls.observation)

    def test_integration_keeps_failed_observation_and_gate_pending(self):
        model=self.geometry['reference_model']
        self.assertEqual(model['source_revision']['observation_run_id'],self.observation['run_id'])
        self.assertEqual(model['gate']['status'],'NOT_PASSED')
        self.assertIsNone(model['evaluation']['metrics'])
        self.assertTrue(model['coverage']['observation'])
        from backend.source_contract import describe_bundle
        self.assertEqual(describe_bundle(model)['schema_version'],'reference_understanding_model/1')
        self.assertTrue(all(c['review_status']=='PENDING_INDEPENDENT_REVIEW' for c in model['gate']['criteria']))

    def test_every_registered_pointer_resolves_in_export(self):
        def resolve(pointer):
            value=self.geometry
            for part in pointer.strip('/').split('/'):
                value=value[int(part)] if isinstance(value,list) else value[part]
            return value
        for row in self.geometry['reference_model']['inventory']:
            self.assertIsNotNone(resolve(row['pointer']),row['id'])

    def test_wrong_parent_or_scope_cannot_merge(self):
        for field,value in [('run_id','different')]:
            bad=copy.deepcopy(self.observation)
            bad[field]=value
            with self.assertRaises(ValueError):
                build_reference_model(self.geometry,bad)
        bad=copy.deepcopy(self.observation)
        bad['source_binding']['core_bbox_native']=[1,1,250,250]
        with self.assertRaises(ValueError):
            build_reference_model(self.geometry,bad)

    def test_dangling_relation_is_visible_and_cannot_clear_gate(self):
        bad=copy.deepcopy(self.geometry)
        bad['reference_relations']['relations'].append({'id':'broken','kind':'Connection',
            'source':{'id':'missing-a','type':'Space'},'target':{'id':'missing-b','type':'Space'},
            'evidence_refs':['missing-proof'],'claim_status':'PROPOSED'})
        model=build_reference_model(bad,self.observation)
        self.assertEqual(model['reference_audit']['status'],'FAILED')
        self.assertEqual(model['gate']['status'],'NOT_PASSED')
        self.assertEqual(len(model['reference_audit']['unresolved_relation_refs']),1)
