"""Synthetic topology and raster attribution regressions; no GT-specific rules."""
from copy import deepcopy
from dataclasses import replace
import hashlib,unittest
import cv2,numpy as np
from backend.boundary_aperture_comparison import Settings,boundary_side,face_support,valid_gap_face_measurement,is_supported_boundary_aperture_change
from backend.comparison_review import ComparisonReview,export_review
from backend.boundary_contours import segmented_bands,corner_gaps
from backend.test_boundary_aperture_comparison import scene,apply

def contour_input(missing_cap=False,layer=''):
    gray=np.full((100,100),255,np.uint8);points=[[10,40],[90,40],[90,46],[10,46],[10,40]];lines=[]
    for i,(a,b) in enumerate(zip(points,points[1:])):
        cv2.line(gray,tuple(a),tuple(b),0,1)
        if missing_cap and i==1:continue
        axis=int(a[1]!=b[1]);lo,hi=sorted([a[axis],b[axis]])
        lines.append(dict(id=f'edge:{i}',axis=axis,normal=a[1-axis],lo=lo,hi=hi,points_native=[a,b],
            source_evidence=dict(path_id=f'independent-path:{i}',layer=layer,stroke_width_native=1.2)))
    return {'lines':lines},gray

def corner_bands():
    return [dict(id='X:axial',source_role='X',axis=1,lo=10,hi=40,n0=50,n1=56,source_refs=['axial:faces-and-caps']),
            dict(id='X:cross',source_role='X',axis=0,lo=30,hi=70,n0=90,n1=96,source_refs=['cross:faces-and-caps'])]

def thin_face_input():
    gray=np.full((100,100),255,np.uint8);gray[47,10:91]=135
    vector={'lines':[dict(id='thin:observed',axis=0,normal=47.44,lo=10,hi=90,
        source_evidence=dict(path_id='thin:path',stroke_width_native=.48))]}
    return gray,vector

def corner_scene():
    data=scene();pages,gs,alignment,_,vectors,_=data
    for i,role in enumerate(('Ref','Target')):
        gray=cv2.imdecode(np.frombuffer(pages[i].png,np.uint8),0);gray[85:215,85:215]=255;lines=[]
        boxes=[[92,92,208,100],[92,100,100,208]]
        boxes+=([[100,200,145,208],[200,100,208,208]] if i==0 else
                [[100,200,208,208],[200,100,208,145]])
        for n,(x,y,u,v) in enumerate(boxes):
            points=[[x,y],[u,y],[u,v],[x,v],[x,y]]
            for k,(a,b) in enumerate(zip(points,points[1:])):
                cv2.line(gray,tuple(a),tuple(b),0,1);axis=int(a[1]!=b[1]);lo,hi=sorted([a[axis],b[axis]])
                lines.append(dict(id=f'edge:{n}:{k}',axis=axis,normal=a[1-axis],lo=lo,hi=hi,points_native=[a,b],
                    source_evidence=dict(path_id=f'separate:{n}:{k}',layer='',stroke_width_native=1.2)))
        if i==1:
            gray[150:196,209]=135
            lines.append(dict(id='adjacent:thin',axis=1,normal=209.44,lo=145,hi=200,points_native=[[209.44,145],[209.44,200]],
                source_evidence=dict(path_id='adjacent:thin',layer='',stroke_width_native=.48)))
        png=cv2.imencode('.png',gray)[1].tobytes();pages[i]=replace(pages[i],png=png)
        gs[i]['source_binding']['image_sha256']=hashlib.sha256(png).hexdigest()
        alignment['sources'][i]['image_sha256']=hashlib.sha256(png).hexdigest();vectors[i]['lines']=lines
    return data

class SegmentedContourTests(unittest.TestCase):
    def test_corner_access_with_adjacent_thin_stroke_reaches_safe_CSV_and_PNG(self):
        data=corner_scene();before=deepcopy(data[1:3]);result=apply(data);row,=result['correspondences']
        self.assertTrue(is_supported_boundary_aperture_change(row))
        self.assertEqual(len(result['boundary_aperture_comparison']['sites']),2)
        self.assertTrue(all(s['gap']['flank_topology']=='ORTHOGONAL_TERMINATION' for s in result['boundary_aperture_comparison']['sites']))
        self.assertTrue(any(v.get('thinner_stroke_correction') for s in result['boundary_aperture_comparison']['sites'] for v in s['face_visibility']['Target']))
        review=ComparisonReview(result,[]);self.assertIn(b'BOUNDARY_APERTURE_RECONFIGURATION',export_review(review,data[0][1],'csv'))
        self.assertNotEqual(export_review(review,data[0][1],'png'),data[0][1].png)
        self.assertEqual(tuple(data[1:3]),tuple(before));self.assertFalse(row['physical_change_inferred'])

    def test_corner_event_cannot_be_confirmed_after_stroke_attribution_is_lost(self):
        result=apply(corner_scene());row=result['correspondences'][0]
        changed=next(v for s in row['boundary_aperture_evidence']['sites'] for v in s['face_visibility']['Target'] if 'thinner_stroke_correction' in v)
        changed['thinner_stroke_correction']['source_lines']=[]
        self.assertFalse(is_supported_boundary_aperture_change(row));self.assertFalse(ComparisonReview(result,[]).confirmable(row))

    def test_independent_paths_form_contour_only_with_both_positive_caps(self):
        vector,gray=contour_input();bands=segmented_bands(vector,gray,2,'X',Settings())
        self.assertEqual(len(bands),1);self.assertEqual(len(bands[0]['path_ids']),4)
        self.assertEqual(len(bands[0]['cap_source_refs']),2)
        vector,gray=contour_input(missing_cap=True)
        self.assertEqual(segmented_bands(vector,gray,2,'X',Settings()),[])

    def test_known_dimension_layer_or_invisible_contour_cannot_be_wall_support(self):
        vector,gray=contour_input(layer='CI_DIM')
        self.assertEqual(segmented_bands(vector,gray,2,'X',Settings()),[])
        vector,gray=contour_input();gray[:]=255
        self.assertEqual(segmented_bands(vector,gray,2,'X',Settings()),[])

    def test_segmented_input_reaches_existing_change_and_claim_paths(self):
        data=scene()
        for vector in data[4]:
            for i,line in enumerate(vector['lines']):line['source_evidence']['path_id']+=':separate:'+str(i)
        row,=apply(data)['correspondences']
        self.assertEqual(row['claim_assessment']['claims'][0]['status'],'SUPPORTED_CHANGE')
        self.assertFalse(row['physical_change_inferred'])

    def test_corner_termination_requires_complete_positive_transverse_band(self):
        bands=corner_bands();gaps=corner_gaps(bands,1,Settings(),[])
        self.assertEqual(len(gaps),1);self.assertEqual(gaps[0]['bbox_native'],[50,40,56,90])
        self.assertEqual(gaps[0]['flank_topology'],'ORTHOGONAL_TERMINATION')
        bands[1]['hi']=53
        self.assertEqual(corner_gaps(bands,1,Settings(),[]),[])

    def test_corner_gap_never_jumps_over_parallel_or_transverse_contours(self):
        for axis in (0,1):
            bands=corner_bands()
            blocker=dict(bands[axis],id='X:blocker')
            if axis==0:blocker.update(axis=0,lo=30,hi=70,n0=60,n1=66)
            else:blocker.update(axis=1,lo=60,hi=70,n0=50,n1=56)
            bands.append(blocker)
            self.assertFalse(any(g['bbox_native']==[50,40,56,90] for g in corner_gaps(bands,1,Settings(),[])))

class FaceAttributionTests(unittest.TestCase):
    def test_adjacent_thinner_stroke_is_separated_without_deleting_raw_measurement(self):
        gray,vector=thin_face_input();before=gray.copy();v=deepcopy(vector)
        result=face_support(gray,[10,40,90,46],0,[],vector=vector,expected_width=1.2)[1]
        self.assertEqual(result['raw_strip_ink_fraction'],1.);self.assertEqual(result['ink_fraction'],0.)
        self.assertTrue(valid_gap_face_measurement(result));np.testing.assert_array_equal(gray,before);self.assertEqual(vector,v)

    def test_central_black_wall_is_never_removed_by_adjacent_line_explanation(self):
        gray,vector=thin_face_input();gray[46,10:91]=0
        result=face_support(gray,[10,40,90,46],0,[],vector=vector,expected_width=1.2)[1]
        self.assertEqual(result['ink_fraction'],1.);self.assertNotIn('thinner_stroke_correction',result)

    def test_no_vector_or_unexplained_pixel_or_equal_width_line_remains_unresolved(self):
        for mode in ('no_vector','unexplained','equal_width'):
            gray,vector=thin_face_input()
            if mode=='no_vector':vector=None
            if mode=='unexplained':gray[45,54]=0
            if mode=='equal_width':vector['lines'][0]['source_evidence']['stroke_width_native']=1.2
            result=face_support(gray,[10,40,90,46],0,[],vector=vector,expected_width=1.2)[1]
            self.assertEqual(result['ink_fraction'],1.,mode);self.assertNotIn('thinner_stroke_correction',result)

    def test_insufficient_unoccluded_samples_are_not_recovered_as_visibility(self):
        gray,vector=thin_face_input()
        result=face_support(gray,[10,40,90,46],0,[[5,44,95,49]],vector=vector,expected_width=1.2)[1]
        self.assertEqual(result['unoccluded_samples'],0);self.assertIsNone(result['ink_fraction'])

    def test_export_proof_requires_explicit_thin_stroke_explanation(self):
        gray,vector=thin_face_input();row=face_support(gray,[10,40,90,46],0,[],vector=vector,expected_width=1.2)[1]
        for mode in ('missing_source','central_black','no_raw_measurement','same_width'):
            bad=deepcopy(row)
            if mode=='missing_source':bad['thinner_stroke_correction']['source_lines']=[]
            if mode=='central_black':bad['thinner_stroke_correction']['central_ink_fraction']=1.
            if mode=='no_raw_measurement':del bad['raw_strip_ink_fraction']
            if mode=='same_width':bad['thinner_stroke_correction']['source_lines'][0]['source_evidence']['stroke_width_native']=1.2
            self.assertFalse(valid_gap_face_measurement(bad),mode)

class PolygonBoundaryTests(unittest.TestCase):
    def test_concave_ring_uses_actual_edges_and_handles_reverse_winding(self):
        ring=[[100,100],[200,100],[200,150],[160,150],[160,200],[100,200]]
        for points in (ring,ring[::-1],ring+[ring[0]]):
            region={'polygon_native':{'outer_rings':[points]}}
            self.assertEqual(boundary_side(region,{'axis':1,'Ref_bbox_native':[200,110,206,140]},1),'EAST')
            self.assertEqual(boundary_side(region,{'axis':0,'Ref_bbox_native':[110,200,150,206]},1),'SOUTH')
            self.assertIsNone(boundary_side(region,{'axis':1,'Ref_bbox_native':[200,160,206,190]},1))

    def test_unrelated_diagonal_edge_does_not_remove_valid_orthogonal_boundary(self):
        region={'polygon_native':{'outer_rings':[[[130,100],[130,140],[100,141],[100,200],[200,200],[200,100]]]}}
        self.assertEqual(boundary_side(region,{'axis':1,'Ref_bbox_native':[200,150,206,190]},1),'EAST')

    def test_non_simple_or_competing_outer_rings_are_not_associated(self):
        for rings in ([[[100,100],[200,200],[200,100],[100,200]]],
                      [[[100,100],[200,100],[200,200],[100,200]]]*2):
            self.assertIsNone(boundary_side({'polygon_native':{'outer_rings':rings}},
                {'axis':1,'Ref_bbox_native':[200,130,206,160]},1))

if __name__=='__main__':unittest.main()
