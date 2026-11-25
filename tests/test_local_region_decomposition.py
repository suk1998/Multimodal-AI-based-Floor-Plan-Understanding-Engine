import unittest
import cv2
import numpy as np
from floorplan_change.legacy_config import Config
from floorplan_change.local_region_decomposition import decompose_foreground


class LocalRegionDecompositionTests(unittest.TestCase):
    def test_connected_t_shape_is_decomposed_by_local_geometry(self):
        image = np.full((240, 320, 3), 255, np.uint8)
        cv2.rectangle(image, (30, 100), (260, 115), (0, 0, 0), -1)
        cv2.rectangle(image, (130, 40), (145, 180), (0, 0, 0), -1)
        result = decompose_foreground(image, Config(registration=False), 12, 4, 15)
        self.assertEqual(int(cv2.connectedComponents((result["foreground"] > 0).astype(np.uint8), 8)[0] - 1), 1)
        self.assertGreaterEqual(len(result["segments"]), 2)
        self.assertGreater(np.count_nonzero(result["orientation"]), 0)

    def test_thickness_map_preserves_thin_and_thick_values(self):
        image = np.full((180, 300, 3), 255, np.uint8)
        cv2.rectangle(image, (20, 30), (260, 35), (0, 0, 0), -1)
        cv2.rectangle(image, (20, 80), (260, 109), (0, 0, 0), -1)
        result = decompose_foreground(image, Config(registration=False), 12, 4, 15)
        thicknesses = [segment["median_thickness"] for segment in result["segments"]]
        self.assertLess(min(thicknesses), max(thicknesses))


if __name__ == "__main__":
    unittest.main()
