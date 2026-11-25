"""PoC 2 synthetic safety cases. These fixtures are not architectural GT."""
import copy
import json
from pathlib import Path
import tempfile
import unittest

import cv2
import numpy as np

import geometry_refinement as gr
import run_geometry_refinement as runner
from space_grounding_geometry import Settings,axis_points,barrier_masks,fit_arc,raster_observations,rectangle_for


def line(identity,axis,n,start,end,stroke=2.,layers=None):
    return {'id':'SYNTHETIC_ONLY:'+identity,'axis':axis,'normal':n,'start':start,'end':end,
        'stroke_px':stroke,'points':axis_points(axis,n,start,end),'raster_support_fraction':1.,
        'source_ids':['SYNTHETIC_ONLY:source:'+identity],'layers':layers or [],'refinement_flags':[]}


def measured_pair(width,length=160,stroke=2.):
    return {'thickness_px':width,'start':0.,'end':float(length),'boundary_stroke_px':[stroke,stroke]}


def band(identity,axis,n,start,end,width):
    return {'id':'SYNTHETIC_ONLY:'+identity,'axis':axis,'normal':n,'n1':n-width/2,'n2':n+width/2,
        'start':start,'end':end,'thickness_px':width,'polygon':rectangle_for(axis,n-width/2,n+width/2,start,end),
        'space_barrier_eligible':True,'arbitration_cut':False,'source_overlap_length_px':end-start,
        'boundary_ids':[identity+':a',identity+':b'],'score':5.}


def raster(lines=(),shape=(190,230),text_polys=()):
    gray=np.full(shape,255,np.uint8)
    for l in lines:cv2.line(gray,tuple(map(round,l['points'][0])),tuple(map(round,l['points'][1])),0,1)
    return raster_observations(gray,list(text_polys),12,Settings())


def door_fixture(include_jambs=True):
    walls=[band('left','H',100,20,80,8),band('right','H',100,120,200,8)]
    k=.5522847498;arc=fit_arc([[120,100],[120,100+40*k],[80+40*k,140],[80,140]])
    arc['id']='SYNTHETIC_ONLY:swing'
    raw=[line('jamb-left','V',80,96,104),line('jamb-right','V',120,96,104)] if include_jambs else []
    gray=np.full((190,230),255,np.uint8)
    cv2.ellipse(gray,(80,100),(40,40),0,0,90,0,1);cv2.line(gray,(80,100),(80,140),0,1)
    for l in raw:cv2.line(gray,tuple(map(round,l['points'][0])),tuple(map(round,l['points'][1])),0,1)
    return walls,[arc],raw,raster_observations(gray,[],12,Settings())


class RefinementSyntheticTests(unittest.TestCase):
    def test_thick_bands_use_measured_width_not_default_architecture_width(self):
        for width in [12,24]:
            ls=[line('a','H',25,20,190),line('b','H',25+width,20,190)]
            bands,_,_,dist=gr.reconstruct_bands(ls,raster(ls),[measured_pair(width)],12,'Ref')
            active=[b for b in bands if b['space_barrier_eligible']]
            self.assertEqual(len(active),1);self.assertEqual(active[0]['thickness_px'],width)
            self.assertEqual(dist['dominant_px'],width)
            self.assertEqual(active[0]['status'],'PROPOSED')

    def test_thin_wall_has_two_geometric_end_contacts(self):
        ls=[line('top-a','H',20,20,200),line('top-b','H',40,20,200),
            line('bottom-a','H',120,20,200),line('bottom-b','H',140,20,200),
            line('thin-a','V',90,40,120,.3),line('thin-b','V',94,40,120,.3)]
        bands,_,_,_=gr.reconstruct_bands(ls,raster(ls),[measured_pair(20),measured_pair(20),measured_pair(4,80,.3)],12,'Ref')
        thin=[b for b in bands if b['axis']=='V' and b['thickness_px']==4]
        self.assertEqual(len(thin),1);self.assertTrue(thin[0]['space_barrier_eligible'])
        self.assertTrue(all(x['contacts'] for x in thin[0]['endpoint_contacts']))

    def test_single_partition_keeps_unknown_width_and_is_not_solid(self):
        ls=[line('top-a','H',20,20,200),line('top-b','H',40,20,200),line('single','V',130,40,120,.3)]
        _,singles,_,_=gr.reconstruct_bands(ls,raster(ls),[measured_pair(20)],12,'Target')
        single=next(s for s in singles if s['source_line_id'].endswith(':single'))
        self.assertIsNone(single['thickness_px']);self.assertFalse(single['space_barrier_eligible'])

    def test_short_wall_bracket_is_retained_with_observed_endpoints(self):
        ls=[line('a','H',25,20,32),line('b','H',29,20,32)]
        bands,_,_,_=gr.reconstruct_bands(ls,raster(ls),[measured_pair(4,12)],12,'Ref')
        self.assertEqual(len(bands),1);self.assertEqual([bands[0]['start'],bands[0]['end']],[20,32])
        self.assertIn('not VERIFIED',bands[0]['endpoints_semantics'])

    def test_t_junction_checks_wall_endpoint_contact(self):
        graph=gr.topology([band('h','H',60,20,180,8),band('v','V',100,60,150,4)],[],{'visible_distance':np.zeros((200,230))},'Ref')
        self.assertEqual(graph['edges'][0]['junction_type'],'T');self.assertEqual(graph['edges'][0]['status'],'SUPPORTED')

    def test_l_junction_checks_both_wall_endpoints(self):
        graph=gr.topology([band('h','H',60,60,180,8),band('v','V',60,60,150,4)],[],{'visible_distance':np.zeros((200,230))},'Ref')
        self.assertEqual(graph['edges'][0]['junction_type'],'L')

    def test_thin_wall_meeting_thick_wall_face_need_not_reach_centerline(self):
        graph=gr.topology([band('thin','H',60,20,80,4),band('thick','V',90,20,150,20)],[],{'visible_distance':np.zeros((200,230))},'Ref')
        self.assertEqual(graph['edges'][0]['junction_type'],'T')
        self.assertEqual(graph['edges'][0]['point_native'][0],80.)
        self.assertEqual(graph['edges'][0]['centerline_intersection_native'][0],90.)

    def test_x_crossing_alone_is_uncertain(self):
        graph=gr.topology([band('h','H',70,20,180,8),band('v','V',100,20,150,4)],[],{'visible_distance':np.zeros((200,230))},'Ref')
        self.assertFalse(graph['edges']);self.assertEqual(graph['uncertain_connections'][0]['junction_type'],'X_UNCERTAIN')

    def test_gap_is_not_painted_as_physical_wall(self):
        walls,arcs,raw,r=door_fixture();gaps,_=gr.opening_grounding(walls,[],arcs,raw,r,12,'Ref')
        adapted=[{'id':b['id'],'pieces':[dict(b,observation_id=b['id'])],**{k:b[k] for k in ['axis','normal','thickness_px']}} for b in walls]
        opening=[dict(g,entity_id=g['element_id'],origin='POC2_OBSERVED_GAP') for g in gaps]
        masks=barrier_masks(adapted,opening,(190,230))
        self.assertEqual(masks['physical'][100,100],0);self.assertGreater(masks['logical'][100,100],0)

    def test_swing_and_both_visible_jamb_paths_propose_door(self):
        walls,arcs,raw,r=door_fixture();gaps,_=gr.opening_grounding(walls,[],arcs,raw,r,12,'Ref')
        door=next(g for g in gaps if g['kind']=='Door')
        self.assertTrue(all(j['connected'] for j in door['door_proposals'][0]['jamb_connections']))
        self.assertEqual(door['status'],'PROPOSED');self.assertIn('Opening2',door['id']);self.assertIn('Door2',door['element_id'])

    def test_nearby_swing_without_jamb_connectivity_does_not_get_host(self):
        walls,arcs,raw,r=door_fixture(False);gaps,attempts=gr.opening_grounding(walls,[],arcs,raw,r,12,'Ref')
        self.assertTrue(attempts);self.assertFalse(any(g['kind']=='Door' for g in gaps))
        self.assertFalse(any(g['eligible_logical_boundary'] for g in gaps))

    def test_window_tracks_are_geometry_not_ocr_code_links(self):
        walls,_,_,_=door_fixture();ls=[line(str(y),'H',y,80,120,.4) for y in [97,100,103]]
        raw=[line('cap-left','V',80,96,104),line('cap-right','V',120,96,104),line('rib','V',100,96,104)]
        r=raster(ls+raw);gaps,_=gr.opening_grounding(walls,ls,[],raw,r,12,'Target')
        window=next(g for g in gaps if g['kind']=='Window')
        self.assertEqual(len(window['track_line_ids']),3);self.assertTrue(window['transverse_frame_ids'])
        self.assertNotIn('refersTo',window);self.assertEqual(window['status'],'PROPOSED')

    def test_solid_band_contradicts_offset_gap_even_with_visible_swing(self):
        walls,arcs,raw,r=door_fixture();walls.append(band('continuous','H',102,80,120,4))
        gaps,_=gr.opening_grounding(walls,[],arcs,raw,r,12,'Ref')
        contradicted=[g for g in gaps if g['contradicting_solid_band_ids']]
        self.assertTrue(contradicted);self.assertTrue(all(not g['eligible_logical_boundary'] for g in contradicted))
        self.assertTrue(all(g['kind']=='Opening' for g in contradicted))

    def test_dimension_layer_is_not_promoted_by_length_or_strong_stroke(self):
        ls=[line('a','H',25,20,190,layers=['A-DIM']),line('b','H',45,20,190,layers=['A-DIM'])]
        bands,_,rejected,_=gr.reconstruct_bands(ls,raster(ls),[measured_pair(20)],12,'Ref')
        self.assertFalse(bands);self.assertTrue(any(r['rejection_reason']=='NONSTRUCTURAL_OR_DASH_CONTEXT' for r in rejected))

    def test_label_box_interior_is_not_a_wall(self):
        ls=[line('a','H',25,20,190),line('b','H',45,20,190)]
        text=[[[25,27],[185,27],[185,43],[25,43]]]
        bands,_,rejected,_=gr.reconstruct_bands(ls,raster(ls,text_polys=text),[measured_pair(20)],12,'Ref')
        self.assertFalse(bands);self.assertTrue(any(r['rejection_reason']=='TEXT_OR_LABEL_FRAME_INTERIOR_NOT_WALL' for r in rejected))

    def test_numeric_dimension_context_without_cad_layer_is_deferred(self):
        ls=[line('a','H',25,20,190),line('b','H',45,20,190),line('extra','H',10,20,190)]
        texts=[{'id':'SYNTHETIC_ONLY:dimension:'+str(i),'text':str(300+i*100),'polygon_native':[[x,26],[x+20,26],[x+20,36],[x,36]]} for i,x in enumerate([50,110])]
        bands,_,_,_=gr.reconstruct_bands(ls,raster(ls),[measured_pair(20)],12,'Target',ocr=texts)
        self.assertTrue(bands);self.assertFalse(any(b['space_barrier_eligible'] for b in bands))
        self.assertTrue(any(b['annotation_context']['dimension_pattern_suspected'] for b in bands))

    def test_missed_ocr_label_rectangle_is_not_an_isolated_solid_wall(self):
        ls=[line('a','H',25,20,60),line('b','H',41,20,60)]
        rectangles=[{'id':'SYNTHETIC_ONLY:frame','bbox':[20,25,60,41]}]
        bands,_,_,_=gr.reconstruct_bands(ls,raster(ls),[measured_pair(16)],12,'Target',rectangles=rectangles)
        self.assertEqual(len(bands),1);self.assertFalse(bands[0]['space_barrier_eligible'])
        self.assertEqual(bands[0]['classification'],'SMALL_CLOSED_FRAME_UNCERTAIN')

    def test_local_arbitration_preserves_long_band_outside_short_conflict(self):
        long=band('long','H',50,10,190,10);short=band('short','H',50,70,90,10);short['score']=7
        selected,_=gr.select_local_bands([long,short])
        observed=[(b['start'],b['end']) for b in selected if b['id']==long['id']]
        self.assertEqual(observed,[(10,70),(90,190)])

    def test_disconnected_collinear_walls_never_become_continuous_solid(self):
        walls=[band('left','H',50,20,80,8),band('right','H',50,120,180,8)]
        graph=gr.topology(walls,[],{'visible_distance':np.zeros((200,230))},'Target')
        self.assertFalse(graph['edges']);self.assertEqual(len([n for n in graph['nodes'] if n['type']=='WallSegment']),2)

    def test_continuation_records_contact_without_creating_union_polygon(self):
        walls=[band('left','H',50,20,80,8),band('right','H',50,80.5,180,8)]
        graph=gr.topology(walls,[],{'visible_distance':np.zeros((200,230))},'Ref')
        self.assertEqual(graph['edges'][0]['type'],'OBSERVED_CONTINUATION');self.assertFalse(graph['edges'][0]['solid_union_geometry_created'])

    def test_gt_template_never_copies_proposals_into_gt_cells(self):
        binding={'role':'Ref','frame_id':'SYNTHETIC_ONLY','source_sha256':'not_gt','page':4}
        data={'Ref':{'source_binding':binding,'wall_candidates':[{'id':'candidate','geometry':{'points':[[1,1],[2,2]]},'baseline_links':[{'baseline_candidate_id':'poc1'}]}],'openings':[],'spaces':[]}}
        with tempfile.TemporaryDirectory() as folder:
            records=runner.gt_templates(Path(folder),data)
            self.assertEqual(records[0]['poc1_candidate_ids'],['poc1'])
            self.assertTrue(all(v is None for r in records for k,v in r.items() if k.startswith('gt_')))
            self.assertTrue(all(r['manual_geometry_native'] is None for r in records))
            self.assertEqual(sum(r['review_kind']=='MISSING_GEOMETRY_BLANK_SLOT' for r in records),6)

    def test_baseline_link_does_not_claim_cross_drawing_identity(self):
        b=band('new','V',40,20,180,6)
        base=[{'id':'Ref:Wall:old','type':'Wall','geometry':{'physical_pieces':[dict(b)]}}]
        links=runner.baseline_links(b,base)
        self.assertEqual(links[0]['baseline_candidate_id'],'Ref:Wall:old')
        self.assertTrue(links[0]['not_physical_identity_or_change_correspondence'])


if __name__=='__main__':unittest.main(verbosity=2)
