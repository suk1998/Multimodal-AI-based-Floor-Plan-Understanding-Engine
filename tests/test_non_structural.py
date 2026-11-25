import unittest
import numpy as np
from floorplan_change.non_structural import classify_change_regions


class NonStructuralClassificationTests(unittest.TestCase):
    def test_sparse_line_is_non_structural(self):
        added = np.zeros((80, 120), bool)
        added[40, 10:100] = True
        region = {"change_id": "CR001", "bbox": [10, 40, 100, 41],
                  "change_types": ["added"], "added_pixels": 90, "removed_pixels": 0}
        structural, non_structural, review = classify_change_regions([region], added, np.zeros_like(added))
        self.assertEqual(len(structural), 0)
        self.assertEqual(non_structural[0]["classification"], "NON_STRUCTURAL")
        self.assertEqual(review, [])

    def test_filled_region_is_structural_candidate(self):
        added = np.zeros((80, 120), bool)
        added[20:50, 20:100] = True
        region = {"change_id": "CR002", "bbox": [20, 20, 100, 50],
                  "change_types": ["added"], "added_pixels": 2400, "removed_pixels": 0}
        structural, non_structural, review = classify_change_regions([region], added, np.zeros_like(added))
        self.assertEqual(structural[0]["classification"], "STRUCTURAL_CANDIDATE")
        self.assertEqual(non_structural, [])
        self.assertEqual(review, [])


if __name__ == "__main__":
    unittest.main()
