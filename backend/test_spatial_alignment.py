"""Stage 9 safety/positive controls. Synthetic geometry is never production input."""
from copy import deepcopy
import hashlib
import unittest

import cv2
import numpy as np
from fastapi.testclient import TestClient

from backend.public_app import COOKIE, create_app
from phase1_pdf import PDFInfo
from backend.spatial_alignment import verify
from backend.stable_intervals import allocate
from backend.structural_alignment import encode, image_binding, transform
from phase1_pdf import PageImage


def fixture(bad_holdout=False):
    # Non-square, distinct origins. Label context is independent of line fitting.
    rng = np.random.default_rng(9)
    gray = rng.integers(90,255,size=(420,440),dtype=np.uint8)
    gray = cv2.GaussianBlur(gray,(3,3),.5)
    pages, bundles = [], []
    for role, shift in [('Ref', [9,6]), ('Target',[0,0])]:
        offset = np.array(shift)
        width,height = ((465,440) if role=='Ref' else (440,420))
        image = cv2.warpAffine(gray,np.float32([[1,0,shift[0]],[0,1,shift[1]]]),(width,height),borderValue=255)
        parents = []
        for axis in (0,1):
            for i,normal in enumerate((35,60,130,162,280,333)):
                line = np.array([[20,normal],[395,normal]])
                if axis:
                    line=line[:,::-1]
                line=line+offset
                delta=np.array([4,0]) if axis else np.array([0,4])
                if role=='Ref' and i==3 and bad_holdout:
                    line=line+3*delta
                faces=np.stack([line,line+delta])
                for points in faces:
                    cv2.line(image,tuple(points[0]),tuple(points[1]),0,1)
                pid=f'{role}:{axis}:{i}'
                parents.append({'parent_structure_id':pid,'fragments':[dict(decision='ADMISSIBLE_GEOMETRIC_HYPOTHESIS',
                    boundary_truncated=False,boundary_faces_native=faces.tolist(),fragment_id=pid+':f',thickness_px=4)]})
        page=PageImage(encode(image),width,height,4,1,role+'-synthetic')
        binding=image_binding(page)
        tag=dict(source_role=role,frame_id=role+':'+binding['image_id'])
        binding.update(**tag,core_bbox_native=[0,0,width,height],image_sha256=hashlib.sha256(page.png).hexdigest())
        regions=[dict(name=name,observation_id=role+':text:'+name,seed_native=(np.array(p)+offset).tolist(),text_height=10) for name,p in
                 [('A',[70,75]),('B',[300,85]),('C',[170,330])]]
        bundle=dict(**tag,run_id=role+'-run',source_binding=binding,space_closure=dict(**tag,regions=regions),
                    architectural_assessment=dict(**tag,structural_parents=parents))
        bundle['reference_model' if role=='Ref' else 'target_model']=tag
        pages.append(page)
        bundles.append(bundle)
    roi=dict(schema_version=1,role='Target',coordinate_system='image_native_pixels',image=image_binding(pages[1]),
             vertices=[[154.2,176.1],[223.1,176.1],[223.1,243.2],[154.2,243.2]])
    return pages,bundles,roi


class SpatialAlignmentTests(unittest.TestCase):
    def test_complete_scoped_translation_round_trip(self):
        pages,models,roi=fixture()
        r=verify(pages,models,roi)
        self.assertEqual(r['status'],'VERIFIED_ROI',r['reasons'])
        self.assertEqual(r['fit_model']['dof'],2)
        self.assertEqual(r['coverage']['fraction'],1)
        self.assertFalse(r['validation_used_for_fit'])
        np.testing.assert_allclose(r['mapping']['ref_roi_vertices'],np.array(roi['vertices'])+[9,6])
        np.testing.assert_allclose(transform(r['mapping']['ref_roi_vertices'],r['inverse_matrix']),roi['vertices'])
        for role in ('ref_parent','target_parent'):
            self.assertTrue({p[role] for p in r['pairs'] if p['usage']=='FIT'}.isdisjoint(
                {p[role] for p in r['pairs'] if p['usage']=='VALIDATION'}))

    def test_scale_is_estimated_from_fit_only_and_held_out_separately(self):
        pages,models,roi=fixture()
        scale=1.08
        p=pages[0]
        raw=cv2.imdecode(np.frombuffer(p.png,np.uint8),0)
        image=cv2.resize(raw,None,fx=scale,fy=scale)
        pages[0]=PageImage(encode(image),image.shape[1],image.shape[0],4,1,p.source_sha256)
        m=models[0]
        binding=m['source_binding']
        binding.update(image_binding(pages[0]), image_sha256=hashlib.sha256(pages[0].png).hexdigest(),
                       core_bbox_native=[0,0,image.shape[1],image.shape[0]])
        for source in (m,binding,m['reference_model'],m['space_closure'],m['architectural_assessment']):
            source['frame_id']='Ref:'+binding['image_id']
        for region in m['space_closure']['regions']:
            region['seed_native']=(np.array(region['seed_native'])*scale).tolist()
        for parent in m['architectural_assessment']['structural_parents']:
            f=parent['fragments'][0]
            f['thickness_px']*=scale
            f['boundary_faces_native']=(np.array(f['boundary_faces_native'])*scale).tolist()
        r=verify(pages,models,roi)
        self.assertEqual(r['fit_model']['dof'],4)
        np.testing.assert_allclose(r['diagnostic_matrix'],[[scale,0,9*scale],[0,scale,6*scale],[0,0,1]],atol=1e-8)
        self.assertTrue(all(r['passed'] for r in r['rows']))

    def test_partial_fit_without_independent_parents_has_no_usable_transform(self):
        pages,models,roi=fixture()
        for m in models:
            assessment=m['architectural_assessment']
            assessment['structural_parents']=[p for i,p in enumerate(assessment['structural_parents']) if i in (0,1,4,6,7,10)]
        r=verify(pages,models,roi)
        self.assertEqual(r['status'],'PARTIAL')
        self.assertIsNotNone(r['diagnostic_matrix'])
        self.assertIsNone(r['matrix'])
        self.assertIsNone(r['mapping'])

    def test_patch_with_no_local_evidence_and_extrapolated_area_withheld(self):
        pages,models,roi=fixture()
        roi['vertices']=[[3,5],[208,5],[208,209],[3,209]]
        r=verify(pages,models,roi)
        self.assertEqual(r['status'],'PARTIAL')
        self.assertGreater(r['coverage']['fraction'],0)
        self.assertLess(r['coverage']['fraction'],1)
        self.assertIsNone(r['mapping']['ref_roi_vertices'])
        self.assertFalse(r['roi_mapping_confirmed'])
        for i in r['mapping']['supported_tile_indices']:
            self.assertEqual(r['coverage']['tiles'][i]['status'],'SUPPORTED')

    def test_shifted_independent_wall_invalidates_even_with_exact_fit(self):
        pages,models,roi=fixture(True)
        r=verify(pages,models,roi)
        self.assertEqual(r['status'],'FAILED',r['reasons'])
        self.assertLess(max(row['normal_max_ref_px'] for row in r['rows'] if row['usage']=='FIT'),1e-8)
        self.assertGreater(max(row['normal_max_ref_px'] for row in r['rows'] if row['usage']=='VALIDATION'),10)
        self.assertIsNone(r['matrix'])
        self.assertIsNone(r['mapping'])

    def test_untextured_roi_cannot_inherit_verified_lines(self):
        pages,models,roi=fixture()
        for i,p in enumerate(pages):
            gray=cv2.imdecode(np.frombuffer(p.png,np.uint8),0)
            # Keep exterior FIT and held-out witnesses untouched; remove texture in one tile interior.
            x=192+(9 if i==0 else 0)
            y=192+(6 if i==0 else 0)
            gray[y:y+32,x:x+32]=255
            pages[i]=PageImage(encode(gray),p.width,p.height,p.page_number,p.zoom,p.source_sha256)
            models[i]['source_binding']['image_sha256']=hashlib.sha256(pages[i].png).hexdigest()
        roi['vertices']=[[195,195],[216,195],[216,216],[195,216]]
        r=verify(pages,models,roi)
        self.assertEqual(r['status'],'PARTIAL')
        self.assertEqual(r['coverage']['validated_pixels'],0)
        self.assertEqual(r['coverage']['tiles'][0]['status'],'INSUFFICIENT_2D_TEXTURE')
        self.assertIsNone(r['mapping'])

    def test_role_render_and_roi_frame_mismatch(self):
        pages,models,roi=fixture()
        with self.assertRaises(ValueError):
            verify(pages,list(reversed(models)),roi)
        models[0]['source_binding']['image_sha256']='wrong'
        with self.assertRaises(ValueError):
            verify(pages,models,roi)
        pages,models,roi=fixture()
        roi['image']=image_binding(pages[0])
        with self.assertRaises(ValueError):
            verify(pages,models,roi)

    def test_shared_parent_or_collinear_fragments_never_split(self):
        pairs=verify(*fixture())['pairs']
        a,b,c=deepcopy(pairs[0]),deepcopy(pairs[1]),deepcopy(pairs[2])
        a['ref_parent']=b['ref_parent']
        c['target_segment']=b['target_segment']
        c['target_other_face']=b['target_other_face']
        allocate([a,b,c],[1,1])
        self.assertEqual(len({p['parent_structure_id'] for p in (a,b,c)}),1)

    def test_ambiguous_identity_not_promoted(self):
        pages,models,roi=fixture()
        parents=models[0]['architectural_assessment']['structural_parents']
        copy=deepcopy(parents[0])
        copy['fragments'][0]['fragment_id']+=':duplicate'
        parents.append(copy)
        r=verify(pages,models,roi)
        self.assertTrue(any(h['reason']=='AMBIGUOUS' for h in r['search']['held']))

    def test_no_label_context_or_single_orientation_deferred(self):
        pages,models,roi=fixture()
        models[0]['space_closure']['regions']=[]
        r=verify(pages,models,roi)
        self.assertEqual(r['status'],'DEFERRED')
        self.assertIsNone(r['diagnostic_matrix'])
        pages,models,roi=fixture()
        for m in models:
            m['architectural_assessment']['structural_parents']=m['architectural_assessment']['structural_parents'][:6]
        self.assertEqual(verify(pages,models,roi)['status'],'DEFERRED')

    def test_workspace_scoped_api_binds_both_jobs_and_current_frame(self):
        app=create_app()
        pages,models,roi=fixture()
        with TestClient(app) as client, TestClient(app) as other:
            for c in (client,other):
                c.get('/api/workspace')
            w=app.state.workspaces.get(client.cookies[COOKIE])
            w.observation_owner='synthetic-owner'
            store=w.api.state.documents
            body={'target_roi':roi}
            for page,g in zip(pages,models):
                role=g['source_role']
                doc=store.add(role+'.pdf',b'test-only',PDFInfo(page.source_sha256,4))
                store.cache[(doc.id,4,1)]=page
                b=g['source_binding']
                b['document_id']=doc.id
                identity=g['run_id']
                app.state.observation_jobs.jobs[identity]=dict(owner=w.observation_owner,run_id=identity,
                    document_id=doc.id,source_sha256=page.source_sha256,binding=b,status='PARTIAL',
                    created_at=1,result=g,failure=None,job_kind='geometry')
                body[role]=dict(geometry_run_id=identity,document_id=doc.id,frame_id=b['frame_id'])
            def post(c,payload):
                return c.post('/api/spatial-alignments',headers={'Origin':'http://testserver'},json=payload)
            self.assertEqual(post(other,body).status_code,404)
            self.assertEqual(post(client,body).json()['status'],'VERIFIED_ROI')
            wrong=deepcopy(body)
            wrong['Ref']=body['Target']
            self.assertEqual(post(client,wrong).status_code,422)
            wrong=deepcopy(body)
            wrong['Target']['frame_id']+=':old'
            self.assertEqual(post(client,wrong).status_code,422)

    def test_public_endpoint_rejects_unowned_jobs_and_client_matrix(self):
        app=create_app()
        with TestClient(app) as client:
            origin={'Origin':'http://testserver'}
            body={'Ref':{'geometry_run_id':'no-job'},'Target':{},'target_roi':{}}
            self.assertEqual(client.post('/api/spatial-alignments',headers=origin,json=body).status_code,409)
            client.get('/api/workspace')
            self.assertEqual(client.post('/api/spatial-alignments',headers=origin,json=body).status_code,404)
            body['matrix']=[]
            self.assertEqual(client.post('/api/spatial-alignments',headers=origin,json=body).status_code,422)
