"""Behavioral regressions for the default comparison path."""
import csv
import io
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
import cv2
import numpy as np
from floorplan_change import Config, run_pipeline
from floorplan_change.evaluation import normalize_ground_truth, evaluate_predictions
from floorplan_change.ui.pdf_loader import load_bytes


def canvas(height=400, width=500):
    return np.full((height, width, 3), 255, np.uint8)


def textured():
    image = canvas(600, 800)
    rng = np.random.default_rng(21)
    for _ in range(70):
        x, y = map(int, rng.integers([40, 40], [670, 510]))
        cv2.rectangle(image, (x, y), (x+15, y+12), (0, 0, 0), 2)
    return image


class ImageComparisonTests(unittest.TestCase):
    def compare(self, ref, target, **settings):
        settings.setdefault("roi", (0., 0., 1., 1.))
        return run_pipeline(ref, target, config=Config(registration=False, **settings))

    def test_identical_and_blank_are_exactly_unchanged(self):
        for image in (canvas(), textured()):
            before = image.copy()
            result = run_pipeline(image, image, config=Config(roi=(0., 0., 1., 1.)))
            self.assertEqual(result["changes"], [])
            np.testing.assert_array_equal(result["target_result"], before)
            np.testing.assert_array_equal(image, before)
            self.assertEqual(result["manifest"]["ref_role"], "immutable_baseline")
            self.assertEqual(result["manifest"]["target_role"], "final_render_canvas")
            self.assertEqual(result["manifest"]["render_policy"], "original_target_plus_change_markers")
            self.assertEqual(result["manifest"]["preserves_target_shape"], list(image.shape))

    def test_addition_and_deletion_change_only_evidence_pixels(self):
        blank = canvas()
        wall = blank.copy()
        cv2.line(wall, (70, 100), (350, 100), (0, 0, 0), 3)
        for ref, target, kind in ((blank, wall, "added"), (wall, blank, "removed")):
            with self.subTest(kind=kind):
                old_ref, old_target = ref.copy(), target.copy()
                result = self.compare(ref, target)
                self.assertEqual(len(result["changes"]), 1)
                self.assertEqual(result["changes"][0]["change_types"], [kind])
                support = (result["added_mask"] > 0) | (result["removed_mask"] > 0)
                np.testing.assert_array_equal(result["target_result"][~support], target[~support])
                self.assertTrue(np.any(result["target_result"][support] != target[support]))
                np.testing.assert_array_equal(ref, old_ref)
                np.testing.assert_array_equal(target, old_target)

    def test_extension_highlights_only_the_new_end(self):
        ref, target = canvas(), canvas()
        cv2.line(ref, (70, 100), (350, 100), (0, 0, 0), 3)
        cv2.line(target, (70, 100), (400, 100), (0, 0, 0), 3)
        result = self.compare(ref, target)
        self.assertEqual(len(result["changes"]), 1)
        self.assertEqual(result["changes"][0]["change_types"], ["added"])
        self.assertGreater(result["changes"][0]["bbox"][0], 350)
        self.assertFalse(result["added_mask"][:, :350].any())

    def test_thickness_symbol_and_color_changes_are_detected(self):
        for name in ("thickness", "symbol", "color"):
            with self.subTest(name=name):
                ref, target = canvas(), canvas()
                if name == "thickness":
                    cv2.line(ref, (70, 100), (350, 100), (0, 0, 0), 3)
                    cv2.line(target, (70, 100), (350, 100), (0, 0, 0), 7)
                elif name == "symbol":
                    cv2.circle(target, (200, 200), 4, (0, 0, 0), -1)
                else:
                    cv2.line(ref, (70, 100), (350, 100), (0, 0, 220), 3)
                    cv2.line(target, (70, 100), (350, 100), (220, 0, 0), 3)
                result = self.compare(ref, target)
                self.assertTrue(result["changes"])
                if name == "color":
                    self.assertTrue(result["added_mask"].any() and result["removed_mask"].any())

    def test_position_and_color_tolerance_and_isolated_noise(self):
        ref, target = canvas(), canvas()
        cv2.line(ref, (70, 100), (350, 100), (0, 0, 0), 3)
        cv2.line(target, (70, 101), (350, 101), (20, 20, 20), 3)
        target[300, 400] = 0
        tolerant = self.compare(ref, target)
        self.assertEqual(tolerant["changes"], [])
        strict = self.compare(ref, target, pixel_tolerance=0, color_tolerance=0)
        self.assertTrue(strict["changes"])

    def test_region_grouping_does_not_fill_gaps_or_inflate_small_noise(self):
        target = canvas()
        target[100:102, 100:102] = 0
        result = self.compare(canvas(), target, grouping_radius=20)
        self.assertEqual(result["changes"], [])
        target[100:105, 150:155] = 0
        target[100:105, 158:163] = 0
        result = self.compare(canvas(), target)
        self.assertEqual(len(result["changes"]), 1)
        self.assertFalse(result["added_mask"][100:105, 155:158].any())

    def test_roi_uses_original_target_coordinates(self):
        target = canvas()
        cv2.circle(target, (80, 80), 8, (0, 0, 0), -1)
        cv2.circle(target, (400, 300), 8, (0, 0, 0), -1)
        result = self.compare(canvas(), target, roi=(.5, .5, 1., 1.))
        self.assertEqual(len(result["changes"]), 1)
        self.assertGreater(result["changes"][0]["bbox"][0], 350)
        np.testing.assert_array_equal(result["target_result"][:200], target[:200])

    def test_original_target_size_and_unobserved_area_are_preserved(self):
        ref = canvas(200, 250)
        target = canvas()
        cv2.line(target, (300, 300), (400, 300), (0, 0, 0), 3)
        result = self.compare(ref, target)
        self.assertEqual(result["target_result"].shape, target.shape)
        self.assertEqual(result["changes"], [])
        self.assertTrue(result["uncompared_mask"][300, 350])
        np.testing.assert_array_equal(result["target_result"], target)

    def test_stable_content_registration_ignores_actual_added_region(self):
        ref = textured()
        for name, angle, scale, shift in (("translation", 0, 1, (13, 9)),
                                           ("rotation_scale", 1.5, 1.025, (12, -4))):
            with self.subTest(name=name):
                matrix = cv2.getRotationMatrix2D((400, 300), angle, scale)
                matrix[:, 2] += shift
                target = cv2.warpAffine(ref, matrix, (880, 680), borderValue=(255, 255, 255))
                cv2.line(target, (600, 580), (740, 580), (0, 0, 0), 4)
                result = run_pipeline(ref, target, config=Config(roi=(0., 0., 1., 1.)))
                np.testing.assert_allclose(np.array(result["alignment"]["matrix"])[:2], matrix, atol=.5)
                self.assertEqual(result["target_result"].shape, target.shape)
                self.assertEqual(len(result["changes"]), 1)
                self.assertGreater(result["changes"][0]["bbox"][1], 570)
                self.assertEqual(result["changes"][0]["change_types"], ["added"])

    def test_translation_alone_is_not_a_change(self):
        ref = textured()
        target = cv2.warpAffine(ref, np.float32([[1, 0, 12], [0, 1, 8]]),
                               (800, 600), borderValue=(255, 255, 255))
        result = run_pipeline(ref, target, config=Config(roi=(0., 0., 1., 1.)))
        self.assertEqual(result["changes"], [])

    def test_large_images_do_not_get_a_shift_from_downsampling(self):
        ref = cv2.resize(textured(), None, fx=3, fy=3, interpolation=cv2.INTER_NEAREST)
        target = ref.copy()
        cv2.rectangle(target, (1800, 1680), (2100, 1710), (0, 0, 0), -1)
        result = run_pipeline(ref, target, config=Config(roi=(0., 0., 1., 1.)))
        np.testing.assert_allclose(np.array(result["alignment"]["matrix"]), np.eye(3), atol=.15)
        self.assertEqual(len(result["changes"]), 1)
        self.assertGreater(result["changes"][0]["bbox"][1], 1670)

    def test_bad_registration_and_config_fail_explicitly(self):
        with self.assertRaises(ValueError):
            run_pipeline(canvas(), np.zeros_like(canvas()), config=Config(roi=(0., 0., 1., 1.)))
        for settings in ({"roi": (.8, 0, .2, 1)}, {"roi": (0, 0, float("nan"), 1)},
                         {"pixel_tolerance": -1}, {"min_change_area": 0},
                         {"grouping_radius": 1.5}, {"color_tolerance": 300}):
            with self.subTest(settings=settings), self.assertRaises(ValueError):
                Config(**settings)
        with self.assertRaises(ValueError):
            run_pipeline(canvas(), np.zeros((20, 20), np.uint8))

    def test_export_ids_evaluation_and_deterministic_results(self):
        ref, target = canvas(), canvas()
        cv2.rectangle(target, (100, 100), (150, 110), (0, 0, 0), -1)
        truth = normalize_ground_truth([{"ground_truth_id": "GT1", "bbox": [100, 100, 51, 11],
                                       "change_types": ["added"]}])
        with tempfile.TemporaryDirectory() as folder:
            result = run_pipeline(ref, target, folder, Config(registration=False, roi=(0., 0., 1., 1.)), ground_truth=truth)
            self.assertEqual(result["evaluation"]["metrics"]["tp"], 1)
            records = json.loads((Path(folder)/"data/change_report.json").read_text(encoding="utf-8"))
            with (Path(folder)/"report/change_report.csv").open(encoding="utf-8-sig", newline="") as stream:
                rows = list(csv.DictReader(stream))
            self.assertEqual(records[0]["change_id"], rows[0]["변경_ID"])
            saved = cv2.imdecode(np.fromfile(Path(folder)/"visual/target_change_regions.png", np.uint8), 1)
            np.testing.assert_array_equal(saved, result["target_result"])
            self.assertTrue((Path(folder)/"evaluation/metrics.json").exists())
        repeat = self.compare(ref, target)
        self.assertEqual(repeat["changes"], result["changes"])

    def test_evaluation_checks_types_per_region(self):
        truth = [{"bbox": [10, 10, 30, 30], "change_types": ["added"]},
                 {"bbox": [50, 50, 70, 70], "change_types": ["removed"]}]
        predictions = [{"bbox": [10, 10, 30, 30], "change_types": ["removed"]},
                       {"bbox": [50, 50, 70, 70], "change_types": ["added"]}]
        metrics = evaluate_predictions(predictions, [], [], truth)
        self.assertEqual(metrics["tp"], 2)
        self.assertEqual(metrics["type_true_positive"], 0)
        for payload in ([None], [{"ground_truth_id": "G", "bbox": [0, 0, None, 2]}],
                        [{"ground_truth_id": "G", "bbox": [0, 0, float("nan"), 2]}]):
            with self.assertRaises(ValueError):
                normalize_ground_truth(payload)

    def test_cli_default_runs_comparison_and_connects_ground_truth(self):
        ref, target = canvas(), canvas()
        cv2.rectangle(target, (100, 100), (150, 110), (0, 0, 0), -1)
        with tempfile.TemporaryDirectory() as folder:
            folder = Path(folder)
            for name, image in (("ref.png", ref), ("target.png", target)):
                cv2.imencode(".png", image)[1].tofile(str(folder/name))
            truth = [{"ground_truth_id": "GT1", "bbox": [100, 100, 51, 11], "change_types": ["added"]}]
            (folder/"gt.json").write_text(json.dumps(truth), encoding="utf-8")
            command = [sys.executable, str(Path(__file__).resolve().parents[1]/"main.py"),
                       "--ref", str(folder/"ref.png"), "--target", str(folder/"target.png"),
                       "--skip-registration", "--roi", "0,0,1,1", "--ground-truth", str(folder/"gt.json"),
                       "--output", str(folder/"result")]
            completed = subprocess.run(command, capture_output=True, text=True, timeout=30)
            self.assertEqual(completed.returncode, 0, completed.stderr)
            self.assertIn("Changed regions: 1", completed.stdout)
            self.assertIn("CR001 added", completed.stdout)
            metrics = json.loads((folder/"result/evaluation/metrics.json").read_text())
            self.assertEqual(metrics["tp"], 1)

    def test_raster_and_pdf_inputs(self):
        image = canvas()
        loaded = load_bytes(cv2.imencode(".png", image)[1].tobytes(), "drawing.png")
        np.testing.assert_array_equal(image, loaded)
        import fitz
        with fitz.open() as pdf:
            pdf.new_page(width=100, height=120)
            pdf.new_page(width=140, height=160)
            data = pdf.tobytes()
        self.assertEqual(load_bytes(data, "drawing.pdf", 2, 1).shape, (160, 140, 3))


if __name__ == "__main__":
    unittest.main()
