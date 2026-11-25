import unittest
import cv2
import numpy as np
from floorplan_change.legacy_config import Config
from floorplan_change.wall_hypothesis import extract_wall_hypotheses
from floorplan_change.wall_thickness_profiling import profile_wall_hypotheses


class WallThicknessProfilingTests(unittest.TestCase):
    def test_original_thickness_orders_thin_and_thick_strips(self):
        image = np.full((240, 320, 3), 255, np.uint8)
        cv2.rectangle(image, (30, 40), (230, 47), (0, 0, 0), -1)
        cv2.rectangle(image, (30, 100), (230, 129), (0, 0, 0), -1)
        hypotheses = extract_wall_hypotheses(image, Config(registration=False))
        profiles = profile_wall_hypotheses(image, hypotheses, 3)["profiles"]
        thicknesses = sorted(profile["estimated_full_thickness_median"] for profile in profiles)
        self.assertEqual(len(thicknesses), 2)
        self.assertLess(thicknesses[0], thicknesses[1])

    def test_dashed_line_has_low_core_support(self):
        image = np.full((160, 300, 3), 255, np.uint8)
        for x in range(20, 280, 18):
            cv2.line(image, (x, 80), (x + 8, 80), (0, 0, 0), 1)
        hypotheses = extract_wall_hypotheses(image, Config(registration=False))
        result = profile_wall_hypotheses(image, hypotheses, 3)
        self.assertEqual(result["profiles"], [])


if __name__ == "__main__":
    unittest.main()
