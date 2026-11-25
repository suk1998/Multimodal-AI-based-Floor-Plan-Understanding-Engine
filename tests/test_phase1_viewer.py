"""Viewer integration checks; the existing extraction tests remain untouched."""
import base64
import json
from pathlib import Path
import sys
import unittest

from streamlit.testing.v1 import AppTest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from phase1_pdf import render_page
from phase1_viewer import image_identity, validate_view_state


class ViewerProtocolTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.pdf = (ROOT / "source_pdfs/자이사업승인도서.pdf").read_bytes()
        cls.image = render_page(cls.pdf, 4)

    def event(self):
        return {"image_id": image_identity(self.image), "image_size": [2382, 1684],
                "coordinate_system": "image_native_pixels", "scale": .4,
                "offset_x": -50., "offset_y": 20.,
                "viewport_width": 650, "viewport_height": 540}

    def test_view_state_is_tied_to_source_page_resolution_and_native_dimensions(self):
        self.assertEqual(validate_view_state(self.event(), self.image)["scale"], .4)
        other = render_page(self.pdf, 5)
        self.assertIsNone(validate_view_state(self.event(), other))
        other_resolution = render_page(self.pdf, 4, 1)
        self.assertIsNone(validate_view_state(self.event(), other_resolution))

    def test_invalid_or_stale_coordinates_are_not_accepted(self):
        for field, value in (("scale", float("nan")), ("scale", 0), ("offset_x", float("inf")),
                             ("viewport_height", -1), ("scale", True),
                             ("image_size", [1191, 842]), ("coordinate_system", "screen_pixels")):
            event = self.event(); event[field] = value
            with self.subTest(field=field, value=value):
                self.assertIsNone(validate_view_state(event, self.image))
        self.assertIsNone(validate_view_state(None, self.image))

    def test_ui_contains_only_two_native_image_components_and_no_duplicate_image_widgets(self):
        app = AppTest.from_file(str(ROOT / "phase1_app.py"), default_timeout=30).run()
        app.file_uploader[0].set_value(("ref.pdf", self.pdf, "application/pdf"))
        app.file_uploader[1].set_value(("target.pdf", (ROOT / "source_pdfs/자이실시도서.pdf").read_bytes(), "application/pdf"))
        app.run()
        self.assertFalse(app.get("imgs"))
        next(s for s in app.selectbox if s.label == "Ref 페이지").select(4).run()
        next(s for s in app.selectbox if s.label == "Target 페이지").select(5).run()
        app.button[0].click().run()
        self.assertFalse(app.exception)
        components = app.get("component_instance")
        self.assertEqual(len(components), 2)
        self.assertFalse(app.get("imgs"))
        for element, record in zip(components, app.session_state["phase1_result"]["pages"]):
            args = json.loads(element.proto.json_args)
            image = record["image"]
            self.assertEqual(args["role"], record["role"])
            self.assertEqual((args["image_width"], args["image_height"]), (image.width, image.height))
            self.assertEqual(args["image_id"], image_identity(image))
            self.assertEqual(base64.b64decode(args["image_data"].split(",", 1)[1]), image.png)
        self.assertEqual(len(app.get("download_button")), 2)


if __name__ == "__main__":
    unittest.main(verbosity=2)
