"""Architecture hypotheses must retain counterevidence and the source frame."""
import time
import unittest
from types import SimpleNamespace

from fastapi.testclient import TestClient

from backend.architectural_geometry import analyze_geometry
from backend.architectural_geometry_assessment import assess_geometry
from backend.observation_jobs import ObservationJobs
from backend.public_app import create_app
from backend.reference_pipeline import analyze_reference
from backend.spatial_ontology import geometry_eligibility
from backend.test_reference_pipeline import input_for, synthetic_ocr, test_validator
from backend.test_reference_restoration import fixture
from integrations.fpue_ontology.understanding_contract_v0_4 import ENGINE_PROFILES

SETTINGS = SimpleNamespace(min_pair_overlap=.55,pixel_tolerance=1.5,min_visibility=.8)
BINDING = dict(frame_id='Ref:test',source_role='Ref',analysis_run_id='test')


def fragment(x):
    return dict(segment_id=str(x), faces_native=[[[x,0],[x+20,0]],[[x,4],[x+20,4]]],
                centerline_native=[[x,2],[x+20,2]],polygon_native=[[x,0],[x+20,0],[x+20,4],[x,4]],
                thickness_px=4,raster_support=[.99,.98],endpoint_contact_ids=['segment-2'],
                arbitration_cut=True,context_truncated=False,observation_refs=['face-1','face-2'],interior_text_ids=[])


def candidates(parts, layer=None):
    wall=dict(entity_id='w',element_type='Wall',geometry_properties={'observed_wall_fragments':parts},
              evidence_refs=['p'],observation_refs=[],geometry_native={'bbox_native':[0,0,100,4]})
    proof=dict(evidence_id='p',supporting=[],contradicting=[])
    obs=[] if layer is None else [dict(observation_id='line',observation_type='PdfLine',
        geometry_native={'points_native':[[0,0],[20,0]]},source_evidence={'layer':layer})]
    return wall,proof,obs


class AssessmentTests(unittest.TestCase):
    def test_independent_intervals_and_untagged_support(self):
        wall,proof,obs=candidates([fragment(0),fragment(80)],'A-DIM')
        a=assess_geometry(BINDING,[wall],[proof],obs,SETTINGS)
        parent=a['structural_parents'][0]
        self.assertEqual(parent['decision'],'PARTIAL_GEOMETRIC_SUPPORT')
        self.assertFalse(parent['unobserved_intervals_filled'])
        self.assertEqual([p['decision'] for p in parent['fragments']],['HOLD','ADMISSIBLE_GEOMETRIC_HYPOTHESIS'])
        self.assertIn('line',wall['observation_refs'])
        self.assertEqual(parent['fragments'][1]['face_side_assignments'],['UNKNOWN','UNKNOWN'])
        self.assertFalse(geometry_eligibility(wall,{'p':proof})['eligible'])

    def test_cad_wall_name_cannot_override_missing_junction_or_cut(self):
        f=fragment(0)
        f.update(endpoint_contact_ids=[],context_truncated=True)
        wall,proof,obs=candidates([f],'A-WAL1')
        part=assess_geometry(BINDING,[wall],[proof],obs,SETTINGS)['structural_parents'][0]['fragments'][0]
        self.assertEqual(part['decision'],'HOLD')
        self.assertIn('NO_OBSERVED_JUNCTION_SUPPORT',part['reasons'])
        self.assertIn('WALL_LAYER_HINT',part['layer_hypotheses'])
        self.assertEqual(part['physical_endpoints_status'],'UNKNOWN')

    def test_contradicted_gap_and_track_alternatives_remain_separate(self):
        wall,proof,obs=candidates([fragment(0)])
        gap=dict(entity_id='track',element_type='Unknown',geometry_properties={
            'opening_width_px':12,'opening_segment_native':[[0,0],[12,0]],
            'classification':'WINDOW_OR_SLIDING_TRACK','host_wall_candidate_ids':['w','nonwall'],
            'gap_status':'REJECTED_SOLID_OVERLAP'},geometry_native={'bbox_native':[0,0,12,2]},
            evidence_refs=['q'],boundary_truncated=False)
        q=dict(evidence_id='q',supporting=[dict(kind='GAP_BRACKETS_TRACKS',cap_visibility=[1,1])])
        a=assess_geometry(BINDING,[wall,gap],[proof,q],obs,SETTINGS)
        opening=a['openings'][0]
        self.assertEqual(opening['decision'],'CONTRADICTED')
        self.assertEqual(opening['unresolved_host_ids'],['nonwall'])
        self.assertEqual(a['installed_elements'][0]['type_candidates'],['Window','SlidingDoor'])
        self.assertEqual([(r['source_id'],r['relation_type'],r['target_id']) for r in a['relations']],
                         [('w','hosts',opening['opening_id']),('track:installation','installedIn',opening['opening_id'])])
        self.assertTrue(all(r['question'] in ENGINE_PROFILES['GEOMETRIC']['questions'] for r in a['review_requests']))
        self.assertTrue(a['policy']['no_space_polygons_or_connections_created'])


class SourceScopeTests(unittest.TestCase):
    def test_ref_geometry_uses_own_observation_scope(self):
        data,image,_,_=fixture()
        jobs=ObservationJobs(reference_runner=lambda p,i,b:analyze_reference(p,i,b,synthetic_ocr,test_validator),
                             geometry_runner=analyze_geometry)
        headers={'Origin':'http://testserver'}
        def wait(client,path,request):
            response=client.post(path,json=request,headers=headers)
            self.assertEqual(response.status_code,202,response.text)
            url=path+'/'+response.json()['run_id']
            for _ in range(250):
                job=client.get(url).json()
                if job['status'] not in ('QUEUED','RUNNING'):
                    self.assertIn(job['status'],('PARTIAL','COMPLETED'),job.get('failure'))
                    return job
                time.sleep(.02)
            self.fail('synthetic job timed out')
        try:
            app=create_app(observation_jobs=jobs)
            with TestClient(app) as c:
                c.get('/api/workspace')
                doc=c.post('/api/documents',files={'file':('source.pdf',data)},headers=headers).json()
                body={**input_for(image),'document_id':doc['id']}
                url='/api/reference-understandings'
                first=wait(c,url,body)
                region=first['result']['drawing_regions']['candidates'][0]
                scoped=wait(c,url,{**body,'drawing_region_selection':{'run_id':first['run_id'],'region_id':region['region_id']}})
                request={**body,'reference_run_id':scoped['run_id'],'drawing_region_bbox_native':[0,0,1,1]}
                result=wait(c,'/api/geometries',request)['result']
                self.assertEqual(result['source_binding']['core_bbox_native'],region['bbox_native'])
                self.assertEqual(result['parent_observation_run_id'],scoped['result']['observation_bundle']['run_id'])
                self.assertEqual(result['source_role'],'Ref')
                for change in ({'roi_bbox_native':[1,1,8,8]},{'source_role':'Target'},{'observation_run_id':'extra'}):
                    self.assertEqual(c.post('/api/geometries',json={**request,**change},headers=headers).status_code,422)
                with TestClient(app) as other:
                    other.get('/api/workspace')
                    self.assertEqual(other.post('/api/geometries',json=request,headers=headers).status_code,404)
        finally:
            jobs.close()


if __name__=='__main__':
    unittest.main()
