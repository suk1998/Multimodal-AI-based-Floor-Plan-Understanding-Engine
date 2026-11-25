import unittest
import cv2
import numpy as np
from floorplan_change.legacy_config import Config
from floorplan_change.wall_hypothesis import extract_wall_hypotheses


class WallHypothesisTests(unittest.TestCase):
    def test_thin_and_thick_regions_are_both_preserved(self):
        image = np.full((240, 320, 3), 255, np.uint8)
        cv2.rectangle(image, (30, 40), (230, 47), (0, 0, 0), -1)
        cv2.rectangle(image, (30, 100), (230, 129), (0, 0, 0), -1)
        result = extract_wall_hypotheses(image, Config(registration=False))
        self.assertEqual(result["stats"]["wall_hypotheses"], 2)
        thicknesses = sorted(h["median_thickness"] for h in result["hypotheses"])
        self.assertLess(thicknesses[0], thicknesses[1])
        self.assertEqual(set(result["component_masks"]), {"WH001", "WH002"})

    def test_short_noise_is_rejected_as_geometry_only(self):
        image = np.full((100, 100, 3), 255, np.uint8)
        image[40, 40] = (0, 0, 0)
        result = extract_wall_hypotheses(image, Config(registration=False))
        self.assertEqual(result["hypotheses"], [])
        self.assertEqual(result["rejected"][0]["reason"], "too_few_pixels")


if __name__ == "__main__":
    unittest.main()
