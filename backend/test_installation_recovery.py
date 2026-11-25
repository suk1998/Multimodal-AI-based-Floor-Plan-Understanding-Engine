"""Synthetic geometry controls, not Page 4 GT or physical-object accuracy."""
import sys,unittest,copy,hashlib
from pathlib import Path
import cv2,numpy as np,pymupdf
from poc import space_grounding_geometry as geometry
sys.modules.setdefault('space_grounding_geometry',geometry)
from poc.geometry_refinement import installation_shapes,opening_grounding,RefinementSettings
from backend.test_architectural_geometry import prepare
from backend.architectural_geometry import analyze_geometry
from backend import test_element_grounding as grounding_fixtures
from backend.test_element_grounding import run_source


def drawing(kind='window',missing=None):
    image=np.full((200,600),255,np.uint8);raw=[];lines=[];arcs=[]
    def stroke(identity,points,width=.8):
        cv2.polylines(image,[np.rint(points).astype(np.int32)],False,0,1)
        a=geometry.axis_segment(points);axis,n,start,end=a
        row=dict(id='Ref:'+identity,points=points,stroke_px=width,path_type='s',layer='',
                 axis=axis,normal=n,start=start,end=end,visibility=1.)
        raw.append(row);return row
    if kind=='window':
        for i,n in enumerate([70.,74.,78.]):
            row=stroke('pdf_path:rail:'+str(i),[[20.,n],[520.,n]])
            lines.append(dict(row,id='pdf:line:'+str(i),raster_support_fraction=1.,refinement_flags=[],source_ids=[row['id']]))
        for x in [20.,520.]:
            if missing=='cap' and x==520:continue
            stroke(f'pdf_path:cap:{x}:0',[[x,70.],[x,74.]])
            stroke(f'pdf_path:cap:{x}:1',[[x,74.],[x,78.]])
        if missing!='rib':stroke('pdf_path:rib',[[270.,68.],[270.,80.]])
        if missing=='dimension':
            for row in lines:row['refinement_flags']=['DIMENSION_PROJECTION_OR_ANNOTATION_LAYER_HINT']
    else:
        r=60.;k=.55228475;control=[[40.,80.],[40.,80.+k*r],[100.-k*r,140.],[100.,140.]]
        arc=geometry.fit_arc(control);arcs=[dict(arc,id='pdf_path:swing:curve:0',layer='')]
        cv2.polylines(image,[np.rint(arc['polyline']).astype(np.int32)],False,0,1)
        if missing!='leaf':stroke('pdf_path:leaf',[[100.,80.],[100.,140.]])
        stroke('pdf_path:left-jamb',[[40.,74.],[40.,82.]])
        if missing!='cap':stroke('pdf_path:right-jamb',[[100.,74.],[100.,82.]])
        if missing=='annotation':arcs[0]['layer']='A-TOI'
    raster=geometry.raster_observations(image,[],12.,geometry.Settings())
    return lines,arcs,raw,raster


class InstallationRecoveryTests(unittest.TestCase):
    def shapes(self,kind='window',missing=None):
        lines,arcs,raw,raster=drawing(kind,missing)
        return installation_shapes(lines,arcs,raw,raster,12.,'Ref',RefinementSettings())

    def test_large_profile_and_split_end_caps_recovered_without_wall_gap(self):
        items=self.shapes();self.assertEqual(len(items),1);item=items[0]
        self.assertEqual(item['width_px'],500.)
        self.assertFalse(item['eligible_logical_boundary'])
        self.assertEqual(item['host_segment_ids'],[])
        self.assertTrue(item['installation_shape_only'])
        self.assertEqual(item['recovery_evidence']['type_alternatives'],['Window','SlidingDoor','Grille'])
        self.assertGreaterEqual(len(item['transverse_frame_ids']),5)

    def test_no_end_cap_no_rib_or_dimension_style_cannot_become_profile(self):
        for missing in ['cap','rib','dimension']:
            self.assertEqual(self.shapes(missing=missing),[],missing)

    def test_swing_leaf_and_both_short_frame_stiles_without_preexisting_gap(self):
        rows=self.shapes('door');self.assertEqual(len(rows),1);door=rows[0]
        self.assertEqual(door['kind'],'Door')
        self.assertTrue(all(j['connected'] for j in door['door_proposals'][0]['jamb_connections']))
        self.assertFalse(door['eligible_logical_boundary'])
        self.assertEqual(door['gap_status'],'INSTALLATION_DRAWING_ONLY_GAP_UNRESOLVED')

    def test_curve_alone_missing_leaf_or_one_jamb_does_not_make_door(self):
        for missing in ['leaf','cap','annotation']:
            self.assertEqual(self.shapes('door',missing),[],missing)

    def test_existing_wall_channel_and_inputs_are_not_modified(self):
        data=drawing();before=copy.deepcopy(data)
        lines,arcs,raw,raster=data;gaps,attempts=opening_grounding([],lines,arcs,raw,raster,12.,'Ref')
        self.assertEqual(data[:3],before[:3]);self.assertEqual(len(gaps),1)
        self.assertTrue(np.array_equal(raster['ink'],before[3]['ink']))
        self.assertFalse(gaps[0]['eligible_logical_boundary'])

    def test_actual_vector_adapter_retains_original_frame_and_evidence_closure(self):
        with pymupdf.open() as doc:
            page=doc.new_page(width=600,height=180)
            for y in [60,64,68]:page.draw_line((30,y),(530,y),width=.8)
            for x in [30,530]:
                page.draw_line((x,60),(x,64),width=.8);page.draw_line((x,64),(x,68),width=.8)
            page.draw_line((270,58),(270,70),width=.8)
            pdf=doc.tobytes()
        data,image,binding,parent=prepare(data=pdf)
        result=analyze_geometry(data,image.png,binding,parent)
        recovered=[i for i in result['architectural_assessment']['installed_elements'] if i.get('installation_shape_only')]
        self.assertTrue(recovered)
        for item in recovered:
            self.assertEqual(item['decision'],'HOLD')
            self.assertEqual(item['physical_wall_gap_status'],'UNKNOWN')
            self.assertEqual(item['frame_id'],binding['frame_id'])
        obs={o['observation_id'] for o in result['observations']}
        for e in result['evidence']:
            self.assertEqual(e['unresolved_source_ids'],[])
            self.assertTrue(set(e['source_observation_refs'])<=obs)
        self.assertTrue(all(e['claim_status']=='PROPOSED' for e in result['entities']))


class FullSourceGroundingTests(unittest.TestCase):
    def test_native_swing_proof_is_not_lost_due_to_local_rerender_budget(self):
        fixture=grounding_fixtures.ElementGroundingTests();data=fixture.door();g=data[1][0];frame=g['frame_id']
        item=g['architectural_assessment']['installed_elements'][0];item['installation_shape_only']=True
        support=g['evidence'][0]['supporting'][0]
        support.update(curve_visibility=1,jamb_connections=[dict(connected=True,observation_refs=[frame+':jamb:'+str(i)]) for i in range(2)])
        g['evidence'][0]['supporting'].append(dict(kind='INSTALLATION_SHAPE_RECOVERY',claim_scope='DRAWING_ONLY'))
        points=[[200.,200.],[205.,210.],[220.,220.]];support['swing_polyline_native']=points
        g['observations']=[dict(observation_id=frame+':arc',observation_type='PdfCurve',geometry_native=dict(points_native=points))]
        g['reference_relations']['door_reviews']=[]
        graph=run_source(data)
        self.assertTrue(any(e['geometry_status']=='SUPPORTED_SWING_LEAF_AND_JAMBS' for e in graph['entities'] if e['kind']=='Door'))
        self.assertTrue(all(m['physical_assignment_status']=='UNBOUND' for m in graph['marker_reviews']))
        g['observations']=[]
        self.assertFalse(any(e.get('geometry_status')=='SUPPORTED_SWING_LEAF_AND_JAMBS' for e in run_source(data)['entities'] if e['kind']=='Door'))


if __name__=='__main__':unittest.main()
