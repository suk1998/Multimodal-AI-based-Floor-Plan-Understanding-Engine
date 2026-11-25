import csv
import hashlib
from io import BytesIO, StringIO
import json
import unittest
from zipfile import ZipFile

import cv2
import numpy as np
from fastapi.testclient import TestClient

from backend.comparison_review import ComparisonReview, FIELDS, export_review
from backend.public_app import COOKIE, create_app
from backend.test_spatial_alignment import fixture
from phase1_pdf import PDFInfo


def saved_fixture(alignment='PARTIAL'):
    pages, models, roi = fixture()
    sources = [{'image': {**roi['image'], 'image_id':model['frame_id'].split(':',1)[1],
                          'source_sha256':page.source_sha256,'width':page.width,'height':page.height},
                'frame_id':model['frame_id']} for page,model in zip(pages,models)]

    def entry(key, domain, judgment, box, fit=False):
        return dict(correspondence_id=key, domain=domain, judgment=judgment,
                    change_types=['MODIFIED'] if judgment=='CHANGED' else [],
                    evidence_refs=['observation-'+key], decision_reason='visible support',
                    alignment_fit_parent=fit, target_ids=[], verified_target_bbox_native=box)

    rows = [entry('wall','STRUCTURAL','CHANGED',[180,198,208,218]),
            entry('marker','ANNOTATION','CHANGED',[250,240,273,257]),
            entry('unknown','STRUCTURAL','UNDETERMINED',None),
            entry('fit','STRUCTURAL','CHANGED',[330,170,340,185],True),
            entry('scope','STRUCTURAL','CHANGED',None)]
    comparison = dict(run_id='test-comparison', input_fingerprint='same-source-and-roi',
                      alignment_status=alignment, status='REVIEW_AVAILABLE', sources=sources,
                      target_roi=roi, source_objects={'Target': [],'Ref': []}, correspondences=rows)
    return pages, comparison


def read_csv(archive, name):
    reader = csv.DictReader(StringIO(archive.read(name).decode('utf-8-sig')))
    rows = list(reader)
    assert reader.fieldnames == list(FIELDS)
    return rows


class ComparisonReviewTests(unittest.TestCase):
    def test_confirmation_rejects_undetermined_fit_and_outside_scope_atomically(self):
        _, comparison = saved_fixture()
        review = ComparisonReview(comparison, [])
        for key in ('missing','unknown','fit','scope'):
            with self.subTest(key=key), self.assertRaises(ValueError):
                review.save({'wall':'CONFIRMED',key:'CONFIRMED'})
        self.assertEqual((review.revision,review.decisions), (0,{}))

    def test_zip_marks_only_confirmed_structure_and_links_file_hashes(self):
        pages, comparison = saved_fixture()
        review = ComparisonReview(comparison, [])
        review.save({'wall':'CONFIRMED','marker':'CONFIRMED','unknown':'DISMISSED'})
        with ZipFile(BytesIO(export_review(review,pages[1]))) as archive:
            manifest = json.loads(archive.read('manifest.json'))
            self.assertEqual(manifest['confirmed_structural_count'],1)
            self.assertTrue(manifest['unresolved_comparison'])
            self.assertEqual(manifest['review_revision'],1)
            for filename,digest in manifest['files'].items():
                self.assertEqual(hashlib.sha256(archive.read(filename)).hexdigest(),digest)
            wall, = read_csv(archive,'change_report.csv')
            self.assertEqual((wall['change_id'],wall['state'],wall['target_page']),('wall','CONFIRMED','4'))
            self.assertEqual([int(wall[k]) for k in ('x1','y1','x2','y2')],[180,198,208,218])
            marker, = read_csv(archive,'annotation_changes.csv')
            self.assertEqual(marker['change_id'],'marker')
            self.assertEqual({v['change_id']:v['state'] for v in read_csv(archive,'review_candidates.csv')},
                             {'unknown':'DISMISSED','fit':'REVIEW','scope':'REVIEW'})
            original = cv2.imdecode(np.frombuffer(pages[1].png,np.uint8),1)
            rendered = cv2.imdecode(np.frombuffer(archive.read('target_changes.png'),np.uint8),1)
            self.assertEqual(rendered.shape,original.shape)
            blue = np.array([220,74,0])
            np.testing.assert_array_equal(rendered[198,190],blue)
            self.assertTrue(np.any(np.all(rendered[177:197,180:217]==blue,axis=-1)))
            np.testing.assert_array_equal(rendered[240:258,250:274],original[240:258,250:274])

    def test_deferred_review_exports_unmarked_original_without_claim_of_zero_changes(self):
        pages, comparison = saved_fixture('DEFERRED')
        comparison['status']='UNDETERMINED'
        review = ComparisonReview(comparison, [])
        with self.assertRaises(ValueError):
            review.save({'wall':'CONFIRMED'})
        review.save({})
        with ZipFile(BytesIO(export_review(review,pages[1]))) as archive:
            manifest = json.loads(archive.read('manifest.json'))
            self.assertTrue(manifest['unresolved_comparison'])
            self.assertEqual(manifest['confirmed_structural_count'],0)
            self.assertEqual(archive.read('target_changes.png'),pages[1].png)
            self.assertEqual(read_csv(archive,'change_report.csv'),[])

    def test_workspace_isolation_revision_conflict_and_restoration(self):
        pages, comparison = saved_fixture()
        app = create_app()
        with TestClient(app) as owner, TestClient(app) as other:
            owner.get('/api/workspace')
            other.get('/api/workspace')
            w = app.state.workspaces.get(owner.cookies[COOKIE])
            store = w.api.state.documents
            documents = []
            for page in pages:
                doc = store.add(page.source_sha256+'.pdf',b'test',PDFInfo(page.source_sha256,4))
                store.cache[(doc.id,4,1)] = page
                documents.append(doc.metadata())
            w.comparisons[comparison['run_id']] = ComparisonReview(comparison,documents)
            url = '/api/comparison-reviews/'+comparison['run_id']
            origin = {'Origin':'http://testserver'}
            data = {'revision':0,'decisions':{'wall':'CONFIRMED'}}
            self.assertEqual(owner.get(url+'/export?revision=0').status_code,409)
            self.assertEqual(other.get(url).status_code,404)
            self.assertEqual(other.put(url,json=data,headers=origin).status_code,404)
            self.assertEqual(owner.put(url,json=data).status_code,403)
            self.assertEqual(owner.put(url,json=data,headers=origin).status_code,200)
            self.assertEqual(owner.put(url,json=data,headers=origin).status_code,409)
            restored = owner.get(url).json()
            self.assertEqual(restored['decisions'],data['decisions'])
            self.assertEqual(restored['documents'],documents)
            self.assertEqual(owner.get('/api/comparison-reviews').json()[0]['run_id'],comparison['run_id'])
            self.assertEqual(other.get('/api/comparison-reviews').json(),[])
            self.assertEqual(other.get(url+'/export?revision=1').status_code,404)
            self.assertEqual(owner.get(url+'/export?revision=0').status_code,409)
            self.assertEqual(owner.get(url+'/export?revision=1').status_code,200)
            self.assertEqual(owner.delete('/api/documents/'+documents[0]['id']+'?preserve_review=1',headers=origin).status_code,204)
            self.assertEqual(owner.get(url).status_code,200)
            self.assertEqual(owner.delete('/api/documents/'+documents[0]['id'],headers=origin).status_code,204)
            self.assertEqual(owner.get(url).status_code,404)
