"""Phase 1 PDF API. Run from repository root: uvicorn backend.app:app.

PDF work intentionally runs synchronously inside async routes on the single
ASGI event-loop thread: PyMuPDF calls must not run concurrently on worker threads.
This local single-worker Phase 1 service has no detection or persistence layer.
"""
from collections import OrderedDict
from dataclasses import dataclass
from pathlib import Path
from typing import Annotated
from urllib.parse import quote
from uuid import uuid4

from fastapi import FastAPI, File, HTTPException, Query, UploadFile
from fastapi.responses import Response

from phase1_pdf import PDFInfo, PDFInputError, PageImage, inspect_pdf, render_page


MAX_UPLOAD_BYTES = 50 * 1024 * 1024


@dataclass(frozen=True)
class Document:
    id: str
    filename: str
    data: bytes
    info: PDFInfo

    def metadata(self):
        return {"id": self.id, "filename": self.filename, "sha256": self.info.sha256,
                "page_count": self.info.page_count, "page_numbering": "1-based"}


class DocumentStore:
    def __init__(self, max_bytes=200 * 1024 * 1024, max_documents=32):
        self.documents: dict[str, Document] = {}
        self.max_bytes, self.max_documents = max_bytes, max_documents
        self.cache: OrderedDict[tuple[str, int, float], PageImage] = OrderedDict()
        self.cache_bytes = 0

    def add(self, filename, data, info):
        # Reuse original identity even when a differently named copy is uploaded at capacity.
        existing = next((d for d in self.documents.values() if d.info.sha256 == info.sha256), None)
        if existing is not None:
            return existing
        if (len(self.documents) >= self.max_documents or
                sum(len(d.data) for d in self.documents.values()) + len(data) > self.max_bytes):
            raise HTTPException(507, "PDF 저장 공간이 가득 찼습니다. 사용하지 않는 PDF를 제거하세요.")
        document = Document(uuid4().hex, Path(filename.replace("\\", "/")).name or "drawing.pdf", data, info)
        self.documents[document.id] = document
        return document

    def get(self, document_id):
        document = self.documents.get(document_id)
        if document is None:
            raise HTTPException(404, "PDF를 찾을 수 없습니다. 서버가 재시작됐다면 다시 업로드하세요.")
        return document

    def remove(self, document_id):
        self.documents.pop(document_id, None)
        for key in list(self.cache):
            if key[0] == document_id:
                self.cache_bytes -= len(self.cache.pop(key).png)

    def page(self, document, number, zoom):
        key = (document.id, number, zoom)
        if key in self.cache:
            self.cache.move_to_end(key)
            return self.cache[key]
        image = render_page(document.data, number, zoom)
        while self.cache and (len(self.cache) >= 12 or self.cache_bytes + len(image.png) > 64*1024*1024):
            _, previous = self.cache.popitem(last=False)
            self.cache_bytes -= len(previous.png)
        if len(image.png) <= 64*1024*1024:
            self.cache[key] = image
            self.cache_bytes += len(image.png)
        return image


def create_app(store: DocumentStore | None = None):
    app = FastAPI(title="Floorplan Phase 1 PDF API", version="1.0")
    documents = store if store is not None else DocumentStore()
    app.state.documents = documents

    @app.get("/api/health")
    async def health():
        return {"status": "ok", "scope": "phase1_pdf_viewer"}

    @app.get("/api/documents")
    async def list_documents():
        return {
            "documents": [{**d.metadata(), "size_bytes": len(d.data)} for d in documents.documents.values()],
            "usage": {"documents": len(documents.documents), "max_documents": documents.max_documents,
                      "bytes": sum(len(d.data) for d in documents.documents.values()),
                      "max_bytes": documents.max_bytes},
        }

    @app.post("/api/documents", status_code=201)
    async def upload(file: Annotated[UploadFile, File()]):
        try:
            data = await file.read(MAX_UPLOAD_BYTES + 1)
            if len(data) > MAX_UPLOAD_BYTES:
                raise HTTPException(413, "PDF는 50MiB 이하로 업로드하세요.")
            # Do not move these shared PyMuPDF calls into FastAPI's thread pool.
            info = inspect_pdf(data)
            return documents.add(file.filename or "drawing.pdf", data, info).metadata()
        except PDFInputError as exc:
            raise HTTPException(422, str(exc)) from exc
        finally:
            await file.close()

    @app.get("/api/documents/{document_id}")
    async def metadata(document_id: str):
        return documents.get(document_id).metadata()

    @app.delete("/api/documents/{document_id}", status_code=204)
    async def remove(document_id: str):
        documents.remove(document_id)
        return Response(status_code=204)

    @app.get("/api/documents/{document_id}/pages/{page_number}/image")
    async def image(document_id: str, page_number: int,
                    zoom: Annotated[float, Query(gt=0, le=4, allow_inf_nan=False)] = 2.0):
        document = documents.get(document_id)
        try:
            result = documents.page(document, page_number, zoom)
        except PDFInputError as exc:
            raise HTTPException(422, str(exc)) from exc
        filename = f"{Path(document.filename).stem}_page_{page_number}_{zoom:g}x.png"
        identity = f"{result.source_sha256}:{page_number}:{zoom:g}:{result.width}x{result.height}"
        return Response(result.png, media_type="image/png", headers={
            "X-Image-Width": str(result.width), "X-Image-Height": str(result.height),
            "X-Page-Number": str(page_number), "X-Source-SHA256": result.source_sha256,
            "X-Image-Id": identity, "X-Coordinate-System": "image_native_pixels",
            "Cache-Control": "no-store", "Content-Disposition": f"inline; filename*=UTF-8''{quote(filename)}",
        })

    from backend.alignment_api import register_alignment_routes
    register_alignment_routes(app, documents)
    from backend.structural_anchor_api import register_structural_anchor_routes
    register_structural_anchor_routes(app, documents)
    return app


app = create_app()
