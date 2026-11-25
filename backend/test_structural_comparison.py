"""Synthetic decision controls and a separately identified original-PDF replay."""
from copy import deepcopy
from dataclasses import replace
import hashlib
import json
from pathlib import Path
from tempfile import TemporaryDirectory

import cv2
import numpy as np
import unittest

from fastapi.testclient import TestClient
from backend.public_app import COOKIE,create_app
from backend.test_spatial_alignment import fixture
from phase1_pdf import PDFInfo

from backend.structural_alignment import encode
from backend.structural_comparison import compare, compare_registered


class Scene:
    def __init__(self):
        self.models=[]
        self.sources=[]
        for role in ('Ref','Target'):
            frame=role+':fixture'
            g=dict(source_role=role,frame_id=frame,run_id=role+'-run',source_binding={'extraction_zoom':1},
                   space_closure={'regions':[],'faces':[]},architectural_assessment={'structural_parents':[],'openings':[],'installed_elements':[]})
            g['reference_relations' if role=='Ref' else 'target_relations']={'door_reviews':[],'code_reviews':[]}
            self.models.append(g)
            self.sources.append(dict(frame_id=frame,geometry_run_id=g['run_id']))
            for name,x,y in [('A',15,18),('B',160,25),('C',28,145),('D',170,143)]:
                self.room(role,name,x,y)
        matrix=[[1,0,0],[0,1,0],[0,0,1]]
        self.alignment=dict(input_fingerprint='test-scope',sources=self.sources,status='VERIFIED_ROI',
            target_roi={'image':{'height':200,'width':200},'vertices':[[1,1],[198,1],[198,198],[1,198]]},
            matrix=matrix,inverse_matrix=matrix,settings={'normal_tolerance_ref_px':2,'angle_tolerance_degrees':1},
            mapping={'supported_tile_indices':[0]},coverage={'tiles':[{'status':'SUPPORTED','target_bbox_native':[1,1,198,198]}]})

    def g(self,role):
        return self.models[0 if role=='Ref' else 1]

    def room(self,role,name,x,y):
        self.g(role)['space_closure']['regions'].append(dict(region_id=role+name,name=name,seed_native=[x+6,y+6],
            polygon_native={'outer_rings':[[[x,y],[x+12,y],[x+12,y+12],[x,y+12]]],'holes':[]},
            observation_id=role+name+'label',boundary_runs=[]))

    def wall(self,role,key,parent,x1,x2,y,width=4,space=None):
        g=self.g(role); f=dict(decision='ADMISSIBLE_GEOMETRIC_HYPOTHESIS',boundary_truncated=False,
            boundary_faces_native=[[[x1,y-width/2],[x2,y-width/2]],[[x1,y+width/2],[x2,y+width/2]]],
            fragment_id=role+key,thickness_px=width,source_observation_refs=[role+key+'-line'])
        g['architectural_assessment']['structural_parents'].append(dict(parent_structure_id=role+parent,fragments=[f]))
        if space:
            faces=g['space_closure']['faces'];faces.append(dict(source_refs=[f['fragment_id']]))
            r=next(r for r in g['space_closure']['regions'] if r['name']==space)
            r['boundary_runs'].append(dict(owner=len(faces),points_native=f['boundary_faces_native'][0]))

    def opening(self,role,key,hosts,x1,x2,y,door=False,spaces=()):
        g=self.g(role); oid=role+key
        assessment=g['architectural_assessment']
        assessment['openings'].append(dict(opening_id=oid,decision='ADMISSIBLE_OPENING_GEOMETRY',
            opening_segment_native=[[x1,y],[x2,y]],host_parent_ids=[role+h for h in hosts],evidence_refs=[oid+'-brackets']))
        if door:
            did=oid+'-door'
            assessment['installed_elements'].append(dict(installation_id=did,type_candidates=['Door'],opening_id=oid,
                decision='ADMISSIBLE_OPENING_GEOMETRY',evidence_refs=[did+'-swing']))
            rel=g['reference_relations' if role=='Ref' else 'target_relations']
            rel['door_reviews'].append(dict(door_id=did,after={'status':'SUPPORTED'},sides=[dict(space_id=role+s) for s in spaces]))

    def rows(self,kind):
        return [r for r in compare_registered(self.models,self.alignment)['correspondences'] if r['entity_type']==kind]


class ComparisonTests(unittest.TestCase):
    def test_conditional_room_outline_cannot_establish_identity(self):
        scene = Scene()
        region = scene.g('Target')['space_closure']['regions'][0]
        region['boundary_runs'].append(dict(owner=-1, points_native=[[15,18],[27,18]]))
        row = next(r for r in scene.rows('Space') if region['region_id'] in r['target_ids'])
        self.assertEqual(row['judgment'], 'UNDETERMINED')
        self.assertNotEqual(row['identity_status'], 'SUPPORTED')

    def test_changed_wall_requires_local_registration_support_in_rendered_pair(self):
        # Same structural geometry in both trials. Changing the stroke contrast
        # changes independent image support at the modified wall, not its identity.
        for ink, expected in [(110, 'CHANGED'), (0, 'UNDETERMINED')]:
            with self.subTest(ink=ink):
                pages, models, roi = fixture()
                for i, role in enumerate(('Ref', 'Target')):
                    page, model = pages[i], models[i]
                    dx, dy = (9, 6) if role == 'Ref' else (0, 0)
                    image = cv2.imdecode(np.frombuffer(page.png, np.uint8), cv2.IMREAD_GRAYSCALE)
                    width = 4 if role == 'Ref' else 10
                    line = np.array([[187+dx, 220+dy], [209+dx, 220+dy]])
                    faces = [line-[0,width//2], line+[0,width//2]]
                    for pts in faces:
                        cv2.line(image, tuple(pts[0]), tuple(pts[1]), ink, 1)
                    fragment = dict(fragment_id=role+'-observed', boundary_truncated=False,
                                    decision='ADMISSIBLE_GEOMETRIC_HYPOTHESIS', thickness_px=width,
                                    boundary_faces_native=[s.tolist() for s in faces])
                    model['architectural_assessment']['structural_parents'].append(
                        dict(parent_structure_id=role+'-wall', fragments=[fragment]))
                    pages[i] = replace(page, png=encode(image))
                    model['source_binding']['image_sha256'] = hashlib.sha256(pages[i].png).hexdigest()
                result = compare(pages, models, roi)
                row, = [r for r in result['correspondences'] if r['target_ids']==['Target-observed']]
                self.assertEqual(row['judgment'], expected)
                self.assertFalse(row['alignment_fit_parent'])
                if expected == 'CHANGED':
                    self.assertEqual(row['ref_ids'], ['Ref-observed'])
                    self.assertEqual(row['measurements']['thickness_delta_ref_px'], 6)
                    self.assertEqual(row['change_types'], ['MODIFIED'])
                    self.assertEqual(row['verified_target_bbox_native'], [187,215,209,225])
                else:
                    self.assertIsNone(row['measurements'])
                    self.assertIsNone(row['verified_target_bbox_native'])

    def test_registered_image_pair_separates_marker_change_from_specification(self):
        pages, models, roi = fixture()
        for i, (role, digit, dx, dy) in enumerate([('Ref','1',9,6), ('Target','2',0,0)]):
            page, model = pages[i], models[i]
            image = cv2.imdecode(np.frombuffer(page.png, np.uint8), cv2.IMREAD_GRAYSCALE)
            center = (190+dx, 210+dy)
            cv2.circle(image, center, 12, 15, 1)
            cv2.putText(image, 'PW', (182+dx,208+dy), cv2.FONT_HERSHEY_SIMPLEX, .30, 15)
            cv2.putText(image, digit, (187+dx,218+dy), cv2.FONT_HERSHEY_SIMPLEX, .30, 15)
            pages[i] = replace(page, png=encode(image))
            model['source_binding']['image_sha256'] = hashlib.sha256(pages[i].png).hexdigest()
            marker = dict(id=role+'-marker', code='PW'+digit,
                          bbox_native=[178+dx,198+dy,202+dx,222+dy],
                          text={'id':role+'-PW'}, number={'id':role+'-'+digit})
            model['reference_relations' if i==0 else 'target_relations'] = {'code_reviews':[marker]}
        result = compare(pages, models, roi)
        self.assertIn(result['alignment_status'], ('VERIFIED_ROI','PARTIAL'))
        markers = [r for r in result['correspondences'] if r['entity_type']=='CodeMarker']
        self.assertEqual({(r['domain'],r['judgment']) for r in markers},
                         {('ANNOTATION','CHANGED'),('SPECIFICATION','UNDETERMINED')})

    def test_tangential_shift_of_equal_wall_intervals_keeps_physical_ends_unknown(self):
        s=Scene();s.wall('Ref','f','w',40,145,70);s.wall('Target','f','w',47,152,70)
        row,=s.rows('Wall')
        self.assertEqual(row['judgment'],'UNDETERMINED')
        self.assertEqual(row['measurements']['observed_span_delta_ref_px'],0)
        self.assertIsNone(row['measurements']['endpoint_displacement_ref_px'])

    def test_small_relative_but_measurable_span_delta_is_not_unchanged(self):
        s=Scene();s.wall('Ref','f','w',40,145,70);s.wall('Target','f','w',40,154,70)
        row,=s.rows('Wall')
        self.assertEqual(row['judgment'],'UNDETERMINED')
        self.assertEqual(row['measurements']['observed_span_delta_ref_px'],9)

    def test_matching_uses_geometry_and_bounded_space_then_measures_moved_wall(self):
        s=Scene();s.wall('Ref','f','w',55,110,70,space='A');s.wall('Target','g','x',55,110,78,space='A')
        before=deepcopy(s.models)
        row,=s.rows('Wall')
        self.assertEqual((row['identity_status'],row['judgment'],row['change_types']),('SUPPORTED','CHANGED',['MOVED']))
        self.assertIsNone(row['measurements']['endpoint_displacement_ref_px'])
        self.assertEqual(row['measurements']['normal_max_ref_px'],8)
        self.assertEqual(before,s.models)

    def test_same_line_changing_observed_interval_is_not_a_physical_endpoint(self):
        s=Scene();s.wall('Ref','f','w',50,145,70);s.wall('Target','f','w',50,80,70)
        row,=s.rows('Wall')
        self.assertEqual(row['judgment'],'UNDETERMINED')
        self.assertIsNone(row['measurements']['endpoint_displacement_ref_px'])

    def test_wall_width_change_and_scoped_agreement(self):
        for width,state in [(4,'UNCHANGED_WITHIN_SCOPE'),(10,'CHANGED')]:
            s=Scene();s.wall('Ref','f','w',55,110,70);s.wall('Target','g','w',55,110,70,width)
            row,=s.rows('Wall')
            self.assertEqual(row['judgment'],state)
            self.assertEqual(row['measurements']['thickness_delta_ref_px'],width-4)
            self.assertEqual(row['state'],'REVIEW')

    def test_shift_without_continuity_and_ambiguous_parallel_walls(self):
        s=Scene();s.wall('Ref','f','w',55,110,70);s.wall('Target','f','w',55,110,77)
        row,=s.rows('Wall');self.assertIn('REMOVED_AND_ADDED',row['alternatives'])
        s.wall('Ref','f2','w2',55,110,84)
        rows=s.rows('Wall')
        self.assertTrue(all(r['judgment']=='UNDETERMINED' for r in rows))
        self.assertTrue(any(r['identity_status']=='AMBIGUOUS' for r in rows))

    def test_split_and_merge_vs_segmentation(self):
        for many,single in [('Ref','Target'),('Target','Ref')]:
            for gap,opening,expected in [(0,False,'UNCHANGED_WITHIN_SCOPE'),(10,False,'UNDETERMINED'),(10,True,'CHANGED')]:
                with self.subTest(many=many,gap=gap,opening=opening):
                    s=Scene();s.wall(single,'whole','host',50,130,75)
                    s.wall(many,'left','host',50,90,75);s.wall(many,'right','host',90+gap,130,75)
                    if opening:s.opening(many,'gap',['host'],90,100,75)
                    row,=s.rows('Wall')
                    self.assertEqual(row['judgment'],expected)
                    self.assertEqual(row['cardinality'],'2:1' if many=='Ref' else '1:2')

    def test_missing_object_never_becomes_deletion(self):
        s=Scene();s.wall('Ref','single','w',40,75,55)
        row,=s.rows('Wall');self.assertEqual((row['identity_status'],row['judgment']),('UNMATCHED','UNDETERMINED'))

    def test_unmeasured_tile_inside_object_and_failed_alignment(self):
        s=Scene()
        for role in ('Ref','Target'):s.wall(role,'test','host',40,130,70)
        s.alignment.update(status='PARTIAL',coverage={'tiles':[
            {'status':'SUPPORTED','target_bbox_native':box} for box in [[1,1,60,198],[80,1,198,198]]]},
            mapping={'supported_tile_indices':[0,1]})
        self.assertIsNone(s.rows('Wall')[0]['measurements'])
        s.alignment.update(status='FAILED',mapping=None)
        self.assertTrue(all(r['judgment']=='UNDETERMINED' for r in s.rows('Space')))

    def test_door_motion_and_relation_change_are_independent(self):
        s=Scene()
        for role in ('Ref','Target'):s.wall(role,'f','host',40,145,90)
        s.opening('Ref','gap',['host'],64,77,90,True,('A','B'))
        s.opening('Target','gap',['host'],80,93,90,True,('A','C'))
        row,=s.rows('Door')
        self.assertEqual(row['judgment'],'CHANGED')
        self.assertEqual(set(row['change_types']),{'MOVED','RELATION_CHANGED'})

    def test_missing_marker_digit_is_not_a_specification_change(self):
        s=Scene()
        for role in ('Ref','Target'):
            g=s.g(role);r=g['reference_relations' if role=='Ref' else 'target_relations']
            r['code_reviews'].append(dict(id=role+'mark',bbox_native=[60,70,80,90],text={'id':role+'str'},
                    number={'id':role+'number'} if role=='Ref' else None,code='PW4' if role=='Ref' else 'PW'))
        row,=s.rows('CodeMarker')
        self.assertEqual((row['domain'],row['judgment']),('ANNOTATION','UNDETERMINED'))

    def test_renamed_space_preserves_object_and_separates_annotation(self):
        s=Scene();s.g('Target')['space_closure']['regions'][0]['name']='renamed'
        rows=[r for r in s.rows('Space') if r['ref_ids']==['RefA']]
        self.assertEqual({(r['domain'],r['judgment']) for r in rows},{('STRUCTURAL','UNCHANGED_WITHIN_SCOPE'),('ANNOTATION','CHANGED')})

    def test_opening_at_locus_requires_positive_solid_and_no_competing_host_opening(self):
        for role,other,event in [('Ref','Target','REMOVED'),('Target','Ref','ADDED')]:
            s=Scene()
            # Corresponding host is grounded by an independently matching fragment.
            for side in ('Ref','Target'):
                s.wall(side,'common','host',40,80,60)
            s.wall(other,'solid','host',100,150,75)
            s.opening(role,'gap',['host'],110,130,75)
            result,=s.rows('Opening')
            self.assertEqual(result['change_types'],[event])
            s.opening(other,'elsewhere',['host'],105,125,102)
            self.assertFalse(any(event in r['change_types'] for r in s.rows('Opening')))

    def test_alignment_fit_wall_cannot_prove_itself_unchanged(self):
        s=Scene()
        for role in ('Ref','Target'):s.wall(role,'f','host',40,90,90)
        s.alignment['pairs']=[dict(usage='FIT',ref_parent='Refhost',target_parent='Targethost')]
        row,=s.rows('Wall')
        self.assertEqual(row['judgment'],'UNDETERMINED')
        self.assertTrue(row['alignment_fit_parent'])

    def test_one_room_two_polygons_requires_physical_divider_and_opposite_free_space(self):
        for witness in [False,True]:
            s=Scene()
            for role,name,x0,x1 in [('Ref','whole',50,130),('Target','north',50,90),('Target','south',90,130)]:
                s.room(role,name,55,x0)
                room=s.g(role)['space_closure']['regions'][-1]
                room['polygon_native']['outer_rings']=[[[55,x0],[135,x0],[135,x1],[55,x1]]]
            if witness:
                s.wall('Target','divider','parent',55,135,90,space='north')
                s.g('Target')['space_closure']['regions'][-1]['boundary_runs']=[{'owner':1,'points_native':[[55,90],[135,90]]}]
                s.g('Ref')['reference_domains']={'layers':{'F':{'rects_native_xyxy':[[55,50,136,131]]}}}
            rows=[r for r in s.rows('Space') if r['ref_ids']==['Refwhole']]
            row,=rows
            self.assertEqual(row['cardinality'],'1:2')
            self.assertEqual(row['judgment'],'CHANGED' if witness else 'UNDETERMINED')

    def test_public_comparison_recomputes_scope_in_own_workspace(self):
        app=create_app()
        pages,models,roi=fixture()
        origin={'Origin':'http://testserver'}
        with TestClient(app) as client,TestClient(app) as other:
            client.get('/api/workspace');other.get('/api/workspace')
            w=app.state.workspaces.get(client.cookies[COOKIE])
            w.observation_owner='comparison-test'
            body={'target_roi':roi}
            store=w.api.state.documents
            for page,g in zip(pages,models):
                role=g['source_role']
                doc=store.add(role+'.pdf',b'fixture',PDFInfo(page.source_sha256,4))
                store.cache[(doc.id,4,1)]=page
                binding=dict(g['source_binding'],document_id=doc.id)
                app.state.observation_jobs.jobs[g['run_id']]=dict(owner=w.observation_owner,document_id=doc.id,
                    binding=binding,source_sha256=page.source_sha256,run_id=g['run_id'],status='PARTIAL',
                    job_kind='geometry',result=g,created_at=0,failure=None)
                body[role]=dict(document_id=doc.id,geometry_run_id=g['run_id'],frame_id=g['frame_id'])
            url='/api/structural-comparisons'
            self.assertEqual(other.post(url,json=body,headers=origin).status_code,404)
            self.assertEqual(client.post(url,json={**body,'matrix':[]},headers=origin).status_code,422)
            result=client.post(url,json=body,headers=origin)
            self.assertEqual(result.status_code,200,result.text[:350])
            self.assertEqual(result.json()['alignment_status'],'VERIFIED_ROI')

    def test_complete_code_difference_has_separate_unresolved_specification(self):
        s=Scene()
        for role,code in [('Ref','PW1'),('Target','PW2')]:
            rel=s.g(role)['reference_relations' if role=='Ref' else 'target_relations']
            rel['code_reviews'].append(dict(id=role+'mark',bbox_native=[60,70,80,90],text={'id':role+'code'},
                                            number={'id':role+'digit'},code=code))
        rows=s.rows('CodeMarker')
        self.assertEqual({(r['domain'],r['judgment']) for r in rows},
                         {('ANNOTATION','CHANGED'),('SPECIFICATION','UNDETERMINED')})

    def test_partner_frame_revision_rejected(self):
        s=Scene();s.models[1]['run_id']='different'
        with self.assertRaises(ValueError):s.rows('Wall')



class OriginalPage4ReplayTests(unittest.TestCase):
    def test_real_pdf_graphic_changes_do_not_override_deferred_object_identity(self):
        from phase1_pdf import render_page
        from scripts.review_structural_comparison import page4_readings

        root = Path(__file__).resolve().parents[1]
        previous = root/'implementation_validation/2026-11-21/step8'
        filenames = (root/'source_pdfs/자이사업승인도서.pdf', root/'source_pdfs/자이실시도서.pdf')
        geometry_files = (previous/'ref_regression/geometry.json', previous/'actual_final/geometry.json')
        if not all(path.is_file() for path in (*filenames, *geometry_files)):
            self.skipTest('Original Page 4 PDFs and saved independent geometries required')
        documents = [path.read_bytes() for path in filenames]
        pages = [render_page(pdf, 4, 2) for pdf in documents]
        geometries = [json.loads(path.read_bytes()) for path in geometry_files]
        roi = json.loads((root/'alignment_inputs/page4_user_roi.json').read_bytes())
        result = compare(pages, geometries, roi)
        rows = result['correspondences']
        self.assertEqual(result['alignment_status'], 'DEFERRED')
        self.assertTrue(rows)
        self.assertTrue(all(r['judgment']=='UNDETERMINED' and r['measurements'] is None for r in rows))
        self.assertTrue(any((r['source_observations'] or {}).get('labels') == ['부부욕실','부부욕실']
                            for r in rows))
        assessment = json.loads((root/'stability_assessment/page4/assessment.json').read_bytes())
        with TemporaryDirectory() as out:
            readings = {r['id']: r for r in page4_readings(documents,pages,assessment,Path(out))}
        for key, delta in [('M01',9.36), ('M02',-10.56), ('S01',0), ('S06',0)]:
            self.assertAlmostEqual(readings[key]['graphic_span_delta_at_2x_px'], delta, places=2)
        self.assertEqual(readings['M04']['source_reading'], 'NO_PAIRED_PRIMITIVE')
