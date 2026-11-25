"""Recognize actual wall-end changes without trusting the former Page 4 audit coordinates."""
import csv
from io import BytesIO, StringIO
import json
from pathlib import Path
import unittest
from zipfile import ZipFile

import numpy as np
import pymupdf

from backend.comparison_review import ComparisonReview, export_review
from backend.spatial_alignment import verify
from backend.structural_comparison import compare
from backend.wall_sections import read_strokes, sections
from phase1_pdf import render_page


class WallSectionTests(unittest.TestCase):
    def test_continuous_caps_from_vector_primitives_in_native_frame(self):
        with pymupdf.open() as doc:
            page = doc.new_page(width=300, height=200)
            page.draw_line((40,60),(200,60),width=.85)
            page.draw_line((40,71),(200,71),width=.85)
            page.draw_line((200,60),(200,71),width=.85)
            page.draw_line((40,86),(200,86),width=.2)
            pdf = doc.tobytes()
        page = render_page(pdf,1,2)
        strokes = read_strokes(pdf,page)
        wall, = sections(strokes,2,[0,0,page.width,page.height])
        self.assertEqual(wall.first.points(), [[80,120],[400,120]])
        self.assertEqual(wall.second.points(), [[80,142],[400,142]])
        self.assertIsNone(wall.returns[0])
        self.assertEqual(wall.returns[1].points(), [[400,120],[400,142]])
        self.assertFalse(wall.first.wall_layer)


class ActualPage4WallChangeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        root = Path(__file__).resolve().parents[1]
        data = [root/'source_pdfs'/name for name in ('자이사업승인도서.pdf','자이실시도서.pdf')]
        saved = root/'implementation_validation/2026-11-21/step8'
        context = [saved/part/'geometry.json' for part in ('ref_regression','actual_final')]
        if not all(file.is_file() for file in data+context):
            raise unittest.SkipTest('Original Page 4 PDFs and independent geometries required')
        pdf = [file.read_bytes() for file in data]
        cls.pages = [render_page(f,4,2) for f in pdf]
        cls.geometries = [json.loads(file.read_bytes()) for file in context]
        cls.roi = json.loads((root/'alignment_inputs/page4_user_roi.json').read_bytes())
        cls.strokes = [read_strokes(data,page) for data,page in zip(pdf,cls.pages)]
        cls.result = compare(cls.pages,cls.geometries,cls.roi,cls.strokes)

    def test_role_independent_alignment_and_changed_wall_not_absorbed(self):
        self.assertEqual(verify(self.pages,self.geometries,self.roi)['status'],'DEFERRED')
        result = self.result
        self.assertEqual(result['alignment_status'],'PARTIAL')
        diagnostics = result['alignment_diagnostics']
        self.assertLess(diagnostics['wall_registration']['independent_max_normal_ref_px'],.5)
        self.assertGreater(result['coverage']['fraction'],.30)
        self.assertLess(result['coverage']['fraction'],.50)
        matrix = np.asarray(diagnostics['matrix'])
        self.assertLess(np.linalg.norm(matrix[:2,2]),.5)
        measured = [r for r in result['correspondences'] if r.get('automatic_confirmation')]
        self.assertEqual(len(measured),5)
        self.assertTrue(all(not r['alignment_fit_parent'] and r['entity_type']=='Wall' for r in measured))
        self.assertEqual(sorted(round(r['measurements']['delta_ref_px'],2) for r in measured),
                         [-10.56,-10.56,-5.04,8.16,9.36])
        # The original retained left-bath wall does not become a new or moved wall.
        self.assertTrue(all(r['verified_target_bbox_native'][0]>700 for r in measured))

    def test_native_csv_png_use_only_evidenced_structure(self):
        review = ComparisonReview(self.result,[])
        review.save({})
        with ZipFile(BytesIO(export_review(review,self.pages[1]))) as archive:
            def rows(name):
                return list(csv.DictReader(StringIO(archive.read(name).decode('utf-8-sig'))))
            exported = rows('change_report.csv')
            self.assertEqual(len(exported),5)
            self.assertEqual({row['element'] for row in exported},{'Wall'})
            self.assertTrue(all(row['decision_source']=='VECTOR_WALL_END_WITH_INDEPENDENT_RASTER_SUPPORT' for row in exported))
            self.assertTrue(any('침실2' in row['space_name'] for row in exported))
            self.assertTrue(any(row['domain']=='ANNOTATION' for row in rows('annotation_changes.csv')))
            self.assertEqual(archive.read('change_report.csv'),export_review(review,self.pages[1],'csv'))
            self.assertEqual(archive.read('target_changes.png'),export_review(review,self.pages[1],'png'))
            self.assertEqual(json.loads(archive.read('manifest.json'))['confirmed_structural_count'],5)
