"""Read-only extraction and real upload/selection regressions for Phase 1."""
import hashlib
from pathlib import Path
import sys
import unittest

import pymupdf
from streamlit.testing.v1 import AppTest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from phase1_pdf import PDFInputError, inspect_pdf, preview_page, render_page


class PDFExtractionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.ref = (ROOT / "source_pdfs/자이사업승인도서.pdf").read_bytes()
        cls.target = (ROOT / "source_pdfs/자이실시도서.pdf").read_bytes()

    def test_real_pdf_counts_and_source_identity(self):
        for data in (self.ref, self.target):
            info = inspect_pdf(data)
            self.assertEqual(info.page_count, 6)
            self.assertEqual(info.sha256, hashlib.sha256(data).hexdigest())

    def test_real_pages_match_existing_original_pngs(self):
        for role, data, name in (("Ref", self.ref, "자이사업승인도서"),
                                  ("Target", self.target, "자이실시도서")):
            with self.subTest(role=role):
                rendered = render_page(data, 4, 2.)
                original = pymupdf.Pixmap(str(ROOT / f"Output/{role}/{name}_page_4_{role.lower()}.png"))
                extracted = pymupdf.Pixmap(rendered.png)
                self.assertEqual((rendered.width, rendered.height), (2382, 1684))
                self.assertEqual(extracted.samples, original.samples)

    def test_independent_different_page_selections_and_preview_resolution(self):
        ref, target = render_page(self.ref, 4), render_page(self.target, 5)
        self.assertEqual((ref.page_number, target.page_number), (4, 5))
        small = preview_page(self.ref, 4)
        self.assertLessEqual(max(small.width, small.height), 361)
        self.assertEqual(small.source_sha256, ref.source_sha256)
        self.assertEqual(inspect_pdf(self.ref).page_count, 6)

    def test_rotation_and_scale_are_preserved(self):
        with pymupdf.open() as document:
            page = document.new_page(width=100, height=150)
            page.set_rotation(90)
            data = document.tobytes()
        image = render_page(data, 1, 2)
        self.assertEqual((image.width, image.height), (300, 200))

    def test_invalid_pdf_page_and_scale_fail_explicitly(self):
        for data in (b"", b"not a PDF", preview_page(self.ref, 1).png):
            with self.subTest(kind=data[:8]), self.assertRaises(PDFInputError):
                inspect_pdf(data)
        for page in (0, 7, 1.5, True):
            with self.subTest(page=page), self.assertRaises(PDFInputError):
                render_page(self.ref, page)
        for zoom in (0, -1, float("nan"), float("inf"), 5, True):
            with self.subTest(zoom=zoom), self.assertRaises(PDFInputError):
                render_page(self.ref, 1, zoom)

    def test_password_protected_and_excessive_render_are_rejected(self):
        with pymupdf.open() as document:
            document.new_page()
            encrypted = document.tobytes(encryption=pymupdf.PDF_ENCRYPT_AES_256,
                                          owner_pw="owner", user_pw="reader")
        with self.assertRaisesRegex(PDFInputError, "암호"):
            inspect_pdf(encrypted)
        with pymupdf.open() as document:
            document.new_page(width=6000, height=6000)
            large = document.tobytes()
        with self.assertRaisesRegex(PDFInputError, "너무 큽니다"):
            render_page(large, 1, 2)


class Phase1UploadTests(unittest.TestCase):
    def setUp(self):
        self.ref = (ROOT / "source_pdfs/자이사업승인도서.pdf").read_bytes()
        self.target = (ROOT / "source_pdfs/자이실시도서.pdf").read_bytes()
        self.app = AppTest.from_file(str(ROOT / "phase1_app.py"), default_timeout=30).run()

    def upload_pair(self):
        self.app.file_uploader[0].set_value(("사업승인도.pdf", self.ref, "application/pdf"))
        self.app.file_uploader[1].set_value(("실시설계도.pdf", self.target, "application/pdf"))
        self.app.run()

    def select(self, label, page):
        next(s for s in self.app.selectbox if s.label == label).select(page).run()

    def test_actual_uploads_independent_selection_and_pair_display(self):
        self.assertTrue(self.app.button[0].disabled)
        self.upload_pair()
        self.assertFalse(self.app.exception)
        self.assertFalse(self.app.button[0].disabled)
        for role in ("Ref", "Target"):
            picker = next(s for s in self.app.selectbox if s.label == f"{role} 페이지")
            self.assertEqual(len(picker.options), 6)
        self.select("Ref 페이지", 4)
        self.select("Target 페이지", 5)
        self.app.button[0].click().run()
        self.assertFalse(self.app.exception)
        result = self.app.session_state["phase1_result"]
        self.assertEqual([p["image"].page_number for p in result["pages"]], [4, 5])
        self.assertEqual([p["image"].source_sha256 for p in result["pages"]],
                         [hashlib.sha256(self.ref).hexdigest(), hashlib.sha256(self.target).hexdigest()])
        self.assertEqual(len(self.app.get("download_button")), 2)
        # A page change must invalidate the displayed pair until extracted again.
        self.select("Target 페이지", 6)
        self.assertNotIn("phase1_result", self.app.session_state)
        self.app.button[0].click().run()
        self.assertEqual(self.app.session_state["phase1_result"]["pages"][0]["image"].page_number, 4)
        self.assertEqual(self.app.session_state["phase1_result"]["pages"][1]["image"].page_number, 6)

    def test_removing_replacing_or_invalid_upload_clears_previous_result(self):
        self.upload_pair()
        self.select("Ref 페이지", 4)
        self.app.button[0].click().run()
        self.app.file_uploader[0].clear().run()
        self.assertNotIn("phase1_result", self.app.session_state)
        self.assertTrue(self.app.button[0].disabled)
        self.app.file_uploader[0].set_value(("사업승인도.pdf", self.target, "application/pdf")).run()
        picker = next(s for s in self.app.selectbox if s.label == "Ref 페이지")
        self.assertEqual(picker.value, 1)  # Same name, changed bytes: reset selection.
        self.app.button[0].click().run()
        self.assertEqual(self.app.session_state["phase1_result"]["pages"][0]["image"].source_sha256,
                         hashlib.sha256(self.target).hexdigest())
        self.app.file_uploader[1].set_value(("bad.pdf", b"not pdf", "application/pdf")).run()
        self.assertFalse(self.app.exception)
        self.assertTrue(self.app.error)
        self.assertNotIn("phase1_result", self.app.session_state)
        self.assertTrue(self.app.button[0].disabled)

    def test_zoom_change_reextracts_pair_at_selected_resolution(self):
        self.upload_pair()
        self.app.button[0].click().run()
        self.app.select_slider[0].set_value(1.).run()
        self.assertNotIn("phase1_result", self.app.session_state)
        self.app.button[0].click().run()
        images = [p["image"] for p in self.app.session_state["phase1_result"]["pages"]]
        self.assertTrue(all((im.width, im.height) == (1191, 842) for im in images))
        self.assertFalse(self.app.exception)


if __name__ == "__main__":
    unittest.main(verbosity=2)
