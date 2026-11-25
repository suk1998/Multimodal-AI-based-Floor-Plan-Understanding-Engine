"""Synthetic controls for direct printed-marker matching, not GT accuracy."""
from copy import deepcopy
from dataclasses import replace
import hashlib,unittest
import cv2,numpy as np
from backend.test_symbol_comparison import scene
from backend.structural_comparison import compare_registered,ComparisonScope
from backend.symbol_comparison import enrich_marker_changes
from backend.marker_locus import measure_marker_locus
from integrations.fpue_ontology.detection_contract import is_supported_symbol_change

def input_scene(common_names=0,before='AW/1',after='PW/9'):
    pages,models,a,_=scene(before,after)
    a['input_fingerprint']='SYNTHETIC_MARKER_MATCHING_ONLY'
    for role,g in zip(['Ref','Target'],models):
        g['space_closure']={'regions':[],'faces':[]}
        g['architectural_assessment']={'structural_parents':[],'openings':[],'installed_elements':[]}
        for i in range(common_names):
            g['space_closure']['regions'].append(dict(region_id=role+':room:'+str(i),
                name='room'+str(i),seed_native=[20+15*i,20],observation_id=role+':label:'+str(i),boundary_runs=[]))
    return pages,models,a

def run(data):
    p,g,a=data;r=compare_registered(g,a)
    enrich_marker_changes(r,p,g,a,ComparisonScope(a));return r

def marker_rows(result):return [r for r in result['correspondences'] if r['entity_type']=='CodeMarker' and r['domain']=='ANNOTATION']

class MarkerLocusMatchingTests(unittest.TestCase):
    def test_zero_or_one_common_room_does_not_block_supported_printed_change(self):
        for names in [0,1]:
            with self.subTest(names=names):
                data=input_scene(names);prior=deepcopy(data[1:]);r=run(data);row,=marker_rows(r)
                self.assertTrue(is_supported_symbol_change(row));self.assertEqual((row['before'],row['after']),('AW/1','PW/9'))
                self.assertEqual(len(row['identity_evidence']['common_room_context']),names)
                self.assertTrue(row['identity_evidence']['direct_registered_marker_locus'])
                self.assertEqual(data[1:],prior)
                physical=[r for r in r['correspondences'] if r['domain']=='SPECIFICATION']
                self.assertEqual(len(physical),1);self.assertEqual(physical[0]['judgment'],'UNDETERMINED')

    def test_same_code_is_not_a_change(self):
        row,=marker_rows(run(input_scene(before='AW/1',after='AW/1')))
        self.assertEqual(row['judgment'],'UNCHANGED_WITHIN_SCOPE');self.assertFalse(is_supported_symbol_change(row))

    def test_equal_outlines_at_moved_loci_do_not_establish_identity(self):
        data=input_scene();m=data[1][1]['target_relations']['code_reviews'][0]
        m['bbox_native']=[x+10 if i%2==0 else x for i,x in enumerate(m['bbox_native'])]
        r=run(data);self.assertTrue(marker_rows(r))
        self.assertTrue(all(row['identity_status']!='SUPPORTED' and not is_supported_symbol_change(row) for row in marker_rows(r)))

    def test_missing_number_is_retained_but_not_confirmed(self):
        data=input_scene();data[1][1]['target_relations']['code_reviews'][0]['number']=None
        row,=marker_rows(run(data));self.assertFalse(is_supported_symbol_change(row))
        self.assertEqual(row['state'],'REVIEW');self.assertTrue(row['symbol_decision']['reason_codes'])

    def test_duplicate_equally_good_outlines_are_ambiguous(self):
        data=input_scene();markers=data[1][0]['reference_relations']['code_reviews'];other=deepcopy(markers[0]);other['id']+=':duplicate';markers.append(other)
        r=run(data);self.assertTrue(marker_rows(r))
        self.assertTrue(all(not is_supported_symbol_change(row) for row in marker_rows(r)))
        self.assertTrue(all(row['identity_status']!='SUPPORTED' for row in marker_rows(r)))

    def test_failed_or_unverified_scope_keeps_candidates(self):
        for defect in ['deferred','outside_scope']:
            data=input_scene()
            if defect=='deferred':data[2]['status']='DEFERRED'
            else:data[2]['coverage']['tiles'][0]['target_bbox_native']=[1,1,50,50]
            with self.subTest(defect=defect):
                r=run(data);self.assertTrue(marker_rows(r))
                self.assertTrue(all(not is_supported_symbol_change(row) and row.get('verified_target_bbox_native') is None for row in marker_rows(r)))

    def test_original_circle_perimeter_still_required(self):
        data=input_scene();p,g,a=data
        white=cv2.imencode('.png',np.full((220,220),255,np.uint8))[1].tobytes()
        p[1]=replace(p[1],png=white);sha=hashlib.sha256(white).hexdigest();g[1]['source_binding']['image_sha256']=sha
        for o in g[1]['observations']:o['source_evidence']['image_sha256']=sha
        row,=marker_rows(run(data));self.assertFalse(is_supported_symbol_change(row))
        self.assertIn('MARKER_LOCUS_OR_ORIGINAL_PERIMETER_UNSUPPORTED',row['symbol_decision']['reason_codes'])

    def test_poor_or_unbound_ocr_cannot_use_geometry_to_confirm_text(self):
        for defect in ['low_score','foreign_source']:
            data=input_scene();o=data[1][1]['observations'][0]
            if defect=='low_score':o['confidence']=.4
            else:o['source_evidence']['source_sha256']='another-pdf'
            with self.subTest(defect=defect):
                row,=marker_rows(run(data));self.assertFalse(is_supported_symbol_change(row));self.assertEqual(row['state'],'REVIEW')

    def test_text_context_gate_not_removed_for_wall_or_installation_objects(self):
        from backend.test_structural_comparison import Scene
        s=Scene()
        for g in s.models:g['space_closure']['regions']=[]
        s.wall('Ref','w','parent',40,140,90);s.wall('Target','w','parent',40,140,90)
        rows=s.rows('Wall');self.assertTrue(rows)
        self.assertTrue(all(row['identity_status']!='SUPPORTED' for row in rows))

    def test_invalid_bounds_and_wrong_locus_are_not_supported(self):
        p,g,a=input_scene();scope=ComparisonScope(a)
        for box in [[87,87,87,123],[float('nan'),87,123,123],[100,87,136,123]]:
            self.assertFalse(measure_marker_locus([87,87,123,123],box,scope,1,2)['supported'])

    def test_each_native_frame_is_mapped_instead_of_assumed_equal(self):
        p,g,a=input_scene();matrix=np.array([[1.,0.,9.],[0.,1.,6.],[0.,0.,1.]])
        a['matrix']=matrix.tolist();a['inverse_matrix']=np.linalg.inv(matrix).tolist()
        scope=ComparisonScope(a);target=[87,87,123,123];ref=[96,93,132,129]
        evidence=measure_marker_locus(ref,target,scope,1,2)
        self.assertTrue(evidence['supported']);self.assertEqual(evidence['center_distance_ref_px'],0)
        self.assertEqual((ref,target),([96,93,132,129],[87,87,123,123]))
        self.assertFalse(measure_marker_locus(target,target,scope,1,2)['supported'])

if __name__=='__main__':unittest.main()
