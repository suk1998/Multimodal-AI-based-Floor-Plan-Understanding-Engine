"""Exercise the actual Compare button and state invalidation, using raster inputs."""
from pathlib import Path
import hashlib
import unittest
from unittest.mock import patch
import cv2
import numpy as np
from streamlit.testing.v1 import AppTest


class UserInterfaceTests(unittest.TestCase):
    def test_drawing_events_require_completion_and_invalidate_old_results(self):
        ref = np.full((200, 300, 3), 255, np.uint8)
        target = ref.copy()
        target[80:90, 120:180] = 0
        sources = [(name, cv2.imencode(".png", image)[1].tobytes())
                   for name, image in (("ref.png", ref), ("target.png", target))]
        image_id = hashlib.sha256(sources[1][1]+b"1").hexdigest()[:20]
        with patch("floorplan_change.ui.pdf_loader.sample_sources", return_value=sources) as inputs, \
             patch("floorplan_change.ui.roi_editor.roi_editor", return_value=None) as picker:
            app = AppTest.from_file(str(Path(__file__).resolve().parents[1]/"app.py"), default_timeout=30).run()
            app.checkbox[0].check().run()
            self.assertTrue(next(b for b in app.button if b.label == "Compare").disabled)
            next(c for c in app.checkbox if c.label == "이미 동일 좌표로 정렬된 이미지").check().run()
            picker.return_value = {"image_id": image_id, "event_id": "1", "state": "complete",
                                   "points": [[.35,.35],[.7,.35],[.7,.5],[.35,.5]]}
            app.run()
            next(b for b in app.button if b.label == "Compare").click().run()
            self.assertEqual(len(app.session_state["result"]["changes"]), 1)
            picker.return_value = {"image_id": image_id, "event_id": "2", "state": "editing", "points": None}
            app.run()
            self.assertNotIn("result", app.session_state)
            self.assertTrue(next(b for b in app.button if b.label == "Compare").disabled)
            picker.return_value = {"image_id": image_id, "event_id": "3", "state": "complete",
                                   "points": [[.1,.1],[.8,.8],[.1,.8],[.8,.1]]}
            app.run()
            self.assertTrue(list(app.error))
            self.assertTrue(next(b for b in app.button if b.label == "Compare").disabled)
            picker.return_value = {"image_id": image_id, "event_id": "4", "state": "complete",
                                   "points": [[.1,.1],[.3,.1],[.3,.3],[.1,.3]]}
            app.run()
            next(b for b in app.button if b.label == "Compare").click().run()
            self.assertEqual(app.session_state["result"]["changes"], [])
            # A new Target must not consume a stale component event for the old page.
            target[150:160, 200:210] = 0
            inputs.return_value = [sources[0], ("target.png", cv2.imencode(".png", target)[1].tobytes())]
            app.run()
            self.assertNotIn("result", app.session_state)
            self.assertTrue(next(b for b in app.button if b.label == "Compare").disabled)
            self.assertFalse(list(app.exception))

    def test_compare_renders_real_changes_and_settings_invalidate_them(self):
        ref = np.full((200, 300, 3), 255, np.uint8)
        target = ref.copy()
        cv2.rectangle(target, (120, 80), (180, 90), (0, 0, 0), -1)
        sources = [(name, cv2.imencode(".png", image)[1].tobytes())
                   for name, image in (("ref.png", ref), ("target.png", target))]
        with patch("floorplan_change.ui.pdf_loader.sample_sources", return_value=sources):
            app = AppTest.from_file(str(Path(__file__).resolve().parents[1]/"app.py"), default_timeout=30).run()
            next(c for c in app.checkbox if c.label == "프로젝트 샘플 PDF 사용").check().run()
            self.assertTrue(next(b for b in app.button if b.label == "Compare").disabled)
            next(c for c in app.checkbox if c.label == "이미 동일 좌표로 정렬된 이미지").check().run()
            next(r for r in app.radio if r.label == "ROI 선택 방법").set_value("좌표 입력").run()
            next(t for t in app.text_area if t.label == "ROI 좌표").set_value("0.35,0.35,0.7,0.5").run()
            next(b for b in app.button if b.label == "Compare").click().run()
            self.assertFalse(list(app.exception))
            self.assertFalse(list(app.error))
            result = app.session_state["result"]
            self.assertEqual(result["manifest"]["architecture"], "aligned-image-difference-v1")
            self.assertEqual(len(result["changes"]), 1)
            self.assertEqual(result["changes"][0]["change_types"], ["added"])
            self.assertTrue(result["rows"])
            np.testing.assert_array_equal(result["target_result"][0, 0], target[0, 0])
            next(c for c in app.checkbox if c.label == "변경 영역 번호 표시").check().run()
            self.assertIn("result", app.session_state)
            next(s for s in app.slider if s.label == "위치 오차 허용 (px)").set_value(2).run()
            self.assertNotIn("result", app.session_state)
            next(b for b in app.button if b.label == "Compare").click().run()
            self.assertIn("result", app.session_state)
            next(t for t in app.text_area if t.label == "ROI 좌표").set_value("").run()
            self.assertNotIn("result", app.session_state)
            self.assertTrue(next(b for b in app.button if b.label == "Compare").disabled)
            next(t for t in app.text_area if t.label == "ROI 좌표").set_value("0.35,0.35,0.7,0.5").run()
            next(b for b in app.button if b.label == "Compare").click().run()
            # A failed re-comparison must not leave a previous successful result visible.
            next(c for c in app.checkbox if c.label == "이미 동일 좌표로 정렬된 이미지").uncheck().run()
            next(b for b in app.button if b.label == "Compare").click().run()
            self.assertTrue(list(app.error))
            self.assertNotIn("result", app.session_state)
            self.assertFalse(list(app.exception))
