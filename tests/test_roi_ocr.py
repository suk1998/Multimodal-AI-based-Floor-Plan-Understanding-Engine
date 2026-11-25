import unittest
from types import SimpleNamespace
from unittest.mock import patch
import numpy as np
from floorplan_change import Config
from floorplan_change.roi_ocr import OCRBackendUnavailable, run_roi_ocr


class FakeTesseract:
    def get_languages(self, config=""):
        return ["kor", "eng"]


class RoiOcrTests(unittest.TestCase):
    def test_shape_first_semantic_outputs_are_roi_scoped(self):
        image = np.full((120, 160, 3), 255, np.uint8)
        polygon = ((.2, .2), (.8, .2), (.8, .5), (.5, .5), (.5, .8), (.2, .8))
        config = Config(registration=False, roi_polygon=polygon)
        anchors = (
                        [{"type": "rectangle_label_box", "id": "RE0001", "bbox": [35, 25, 70, 35], "area": 350}],
                        [{"type": "circle_window_symbol", "id": "CI0001", "bbox": [85, 25, 93, 33],
                            "circle": [89, 29, 4], "area": 64}],
        )

        room_records = [{"type": "room_label", "text": "거실", "bbox": [40, 30, 55, 40],
                        "center": [47.5, 35.0], "confidence": 90.0, "source": "roi_text_box"}]

        with patch("floorplan_change.roi_ocr.pytesseract", FakeTesseract()), \
             patch("floorplan_change.roi_ocr.Output", object()), \
             patch("floorplan_change.roi_ocr._shape_anchors", return_value=anchors), \
             patch("floorplan_change.roi_ocr._roi_room_ocr", return_value=room_records), \
             patch("floorplan_change.roi_ocr._crop_ocr", return_value=[{"text": "PW", "bbox": [90, 30, 95, 35],
                 "center": [92.5, 32.5], "confidence": 90.0}]):
            result = run_roi_ocr(image, image, config)

        target_mask = result["target"]["mask"] > 0
        self.assertEqual([item["text"] for item in result["target"]["room_labels"]], ["거실"])
        self.assertEqual([item["text"] for item in result["target"]["window_symbols"]], ["PW"])
        for item in result["target"]["room_labels"] + result["target"]["window_symbols"]:
            x1, y1, x2, y2 = item["bbox"]
            self.assertTrue(target_mask[y1:y2, x1:x2].all())
        self.assertEqual(result["target"]["text_mask"].shape, image.shape[:2])

    def test_ocr_without_backend_fails_explicitly(self):
        image = np.full((80, 100, 3), 255, np.uint8)
        with patch("floorplan_change.roi_ocr.pytesseract", None):
            with self.assertRaisesRegex(OCRBackendUnavailable, "BACKEND_UNAVAILABLE"):
                run_roi_ocr(image, image, Config(registration=False, roi=(.1, .1, .9, .9)))


if __name__ == "__main__":
    unittest.main()
