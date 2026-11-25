"""TEST ONLY geometric constraints and real Page 4 frozen counterexample, not GT."""
from copy import deepcopy
import json
from pathlib import Path
import unittest

import cv2
import numpy as np
from fastapi.testclient import TestClient

from backend.app import create_app
from backend.structural_alignment import encode, image_binding, transform
from backend.structural_anchor_alignment import (SEMANTICS, SCOPE, Settings, align_structural_anchors, assess_matrix, eligibility, fit_lines)
from phase1_pdf import PageImage

ROOT = Path(__file__).resolve().parents[1]


def pair(id, ref, target, usage='FIT', parent=None):
    return {'id': id, 'revision': 1, 'parent_structure_id': parent or id, 'boundary_kind': 'OUTER', 'usage': usage,
            'ref_segment': np.asarray(ref).tolist(), 'target_segment': np.asarray(target).tolist(),
            'rationale': 'TEST ONLY geometry, not architectural stability or user approval',
            'endpoint_semantics': SEMANTICS, 'landmark_correspondence': False, 'source': {'kind': 'MANUAL'},
            'review': {'status': 'REVIEWED', 'scope': SCOPE, 'applies_to_revision': 1, 'identity_hypothesis_checked': True,
                       'interval_scope_checked': True, 'reason': 'TEST ONLY geometry, not architectural stability or user approval', 'at': '2026-11-10T00:00:00Z'}}


def fixture():
    angle = np.deg2rad(2); scale = 1.02
    matrix = np.array([[scale*np.cos(angle), -scale*np.sin(angle), 12], [scale*np.sin(angle), scale*np.cos(angle), 8], [0, 0, 1]])
    lines = [[[100, 100], [100, 200]], [[600, 400], [600, 500]], [[300, 100], [450, 100]],
             [[150, 350], [150, 450]], [[250, 480], [450, 480]], [[680, 200], [680, 350]]]
    pairs = []
    images = [np.full((800, 1000), 255, np.uint8), np.full((600, 800), 255, np.uint8)]
    for i, target in enumerate(lines):
        ref = transform(target, matrix); ref = ref[0]+np.array([[.1], [.8]])*(ref[1]-ref[0])
        if i % 2: ref = ref[::-1]
        p = pair(str(i), ref, target, 'FIT' if i < 3 else 'VALIDATION'); pairs.append(p)
        for im, points in zip(images, [p['ref_segment'], p['target_segment']]): cv2.line(im, *[tuple(np.rint(x).astype(int)) for x in points], 0, 2)
    pages = [PageImage(encode(im), im.shape[1], im.shape[0], 4, 2, name) for im, name in zip(images, ['ref-fixture', 'target-fixture'])]
    roi = {'schema_version': 1, 'role': 'Target', 'coordinate_system': 'image_native_pixels', 'image': image_binding(pages[1]), 'vertices': [[50, 50], [730, 50], [730, 550], [50, 550]]}
    file = {'schema_version': 1, 'kind': 'STRUCTURAL_ANCHOR_SET', 'coordinate_system': 'image_native_pixels', 'endpoint_semantics': SEMANTICS, 'landmark_correspondence': False, 'ref_image': image_binding(pages[0]), 'target_image': image_binding(pages[1]), 'pairs': pairs}
    return pages, roi, file, matrix


class LineGeometryTests(unittest.TestCase):
    def test_known_similarity_with_unequal_reversed_analysis_windows(self):
        pages, roi, file, matrix = fixture(); result, previews = align_structural_anchors(*pages, roi, file)
        self.assertEqual(result['status'], 'VERIFIED_SAMPLED', result['reasons'])
        np.testing.assert_allclose(result['matrix'], matrix, atol=1e-9)
        self.assertFalse(result['fit_conditioning']['paired_endpoint_constraints']); self.assertFalse(result['validation_used_for_estimation'])
        self.assertIsNone(result['mapping']); self.assertFalse(result['roi_mapping_confirmed']); self.assertIn('overlay', previews)
        self.assertTrue(all(r['physical_endpoint_change'] == 'NOT_ASSESSED' and r['opening_change'] == 'NOT_ASSESSED' for r in result['rows']))

    def test_overlay_legend_matches_ref_red_and_target_cyan(self):
        pages, roi, file, matrix = fixture()
        ref_gray, target_gray = [cv2.imdecode(np.frombuffer(p.png, np.uint8), 0) for p in pages]
        ref_marker = tuple(np.rint(transform([[400, 300]], matrix)[0]).astype(int))
        cv2.circle(ref_gray, ref_marker, 5, 0, -1); cv2.circle(target_gray, (500, 300), 5, 0, -1)
        pages = [PageImage(encode(gray), p.width, p.height, p.page_number, p.zoom, p.source_sha256) for gray, p in zip([ref_gray, target_gray], pages)]
        result, previews = align_structural_anchors(*pages, roi, file)
        overlay = cv2.imdecode(np.frombuffer(previews['overlay'], np.uint8), 1)
        self.assertEqual(result['status'], 'VERIFIED_SAMPLED')
        # cv2 stores BGR: Ref-only ink must be red; Target-only ink cyan.
        np.testing.assert_array_equal(overlay[300, 400], [0, 0, 255])
        np.testing.assert_array_equal(overlay[300, 500], [255, 255, 0])

    def test_sliding_or_shortening_interval_on_same_line_does_not_create_landmarks(self):
        pages, roi, file, matrix = fixture()
        changed = deepcopy(file['pairs'][:3])
        for p in changed:
            for side in ['ref_segment', 'target_segment']:
                pts = np.asarray(p[side]); p[side] = (pts[0]+np.array([[.25], [.65]])*(pts[1]-pts[0])).tolist()
        estimated, _, error = fit_lines(changed, [1000, 800], [800, 600], Settings())
        self.assertIsNone(error); np.testing.assert_allclose(estimated, matrix, atol=1e-9)

    def test_unreviewed_invalidated_and_stale_revision_are_never_fit(self):
        pages, roi, file, _ = fixture()
        for status in ['UNREVIEWED', 'INVALIDATED', 'REJECTED']:
            for p in file['pairs']: p['review']['status'] = status
            result, _ = align_structural_anchors(*pages, roi, file)
            self.assertEqual(result['status'], 'DEFERRED'); self.assertEqual(result['fit_ids'], []); self.assertIsNone(result['matrix'])
        p = pair('x', [[1, 1], [1, 9]], [[1, 1], [1, 9]]); p['revision'] += 1
        self.assertFalse(eligibility(p)[0])

    def test_hold_exclude_and_incomplete_correspondence_are_not_eligible(self):
        p = pair('x', [[1, 1], [1, 9]], [[1, 1], [1, 9]])
        for usage in ['HOLD', 'EXCLUDE']:
            p['usage'] = usage; self.assertFalse(eligibility(p)[0])
        p['usage'] = 'FIT'; p['ref_segment'] = None; self.assertFalse(eligibility(p)[0])

    def test_parallel_or_concentrated_fitting_intervals_defer(self):
        pages, roi, file, _ = fixture()
        file['pairs'][2]['ref_segment'] = [[300, 100], [300, 200]]; file['pairs'][2]['target_segment'] = [[300, 100], [300, 200]]
        result, _ = align_structural_anchors(*pages, roi, file); self.assertEqual(result['status'], 'DEFERRED'); self.assertIsNone(result['matrix'])
        _, _, file, _ = fixture()
        for p in file['pairs'][:3]:
            for side in ['ref_segment', 'target_segment']: p[side] = (np.array(p[side])*.001+300).tolist()
        result, _ = align_structural_anchors(*pages, roi, file); self.assertEqual(result['status'], 'DEFERRED')

    def test_concurrent_lines_have_unidentifiable_scale(self):
        pairs = [pair(str(i), p, p) for i, p in enumerate([[[200, 10], [200, 400]], [[10, 200], [400, 200]], [[10, 10], [400, 400]]])]
        matrix, diagnostics, why = fit_lines(pairs, [500, 500], [500, 500], Settings())
        self.assertIsNone(matrix); self.assertLess(diagnostics['rank'], 3); self.assertTrue(why)

    def test_validation_disagreement_fails_without_changing_fit(self):
        pages, roi, file, expected = fixture(); file['pairs'][3]['ref_segment'] = (np.array(file['pairs'][3]['ref_segment'])+[40, 0]).tolist()
        result, previews = align_structural_anchors(*pages, roi, file)
        self.assertEqual(result['status'], 'FAILED'); self.assertFalse(result['approval_eligible']); self.assertIsNone(result['matrix']); self.assertIsNone(result['provisional_mapping']); self.assertEqual(previews, {})
        np.testing.assert_allclose(result['rejected_matrix'], expected, atol=1e-9)
        self.assertLess(max(r['normal_max_ref_px'] for r in result['rows'] if r['usage'] == 'FIT'), 1e-8)
        self.assertGreater(next(r for r in result['rows'] if r['id'] == '3')['normal_max_ref_px'], 35)

    def test_shared_parent_case_and_whitespace_defer(self):
        pages, roi, file, _ = fixture(); file['pairs'][3]['parent_structure_id'] = ' 0 '
        result, _ = align_structural_anchors(*pages, roi, file); self.assertEqual(result['status'], 'DEFERRED'); self.assertTrue(result['parent_conflicts'])

    def test_blank_fit_support_cannot_verify(self):
        pages, roi, file, _ = fixture(); pages = [PageImage(encode(np.full((p.height, p.width), 255, np.uint8)), p.width, p.height, p.page_number, p.zoom, p.source_sha256) for p in pages]
        result, _ = align_structural_anchors(*pages, roi, file); self.assertEqual(result['status'], 'DEFERRED'); self.assertIsNone(result['mapping'])

    def test_missing_or_sparse_validation_is_partial(self):
        for count in [3, 4]:
            pages, roi, file, _ = fixture(); file['pairs'] = file['pairs'][:count]
            result, _ = align_structural_anchors(*pages, roi, file); self.assertEqual(result['status'], 'PARTIAL'); self.assertFalse(result['approval_eligible'])

    def test_duplicate_support_is_not_independent_despite_different_parent(self):
        pages, roi, file, _ = fixture(); p = deepcopy(file['pairs'][0]); p.update(id='duplicate', parent_structure_id='another', usage='VALIDATION'); file['pairs'].append(p)
        result, _ = align_structural_anchors(*pages, roi, file); self.assertEqual(result['status'], 'PARTIAL'); self.assertTrue(result['duplicate_geometric_support'])

    def test_boundaries_and_malformed_or_landmark_inputs_rejected(self):
        pages, roi, file, _ = fixture()
        bad = deepcopy(file); bad['target_image']['page'] = 5
        with self.assertRaises(ValueError): align_structural_anchors(*pages, roi, bad)
        bad = deepcopy(file); bad['schema_version'] = True
        with self.assertRaises(ValueError): align_structural_anchors(*pages, roi, bad)
        bad = deepcopy(file); bad['pairs'][0]['source']['kind'] = 'SIFT'
        with self.assertRaises(ValueError): align_structural_anchors(*pages, roi, bad)
        file['pairs'][0]['landmark_correspondence'] = True
        with self.assertRaises(ValueError): align_structural_anchors(*pages, roi, file)

    def test_direction_failure_is_separate_from_normal_tolerance(self):
        pages, roi, file, _ = fixture(); p = file['pairs'][3]; points = np.asarray(p['ref_segment']); center = points.mean(0)
        a = np.deg2rad(.5); rot = np.array([[np.cos(a), -np.sin(a)], [np.sin(a), np.cos(a)]])
        p['ref_segment'] = ((points-center) @ rot.T+center).tolist()
        result, _ = align_structural_anchors(*pages, roi, file, Settings(normal_tolerance_ref_px=30, angle_tolerance_degrees=.1))
        self.assertEqual(result['status'], 'FAILED')
        row = next(r for r in result['rows'] if r['id'] == '3'); self.assertLess(row['normal_max_ref_px'], 30); self.assertGreater(row['angle_degrees'], .1)

    def test_out_of_image_roi_stays_provisional_and_unapprovable(self):
        pages, roi, file, _ = fixture()
        for p in file['pairs']: p['ref_segment'] = (np.array(p['ref_segment'])+[-100, 0]).tolist()
        gray = cv2.imdecode(np.frombuffer(pages[0].png, np.uint8), 0)
        gray = cv2.warpAffine(gray, np.array([[1., 0, -100], [0, 1, 0]]), (1000, 800), borderValue=255)
        p = pages[0]; pages[0] = PageImage(encode(gray), p.width, p.height, p.page_number, p.zoom, p.source_sha256)
        result, _ = align_structural_anchors(*pages, roi, file)
        self.assertEqual(result['status'], 'PARTIAL'); self.assertTrue(result['provisional_mapping']['outside_ref_vertex_indices'])
        self.assertFalse(result['approval_eligible']); self.assertIsNone(result['mapping'])

    def test_frozen_page4_north_absorption_counterexample(self):
        assessment = json.loads((ROOT/'stability_assessment/page4/assessment.json').read_text())
        case = next(r for r in assessment['absorption_audit'] if r['model'] == 'north_changed_envelope_only_similarity')
        self.assertAlmostEqual(case['north_envelope_corner_rmse_px'], .543, places=3)
        self.assertAlmostEqual(case['heldout_normal_max_px'], 35.623, places=3)
        pairs = [pair(c['id'], c['ref_probe'], c['target_probe'], 'VALIDATION', c['parent_structure_hypothesis']) for c in assessment['cases'] if c['id'] in ['S04', 'S05', 'S06']]
        grays = [cv2.imread(str(ROOT/f'Output/{role}/{stem}_page_4_{role.lower()}.png'), 0) for role, stem in [('Ref', '자이사업승인도서'), ('Target', '자이실시도서')]]
        rows, failures = assess_matrix(pairs, np.array(case['matrix_target_to_ref']), grays, Settings())
        self.assertTrue(failures); self.assertAlmostEqual(max(r['normal_max_ref_px'] for r in rows), case['heldout_normal_max_px'], places=7)


class LineAPITests(unittest.TestCase):
    def setUp(self):
        self.pages, self.roi, self.file, _ = fixture(); self.app = create_app(); self.client = TestClient(self.app); self.client.__enter__()
        store = self.app.state.documents
        self.old_page, self.old_get = store.page, store.get
        store.get = lambda id: id
        store.page = lambda doc, page, zoom: self.pages[0 if doc == 'ref' else 1]
        self.ctx = {'version': 1, 'input_token': 'token-1'}
        self.client.put('/api/structural-alignment-contexts/test-context', json=self.ctx)
        self.body = {**self.ctx, 'context_id': 'test-context', 'ref': {'document_id': 'ref', 'page': 4, 'zoom': 2}, 'target': {'document_id': 'target', 'page': 4, 'zoom': 2}, 'target_roi': self.roi, 'anchor_file': self.file, 'execution_context': 'TEST_ONLY'}

    def tearDown(self): self.client.__exit__(None, None, None)

    def run_result(self):
        r = self.client.post('/api/structural-alignments', json=self.body); self.assertEqual(r.status_code, 201, r.text); return r.json()

    def test_only_verified_sample_scope_can_be_reviewed_and_never_maps_roi_finally(self):
        result = self.run_result(); url = '/api/structural-alignments/'+result['run_id']+'/review'
        body = {**self.ctx, 'decision': 'approve', 'reason': 'TEST ONLY review', 'sampled_line_scope_confirmed': False}
        self.assertEqual(self.client.post(url, json=body).status_code, 409)
        body['sampled_line_scope_confirmed'] = True; result = self.client.post(url, json=body).json()
        self.assertEqual(result['review']['state'], 'TEST_APPROVED_SAMPLED'); self.assertFalse(result['roi_mapping_confirmed']); self.assertIsNone(result['mapping'])

    def test_context_update_revokes_result_approval_and_preview(self):
        result = self.run_result(); url = '/api/structural-alignments/'+result['run_id']
        self.client.post(url+'/review', json={**self.ctx, 'decision': 'approve', 'reason': 'TEST ONLY review', 'sampled_line_scope_confirmed': True})
        self.client.put('/api/structural-alignment-contexts/test-context', json={'version': 2, 'input_token': 'token-2'})
        stale = self.client.get(url).json(); self.assertEqual(stale['validity'], 'INVALIDATED'); self.assertIsNone(stale['matrix']); self.assertEqual(stale['review']['state'], 'INVALIDATED')
        self.assertEqual(self.client.get(url+'/preview/overlay').status_code, 404)
        self.assertEqual(self.client.post(url+'/review', json={**self.ctx, 'decision': 'approve', 'reason': 'TEST ONLY review', 'sampled_line_scope_confirmed': True}).status_code, 409)
        self.assertEqual(self.client.post('/api/structural-alignments', json=self.body).status_code, 409)
        self.assertEqual(self.client.put('/api/structural-alignment-contexts/test-context', json=self.ctx).status_code, 409)

    def test_unreviewed_input_is_deferred_and_approval_blocked(self):
        for p in self.file['pairs']: p['review']['status'] = 'UNREVIEWED'
        result = self.run_result(); self.assertEqual(result['status'], 'DEFERRED')
        r = self.client.post('/api/structural-alignments/'+result['run_id']+'/review', json={**self.ctx, 'decision': 'approve', 'reason': 'TEST ONLY review', 'sampled_line_scope_confirmed': True})
        self.assertEqual(r.status_code, 409)

    def test_settings_and_source_mismatch_rejected(self):
        self.body['settings'] = {'normal_tolerance_ref_px': -1}; self.assertEqual(self.client.post('/api/structural-alignments', json=self.body).status_code, 422)
        self.body.pop('settings'); self.file['ref_image']['page'] = 5
        self.assertEqual(self.client.post('/api/structural-alignments', json=self.body).status_code, 422)


if __name__ == '__main__': unittest.main(verbosity=2)
