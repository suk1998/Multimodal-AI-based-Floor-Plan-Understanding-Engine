"""Synthetic contract checks; real drawing checks are reported separately.

Run with --real-results DIR to inspect artifacts without treating them as GT.
"""
import argparse
from collections import Counter
from io import BytesIO
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

import cv2
import numpy as np
from PIL import Image
import pymupdf

import space_grounding as sg
from space_grounding_geometry import (Settings,arc_evidence,barrier_masks,fit_arc,
    merge_lines,pair_boundaries,pdf_to_native_matrix,raster_observations,
    rectangle_for,relation_error,sample_line,transform_points)


def make_wall(identity,axis,n,start,end,thickness):
    p={'axis':axis,'normal':n,'start':start,'end':end,'thickness_px':thickness,
       'polygon':rectangle_for(axis,n-thickness/2,n+thickness/2,start,end),
       'observation_id':identity+':pair'}
    return {'id':identity,'axis':axis,'normal':n,'thickness_px':thickness,'pieces':[p]}


def room_fixture(with_separator=True):
    walls=[make_wall('Ref:Wall:top','H',20,20,200,4),make_wall('Ref:Wall:bottom','H',140,20,200,4),
        make_wall('Ref:Wall:left','V',20,20,140,4),make_wall('Ref:Wall:right','V',200,20,140,4),
        {'id':'Ref:Wall:middle','axis':'V','normal':110,'thickness_px':6,
         'pieces':[make_wall('a','V',110,20,60,6)['pieces'][0],make_wall('b','V',110,90,140,6)['pieces'][0]]}]
    opening={'id':'Ref:opening:test','entity_id':'Ref:Door:test','kind':'Door','axis':'V','normal':110,
        'start':60,'end':90,'host_thickness_px':6,'host_wall_ids':['Ref:Wall:middle'],
        'logical_segment':[[110,60],[110,90]],'polygon':rectangle_for('V',107,113,60,90),
        'eligible_logical_boundary':with_separator,'origin':'BRACKETED_WALL_GAP','arc':{'synthetic_only':True}}
    b=barrier_masks(walls,[opening],(160,230));r={'ink':np.zeros((160,230),np.uint8),'text_mask':np.zeros((160,230),np.uint8)}
    binding={'role':'Ref','frame_id':'SYNTHETIC_ONLY:Ref','source_sha256':'synthetic_not_gt'}
    labels=[{'id':'Ref:text:a','entity_id':'Ref:TextRegion:a','text':'침실1','polygon_native':[[44,45],[74,45],[74,57],[44,57]]},
            {'id':'Ref:text:b','entity_id':'Ref:TextRegion:b','text':'드레스룸','polygon_native':[[140,45],[178,45],[178,57],[140,57]]}]
    for l in labels:cv2.fillPoly(r['text_mask'],[np.array(l['polygon_native'],np.int32)],255)
    return walls,opening,b,r,binding,labels


class GeometryContractTests(unittest.TestCase):
    def test_pdf_rotations_and_crop_local_coordinates_match_raster(self):
        for rotation in [0,90,180,270]:
            with self.subTest(rotation=rotation):
                doc=pymupdf.open();page=doc.new_page(width=180,height=130)
                page.draw_line((45,35),(115,80),color=(0,0,0),width=2)
                page.set_cropbox(pymupdf.Rect(20,10,155,115));page.set_rotation(rotation)
                p=page.get_drawings()[0]['items'][0]
                pix=page.get_pixmap(matrix=pymupdf.Matrix(2,2),colorspace=pymupdf.csRGB,alpha=False)
                gray=np.array(Image.open(BytesIO(pix.tobytes('png'))).convert('L'))
                matrix=pdf_to_native_matrix(page,2,(pix.x,pix.y))
                native=transform_points([list(p[1]),list(p[2])],matrix)
                samples=sample_line(gray,native)
                self.assertGreater(np.mean(samples<80),.97)
                inverse=transform_points(native,np.linalg.inv(matrix));np.testing.assert_allclose(inverse,[list(p[1]),list(p[2])],atol=1e-8)
    def test_out_of_frame_samples_are_not_clipped_into_visible_ink(self):
        field=np.zeros((20,20),np.float32)
        samples=sample_line(field,[[-10,5],[-2,5]])
        self.assertTrue(np.isnan(samples).all())
    def test_parallel_wall_pair_rejects_thin_dimension_and_ocr_strokes(self):
        gray=np.full((140,180),255,np.uint8)
        segments=[]
        for name,x,width in [('wall1',30,1.4),('wall2',42,1.4),('dimension1',95,.24),('dimension2',108,.24),('text1',140,1.4),('text2',153,1.4)]:
            cv2.line(gray,(x,20),(x,115),0,1)
            segments.append({'id':name,'points':[[x,20],[x,115]],'stroke_px':width,'layer':'','path_type':'s'})
        polygon=[[136,15],[157,15],[157,120],[136,120]]
        raster=raster_observations(gray,[polygon],16,Settings())
        lines,rejected=merge_lines(segments,raster,16,Settings());pairs,pair_rejected=pair_boundaries(lines,raster,16,Settings())
        self.assertEqual(rejected['rejected_counts']['OCR_TEXT_OVERLAP'],2)
        self.assertTrue(any(abs(p['thickness_px']-12)<.01 for p in pairs))
        self.assertFalse(any(any('dimension' in s for line in lines if line['id'] in p['boundary_ids'] for s in line['source_ids']) for p in pairs))
        self.assertGreater(pair_rejected['rejected_counts']['THIN_PARALLEL_NOT_ENOUGH_WALL_EVIDENCE'],0)
    def test_faint_arc_is_visible_evidence_while_hidden_arc_is_rejected(self):
        gray=np.full((180,180),255,np.uint8);gray[10:40,10:40]=0
        cv2.ellipse(gray,(100,100),(40,40),0,0,90,225,1);cv2.line(gray,(100,100),(140,100),225,1)
        k=.5522847498;arc=fit_arc([[140,100],[140,100+40*k],[100+40*k,140],[100,140]])
        arc['id']='SYNTHETIC_ONLY:arc'
        r=raster_observations(gray,[],16,Settings())
        found=arc_evidence(arc,'V',100,100,140,r,16,8)
        self.assertIsNotNone(found)
        self.assertGreater(found['curve_raster_support'],.9)
        blank=raster_observations(np.full_like(gray,255),[],16,Settings())
        self.assertIsNone(arc_evidence(arc,'V',100,100,140,blank,16,8))
    def test_nearby_swing_outside_wall_band_does_not_assign_host(self):
        gray=np.full((180,180),255,np.uint8);cv2.ellipse(gray,(100,100),(40,40),0,0,90,0,1);cv2.line(gray,(100,100),(140,100),0,1)
        k=.5522847498;arc=fit_arc([[140,100],[140,100+40*k],[100+40*k,140],[100,140]]);arc['id']='SYNTHETIC_ONLY:arc'
        r=raster_observations(gray,[],16,Settings())
        self.assertIsNone(arc_evidence(arc,'V',108,100,140,r,16,6))
    def test_door_logical_separator_preserves_distinct_rooms(self):
        walls,opening,b,r,binding,labels=room_fixture(True)
        spaces,_,attempts=sg.build_spaces(labels,{'rectangles':[]},walls,[opening],b,r,12,binding,Settings())
        named=[s for s in spaces if s['name_hypotheses']]
        self.assertEqual(len(named),2);self.assertEqual(len({s['component_id'] for s in named}),2)
        self.assertTrue(all(s['geometry']['polygon_native'] is not None for s in named))
        self.assertTrue(all(s['geometry']['bbox_xyxy'][2]-s['geometry']['bbox_xyxy'][0]>70 for s in named))
    def test_missing_separator_never_merges_two_labels_into_room_polygon(self):
        walls,opening,b,r,binding,labels=room_fixture(False)
        spaces,_,_=sg.build_spaces(labels,{'rectangles':[]},walls,[opening],b,r,12,binding,Settings())
        self.assertEqual(len(spaces),2)
        self.assertTrue(all(s['geometry']['polygon_native'] is None for s in spaces))
        self.assertTrue(all(any('MULTIPLE_LABEL' in issue for issue in s['uncertainties']) for s in spaces))
    def test_detector_box_larger_than_label_frame_still_blocks_room_merge(self):
        walls,opening,b,r,binding,labels=room_fixture(False)
        labels[1]['text']='욕실'  # Non-query label, no fixed list of room names.
        rectangles=[{'id':'SYNTHETIC_ONLY:label_frame','bbox':[141.2,48.5,175.5,56.6]}]
        self.assertTrue(sg.boxed_label(labels[1],rectangles))
        spaces,_,_=sg.build_spaces(labels,{'rectangles':rectangles},walls,[opening],b,r,12,binding,Settings())
        self.assertEqual(len(spaces),2);self.assertTrue(all(s['geometry']['polygon_native'] is None for s in spaces))
    def test_blank_page_label_cannot_create_ocr_box_space_polygon(self):
        walls,opening,b,r,binding,labels=room_fixture(False)
        b=barrier_masks([],[],(160,230))
        spaces,_,_=sg.build_spaces(labels[:1],{'rectangles':[]},[],[],b,r,12,binding,Settings())
        self.assertIsNone(spaces[0]['geometry']['polygon_native']);self.assertIsNone(spaces[0]['geometry']['bbox_xyxy'])
        self.assertEqual(spaces[0]['geometry']['partial_physical_boundary_segments'],[])
    def test_enclosed_geometry_without_ocr_remains_unlabeled(self):
        walls,opening,b,r,binding,labels=room_fixture(True)
        spaces,_,attempts=sg.build_spaces([],{'rectangles':[]},walls,[opening],b,r,12,binding,Settings())
        self.assertEqual(len(spaces),2);self.assertTrue(all(not s['name_hypotheses'] for s in spaces))
        self.assertTrue(all(a['status']=='NOT_DETECTED_IN_EXISTING_OCR' for a in attempts))
    def test_threshold_cut_cannot_erase_another_physical_wall(self):
        walls=[make_wall('Ref:Wall:a','H',60,10,130,8),make_wall('Ref:Wall:b','V',80,20,100,8)]
        opening={'id':'Ref:gap','entity_id':'Ref:Door:test','kind':'Door','axis':'H','normal':60,'start':50,'end':100,
            'polygon':rectangle_for('H',56,64,50,100),'logical_segment':[[50,60],[100,60]],
            'origin':'ARC_OVER_VISIBLE_THRESHOLD','host_wall_ids':['Ref:Wall:a'],'eligible_logical_boundary':True}
        b=barrier_masks(walls,[opening],(130,150))
        self.assertEqual(b['physical'][60,70],0);self.assertGreater(b['physical'][60,80],0)
    def test_relation_types_and_evidence_are_enforced(self):
        by={kind:{'id':kind,'type':kind,'frame_id':'test'} for kind in ['Space','Wall','TextRegion','Door','Window','Symbol']}
        by['other']={'id':'other','type':'Space','frame_id':'test'}
        self.assertIsNotNone(relation_error({'subject_id':'Space','predicate':'boundedBy','object_ids':['TextRegion']},by))
        self.assertIsNotNone(relation_error({'subject_id':'TextRegion','predicate':'labels','object_ids':['TextRegion']},by))
        r={'subject_id':'Door','predicate':'connects','object_ids':['Space','other']}
        self.assertEqual(relation_error(r,by),'MISSING_OPENING_AND_TWO_SIDES_EVIDENCE')
        r['opening_and_two_sides_evidence']={'opening_observation_id':'SYNTHETIC_ONLY:opening','arc':{'synthetic_only':True},
            'side_sample_points_native':[[10,10],[20,10]],'side_component_ids':[1,2],'side_space_ids':['Space','other'],'conditional_logical_separator':True}
        self.assertIsNone(relation_error(r,by))
        invalid={**r,'opening_and_two_sides_evidence':{'synthetic_only':True}}
        self.assertEqual(relation_error(invalid,by),'MISSING_OPENING_AND_TWO_SIDES_EVIDENCE')
        self.assertIsNotNone(relation_error({**r,'subject_id':'Window'},by))
        self.assertEqual(relation_error({'subject_id':'Symbol','predicate':'refersTo','object_ids':['Window']},by),'MISSING_DIRECT_POINTER_EVIDENCE')
        by['other']['frame_id']='other-drawing';self.assertEqual(relation_error(r,by),'CROSS_FRAME_RELATION')
    def test_connects_requires_two_enclosed_sides_and_is_deferred_otherwise(self):
        walls,opening,b,r,binding,labels=room_fixture(True)
        spaces,_,_=sg.build_spaces(labels,{'rectangles':[]},walls,[opening],b,r,12,binding,Settings())
        elements=[{'id':w['id'],'type':'Wall','frame_id':binding['frame_id'],'geometry_evidence_ids':[w['id']+':synthetic']} for w in walls]
        elements+=[{'id':l['entity_id'],'type':'TextRegion','frame_id':binding['frame_id'],'geometry_evidence_ids':[l['id']]} for l in labels]
        elements.append({'id':opening['entity_id'],'type':'Door','frame_id':binding['frame_id'],'geometry_evidence_ids':[opening['id']]})
        relations,attempts=sg.build_relations(elements,spaces,walls,[opening],b,binding)
        self.assertEqual(sum(x['predicate']=='connects' for x in relations),1)
        spaces[0]['geometry']['polygon_native']=None
        relations,attempts=sg.build_relations(elements,spaces,walls,[opening],b,binding)
        self.assertFalse(any(x['predicate']=='connects' for x in relations))
        self.assertTrue(any(x['predicate']=='connects' and x.get('attempt_status')=='DEFERRED' for x in attempts))
    def test_existing_output_and_failures_are_not_overwritten(self):
        with tempfile.TemporaryDirectory() as d:
            out=Path(d)/'old';out.mkdir();marker=out/'failure.json';marker.write_text('{"status":"FAILED"}')
            with patch.object(sys,'argv',['probe','--out',str(out)]):
                with self.assertRaises(SystemExit):sg.main()
            self.assertEqual(marker.read_text(),'{"status":"FAILED"}');self.assertEqual(len(list(out.iterdir())),1)


def validate_real_results(out):
    checks=[]
    def check(name,condition,details=None):checks.append({'name':name,'passed':bool(condition),'details':details})
    run=json.loads((out/'run.json').read_text());obs=json.loads((out/'geometry_observations.json').read_text());spaces=json.loads((out/'space_candidates.json').read_text());elements=json.loads((out/'element_candidates.json').read_text());relations=json.loads((out/'relation_candidates.json').read_text());vlm=json.loads((out/'vlm_geometry_comparison.json').read_text());bundle=json.loads((out/'understanding_bundle.json').read_text())
    check('completed_but_partial_status_not_successful_full_understanding',run['status']=='COMPLETE_WITH_PARTIAL_GROUNDING')
    check('no_new_vlm_calls',run['vlm_model_calls']==vlm['model_calls']==0)
    ids=[e['id'] for e in bundle['entities']]
    check('all_entity_ids_unique_in_bundle',len(set(ids))==len(ids),{'entity_count':len(ids)})
    check('no_architectural_gt_metrics_invented',all(d['metrics'] is None and d['architectural_gt'] is None for d in [run,obs,spaces,elements,relations,bundle]))
    for role in ['Ref','Target']:
        o=obs['drawings'][role];ss=spaces['drawings'][role];ee=elements['drawings'][role];rr=relations['drawings'][role];by={e['id']:e for e in ss+ee};binding=o['source_binding']
        check(role+'_pdf_vector_to_original_raster_identity',o['frame_registry']['rerender_pixel_differences']==0)
        copied=np.asarray(Image.open(out/role/'source_page.png'));source=np.asarray(Image.open(Path(run['input_manifest']).parent/role/'page.png'))
        check(role+'_original_pixel_coordinates_preserved',np.array_equal(copied,source) and copied.shape[:2]==(binding['height'],binding['width']))
        outside=[]
        for e in ss+ee:
            box=e['geometry'].get('bbox_xyxy');point=e['geometry'].get('seed_native')
            if box and not(0<=box[0]<box[2]<=binding['width'] and 0<=box[1]<box[3]<=binding['height']):outside.append(e['id'])
            if point and not(0<=point[0]<binding['width'] and 0<=point[1]<binding['height']):outside.append(e['id'])
        check(role+'_candidate_native_coordinates_in_original_frame',not outside,outside)
        check(role+'_parallel_wall_boundaries_observed',len(o['parallel_boundary_observations'])>0,{'count':len(o['parallel_boundary_observations'])})
        check(role+'_text_and_thin_parallel_rejections_recorded',o['pdf_rejected_observations']['rejected_counts'].get('OCR_TEXT_OVERLAP',0)>0 and o['wall_pair_rejections']['rejected_counts'].get('THIN_PARALLEL_NOT_ENOUGH_WALL_EVIDENCE',0)>0)
        check(role+'_opening_and_arc_evidence_preserved',bool(o['opening_observations']) and bool(o['arc_observations']))
        check(role+'_seed_and_partial_boundaries_observed',any(s['geometry']['seed_native'] and s['geometry']['partial_physical_boundary_segments'] for s in ss))
        grouped=Counter(s['component_id'] for s in ss if s['geometry']['polygon_native'] is not None)
        check(role+'_no_multiple_named_polygons_for_same_component',all(v==1 for v in grouped.values()))
        named_groups={}
        for s in ss:
            if s['name_hypotheses']:named_groups.setdefault(s['component_id'],[]).append(s)
        bad_merged=[cid for cid,group in named_groups.items() if len({sg.normalized(n['text']) for s in group for n in s['name_hypotheses']})>1 and any(s['geometry']['polygon_native'] is not None for s in group)]
        check(role+'_multiple_label_component_forces_unknown_not_merged_polygon',not bad_merged,bad_merged)
        label_copy=[]
        for s in ss:
            if s['geometry']['polygon_native'] is None:continue
            for tid in s['related_text_region_ids']:
                label_area=np.prod(np.subtract(by[tid]['geometry']['bbox_xyxy'][2:],by[tid]['geometry']['bbox_xyxy'][:2]));box=s['geometry']['bbox_xyxy'];region_area=(box[2]-box[0])*(box[3]-box[1])
                if region_area<=label_area*4:label_copy.append(s['id'])
        check(role+'_region_is_not_an_expanded_ocr_box',not label_copy,{'suspicious_ids':label_copy})
        errors=[{'id':r['id'],'error':relation_error(r,by)} for r in rr['candidates'] if relation_error(r,by)]
        check(role+'_all_generated_relation_types_valid',not errors,errors)
        check(role+'_only_own_frame_entities_and_relations',all(e['frame_id']==binding['frame_id'] and e['role']==role for e in ss+ee+rr['candidates']))
        check(role+'_no_unsupported_architectural_verified_claims',all(c['verification_status']!='VERIFIED' for e in ss+ee for c in e['claims']))
        check(role+'_unknown_boundaries_or_deferred_connectivity_preserved',any(s['geometry']['polygon_native'] is None for s in ss) or any(a.get('attempt_status')=='DEFERRED' for a in rr['attempts']))
        check(role+'_no_automatic_symbol_to_window_refersTo',not any(r['predicate']=='refersTo' for r in rr['candidates']))
        check(role+'_own_ocr_used_for_all_space_names',all(t in by and by[t]['type']=='TextRegion' for s in ss for t in s['related_text_region_ids']))
    check('original_qwen_format_failure_retained',any(f.get('status')=='OUTPUT_FORMAT_FAILED' for f in vlm['drawings']['Ref']['failures']))
    check('target_missing_pantry_not_filled_from_reference',any(q['query']=='팬트리' and q['status']=='NOT_DETECTED_IN_EXISTING_OCR' and not q['space_ids'] for q in obs['drawings']['Target']['query_observation_attempts']))
    check('unlabeled_geometry_spaces_are_supported_by_geometry_not_invented_names',any(not s['name_hypotheses'] and s['geometry']['polygon_native'] for ss in spaces['drawings'].values() for s in ss))
    check('bad_prior_vlm_relation_types_preserved_separately',sum(len(v['relation_constraint_failures']) for v in vlm['drawings'].values())>0)
    # Actual Target re-execution with access to Ref source/image/OCR denied.
    # This is a separate consistency check, not another model inference or GT.
    manifest=json.loads(Path(run['input_manifest']).read_text());inputs=Path(run['input_manifest']).parent
    ocr_root=sg.ROOT/'poc/results/page4_ocr';ref_source=Path(manifest['drawings']['Ref']['binding']['source_path']).resolve()
    blocked_prefixes=[(inputs/'Ref').resolve(),(ocr_root/'Ref').resolve()];denied=[];target_opens=set()
    def guard(event,args):
        if event!='open' or not args or not isinstance(args[0],(str,bytes)):return
        try:p=Path(args[0].decode() if isinstance(args[0],bytes) else args[0]).resolve()
        except (OSError,ValueError):return
        if p==ref_source or any(p.is_relative_to(prefix) for prefix in blocked_prefixes):
            denied.append(str(p));raise RuntimeError('Peer Ref source access denied in independent Target check')
        if p.is_relative_to(inputs) or p.is_relative_to(ocr_root) or p==Path(manifest['drawings']['Target']['binding']['source_path']):target_opens.add(str(p))
    sys.addaudithook(guard)
    independence={'kind':'actual Target extraction rerun with Ref source/image/OCR access denied','denied_accesses':denied}
    try:
        _,target_spaces,target_elements,target_relations,_=sg.analyze_drawing(inputs,ocr_root,'Target',manifest['drawings']['Target']['binding'],Settings(**run['config']))
        canonical=lambda value:json.dumps(sg.jsonable(value),ensure_ascii=False,sort_keys=True,separators=(',',':'))
        expected=[spaces['drawings']['Target'],elements['drawings']['Target'],relations['drawings']['Target']['candidates']]
        actual=[target_spaces,target_elements,target_relations]
        matches=[canonical(a)==canonical(b) for a,b in zip(expected,actual)]
        independence.update(same_spaces_elements_relations=matches,observed_input_opens=sorted(target_opens))
        check('actual_target_independent_when_reference_inputs_are_inaccessible',all(matches) and not denied,independence)
    except Exception as exc:
        independence['error']=str(exc);check('actual_target_independent_when_reference_inputs_are_inaccessible',False,independence)
    report={'kind':'actual Page 4 consistency and evidence checks, NOT architectural accuracy or independent GT',
        'checks':checks,'passed':sum(c['passed'] for c in checks),'failed':sum(not c['passed'] for c in checks),'gt_metrics':None,
        'remaining_gt_needed':['wall-vs-dimension/furniture labels','physical wall endpoints and thickness','opening and Door/Window subtype','space polygons and logical boundaries','code-to-window correspondence','independent Ref/Target spatial grounding relationships']}
    sg.write_json(out/'real_validation.json',report);print(json.dumps(report,ensure_ascii=False,indent=2))
    if report['failed']:raise SystemExit(1)


if __name__=='__main__':
    if '--real-results' in sys.argv:
        parser=argparse.ArgumentParser();parser.add_argument('--real-results',type=Path,required=True);validate_real_results(parser.parse_args().real_results)
    else:unittest.main(verbosity=2)
