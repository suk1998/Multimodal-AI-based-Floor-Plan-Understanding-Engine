"""Stage 6 regression guards for topology, identity and source evidence."""
import unittest

import numpy as np

from backend.reference_relations import code_attempts, doorway_sides, relation, subject


class ReferenceRelationsTests(unittest.TestCase):
    def test_type_constraints_reject_code_as_space_and_self_connection(self):
        with self.assertRaises(ValueError):
            relation('run','Connection',subject('text','TextRegion'),subject('room','Space'),['proof'])
        with self.assertRaises(ValueError):
            relation('run','Connection',subject('space','Space'),subject('space','Space'),['proof'])

    def test_code_reading_never_uses_proximity_for_instance_attachment(self):
        text = [{'id':'letters','normalized':'PW','bbox_native':[20,25,29,31]},
                {'id':'digit','normalized':'4','bbox_native':[21,19,28,24]}]
        symbol = {'id':'path','bbox':[15,15,35,35],'center':[25,25]}
        nearby = {'opening_id':'open','decision':'HOLD','opening_segment_native':[[12,40],[38,40]]}
        reviews, links = code_attempts(text,[symbol],[nearby],[0,0,75,75],'run')
        self.assertEqual(reviews[0]['code'],'PW4')
        self.assertEqual(reviews[0]['referent_status'],'HOLD')
        self.assertEqual(len(reviews[0]['nearby_opening_hypotheses']),1)
        self.assertEqual([link['kind'] for link in links],['LabelAssociation'])

    def test_sides_keep_unknown_residual_separate_from_named_room(self):
        bedroom = {'region_id':'bed','polygon_native':{'outer_rings':[[[30,4],[75,4],[75,55],[30,55]]],'holes':[]}}
        residual = np.ones((90,90),np.int32); residual[:,30:] = 0
        sides = doorway_sides([[28,12],[28,40]],[bedroom],18,residual,[0,0,90,90])
        self.assertEqual([side['space_id'] for side in sides],['bed',None])
        self.assertTrue(all(probe.get('residual_component')==1 for probe in sides[1]['probes']))

    def test_two_doors_between_same_pair_have_distinct_identity(self):
        args = ('run','Connection',subject('a','Space'),subject('b','Space'),['source'])
        self.assertNotEqual(relation(*args,via_entity_id='one')['id'],relation(*args,via_entity_id='two')['id'])

    def test_both_sides_stay_tied_to_the_same_geometric_opening(self):
        left = {'region_id':'living','polygon_native':{'outer_rings':[[[10,5],[30,5],[30,60],[10,60]]],'holes':[]}}
        right = {'region_id':'bed','polygon_native':{'outer_rings':[[[32,5],[70,5],[70,60],[32,60]]],'holes':[]}}
        opening = [[31,14],[31,43]]
        clear = np.zeros((80,90),np.int32)
        sides = doorway_sides(opening,[left,right],18,clear,[0,0,90,80])
        self.assertEqual([side['space_id'] for side in sides],['bed','living'])

    def test_shared_function_membership_does_not_create_door_connections(self):
        from backend.reference_relations import shared_membership
        labels = [
            {'name':'거실','seed_native':[48,90],'observation_id':'pdf1'},
            {'name':'주방/식당','seed_native':[75,90],'observation_id':'ocr1'},
        ]
        review = {'id':'local','after':{'status':'PROPOSED','shared_region':{'pixel_count':90},'labels':labels}}
        entities, links = shared_membership(review,'run')
        self.assertEqual([row['type'] for row in entities],['PartitionRegion','FunctionalSpace','FunctionalSpace'])
        self.assertEqual([row['kind'] for row in links],['LabelAssociation','RegionMembership']*2)
        self.assertTrue(all(row['claim_status']=='PROPOSED' for row in links))
