"""Native sample algebra and competing interpretations, independently of Page 4."""
import copy
import unittest

import numpy as np

from backend.reference_domains import derive_reference_domains, describe
from backend.test_reference_space_closure import BOX, layout, ring


def calculate(part, fragments=(), entities=()):
    assessment = {'source_binding':part['source_binding'],'frame_id':part['frame_id'],
                  'structural_parents':[{'fragments':fragments}]}
    return derive_reference_domains(part,assessment,[],entities)


def unpack(layer, shape=(190,210)):
    out = np.zeros(shape,bool)
    for x,y,u,v in layer['rects_native_xyxy']:
        out[y:v,x:u] = True
    return out


class ReferenceDomainTests(unittest.TestCase):
    def test_native_output_preserves_holes_thin_corridors_and_islands(self):
        m = np.zeros((15,25),bool)
        m[1:6,2:7] = m[8:12,15:19] = m[3,6:18] = m[3:9,16] = m[13,23] = True
        m[2:4,3:5] = False
        layer = describe(m,(10,20))
        self.assertEqual((layer['components'],layer['holes']),(2,1))
        np.testing.assert_array_equal(unpack(layer)[20:35,10:35],m)
        self.assertEqual(layer['pixel_count'],m.sum())

    def test_open_shared_function_without_individual_polygons(self):
        p = layout(BOX,[('거실',45,70),('주방/식당',95,70)])
        r = calculate(p)
        self.assertEqual(r['status'],'PARTIAL')
        self.assertEqual(r['open_plan']['status'],'PROPOSED')
        self.assertTrue(all(l['individual_polygon_native'] is None for l in r['open_plan']['labels']))
        m = unpack(r['layers']['U'])
        self.assertTrue(m[30,30])
        self.assertFalse(m[19,30])
        self.assertFalse(r['inventory_complete'])
        self.assertEqual(r['layers']['P']['pixel_count'],0)
        self.assertEqual(r['layers']['R']['pixel_count'],r['layers']['F']['pixel_count'])

    def test_doorway_cannot_prove_openplan_or_become_occupied_area(self):
        p = layout(BOX+[((70,20),(70,58)),((70,82),(70,120))],[('거실',45,70),('주방/식당',95,70)])
        r = calculate(p)
        self.assertEqual(r['open_plan']['status'],'HOLD')
        self.assertFalse(r['open_plan']['creates_connections'])
        self.assertEqual(r['layers']['F']['components'],1)
        self.assertEqual(r['layers']['W']['pixel_count'],0)

    def test_split_at_opening_even_when_envelope_was_observed(self):
        p = layout(BOX,[('거실',45,70),('주방/식당',95,70)])
        e = dict(source_role='Ref',frame_id=p['frame_id'],geometry_properties={'opening_segment_native':[[70,20],[70,120]]})
        r = calculate(p,entities=[e])
        self.assertEqual(r['open_plan']['reason'],'OPENING_OR_DOMAIN_BREAK_BETWEEN_LABELS')
        self.assertEqual(r['envelope']['mode'],'OBSERVED')

    def test_core_overlap_exclusivity_and_partial_inventory(self):
        lines=BOX+[((65,20),(65,120))]
        p=layout(lines,[('침실1',40,75),('PS',90,75),('거실',43,48)])
        # A synthetic prior revision assigns a duplicating room polygon over a
        # shaft, and one partially outside U. Check exact clipping evidence.
        core=p['regions'][1]
        self.assertTrue(core['polygon_native'])
        p['regions'][0].update(polygon_native=copy.deepcopy(core['polygon_native']))
        extra=copy.deepcopy(p['regions'][0]);extra.update(region_id='partial',name='팬트리',
            polygon_native={'outer_rings':[[[10,40],[45,40],[45,65],[10,65]]],'holes':[]})
        p['regions'].append(extra)
        fragment={'decision':'ADMISSIBLE_GEOMETRIC_HYPOTHESIS','fragment_id':'test-wall',
                  'occupancy_polygon_native':[[88,30],[93,30],[93,110],[88,110]]}
        r=calculate(p,[fragment])
        self.assertGreater(r['area_audit']['W_intersect_C'],0)
        self.assertGreater(next(e for e in r['partition_ledger'] if e['region_id']=='partial')['outside_U'],0)
        self.assertEqual(r['partition_ledger'][0]['after_clipping'],0)
        a={k:unpack(m) for k,m in r['layers'].items()}
        np.testing.assert_array_equal(a['F'],a['U'] & ~(a['W']|a['C']))
        np.testing.assert_array_equal(a['R'],a['F'] & ~a['P'])
        self.assertFalse((a['P'] & a['C']).any())
        self.assertEqual(r['open_plan']['reason'],'MISSING_OR_AMBIGUOUS_FUNCTION_LABELS')

    def test_open_or_cropped_plan_does_not_use_drawing_bbox(self):
        for p in [layout(BOX[:2],[('거실',45,40),('침실2',90,80)]),
                  layout(BOX,[('거실',45,40),('침실2',90,80)],scope=(40,10,150,140))]:
            r=calculate(p)
            self.assertEqual(r['status'],'HOLD')
            self.assertFalse(r['layers'])

    def test_competing_units_held(self):
        second=ring((140,20),(195,20),(195,100),(140,100))
        r=calculate(layout(BOX+second,[('거실',45,45),('주방/식당',85,85),('침실1',155,45),('침실2',175,75)]))
        self.assertEqual(r['reason'],'COMPETING_DWELLING_ENVELOPES')

    def test_cross_frame_evidence_and_budget(self):
        p=layout(BOX,[])
        p['status']='NOT_COMPUTED_RESOURCE_BUDGET'
        self.assertEqual(calculate(p)['status'],'HOLD')
        with self.assertRaises(ValueError):
            calculate(p,entities=[dict(source_role='Target',frame_id='another')])
