"""Read-only API regressions using the supplied real PDFs and PNG baselines."""
import hashlib
from pathlib import Path
import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient
import pymupdf

from backend.app import DocumentStore, create_app
from phase1_pdf import render_page

ROOT = Path(__file__).resolve().parents[1]


class Phase1APITests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.ref = (ROOT/"source_pdfs/자이사업승인도서.pdf").read_bytes()
        cls.target = (ROOT/"source_pdfs/자이실시도서.pdf").read_bytes()

    def setUp(self):
        self.app = create_app()
        self.client = TestClient(self.app)
        self.client.__enter__()

    def tearDown(self):
        self.client.__exit__(None,None,None)

    def upload(self, data=None, name="drawing.pdf"):
        return self.client.post("/api/documents", files={"file":(name, self.ref if data is None else data,"application/pdf")})

    def test_real_uploads_have_separate_ids_page_lists_and_hashes(self):
        ref, target = self.upload(), self.upload(self.target)
        self.assertEqual((ref.status_code,target.status_code),(201,201))
        a,b = ref.json(),target.json()
        self.assertNotEqual(a["id"],b["id"])
        self.assertEqual((a["page_count"],b["page_count"]),(6,6))
        self.assertEqual(a["sha256"],hashlib.sha256(self.ref).hexdigest())
        self.assertEqual(self.client.get(f"/api/documents/{a['id']}").json(),a)

    def test_same_content_reuses_identity_at_full_capacity_and_keeps_render_cache(self):
        store=DocumentStore(max_bytes=len(self.ref),max_documents=1)
        with TestClient(create_app(store)) as client:
            first=client.post("/api/documents",files={"file":("ref.pdf",self.ref)}).json()
            rendered=client.get(f"/api/documents/{first['id']}/pages/4/image?zoom=1")
            for _ in range(9):
                same=client.post("/api/documents",files={"file":("renamed.pdf",self.ref)})
                self.assertEqual(same.status_code,201)
                self.assertEqual(same.json(),first)
            listing=client.get("/api/documents").json()
            self.assertEqual(listing['usage']['documents'],1)
            self.assertEqual(listing['usage']['bytes'],len(self.ref))
            self.assertEqual(listing['documents'][0]['size_bytes'],len(self.ref))
            self.assertEqual(len(store.cache),1)
            self.assertEqual(client.get(f"/api/documents/{first['id']}/pages/4/image?zoom=1").content,rendered.content)
            self.assertEqual(client.post("/api/documents",files={"file":("new.pdf",self.target)}).status_code,507)
            self.assertEqual(client.delete(f"/api/documents/{first['id']}").status_code,204)
            self.assertEqual(client.get("/api/documents").json()['documents'],[])

    def test_actual_page4_png_is_pixel_identical_to_preserved_original(self):
        for data,role,name in [(self.ref,"Ref","자이사업승인도서"),(self.target,"Target","자이실시도서")]:
            document = self.upload(data).json()
            result = self.client.get(f"/api/documents/{document['id']}/pages/4/image?zoom=2")
            self.assertEqual(result.status_code,200)
            got = pymupdf.Pixmap(result.content)
            original = pymupdf.Pixmap(str(ROOT/f"Output/{role}/{name}_page_4_{role.lower()}.png"))
            self.assertEqual(got.samples,original.samples)
            self.assertEqual(result.headers["x-coordinate-system"],"image_native_pixels")
            self.assertEqual(result.headers["x-source-sha256"],document["sha256"])
            self.assertEqual((result.headers["x-image-width"],result.headers["x-image-height"]),("2382","1684"))

    def test_different_page_selection_and_resolution(self):
        ref,target = self.upload().json(),self.upload(self.target).json()
        for document,data,page in [(ref,self.ref,4),(target,self.target,5)]:
            response=self.client.get(f"/api/documents/{document['id']}/pages/{page}/image?zoom=1")
            self.assertEqual(response.status_code,200)
            self.assertEqual(response.headers["x-page-number"],str(page))
            self.assertEqual(pymupdf.Pixmap(response.content).samples,pymupdf.Pixmap(render_page(data,page,1).png).samples)
            self.assertEqual(response.headers["x-image-width"],"1191")

    def test_invalid_pages_zoom_and_unknown_document(self):
        document=self.upload().json();base=f"/api/documents/{document['id']}/pages"
        for suffix in ["0/image","7/image","4/image?zoom=0","4/image?zoom=nan","4/image?zoom=inf","4/image?zoom=5"]:
            self.assertEqual(self.client.get(f"{base}/{suffix}").status_code,422,suffix)
        self.assertEqual(self.client.get("/api/documents/missing/pages/1/image").status_code,404)

    def test_invalid_empty_disguised_and_encrypted_pdf(self):
        for data in [b"",b"not a PDF",render_page(self.ref,1,.5).png]:
            self.assertEqual(self.upload(data).status_code,422)
        with pymupdf.open() as document:
            document.new_page()
            data=document.tobytes(encryption=pymupdf.PDF_ENCRYPT_AES_256,owner_pw="owner",user_pw="reader")
        self.assertEqual(self.upload(data).status_code,422)

    def test_deletion_releases_document_and_image_cache(self):
        document=self.upload().json()
        self.client.get(f"/api/documents/{document['id']}/pages/4/image")
        self.assertTrue(self.app.state.documents.cache)
        self.assertEqual(self.client.delete(f"/api/documents/{document['id']}").status_code,204)
        self.assertFalse(self.app.state.documents.cache)
        self.assertEqual(self.client.get(f"/api/documents/{document['id']}").status_code,404)
        self.assertEqual(self.client.delete(f"/api/documents/{document['id']}").status_code,204)

    def test_upload_size_and_memory_capacity_fail_without_replacing_documents(self):
        with patch("backend.app.MAX_UPLOAD_BYTES",8):
            self.assertEqual(self.upload().status_code,413)
        with TestClient(create_app(DocumentStore(max_bytes=len(self.ref)))) as client:
            first=client.post("/api/documents",files={"file":("ref.pdf",self.ref)}).json()
            self.assertEqual(client.post("/api/documents",files={"file":("other.pdf",self.target)}).status_code,507)
            self.assertEqual(client.get(f"/api/documents/{first['id']}").status_code,200)

    def test_filename_is_metadata_only_and_same_name_does_not_replace_source(self):
        first=self.upload(name="../../drawing.pdf").json()
        second=self.upload(self.target,name="drawing.pdf").json()
        self.assertEqual(first["filename"],"drawing.pdf")
        self.assertNotEqual(first["id"],second["id"])
        self.assertNotEqual(first["sha256"],second["sha256"])

    def test_excessive_raster_dimensions_use_original_extraction_guard(self):
        with pymupdf.open() as document:
            document.new_page(width=6000,height=6000)
            data=document.tobytes()
        uploaded=self.upload(data).json()
        self.assertEqual(self.client.get(f"/api/documents/{uploaded['id']}/pages/1/image?zoom=2").status_code,422)


if __name__ == "__main__":
    unittest.main(verbosity=2)
