"""Synthetic spatial contracts; fixtures are explicitly TEST ONLY, never GT."""
import copy,json,time,unittest
import pymupdf
from fastapi.testclient import TestClient
from phase1_pdf import render_page
from backend.architectural_observations import analyze,validate_request
from backend.architectural_geometry import analyze_geometry
from backend.architectural_spatial import analyze_spatial,normalize_space_name,SPACE_NAMES,polygon_measures
from backend.observation_jobs import ObservationJobs
from backend.public_app import create_app
from backend.test_observations import fixture,request_for,empty_ocr


def synthetic(labels=('침실1','드레스룸'),door=False,divider=True,role='Target',extra_wall=False):
    doc=pymupdf.open();page=doc.new_page(width=300,height=220)
    boxes=[[20,20,280,28],[20,192,280,200],[20,28,28,192],[272,28,280,192]]
    if divider:boxes+= [[146,28,154,94],[146,126,154,192]] if door else [[146,28,154,192]]
    if extra_wall:boxes.append([84,28,92,192])
    for b in boxes:page.draw_rect(pymupdf.Rect(*b),fill=(.1,.1,.1),color=(.1,.1,.1))
    if door:
        page.draw_line(pymupdf.Point(150,94),pymupdf.Point(182,94))
        page.draw_bezier(pymupdf.Point(150,126),pymupdf.Point(167.67,126),pymupdf.Point(182,111.67),pymupdf.Point(182,94))
    data=doc.tobytes();doc.close();image=render_page(data,1,1);identity=f'{image.source_sha256}:1:1:300x220'
    request={'source_role':'Target','page':1,'roi_bbox_native':[5,5,295,215],'image':{'source_sha256':image.source_sha256,'image_id':identity,'frame_id':'Target:'+identity,'width':300,'height':220,'extraction_zoom':1}}
    binding=validate_request(request,image);binding['document_id']='TEST_ONLY';binding['source_role']=role;binding['frame_id']=role+':'+identity
    observations=[]
    for i,name in enumerate(labels):
        x=65 if i%2==0 else 205;y=90;poly=[[x,y],[x+24,y],[x+24,y+12],[x,y+12]]
        observations.append({'observation_id':f'TEST_ONLY:{role}:text:{i}','source_role':role,'frame_id':binding['frame_id'],'observation_type':'TextRegion','geometry_native':{'points_native':poly},'confidence':.9,'source_evidence':{'text':name}})
    entities=[];proofs=[]
    for i,b in enumerate(boxes):
        x0,y0,x1,y1=b;identity=f'TEST_ONLY:{role}:wall:{i}';axis='H' if x1-x0>y1-y0 else 'V'
        entities.append({'entity_id':identity,'source_role':role,'frame_id':binding['frame_id'],'element_type':'Wall','geometry_native':{'bbox_native':b,'polygons_native':[[[x0,y0],[x1,y0],[x1,y1],[x0,y1]]]},'geometry_properties':{'orientation':axis,'classification':'MEASURED_WALL_BAND'},'evidence_refs':[identity+':evidence'],'observation_refs':[],'claim_status':'PROPOSED'})
        observation_id=identity+':boundary'
        observations.append({'observation_id':observation_id,'source_role':role,'frame_id':binding['frame_id'],'observation_type':'PdfLine','geometry_native':{'points_native':[[x0,y0],[x1,y1]]},'source_evidence':{'kind':'TEST_ONLY_SYNTHETIC_PDF_BOUNDARY'}})
        proofs.append({'evidence_id':identity+':evidence','source_observation_refs':[observation_id],'supporting':[{'kind':'TEST_ONLY_SYNTHETIC_BAND_MATCHING_PDF'}],'contradicting':[]})
    if door:
        identity='TEST_ONLY:door';entities.append({'entity_id':identity,'source_role':role,'frame_id':binding['frame_id'],'element_type':'Door','geometry_native':{'bbox_native':[146,94,154,126]},'geometry_properties':{'opening_segment_native':[[150,94],[150,126]],'host_wall_candidate_ids':[entities[4]['entity_id']],'gap_status':'PROPOSED_OBSERVED_BRACKETS'},'evidence_refs':[identity+':evidence'],'observation_refs':[],'claim_status':'PROPOSED'})
        entities[-1]['geometry_properties']['jamb_bracket_candidate_ids']=[entities[4]['entity_id'],entities[5]['entity_id']]
        observation_id=identity+':swing'
        observations.append({'observation_id':observation_id,'source_role':role,'frame_id':binding['frame_id'],'observation_type':'PdfCurve','geometry_native':{'points_native':[[150,126],[167.67,126],[182,111.67],[182,94]]},'source_evidence':{'kind':'TEST_ONLY_SYNTHETIC_PDF_SWING'}})
        proofs.append({'evidence_id':identity+':evidence','source_observation_refs':[observation_id],'supporting':[{'kind':'SWING_LEAF_AND_BOTH_JAMBS','arc_observation_ref':observation_id,'jamb_connections':[{'connected':True},{'connected':True}],'curve_visibility':1,'leaf_candidates':[{'visible_fraction':1}]}],'contradicting':[]})
    parent={'schema_version':'architectural_geometry_bundle/1','bundle_kind':'GEOMETRIC_ENTITY_CANDIDATES','status':'COMPLETED','run_id':'TEST_ONLY_GEOMETRY','parent_observation_run_id':'TEST_ONLY_OBSERVATION','source_binding':copy.deepcopy(binding),'source_role':role,'frame_id':binding['frame_id'],'entities':entities,'evidence':proofs,'observations':observations,
        'analysis_scope':{'input_crop_sha256':image_hash(image),'core_roi':{'bbox_native':binding['core_bbox_native']},'context':{'bbox_native':binding['context_bbox_native']}},'frame_registry':{'native_frame_id':binding['frame_id'],'test_only':True},'coverage':{'test_only':True,'context_geometry_input_complete':True}}
    return data,image,binding,parent

def image_hash(image):
    import cv2,numpy as np,hashlib
    pixels=cv2.imdecode(np.frombuffer(image.png,np.uint8),cv2.IMREAD_COLOR)
    return hashlib.sha256(cv2.imencode('.png',pixels)[1].tobytes()).hexdigest()

def run_fixture(**kwargs):
    data,image,binding,parent=synthetic(**kwargs);return analyze_spatial(data,image.png,binding,parent)

class SpatialAdapterTests(unittest.TestCase):
    def test_whitelist_is_exactly_14_with_typographic_normalization_only(self):
        self.assertEqual(len(SPACE_NAMES),14)
        for raw,expected in [(' 침실 １ ','침실1'),('주방 ／ 식당','주방/식당'),('하향식 피난구','하향식피난구'),('주방·식당','주방/식당')]:self.assertEqual(normalize_space_name(raw),expected)
        for raw in ['안방','침실I','발코니','침실4','주방','식당','PW4','공간']:self.assertIsNone(normalize_space_name(raw))
    def test_closed_regions_follow_band_faces_not_OCR_rectangles(self):
        r=run_fixture();self.assertEqual(r['summary']['space_region_polygons'],2)
        for s in r['spaces']:
            self.assertEqual(s['boundary_status'],'CLOSED_CANDIDATE');self.assertGreater(s['area_native_px2'],1000);self.assertEqual(s['claim_status'],'PROPOSED');self.assertIsNone(s['area_square_metres']);self.assertNotEqual(s['bbox_native'],s['name_observation']['ocr_bbox_native']);self.assertGreaterEqual(len(s['boundary_geometry_refs']),4)
    def test_geometry_and_observation_inputs_are_not_mutated(self):
        data,im,b,p=synthetic();before=json.dumps(p,sort_keys=True);analyze_spatial(data,im.png,b,p);self.assertEqual(json.dumps(p,sort_keys=True),before)
    def test_door_logical_separator_prevents_room_merge_but_not_physical_passage(self):
        r=run_fixture(door=True);self.assertEqual(r['summary']['space_region_polygons'],2);self.assertGreater(r['diagnostics']['conditional_free_components'],r['diagnostics']['physical_free_components'])
        self.assertTrue(any(e.get('physical_component_label_names')==['침실1','드레스룸'] for e in r['evidence']));self.assertEqual(sum(relation['relation_type']=='Connection' for relation in r['relations']),1)
    def test_bare_opening_or_missing_jamb_never_invents_a_separator(self):
        for edit in ['opening','jamb']:
            data,im,b,p=synthetic(door=True)
            if edit=='opening':p['entities'][-1]['element_type']='Opening'
            else:p['evidence'][-1]['supporting'][0]['jamb_connections'][1]['connected']=False
            r=analyze_spatial(data,im.png,b,p);self.assertEqual(r['summary']['space_region_polygons'],0);self.assertTrue(all(s['boundary_status']=='AMBIGUOUS' for s in r['spaces']))
    def test_supported_track_separator_keeps_Unknown_type_and_no_semantic_mapping(self):
        data,im,b,p=synthetic(door=True);door=p['entities'][-1];door['element_type']='Unknown';door['geometry_properties']['classification']='WINDOW_OR_SLIDING_TRACK'
        p['evidence'][-1]['supporting']=[{'kind':'GAP_BRACKETS_TRACKS','tracks_native':[[[146,94],[146,126]],[[150,94],[150,126]],[[154,94],[154,126]]],'cap_visibility':[1,1]}]
        r=analyze_spatial(data,im.png,b,p);self.assertEqual(r['summary']['space_region_polygons'],2);self.assertEqual(r['geometry_candidates'][-1]['element_type'],'Unknown');self.assertFalse(any(relation['relation_type']=='Connection' for relation in r['relations']));self.assertTrue(any('TRACK' in b['kind'] for space in r['spaces'] for b in space['boundary_segments_native']));self.assertFalse(any('DOOR' in b['kind'] for space in r['spaces'] for b in space['boundary_segments_native']))
    def test_conflicting_boundary_produces_multiple_partition_uncertainty(self):
        data,im,b,p=synthetic();p['evidence'][-1]['contradicting']=[{'flags':['FRAME_DOOR_WINDOW_OR_FINISH_HINT']}]
        r=analyze_spatial(data,im.png,b,p);self.assertTrue(any(e.get('cautious_geometry_partition_differs') for e in r['evidence']));self.assertTrue(all(s['boundary_status']=='AMBIGUOUS' for s in r['spaces'] if s['space_name']!='UNLABELED_SPACE'));self.assertEqual(r['summary']['space_region_polygons'],0)
    def test_missing_wall_reports_label_merge_and_does_not_output_one_merged_polygon(self):
        r=run_fixture(divider=False);self.assertEqual(r['summary']['space_region_polygons'],0);self.assertTrue(r['diagnostics']['same_component_label_conflicts']);self.assertTrue(all(s['polygon_native'] is None for s in r['spaces']))
    def test_false_wall_partition_dependency_is_reported_without_false_GT_verdict(self):
        r=run_fixture(labels=('침실1',),extra_wall=True);self.assertTrue(r['diagnostics']['partition_dependencies']);self.assertTrue(any(d['geometry_id']=='TEST_ONLY:Target:wall:5' for d in r['diagnostics']['partition_dependencies']));self.assertGreater(r['summary']['unlabeled_space_candidates'],0)
    def test_core_roi_cut_does_not_become_a_closing_wall_or_area(self):
        data,im,b,p=synthetic();b['core_bbox_native']=[50,40,240,180];p['source_binding']=copy.deepcopy(b);p['analysis_scope']['core_roi']['bbox_native']=b['core_bbox_native'];r=analyze_spatial(data,im.png,b,p)
        self.assertTrue(all(s['boundary_status']=='PARTIAL_BOUNDARY' for s in r['spaces']));self.assertTrue(all(s['polygon_native'] is None and s['area_native_px2'] is None and s['boundary_truncated'] for s in r['spaces']))
    def test_no_geometry_boundaries_leave_open_space_without_a_polygon(self):
        data,im,b,p=synthetic(labels=('침실1',));p['entities']=[];p['evidence']=[];r=analyze_spatial(data,im.png,b,p)
        named=next(s for s in r['spaces'] if s['space_name']=='침실1');self.assertEqual(named['boundary_status'],'OPEN_BOUNDARY');self.assertIsNone(named['polygon_native']);self.assertEqual(named['boundary_geometry_refs'],[])
    def test_context_open_edge_does_not_create_a_polygon(self):
        data,im,b,p=synthetic(labels=('침실1',));p['entities']=p['entities'][1:];r=analyze_spatial(data,im.png,b,p)
        named=next(s for s in r['spaces'] if s['space_name']=='침실1');self.assertIsNone(named['polygon_native']);self.assertTrue(named['boundary_truncated']);self.assertIn(named['boundary_status'],['PARTIAL_BOUNDARY','OPEN_BOUNDARY'])
    def test_label_on_wall_does_not_become_an_interior_seed(self):
        data,im,b,p=synthetic(labels=('침실1',));p['observations'][0]['geometry_native']['points_native']=[[142,88],[158,88],[158,100],[142,100]];r=analyze_spatial(data,im.png,b,p);named=next(s for s in r['spaces'] if s['space_name']=='침실1');self.assertIsNone(named['seed_native']);self.assertEqual(named['boundary_status'],'UNRESOLVED')
    def test_furniture_context_does_not_prove_a_room_interior(self):
        data,im,b,p=synthetic(labels=('침실1',));e=copy.deepcopy(p['entities'][0]);e['entity_id']='TEST_ONLY:fixture';e['element_type']='Unknown';e['geometry_native']['polygons_native']=[[[50,65],[110,65],[110,130],[50,130]]];e['evidence_refs']=['TEST_ONLY:fixture:evidence'];p['entities'].append(e);p['evidence'].append({'evidence_id':'TEST_ONLY:fixture:evidence','supporting':[],'contradicting':[{'flags':['FURNITURE_FIXTURE_LAYER_HINT']}]});r=analyze_spatial(data,im.png,b,p);named=next(s for s in r['spaces'] if s['space_name']=='침실1');self.assertIsNone(named['seed_native'])
    def test_unlabeled_geometry_does_not_fabricate_missing_whitelist_names(self):
        r=run_fixture(labels=());self.assertTrue(r['spaces']);self.assertTrue(all(s['space_name']=='UNLABELED_SPACE' for s in r['spaces']));self.assertEqual(r['summary']['OCR_space_name_candidates'],0)
    def test_boundary_evidence_resolves_in_parent_and_native_frame_is_independent(self):
        r=run_fixture(role='Ref');ids={g['entity_id'] for g in r['geometry_candidates']};ev={e['evidence_id'] for e in r['evidence']};obs={o['observation_id'] for o in r['observations']}
        for s in r['spaces']:
            self.assertEqual(s['source_role'],'Ref');self.assertEqual(s['frame_id'],r['frame_id']);self.assertTrue(set(s['boundary_geometry_refs'])<=ids);self.assertTrue(set(s['evidence_refs'])<=ev);self.assertTrue(set(s['name_evidence_refs'])<=obs)
        self.assertEqual(r['coordinate_system']['compass_orientation'],'UNKNOWN_UNVERIFIED');self.assertNotIn('Target:',json.dumps(r['spaces']))
    def test_mismatched_geometry_frame_or_source_is_rejected(self):
        data,im,b,p=synthetic()
        for key in ['source_sha256','frame_id','document_id','core_bbox_native']:
            bad=copy.deepcopy(b);bad[key]='different'
            with self.assertRaises(ValueError):analyze_spatial(data,im.png,bad,p)
    def test_input_incompleteness_propagates_without_claiming_grounding_success(self):
        data,im,b,p=synthetic();p['status']='PARTIAL';p['coverage']['failure']='TEST_ONLY_missing_geometry';r=analyze_spatial(data,im.png,b,p);self.assertEqual(r['status'],'PARTIAL');self.assertEqual(r['coverage']['input_geometry_coverage']['failure'],'TEST_ONLY_missing_geometry');self.assertFalse(r['policy']['GT_metrics_computed'])
    def test_valid_multipolygon_holes_area_and_invalid_crossing_are_separate(self):
        p={'outer_rings':[[[0,0],[100,0],[100,100],[0,100]]],'holes':[[[10,10],[30,10],[30,30],[10,30]]]};self.assertEqual(polygon_measures(p)['area_native_px2'],9600)
        self.assertIsNone(polygon_measures({'outer_rings':[[[0,0],[20,20],[0,20],[20,0]]],'holes':[]}))
    def test_nearby_numeric_OCR_alone_does_not_remove_wall(self):
        data,im,b,p=synthetic();before=analyze_spatial(data,im.png,b,p);text=copy.deepcopy(p['observations'][0]);text['observation_id']='TEST_ONLY:numeric';text['source_evidence']['text']='2600';text['geometry_native']['points_native']=[[65,8],[90,8],[90,17],[65,17]];p['observations'].append(text);after=analyze_spatial(data,im.png,b,p);self.assertEqual(before['summary']['space_region_polygons'],after['summary']['space_region_polygons']);self.assertEqual(len(before['diagnostics']['boundary_input_decisions']),len(after['diagnostics']['boundary_input_decisions']))

class SpatialAPIContracts(unittest.TestCase):
    def setUp(self):
        self.jobs=ObservationJobs(runner=lambda pdf,png,b:analyze(pdf,png,b,empty_ocr),geometry_runner=analyze_geometry,spatial_runner=analyze_spatial);self.app=create_app(observation_jobs=self.jobs);self.client=TestClient(self.app);self.client.get('/api/workspace');self.headers={'Origin':'http://testserver'};self.data=fixture();doc=self.client.post('/api/documents',files={'file':('TEST_ONLY_spatial.pdf',self.data)},headers=self.headers).json();im,self.body=request_for(self.data);self.body['document_id']=doc['id'];self.doc=doc
        obs=self.client.post('/api/observations',json=self.body,headers=self.headers).json();self.wait('/api/observations',obs['run_id']);self.body['observation_run_id']=obs['run_id'];g=self.client.post('/api/geometries',json=self.body,headers=self.headers).json();self.parent=self.wait('/api/geometries',g['run_id']);self.body['geometry_run_id']=g['run_id']
    def wait(self,path,identity):
        for _ in range(300):
            r=self.client.get(path+'/'+identity).json()
            if r['status'] not in ['QUEUED','RUNNING']:return r
            time.sleep(.01)
        self.fail('job did not complete')
    def tearDown(self):self.jobs.close();self.client.close()
    def test_uploaded_PDF_geometry_to_spatial_job_uses_exact_source_chain(self):
        response=self.client.post('/api/spatials',json=self.body,headers=self.headers);self.assertEqual(response.status_code,202,response.text);job=self.wait('/api/spatials',response.json()['run_id']);self.assertIn(job['status'],['COMPLETED','PARTIAL']);self.assertEqual(job['result']['parent_geometry_run_id'],self.parent['run_id']);self.assertEqual(job['result']['parent_observation_run_id'],self.body['observation_run_id']);self.assertEqual(job['result']['source_binding']['source_sha256'],self.doc['sha256'])
    def test_other_visitor_stale_ROI_parent_or_Ref_request_is_rejected(self):
        for key,value in [('source_role','Ref'),('roi_bbox_native',[1,1,40,40]),('observation_run_id','other')]:
            b=copy.deepcopy(self.body);b[key]=value;self.assertEqual(self.client.post('/api/spatials',json=b,headers=self.headers).status_code,422)
        with TestClient(self.app) as other:
            other.get('/api/workspace');self.assertEqual(other.post('/api/spatials',json=self.body,headers=self.headers).status_code,404)
    def test_origin_request_size_wrong_kind_and_failure_are_explicit(self):
        self.assertEqual(self.client.post('/api/spatials',json=self.body).status_code,403);self.assertEqual(self.client.post('/api/spatials',content=b'x'*70000,headers=self.headers).status_code,413);self.assertEqual(self.client.get('/api/spatials/'+self.parent['run_id']).status_code,404)
        def fail(*args):raise RuntimeError('TEST_ONLY /home/private')
        self.jobs.spatial_runner=fail;job=self.wait('/api/spatials',self.client.post('/api/spatials',json=self.body,headers=self.headers).json()['run_id']);self.assertEqual(job['status'],'FAILED');self.assertIsNone(job['result']);self.assertNotIn('/home/private',json.dumps(job));self.assertEqual(job['failure']['code'],'SPATIAL_WORKER_FAILED')
    def test_spatial_retries_keep_current_observation_geometry_chain_available(self):
        for _ in range(2):self.wait('/api/spatials',self.client.post('/api/spatials',json=self.body,headers=self.headers).json()['run_id'])
        self.assertEqual(self.client.get('/api/observations/'+self.body['observation_run_id']).status_code,200);self.assertEqual(self.client.get('/api/geometries/'+self.body['geometry_run_id']).status_code,200)

if __name__=='__main__':unittest.main()
