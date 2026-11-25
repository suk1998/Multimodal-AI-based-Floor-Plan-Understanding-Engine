"""Tests coordinate and evidence safety contracts, not model accuracy."""
import copy
import unittest

from run import map_box, parse_proposal, validate_roi


class CoordinateEvidenceTests(unittest.TestCase):
    def test_tile_normalized_box_maps_to_native_without_ui_coordinates(self):
        view = {"native_bbox_xyxy": [100, 200, 1300, 1000]}
        self.assertEqual(map_box([100, 250, 900, 750], view), [220, 400, 1180, 800])

    def test_outside_invalid_and_zero_area_boxes_are_not_clipped_into_validity(self):
        view = {"native_bbox_xyxy": [100, 200, 1300, 1000]}
        for box in [[-1,0,10,10], [0,0,1001,1000], [0,0,0,10], [0,0,float("nan"),2], [True,0,10,10]]:
            with self.subTest(box=box), self.assertRaises(ValueError):
                map_box(box, view)

    def test_invalid_geometry_and_dangling_relations_are_preserved_as_issues(self):
        text = '{"entities":[{"id":"a","bbox_1000":[0,0,100,100]},{"id":"b","bbox_1000":[-1,0,100,100]}],"relations":[{"subject_id":"a","object_id":"b"}],"uncertainties":[]}'
        value = parse_proposal(text, {"native_bbox_xyxy": [0,0,100,100]})
        self.assertEqual(len(value["issues"]), 2)
        self.assertEqual(value["relations"], [])
        self.assertEqual(value["entities"][0]["verification_status"], "PROPOSED")

    def test_roi_cannot_transfer_between_pdf_page_zoom_or_size(self):
        binding = {"source_sha256":"abc", "page":4, "extraction_zoom":2., "width":100, "height":100}
        roi = {"schema_version":1, "role":"Target", "coordinate_system":"image_native_pixels", "image":binding,
               "vertices":[[1,1],[90,1],[90,90]]}
        self.assertEqual(validate_roi(roi, binding), roi)
        for key, value in [("source_sha256","def"),("page",5),("extraction_zoom",1.),("width",200),("height",200)]:
            changed = copy.deepcopy(roi)
            changed["image"][key] = value
            with self.subTest(key=key), self.assertRaisesRegex(ValueError,"binding mismatch"):
                validate_roi(changed, binding)

    def test_truncated_model_json_is_failure_not_empty_success(self):
        with self.assertRaises(ValueError):
            parse_proposal('{"entities":[', {"native_bbox_xyxy":[0,0,100,100]})


if __name__ == "__main__":
    unittest.main(verbosity=2)
