"""Alignment/coordinate contracts; synthetic cases are geometry tests, not GT."""
from copy import deepcopy
from pathlib import Path
import json
import unittest

import cv2
import numpy as np
from fastapi.testclient import TestClient

from backend.app import create_app
from backend.structural_alignment import align_pages,encode,image_binding,transform,validate_roi
from phase1_pdf import PageImage,render_page

ROOT=Path(__file__).resolve().parents[1]


def fixture(matrix=None,ref_size=(430,350),checks=None):
    matrix=np.array(matrix if matrix is not None else [[1.1,-.1,27],[.1,1.1,18],[0,0,1]],dtype=float)
    fit=[[60,60],[240,60],[60,200],[240,200]]
    check=checks or [[160,80],[280,170],[160,230]]
    target=np.full((260,320),255,np.uint8)
    for x,y in fit+check:
        cv2.line(target,(x-20,y),(x+20,y),0,5);cv2.line(target,(x,y-20),(x,y+20),0,5)
    ref=cv2.warpAffine(target,matrix[:2],ref_size,borderValue=255)
    pages=[PageImage(encode(im),im.shape[1],im.shape[0],4,2,sha) for im,sha in [(ref,'fixture-ref'),(target,'fixture-target')]]
    roi={'schema_version':1,'role':'Target','coordinate_system':'image_native_pixels','image':image_binding(pages[1]),'vertices':[[20,20],[300,20],[300,240],[20,240]]}
    anchors=[]
    for role,points in [('fit',fit),('check',check)]:
        for i,p in enumerate(points):anchors.append({'id':f'{role}-{i}','target':p,'ref':transform([p],matrix)[0].tolist(),'role':role,'confirmed':True,'reason':'Geometry fixture only, not architectural GT.'})
    return pages,roi,anchors,matrix


class GeometryTests(unittest.TestCase):
    def test_rotation_scale_translation_and_unequal_sizes_have_explicit_direction(self):
        pages,roi,anchors,matrix=fixture();result,previews=align_pages(*pages,roi,'manual',anchors)
        self.assertIsNotNone(result['matrix']);np.testing.assert_allclose(result['matrix'],matrix,atol=1e-4)
        np.testing.assert_allclose(result['mapping']['ref_vertices_unclipped'],transform(roi['vertices'],matrix),atol=1e-4)
        np.testing.assert_allclose(transform(transform(roi['vertices'],result['matrix']),result['inverse_matrix']),roi['vertices'],atol=1e-6)
        self.assertEqual(cv2.imdecode(np.frombuffer(previews['overlay'],np.uint8),0).shape,(260,320))
        self.assertEqual(result['target_roi'],roi)

    def test_out_of_ref_and_sparse_support_are_explicit_partial(self):
        pages,roi,anchors,_=fixture([[1,0,-50],[0,1,-20],[0,0,1]],(230,200),[[130,80],[210,150],[120,190]])
        result,_=align_pages(*pages,roi,'manual',anchors)
        self.assertEqual(result['status'],'PARTIAL');self.assertGreater(result['mapping']['outside_ref_pixels'],0)
        self.assertTrue(result['mapping']['outside_ref_vertex_indices']);self.assertFalse(result['mapping']['hull_is_dense_validation'])

    def test_bad_independent_check_rejects_mapping_even_with_perfect_fit(self):
        pages,roi,anchors,_=fixture();anchors[-1]['ref'][0]+=60
        result,_=align_pages(*pages,roi,'manual',anchors)
        self.assertEqual(result['status'],'FAILED');self.assertIsNone(result['matrix']);self.assertIsNone(result['mapping'])
        self.assertGreater(result['residuals']['check_ref_px']['p95'],3)

    def test_blank_images_never_return_identity_fallback(self):
        im=np.full((260,320),255,np.uint8);page=PageImage(encode(im),320,260,4,2,'blank')
        roi={'schema_version':1,'role':'Target','coordinate_system':'image_native_pixels','image':image_binding(page),'vertices':[[20,20],[200,20],[200,200]]}
        result,_=align_pages(page,page,roi);self.assertEqual(result['status'],'FAILED');self.assertIsNone(result['matrix'])

    def test_collinear_fitting_points_fail(self):
        pages,roi,anchors,_=fixture();anchors=[{'id':str(i),'target':[50+i*30,50],'ref':[50+i*30,50],'role':'fit','confirmed':True,'reason':'test'} for i in range(3)]
        result,_=align_pages(*pages,roi,'manual',anchors);self.assertEqual(result['status'],'FAILED');self.assertIsNone(result['matrix'])

    def test_reused_fit_patch_is_not_an_independent_check(self):
        pages,roi,anchors,_=fixture();a=deepcopy(anchors[0]);a.update(id='duplicate-check',role='check');anchors.append(a)
        result,_=align_pages(*pages,roi,'manual',anchors)
        self.assertFalse(result['verification']['fit_check_patches_separated']);self.assertFalse(result['approval_eligible'])

    def test_roi_binding_and_invalid_geometry_fail_without_coercion(self):
        pages,roi,_,_=fixture()
        for field,value in [('page',5),('extraction_zoom',1),('source_sha256','other'),('width',100)]:
            bad=deepcopy(roi);bad['image'][field]=value
            with self.assertRaises(ValueError):validate_roi(bad,pages[1])
        for points in [[],[[0,0],[20,20],[0,20],[20,0]],[[0,0],[400,0],[0,20]],[[True,1],[20,1],[1,20]],[["1",1],[20,1],[1,20]]]:
            bad=deepcopy(roi);bad['vertices']=points
            with self.assertRaises(ValueError):validate_roi(bad,pages[1])


class AlignmentAPITests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.ref=(ROOT/'source_pdfs/자이사업승인도서.pdf').read_bytes();cls.target=(ROOT/'source_pdfs/자이실시도서.pdf').read_bytes()
        cls.roi=json.loads((ROOT/'alignment_inputs/page4_user_roi.json').read_text())

    def setUp(self):
        self.app=create_app();self.client=TestClient(self.app);self.client.__enter__()
        self.ids=[self.client.post('/api/documents',files={'file':(name,data)}).json()['id'] for name,data in [('ref.pdf',self.ref),('target.pdf',self.target)]]
        self.body={'ref':{'document_id':self.ids[0],'page':4,'zoom':2},'target':{'document_id':self.ids[1],'page':4,'zoom':2},'target_roi':deepcopy(self.roi)}

    def tearDown(self):self.client.__exit__(None,None,None)

    def run_alignment(self):
        response=self.client.post('/api/alignments',json=self.body);self.assertEqual(response.status_code,201,response.text);return response.json()

    def test_real_page4_uses_user_roi_and_independent_partial_verification(self):
        result=self.run_alignment()
        self.assertEqual(result['status'],'PARTIAL');self.assertEqual(result['review']['state'],'NOT_APPROVED')
        self.assertTrue(result['approval_eligible']);self.assertGreaterEqual(result['residuals']['check_ref_px']['count'],3)
        self.assertLess(result['residuals']['check_ref_px']['p95'],1);self.assertLess(result['mapping']['sampled_hull_roi_fraction'],.85)
        self.assertEqual(result['target_roi'],self.roi)
        response=self.client.get(f"/api/alignments/{result['run_id']}/preview/overlay")
        self.assertEqual(response.status_code,200);self.assertEqual(cv2.imdecode(np.frombuffer(response.content,np.uint8),0).shape,(1684,2382))

    def test_roi_page_zoom_source_mismatch_and_unknown_document_are_rejected(self):
        self.body['target']['page']=5;self.assertEqual(self.client.post('/api/alignments',json=self.body).status_code,422)
        self.body['target']['page']=4;self.body['target']['zoom']=1;self.assertEqual(self.client.post('/api/alignments',json=self.body).status_code,422)
        self.body['target']['document_id']='missing';self.assertEqual(self.client.post('/api/alignments',json=self.body).status_code,404)

    def test_approval_is_explicit_and_retains_partial_scope(self):
        result=self.run_alignment();url=f"/api/alignments/{result['run_id']}/review"
        review={'decision':'approve','reason':'TEST ONLY simulated review; not user approval.','stable_structures_confirmed':False}
        self.assertEqual(self.client.post(url,json=review).status_code,409)
        review['stable_structures_confirmed']=True;response=self.client.post(url,json=review)
        self.assertEqual(response.status_code,200);self.assertEqual(response.json()['review']['state'],'APPROVED_PARTIAL')
        self.assertEqual(response.json()['status'],'PARTIAL');self.assertEqual(response.json()['review']['scope'],'sampled_support_only')

    def test_failed_alignment_cannot_be_approved(self):
        self.body.update(mode='manual',anchors=[]);result=self.run_alignment();self.assertEqual(result['status'],'FAILED')
        response=self.client.post(f"/api/alignments/{result['run_id']}/review",json={'decision':'approve','stable_structures_confirmed':True,'reason':'TEST only invalid approval'})
        self.assertEqual(response.status_code,409)

    def test_deleted_sources_and_evicted_results_cannot_keep_live_approval(self):
        result=self.run_alignment();self.client.delete('/api/documents/'+self.ids[0])
        self.assertEqual(self.client.get('/api/alignments/'+result['run_id']).status_code,404)
        self.app.state.alignments.clear();self.assertEqual(self.client.get('/api/alignments/'+result['run_id']).status_code,410)

    def test_original_pdf_and_png_paths_are_never_written(self):
        result=self.run_alignment()
        for data,role,stem in [(self.ref,'Ref','자이사업승인도서'),(self.target,'Target','자이실시도서')]:
            original=cv2.imread(str(ROOT/f'Output/{role}/{stem}_page_4_{role.lower()}.png'))
            page=render_page(data,4,2);np.testing.assert_array_equal(original,cv2.imdecode(np.frombuffer(page.png,np.uint8),1))
        self.assertNotIn('changes',result)


if __name__=='__main__':unittest.main(verbosity=2)
