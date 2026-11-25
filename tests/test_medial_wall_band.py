import unittest
import cv2
import numpy as np
from floorplan_change.legacy_config import Config
from floorplan_change.medial_wall_band import extract_medial_wall_band


class MedialWallBandTests(unittest.TestCase):
    def test_thick_strip_has_wider_medial_profile(self):
        image = np.full((180, 300, 3), 255, np.uint8)
        cv2.rectangle(image, (20, 30), (260, 35), (0, 0, 0), -1)
        cv2.rectangle(image, (20, 80), (260, 99), (0, 0, 0), -1)
        result = extract_medial_wall_band(image, Config(registration=False))
        widths = sorted(path["width_median"] for path in result["paths"])
        self.assertGreaterEqual(len(widths), 2)
        self.assertLess(widths[0], widths[-1])
        self.assertGreater(result["stats"]["skeleton_pixels"], 0)

    def test_dashed_line_produces_short_or_disconnected_paths(self):
        image = np.full((120, 300, 3), 255, np.uint8)
        for x in range(20, 280, 20):
            cv2.line(image, (x, 60), (x + 8, 60), (0, 0, 0), 1)
        result = extract_medial_wall_band(image, Config(registration=False))
        self.assertGreater(result["stats"]["medial_path_count"], 1)


if __name__ == "__main__":
    unittest.main()
