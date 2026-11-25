"""Synthetic boundary/seed topology; fixture outlines are not architectural GT."""
import unittest

import cv2
import numpy as np

from backend.reference_space_closure import inner_rectangle, partition_conflicts, restore_partitions


def layout(lines, labels, scope=(0,0,210,190), layer='A-WAL1', fragments=()):
    gray=np.full((190,210),255,np.uint8)
    frame='Ref:synthetic'
    binding=dict(source_role='Ref',frame_id=frame,width=210,height=190,
                 extraction_zoom=1,core_bbox_native=list(scope),analysis_run_id='test')
    records=[]
    for i,(a,b) in enumerate(lines):
        cv2.line(gray,tuple(a),tuple(b),0,1)
        records.append(dict(observation_id=f'p{i}',source_role='Ref',frame_id=frame,observation_type='PdfLine',
                            geometry_native={'points_native':[a,b]},source_evidence={'layer':layer,'path_type':'s'}))
    for i,(name,x,y) in enumerate(labels):
        records.append(dict(observation_id=f't{i}',source_role='Ref',frame_id=frame,observation_type='TextRegion',
                            geometry_native={'points_native':[[x-6,y-5],[x+6,y-5],[x+6,y+5],[x-6,y+5]]},
                            source_evidence={'text':name}))
    a=dict(source_binding=binding,source_role='Ref',frame_id=frame,structural_parents=list(fragments))
    return restore_partitions(binding,records,a,gray)


def ring(*vertices):
    return list(zip(vertices,vertices[1:]+vertices[:1]))


BOX=ring((20,20),(120,20),(120,120),(20,120))


class ReferenceClosureTests(unittest.TestCase):
    def test_l_shape_has_reentrant_corner_and_seed(self):
        edges=ring((30,20),(120,20),(120,140),(60,140),(60,70),(30,70))
        row=layout(edges,[('드레스룸',80,90)])['regions'][0]
        self.assertEqual(row['chosen_mode'],'OBSERVED')
        poly=np.asarray(row['polygon_native']['outer_rings'][0],np.float32)
        self.assertLess(cv2.pointPolygonTest(poly,(40,100),False),0)
        self.assertGreater(cv2.pointPolygonTest(poly,(80,90),False),0)

    def test_three_sides_keep_missing_side_as_inferred(self):
        r=layout(BOX[:3],[('침실1',75,80)])
        row=r['regions'][0]
        self.assertEqual(row['chosen_mode'],'U_ENDS')
        self.assertTrue(any(b['owner']<0 for b in row['boundary_runs']))
        for b in row['boundary_runs']:
            if b['owner']<0:
                bridge=r['closures'][-b['owner']-1]
                self.assertEqual(bridge['kind'],'OPEN_ENDS_OF_U')
                self.assertEqual(bridge['physical_wall_existence'],'UNKNOWN')
        self.assertFalse(r['creates_physical_walls'])

    def test_hole_and_diagonal_survive_without_a_label(self):
        edges=ring((20,20),(140,20),(170,70),(140,160),(20,160))+ring((55,60),(75,60),(75,85),(55,85))
        result=layout(edges,[])
        outer=max(result['unlabelled_regions'],key=lambda r:r['area_native_px2'])
        self.assertEqual(len(outer['polygon_native']['holes']),1)
        self.assertGreater(len(outer['polygon_native']['outer_rings'][0]),4)

    def test_two_labels_and_functional_label_do_not_acquire_exclusive_areas(self):
        r=layout(BOX,[('침실1',55,50),('드레스룸',85,80)])
        for row in r['regions']:
            self.assertIsNone(row['polygon_native'])
            self.assertIn('SHARED_OPEN_PLAN',row['interpretations'])
        self.assertIsNone(layout(BOX,[('주방/식당',70,70)])['regions'][0]['polygon_native'])

    def test_narrow_leak_and_scope_cut_are_withheld(self):
        edges=ring((35,30),(150,30),(150,125),(35,125),(35,75),(8,75),(8,70),(35,70))
        row=layout(edges,[('침실2',85,75)])['regions'][0]
        self.assertEqual(row['alternatives'][0]['reason'],'LONG_NARROW_EXTENSION_REQUIRES_REOBSERVATION')
        row=layout(BOX,[('침실1',60,60)],scope=(45,0,210,190))['regions'][0]
        self.assertIsNone(row['polygon_native'])
        self.assertTrue(row['boundary_runs'])

    def test_layerless_admissible_fragments_and_nonstructural_single_lines(self):
        r=layout(BOX,[('침실1',55,55)],layer='DIM')
        self.assertIsNone(r['regions'][0]['polygon_native'])
        frags=[dict(decision='ADMISSIBLE_GEOMETRIC_HYPOTHESIS',fragment_id=f'frag{i}',
                    boundary_faces_native=[line],raster_support=[1.]) for i,line in enumerate(BOX)]
        r=layout(BOX,[('침실1',55,55)],layer='',fragments=[{'parent_structure_id':'wall','fragments':frags}])
        self.assertEqual(r['regions'][0]['status'],'REGION_PROPOSED')
        self.assertTrue(all(f['parent_structure_id']=='wall' for f in r['faces']))

    def test_four_side_fit_stays_inside_the_native_region(self):
        patch=np.ones((60,80),np.uint8)
        patch[-1,20:38]=0
        ring=inner_rectangle(patch)
        self.assertIsNotNone(ring)
        (left,top),_,(right,bottom),_=ring
        self.assertTrue(patch[top:bottom+1,left:right+1].all())
        patch[0:18,0:22]=0
        self.assertIsNone(inner_rectangle(patch))

    def test_overlap_check_uses_interiors_and_preserves_shared_sides(self):
        def polygon(x):
            return {'bbox_native':[x,20,x+20,40], 'polygon_native':{
                'outer_rings':[[[x,20],[x+20,20],[x+20,40],[x,40]]],'holes':[]}}
        origin=np.array([10,10])
        self.assertFalse(partition_conflicts([polygon(20),polygon(40)],origin,(80,80)))
        self.assertEqual(partition_conflicts([polygon(20),polygon(32)],origin,(80,80)),{0:{1},1:{0}})

    def test_wall_separating_two_seeds_yields_disjoint_regions(self):
        r=layout(BOX+[((70,20),(70,120))],[('침실1',40,55),('팬트리',95,55)])
        a,b=r['regions']
        self.assertLess(a['bbox_native'][2],b['bbox_native'][0])


if __name__=='__main__':
    unittest.main()
