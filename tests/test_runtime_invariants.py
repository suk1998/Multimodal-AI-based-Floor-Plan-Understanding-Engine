import unittest
import cv2
import numpy as np
from floorplan_change import Config, run_pipeline
from floorplan_change.registration import align_reference
from floorplan_change.roi import roi_mask


class RuntimeInvariantTests(unittest.TestCase):
    def test_polygon_roi_preserves_target_and_blocks_outside_changes(self):
        ref = np.full((180, 240, 3), 255, np.uint8)
        target = ref.copy()
        polygon = ((.2, .2), (.7, .2), (.7, .7), (.45, .7), (.45, .5), (.2, .5))
        cv2.rectangle(target, (55, 55), (105, 75), (0, 0, 0), -1)
        cv2.rectangle(target, (190, 140), (225, 165), (0, 0, 0), -1)
        result = run_pipeline(ref, target, config=Config(registration=False, roi_polygon=polygon))
        mask = roi_mask(target.shape, polygon=polygon) > 0
        self.assertEqual(result["target_result"].shape, target.shape)
        self.assertEqual(result["target_numbered_result"].shape, target.shape)
        self.assertTrue(result["changes"])
        self.assertTrue(all(mask[y, x] for x, y in [(55, 55)]))
        self.assertFalse(result["added_mask"][~mask].any())
        self.assertFalse(result["removed_mask"][~mask].any())
        self.assertFalse(result["comparison_mask"][~mask].any())
        np.testing.assert_array_equal(result["target_result"][~mask], target[~mask])
        np.testing.assert_array_equal(result["target_numbered_result"][~mask], target[~mask])
        self.assertEqual(result["manifest"]["preserves_target_shape"], list(target.shape))

    def test_registration_maps_common_roi_into_target_coordinates(self):
        ref = np.full((240, 320, 3), 255, np.uint8)
        rng = np.random.default_rng(4)
        for _ in range(35):
            x, y = rng.integers([45, 45], [250, 180])
            cv2.rectangle(ref, (int(x), int(y)), (int(x + 12), int(y + 9)), (0, 0, 0), 2)
        transform = np.float32([[1, 0, 14], [0, 1, 9]])
        target = cv2.warpAffine(ref, transform, (320, 240), borderValue=(255, 255, 255))
        polygon = ((.1, .1), (.85, .1), (.85, .85), (.1, .85))
        aligned, valid, requested, info = align_reference(
            ref, target, Config(roi_polygon=polygon))
        self.assertEqual(aligned.shape, target.shape)
        self.assertEqual(requested.shape, target.shape[:2])
        self.assertFalse((valid > 0)[requested == 0].any())
        matrix = np.asarray(info["matrix"])
        np.testing.assert_allclose(matrix[:2, 2], [14, 9], atol=2)
        self.assertGreater(info["comparison_coverage"], .5)

    def test_roi_is_required_at_runtime(self):
        image = np.full((80, 100, 3), 255, np.uint8)
        with self.assertRaises(ValueError):
            run_pipeline(image, image, config=Config(registration=False))


if __name__ == "__main__":
    unittest.main()
