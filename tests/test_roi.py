"""Only the selected building contributes detections, even with a concave outline."""
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
import cv2
import numpy as np
from floorplan_change import Config, run_pipeline
from floorplan_change.roi import roi_mask
from floorplan_change.evaluation import normalize_ground_truth


class SubjectRoiTests(unittest.TestCase):
    def test_roi_is_required_in_api_and_cli(self):
        image = np.full((100, 100, 3), 255, np.uint8)
        with self.assertRaisesRegex(ValueError, "ROI"):
            run_pipeline(image, image)
        with self.assertRaisesRegex(ValueError, "ROI"):
            run_pipeline(image, image, config=Config(registration=False))
        cli = subprocess.run([sys.executable, str(Path(__file__).resolve().parents[1]/"main.py"),
                              "--ref", "unused.png", "--target", "unused.png"],
                             capture_output=True, text=True, timeout=30)
        self.assertEqual(cli.returncode, 2)
        self.assertIn("--roi", cli.stderr)

    def test_concave_building_excludes_titles_dimensions_and_other_drawing(self):
        ref = np.full((400, 500, 3), 255, np.uint8)
        target = ref.copy()
        # The top right quarter of this L shape is NOT part of the building.
        polygon = ((.2, .2), (.5, .2), (.5, .5), (.8, .5), (.8, .8), (.2, .8))
        cv2.rectangle(target, (110, 100), (160, 108), (0, 0, 0), -1)  # building addition
        cv2.rectangle(ref, (280, 250), (330, 258), (0, 0, 0), -1)  # building deletion
        cv2.rectangle(target, (300, 100), (350, 110), (0, 0, 0), -1)  # concave notch
        cv2.putText(target, "TITLE CHANGED", (50, 30), cv2.FONT_HERSHEY_SIMPLEX, .8, (0, 0, 0), 2)
        cv2.line(target, (420, 80), (420, 320), (0, 0, 0), 3)
        result = run_pipeline(ref, target, config=Config(registration=False, roi_polygon=polygon))
        self.assertEqual(len(result["changes"]), 2)
        mask = result["subject_roi_mask"]
        self.assertEqual(mask[105, 320], 0)
        for name in ("added_mask", "removed_mask", "comparison_mask"):
            self.assertFalse(result[name][mask == 0].any())
        for name in ("target_result", "target_numbered_result"):
            np.testing.assert_array_equal(result[name][mask == 0], target[mask == 0])
        self.assertTrue(result["added_mask"][104, 130])
        self.assertTrue(result["removed_mask"][254, 300])

    def test_identical_roi_boundaries_produce_no_artifacts(self):
        image = np.full((200, 200, 3), 255, np.uint8)
        cv2.line(image, (0, 100), (199, 100), (0, 0, 0), 5)
        polygon = ((.1, .1), (.8, .15), (.4, .8))
        result = run_pipeline(image, image, config=Config(roi_polygon=polygon))
        self.assertEqual(result["changes"], [])
        np.testing.assert_array_equal(result["target_result"], image)

    def test_outside_ink_cannot_hide_an_inside_change(self):
        ref = np.full((100, 100, 3), 255, np.uint8)
        target = ref.copy()
        cv2.line(ref, (49, 30), (49, 70), (0, 0, 0), 1)  # outside rectangle
        cv2.line(target, (50, 30), (50, 70), (0, 0, 0), 1)  # first inside column
        result = run_pipeline(ref, target, config=Config(registration=False, roi=(.5, .2, .9, .8)))
        self.assertEqual(len(result["changes"]), 1)
        self.assertEqual(result["changes"][0]["change_types"], ["added"])

    def test_invalid_polygon_is_rejected_and_closed_ring_is_normalized(self):
        invalid = [
            ((.2, .2), (.8, .8), (.2, .8), (.8, .2)),
            ((.2, .2), (.4, .4), (.8, .8)),
            ((-.1, .2), (.8, .2), (.8, .8)),
            ((.1, .1), (float("nan"), .2), (.8, .8)),
        ]
        for polygon in invalid:
            with self.subTest(polygon=polygon), self.assertRaises(ValueError):
                Config(roi_polygon=polygon)
        config = Config(roi_polygon=[[.1, .1], [.8, .1], [.8, .8], [.1, .1]])
        self.assertEqual(len(config.roi_polygon), 3)
        with self.assertRaises(ValueError):
            Config(roi=(0, 0, 1, 1), roi_polygon=config.roi_polygon)

    def test_evaluation_ignores_annotations_outside_the_building(self):
        ref = np.full((100, 100, 3), 255, np.uint8)
        target = ref.copy()
        target[25:35, 25:35] = 0
        truth = normalize_ground_truth([
            {"ground_truth_id": "inside", "bbox": [25, 25, 10, 10], "change_types": ["added"]},
            {"ground_truth_id": "title", "bbox": [70, 70, 20, 20], "change_types": ["added"]}])
        result = run_pipeline(ref, target, config=Config(registration=False, roi=(.1, .1, .5, .5)), ground_truth=truth)
        metrics = result["evaluation"]["metrics"]
        self.assertEqual(metrics["tp"], 1)
        self.assertEqual(metrics["fn"], 0)
        self.assertEqual(metrics["outside_comparison_gt_ids"], ["title"])

    def test_cli_accepts_polygon_file_and_exports_subject_mask(self):
        ref = np.full((200, 200, 3), 255, np.uint8)
        target = ref.copy()
        target[50:70, 50:70] = 0
        target[150:170, 150:170] = 0
        with tempfile.TemporaryDirectory() as folder:
            folder = Path(folder)
            for name, image in (("ref.png", ref), ("target.png", target)):
                cv2.imencode(".png", image)[1].tofile(str(folder/name))
            (folder/"roi.json").write_text(json.dumps([[.1,.1],[.5,.1],[.5,.5],[.1,.5]]))
            completed = subprocess.run([sys.executable, str(Path(__file__).resolve().parents[1]/"main.py"),
                "--ref", str(folder/"ref.png"), "--target", str(folder/"target.png"), "--skip-registration",
                "--roi-polygon", str(folder/"roi.json"), "--output", str(folder/"result")],
                capture_output=True, text=True, timeout=30)
            self.assertEqual(completed.returncode, 0, completed.stderr)
            records = json.loads((folder/"result/data/change_report.json").read_text(encoding="utf-8"))
            self.assertEqual(len(records), 1)
            self.assertTrue((folder/"result/masks/subject_roi_mask.png").exists())
            self.assertTrue((folder/"result/visual/target_roi.png").exists())
