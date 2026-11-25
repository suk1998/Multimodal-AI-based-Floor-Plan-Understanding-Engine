"""Synthetic boundary-access reconfiguration; no Page 4/GT input to rules."""
from copy import deepcopy
from dataclasses import replace
import csv,hashlib,unittest
from io import StringIO
import cv2,numpy as np
from backend.test_door_correspondence import scene as support_scene
from backend.boundary_aperture_comparison import attach_boundary_aperture_comparison,is_supported_boundary_aperture_change
from backend.comparison_claims import attach_claim_assessment
from backend.comparison_review import ComparisonReview,export_review
from backend.change_policy import is_final_change_kind

def scene(mode='reconfigured',ref_shift=(0,0)):
    pages,geometries,alignment,strokes,_,result=support_scene();vectors=[]
    for i,role in enumerate(['Ref','Target']):
        page=pages[i];g=geometries[i];frame=g['frame_id'];delta=np.array(ref_shift if i==0 else [0,0],float)
        image=cv2.imdecode(np.frombuffer(page.png,np.uint8),0)
        if i==0 and np.any(delta):image=cv2.warpAffine(image,np.c_[np.eye(2),delta],(320,320),borderValue=255)
        # Existing fixture's door pixels do not form band contours. Only these
        # declared synthetic rectangles supply candidate geometry.
        lines=[];number=0
        def contour(box):
            nonlocal number
            number+=1;x,y,u,v=box;points=np.array([[x,y],[u,y],[u,v],[x,v],[x,y]],float)+delta
            for k,(a,b) in enumerate(zip(points,points[1:])):
                cv2.line(image,tuple(np.rint(a).astype(int)),tuple(np.rint(b).astype(int)),0,1)
                axis=int(abs(b[1]-a[1])>abs(b[0]-a[0]));lo,hi=sorted([float(a[axis]),float(b[axis])])
                lines.append(dict(id=f'pdf_path:{number}:{k}:0',axis=axis,lo=lo,hi=hi,normal=float(a[1-axis]),points_native=[a.tolist(),b.tolist()],
                    source_evidence=dict(path_id=f'pdf_path:{number}',layer='A-WAL3' if i==0 else '',stroke_width_native=.96)))
        contour([92,92,208,100]);contour([92,100,100,208])
        old_gap=i==0 or mode=='unchanged'
        new_gap=i==1 and mode!='unchanged'
        if old_gap:contour([100,200,110,208]);contour([160,200,200,208])
        elif mode!='missing_old_counterpart':contour([100,200,208,208])
        if new_gap:contour([200,100,208,125]);contour([200,175,208,200])
        else:contour([200,100,208,208])
        png=cv2.imencode('.png',image)[1].tobytes();pages[i]=replace(page,png=png)
        g['source_binding']['image_sha256']=hashlib.sha256(png).hexdigest();alignment['sources'][i]['image_sha256']=hashlib.sha256(png).hexdigest()
        own_text=frame+':room-label';box=np.array([[130,140],[155,140],[155,150],[130,150]],float)+delta
        g['observations']=[dict(observation_id=own_text,observation_type='TextRegion',frame_id=frame,source_role=role,
            geometry_native={'points_native':box.tolist()},source_evidence={'text':'Storage X'})] if i==0 else []
        ring=(np.array([[100,100],[200,100],[200,200],[100,200]],float)+delta).tolist()
        g['space_closure']['regions']=[dict(region_id=frame+':room',observation_id=own_text,name='Storage X',
            polygon_native={'outer_rings':[ring],'holes':[]},inferred_perimeter_fraction=0.,boundary_runs=[],claim_status='PROPOSED')] if i==0 else []
        vectors.append(dict(source_sha256=page.source_sha256,page=1,extraction_zoom=2,image_size=[320,320],lines=lines))
    if any(ref_shift):
        alignment['matrix']=[[1,0,ref_shift[0]],[0,1,ref_shift[1]],[0,0,1]]
        alignment['inverse_matrix']=[[1,0,-ref_shift[0]],[0,1,-ref_shift[1]],[0,0,1]]
        for pair in alignment['pairs']:
            for key in ['ref_segment','ref_other_face']:pair[key]=(np.array(pair[key])+ref_shift).tolist()
    return pages,geometries,alignment,strokes,vectors,result

def apply(data,mask=None):
    p,g,a,s,v,result=data
    attach_boundary_aperture_comparison(result,p,g,a,s,v,type('Scope',(),{'roi_mask':np.ones((320,320),np.uint8) if mask is None else mask})())
    attach_claim_assessment(result,g)
    return result

class BoundaryApertureTests(unittest.TestCase):
    def test_two_positive_occupancy_changes_survive_missing_target_label_and_reach_exports(self):
        data=scene();before=deepcopy(data[1:3]);result=apply(data);row,=result['correspondences']
        self.assertTrue(is_supported_boundary_aperture_change(row));self.assertTrue(is_final_change_kind(row))
        self.assertEqual(result['status'],'REVIEW_AVAILABLE');self.assertIn('남측',row['change_description']);self.assertIn('동측',row['change_description'])
        self.assertEqual(row['boundary_aperture_evidence']['group']['target_space_identity_status'],'UNKNOWN_NOT_CREATED_FROM_REFERENCE')
        self.assertEqual(row['claim_assessment']['claims'][0]['status'],'SUPPORTED_CHANGE')
        self.assertTrue(all(c['status']=='DEFERRED' for c in row['claim_assessment']['claims'][1:]))
        review=ComparisonReview(result,[]);self.assertEqual(review.state(row),'CONFIRMED')
        records=list(csv.DictReader(StringIO(export_review(review,data[0][1],'csv').decode('utf-8-sig'))))
        self.assertEqual(len(records),1);self.assertEqual(records[0]['physical_change_inferred'],'False')
        self.assertNotEqual(export_review(review,data[0][1],'png'),data[0][1].png)
        self.assertEqual((data[1],data[2]),tuple(before));self.assertEqual(data[1][1]['space_closure']['regions'],[])

    def test_unchanged_gap_does_not_create_access_event(self):
        result=apply(scene('unchanged'));self.assertEqual(result['correspondences'],[])

    def test_no_positive_continuous_band_is_not_deletion(self):
        result=apply(scene('missing_old_counterpart'));self.assertEqual(result['correspondences'],[])

    def test_shifted_native_frames_use_fixed_transform_not_equal_coordinates(self):
        row,=apply(scene(ref_shift=(9,7)))['correspondences']
        self.assertTrue(is_supported_boundary_aperture_change(row))
        np.testing.assert_allclose(np.array(row['ref_bbox_native'])-row['target_observed_bbox_native'],[9,7,9,7])

    def test_verified_support_failure_and_fit_overlap_remain_deferred(self):
        for kind in ['holdout','fit','hull']:
            data=scene();a=data[2]
            if kind=='holdout':a['rows'][0]['passed']=False
            if kind=='hull':a['coverage']['directional_validation_hulls_target']=[[[1,1],[80,1],[80,80],[1,80]]]
            if kind=='fit':a['pairs'].append(dict(id='test-fit',usage='FIT',parent_structure_id='fit-other',
                ref_segment=[[110,200],[160,200]],ref_other_face=[[110,208],[160,208]],
                target_segment=[[110,200],[160,200]],target_other_face=[[110,208],[160,208]]))
            result=apply(data);self.assertTrue(result['correspondences'])
            self.assertFalse(any(is_supported_boundary_aperture_change(r) for r in result['correspondences']),kind)

    def test_occluded_text_is_unknown_not_gap_evidence(self):
        data=scene();g=data[1][1];frame=g['frame_id']
        g['observations']=[dict(observation_id=frame+':occluder',observation_type='TextDetection',geometry_native={'points_native':[[195,120],[215,120],[215,180],[195,180]]})]
        result=apply(data);self.assertFalse(any(is_supported_boundary_aperture_change(r) for r in result['correspondences']))
        self.assertTrue(any('INSUFFICIENT_UNOCCLUDED_SOURCE_SAMPLES' in s['reason_codes'] for s in result['boundary_aperture_comparison']['sites']))

    def test_dimension_layer_rectangles_cannot_supply_wall_band_evidence(self):
        data=scene()
        for v in data[4]:
            for line in v['lines']:line['source_evidence']['layer']='CI_DIM'
        self.assertEqual(apply(data)['correspondences'],[])

    def test_roi_must_cover_both_change_sites(self):
        mask=np.ones((320,320),np.uint8);mask[120:180,195:215]=0
        self.assertFalse(any(is_supported_boundary_aperture_change(r) for r in apply(scene(),mask)['correspondences']))

    def test_multiple_reference_enclosure_contexts_are_not_arbitrarily_resolved(self):
        data=scene();g=data[1][0];r=deepcopy(g['space_closure']['regions'][0]);r['region_id']+='other';g['space_closure']['regions'].append(r)
        result=apply(data);self.assertEqual(len(result['correspondences']),2)
        self.assertTrue(all('COMPETING_REFERENCE_BOUNDARY_CONTEXTS' in r['decision_constraints'] for r in result['correspondences']))
        self.assertFalse(any(is_supported_boundary_aperture_change(r) for r in result['correspondences']))

    def test_no_reference_label_never_invents_a_named_target_space(self):
        data=scene();data[1][0]['observations']=[];result=apply(data)
        self.assertEqual(result['correspondences'],[]);self.assertEqual(len(result['boundary_aperture_comparison']['sites']),2)

    def test_wrong_pdf_frame_and_nonfinite_transform_fail_explicitly(self):
        data=scene();data[4][1]['source_sha256']='wrong'
        with self.assertRaises(ValueError):apply(data)
        data=scene();data[2]['matrix'][0][0]=float('nan');result=apply(data)
        self.assertEqual(result['boundary_aperture_comparison']['reason'],'NONAXIAL_OR_INVALID_TRANSFORM')

    def test_export_cannot_promote_a_claim_missing_any_site_proof(self):
        result=apply(scene());original=result['correspondences'][0]
        for defect in ['missing_site','no_gap_pixels','no_solid_pixels','not_independent','ambiguous_context','physical_identity','source_frame']:
            row=deepcopy(original);proof=row['boundary_aperture_evidence'];site=proof['sites'][0]
            if defect=='missing_site':proof['sites'].pop()
            if defect=='no_gap_pixels':site['face_visibility'][site['gap_source_role']][0]['ink_fraction']=1.
            if defect=='no_solid_pixels':site['face_visibility']['Target'][0]['ink_fraction']=0.
            if defect=='not_independent':site['registration']['core_used_for_fit']=True
            if defect=='ambiguous_context':proof['group']['reason_codes']=['CONFLICT']
            if defect=='physical_identity':proof['group']['physical_door_identity_status']='SUPPORTED'
            if defect=='source_frame':site['source_bindings']['Target']['frame_id']='Ref:foreign'
            self.assertFalse(is_supported_boundary_aperture_change(row),defect)
            self.assertFalse(ComparisonReview(result,[]).confirmable(row),defect)

if __name__=='__main__':unittest.main()
