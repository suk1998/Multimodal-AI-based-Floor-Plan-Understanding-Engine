"""Independent, read-only PDF inspection and page extraction for Phase 1.

Reuses the PyMuPDF Matrix/get_pixmap approach in pdf_2_images.py without
importing its directory-creation side effects or the missing comparison engine.
Page numbers at this module's boundary are always 1-based. PNG bytes are RGB.
"""
from contextlib import contextmanager
from dataclasses import dataclass
import hashlib
import math

import pymupdf


MAX_RENDER_PIXELS = 40_000_000


class PDFInputError(ValueError):
    """A PDF or selection that cannot be displayed to the user."""


@dataclass(frozen=True)
class PDFInfo:
    sha256: str
    page_count: int


@dataclass(frozen=True)
class PageImage:
    png: bytes
    width: int
    height: int
    page_number: int
    zoom: float
    source_sha256: str


@contextmanager
def _document(data: bytes):
    if not isinstance(data, bytes) or not data:
        raise PDFInputError("비어 있는 파일입니다. PDF를 다시 선택하세요.")
    try:
        with pymupdf.open(stream=data, filetype="pdf") as document:
            if not document.is_pdf:
                raise PDFInputError("PDF 형식의 파일을 선택하세요.")
            if document.needs_pass:
                raise PDFInputError("암호가 필요한 PDF입니다. 암호를 해제한 파일을 선택하세요.")
            if document.page_count < 1:
                raise PDFInputError("페이지가 없는 PDF입니다.")
            yield document
    except RuntimeError as exc:
        raise PDFInputError("PDF를 읽거나 이미지를 추출할 수 없습니다. 파일 상태를 확인하세요.") from exc


def inspect_pdf(data: bytes) -> PDFInfo:
    with _document(data) as document:
        return PDFInfo(hashlib.sha256(data).hexdigest(), document.page_count)


def _page(document, page_number):
    if type(page_number) is not int or not 1 <= page_number <= document.page_count:
        raise PDFInputError(f"페이지는 1~{document.page_count} 사이의 정수로 선택하세요.")
    return document.load_page(page_number - 1)


def _render(page, data, page_number, zoom):
    if isinstance(zoom, bool) or not isinstance(zoom, (int, float)) or not math.isfinite(zoom) or not 0 < zoom <= 4:
        raise PDFInputError("추출 배율은 0 초과 4 이하의 유한한 숫자여야 합니다.")
    width, height = page.rect.width * zoom, page.rect.height * zoom
    if not math.isfinite(width + height) or width <= 0 or height <= 0:
        raise PDFInputError("유효한 페이지 크기를 확인할 수 없습니다.")
    if math.ceil(width) * math.ceil(height) > MAX_RENDER_PIXELS:
        raise PDFInputError("추출 이미지가 너무 큽니다. PDF 배율을 낮춰 다시 시도하세요.")
    pixmap = page.get_pixmap(matrix=pymupdf.Matrix(zoom, zoom),
                             colorspace=pymupdf.csRGB, alpha=False)
    return PageImage(pixmap.tobytes("png"), pixmap.width, pixmap.height,
                     page_number, float(zoom), hashlib.sha256(data).hexdigest())


def render_page(data: bytes, page_number: int, zoom: float = 2.0) -> PageImage:
    """Extract one native page raster; never align, crop, or write input files."""
    with _document(data) as document:
        return _render(_page(document, page_number), data, page_number, zoom)


def preview_page(data: bytes, page_number: int) -> PageImage:
    """Small page-list preview, separate from the selected extraction resolution."""
    with _document(data) as document:
        page = _page(document, page_number)
        zoom = min(1.0, 360.0 / max(page.rect.width, page.rect.height, 1.0))
        return _render(page, data, page_number, zoom)
