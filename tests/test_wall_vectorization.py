import unittest
import cv2
import numpy as np
from floorplan_change.legacy_config import Config
from floorplan_change.wall_vectorization import extract_wall_vectors


class WallVectorizationTests(unittest.TestCase):
    def test_regions_become_vectors_without_skeleton(self):
        image = np.full((240, 320, 3), 255, np.uint8)
        cv2.rectangle(image, (30, 40), (230, 47), (0, 0, 0), -1)
        cv2.rectangle(image, (30, 100), (230, 129), (0, 0, 0), -1)
        result = extract_wall_vectors(image, Config(registration=False))
        self.assertEqual(result["stats"]["wall_vector_count"], 2)
        self.assertTrue({"axis_coverage", "median_cross_section_width", "cross_section_width_cv",
                 "stable_width_ratio", "cross_section_fill_ratio", "sparse_network_ratio",
                 "wallness_accepted", "wallness_rejection_reason"}.issubset(result["vectors"][0]))
        thickness = sorted(vector["median_thickness"] for vector in result["vectors"])
        self.assertLess(thickness[0], thickness[1])
        self.assertEqual(set(result["component_masks"]), {"WV0001", "WV0002"})

    def test_short_region_is_only_geometry_rejected(self):
        image = np.full((100, 100, 3), 255, np.uint8)
        image[40, 40] = (0, 0, 0)
        result = extract_wall_vectors(image, Config(registration=False))
        self.assertEqual(result["vectors"], [])
        self.assertEqual(result["rejected"][0]["reason"], "too_few_pixels")


if __name__ == "__main__":
    unittest.main()
