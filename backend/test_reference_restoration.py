"""Synthetic geometry/lineage tests; not actual Page 4 correctness metrics."""
import copy
from dataclasses import replace
import json
import unittest
from unittest.mock import patch

import numpy as np
import pymupdf

from phase1_pdf import render_page
from backend.reference_restoration import (RestorationSettings, restore_reference, propose_region,
    interval_union, interval_gaps, layer_role, reobserve, gap_assessments, local_relation_attempts)
from backend.reference_ontology_adapter import build_region_graphs


def fixture(annotation=False):
    document = pymupdf.open()
    page = document.new_page(width=300, height=300)
    walls = document.add_ocg('A-WAL1')
    for a, b in [((60, 60), (240, 60)), ((60, 60), (60, 240)), ((60, 240), (240, 240))]:
        page.draw_line(a, b, width=2, oc=walls)
    if annotation:
        hidden = document.add_ocg('A-HID')
        page.draw_rect(pymupdf.Rect(100, 100, 200, 200), width=1, oc=hidden)
    pdf = document.tobytes()
    document.close()
    image = render_page(pdf, 1, 1)
    import hashlib
    binding = {'source_role': 'Ref', 'source_sha256': image.source_sha256, 'image_sha256': hashlib.sha256(image.png).hexdigest(),
        'frame_id': 'Ref:SYNTHETIC:'+image.source_sha256, 'page': 1, 'extraction_zoom': 1,
        'width': image.width, 'height': image.height, 'origin': 'SYNTHETIC_CONTRACT_TEST'}
    ocr = [{'id': 'synthetic-text', 'frame_id': binding['frame_id'], 'text': '침실1',
        'polygon_native': [[142, 142], [158, 142], [158, 158], [142, 158]], 'recognition_score': None}]
    return pdf, image, binding, ocr


def u_lines():
    return [{'id': name, 'axis': axis, 'normal': normal, 'start': start, 'end': end, 'layers': ['A-WAL1']}
        for name, axis, normal, start, end in [('left', 'V', 20., 20., 80.), ('top', 'H', 20., 20., 80.), ('bottom', 'H', 80., 20., 80.)]]


class FragmentRestorationTests(unittest.TestCase):
    def test_three_observed_sides_propose_fourth_side_without_a_door(self):
        result = propose_region(u_lines(), [50, 50], 10)
        self.assertEqual(result['status'], 'REGION_PROPOSED')
        self.assertEqual(result['bbox_native'], [20., 20., 80., 80.])
        right = next(edge for edge in result['edges'] if edge['side'] == 'right')
        self.assertEqual(right['observed_fraction'], 0)
        self.assertEqual(right['inferred_intervals'], [[20., 80.]])

    def test_absent_geometry_does_not_expand_the_label_into_a_room(self):
        result = propose_region([], [50, 50], 10)
        self.assertEqual(result['status'], 'PARTIAL_BOUNDARY')
        self.assertIsNone(result['polygon_native'])

    def test_a_nearer_parallel_wall_face_is_preferred(self):
        lines = u_lines()+[{'id': 'inner-face', 'axis': 'V', 'normal': 25., 'start': 20., 'end': 80., 'layers': ['A-FIN1']}]
        result = propose_region(lines, [50, 50], 10)
        self.assertEqual(result['bbox_native'][0], 25.)
        self.assertFalse(result['nearer_parallel_face_ids'])

    def test_multiple_space_labels_do_not_become_one_confirmed_room(self):
        result = propose_region(u_lines(), [50, 50], 10, [{'id': 'other', 'seed': [70, 60]}])
        self.assertEqual(result['status'], 'UNRESOLVED')
        self.assertIn('other', result['other_label_ids_inside'])

    def test_real_pdf_reobservation_rejects_hidden_annotation_and_corrects_polygon(self):
        pdf, image, binding, ocr = fixture(annotation=True)
        before = copy.deepcopy((binding, ocr))
        result, assets = restore_reference(pdf, image.png, binding, ocr)
        case = result['cases'][0]
        self.assertEqual(case['after']['status'], 'REGION_PROPOSED')
        self.assertTrue(np.allclose(case['after']['bbox_native'], [60, 60, 240, 240]))
        self.assertNotEqual(case['before']['bbox_native'], case['after']['bbox_native'])
        self.assertTrue(any(row['reason'] == 'ANNOTATION_OR_FIXTURE_HINT' for row in case['reobservation']['rejected_lines']))
        self.assertTrue(case['reobservation']['PDF_reread'])
        self.assertTrue(assets)
        self.assertEqual((binding, ocr), before)
        self.assertEqual(case['claim_status'], 'PROPOSED')
        self.assertFalse(case['full_space_extent_verified'])

    def test_rerender_coordinates_map_back_to_native_frame(self):
        pdf, image, binding, ocr = fixture()
        data, png = reobserve(pdf, binding, [13, 17, 280, 290], 16, 'synthetic-request')
        matrix = np.asarray(data['rerender_to_native'])
        point = np.array([60., 60., 1.])
        local = np.linalg.inv(matrix)@point
        self.assertTrue(np.allclose(matrix@local, point))
        self.assertEqual(data['rerender_zoom'], 2.)
        self.assertTrue(png)

    def test_pdf_or_native_image_replacement_is_rejected(self):
        pdf, image, binding, ocr = fixture()
        for bad_pdf, bad_png in [(pdf+b' ', image.png), (pdf, b'wrong')]:
            with self.assertRaisesRegex(ValueError, 'SHA mismatch'):
                restore_reference(bad_pdf, bad_png, binding, ocr)

    def test_target_and_mixed_ocr_frame_are_rejected(self):
        pdf, image, binding, ocr = fixture()
        with self.assertRaisesRegex(ValueError, 'Reference-only'):
            restore_reference(pdf, image.png, {**binding, 'source_role': 'Target'}, ocr)
        ocr[0]['frame_id'] = 'Target:OTHER'
        with self.assertRaisesRegex(ValueError, 'OCR input frame'):
            restore_reference(pdf, image.png, binding, ocr)

    def test_reobservation_budget_does_not_report_unexecuted_corrections(self):
        pdf, image, binding, ocr = fixture()
        with patch('backend.reference_restoration.reobserve') as observe:
            result, assets = restore_reference(pdf, image.png, binding, ocr,
                replace(RestorationSettings(), maximum_reobservations=0))
        observe.assert_not_called()
        self.assertEqual(result['cases'][0]['reobservation_status'], 'BUDGET_EXCEEDED')
        self.assertFalse(result['reobservation_requests'])
        self.assertFalse(assets)

    def test_layer_hints_distinguish_hidden_and_surface_patterns_from_walls(self):
        self.assertEqual(layer_role(['X|A-HID']), 'ANNOTATION_OR_FIXTURE_HINT')
        self.assertEqual(layer_role(['X|A-FIN2']), 'SURFACE_PATTERN_HINT')
        self.assertEqual(layer_role(['X|A-WAL1']), 'STRUCTURAL_BOUNDARY_HINT')
        self.assertEqual(layer_role(['X|A-DOR']), 'OPENING_SYMBOL_HINT')

    def test_tiny_line_gap_does_not_become_a_door_from_a_nearby_arc(self):
        candidate = {'edges': [{'side': 'left', 'axis': 'V', 'normal': 20, 'inferred_intervals': [[30, 34], [40, 100]]}]}
        arc = {'id': 'arc', 'layer': 'A-DOR', 'center': [20, 40], 'radius': 60, 'angle_degrees': 90, 'polyline': [[20, 100], [80, 40]]}
        gaps = gap_assessments(candidate, {'arcs': [arc]}, 10)
        self.assertEqual(gaps[0]['interpretation'], 'UNKNOWN')
        self.assertEqual(gaps[1]['interpretation'], 'DOOR_CANDIDATE')

    def test_shared_wall_alone_does_not_prove_a_connection(self):
        def case(identity, side, gaps):
            return {'id': identity, 'after': {'status': 'REGION_PROPOSED', 'edges': [
                {'side': side, 'axis': 'V', 'normal': 100., 'start': 0., 'end': 100., 'inferred_intervals': gaps}]}}
        result = local_relation_attempts([case('A', 'right', [[30, 70]]), case('B', 'left', [])], 10)
        self.assertEqual(result[0]['kind'], 'Adjacency')
        result = local_relation_attempts([case('A', 'right', [[30, 70]]), case('B', 'left', [[30, 70]])], 10)
        self.assertEqual(result[0]['kind'], 'Connection')
        self.assertEqual(result[0]['existence'], 'UNKNOWN')
        self.assertFalse(result[0]['physical_crossing_path_verified'])

    def test_adapter_keeps_revisions_inferred_edges_and_stale_claims(self):
        pdf, image, binding, ocr = fixture(annotation=True)
        result, assets = restore_reference(pdf, image.png, binding, ocr)
        before, after = build_region_graphs(result)
        self.assertEqual(after['source_binding']['origin'], 'SYNTHETIC_CONTRACT_TEST')
        self.assertTrue(any(e['expected_type'] == 'ClosureBoundary' for e in after['entities']))
        self.assertFalse(any(e['expected_type'] == 'Wall' for e in after['entities']))
        self.assertTrue(any(c['status'] == 'STALE' for c in after['claims']))
        self.assertFalse(any(c['status'] == 'STALE' for c in before['claims']))
        self.assertEqual(before['observations'], after['observations'][:len(before['observations'])])
        self.assertFalse(any(c['status'] == 'VERIFIED' for c in after['claims']))

    def test_invalid_work_budget_is_rejected(self):
        pdf, image, binding, ocr = fixture()
        with self.assertRaisesRegex(ValueError, 'budget'):
            restore_reference(pdf, image.png, binding, ocr, replace(RestorationSettings(), side_alternatives=100))


if __name__ == '__main__':
    unittest.main()
