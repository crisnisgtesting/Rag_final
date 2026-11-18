"""PyMuPDF extraction and page rendering."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import pymupdf as fitz

from app.utils.hashing import sha256_bytes, sha256_text


class PdfExtractionError(RuntimeError):
    pass


@dataclass
class PageExtraction:
    page_number: int
    width_pt: float
    height_pt: float
    text: str
    blocks: list[dict[str, Any]] = field(default_factory=list)
    images: list[dict[str, Any]] = field(default_factory=list)
    links: list[str] = field(default_factory=list)
    tables: list[dict[str, Any]] = field(default_factory=list)
    image_count: int = 0
    image_coverage: float = 0.0
    char_count: int = 0
    text_hash: str | None = None
    layout: dict[str, Any] = field(default_factory=dict)

    @property
    def has_text(self) -> bool:
        return self.char_count > 0


@dataclass
class DocumentExtraction:
    page_count: int
    metadata: dict[str, Any]
    toc: list[list[Any]]
    pages: list[PageExtraction]


def extract_pdf(
    path: Path,
    *,
    extract_tables: bool = True,
    max_pages: int | None = None,
) -> DocumentExtraction:
    """Extract text, layout, images and tables from a PDF."""
    try:
        document = fitz.open(path)
    except Exception as exc:  # noqa: BLE001 - wrap PyMuPDF errors
        raise PdfExtractionError(f"Could not open PDF {path.name}: {exc}") from exc

    try:
        if document.needs_pass:
            raise PdfExtractionError(f"PDF {path.name} is encrypted")
        page_limit = min(document.page_count, max_pages) if max_pages else document.page_count
        pages = [
            _extract_page(document, index, extract_tables=extract_tables)
            for index in range(page_limit)
        ]
        return DocumentExtraction(
            page_count=document.page_count,
            metadata={key: value for key, value in (document.metadata or {}).items() if value},
            toc=document.get_toc(simple=True) or [],
            pages=pages,
        )
    finally:
        document.close()


def _extract_page(document: fitz.Document, index: int, *, extract_tables: bool) -> PageExtraction:
    page = document.load_page(index)
    rect = page.rect
    text = page.get_text("text", sort=True) or ""
    text = _normalize_text(text)

    blocks = [
        {
            "number": block[0],
            "bbox": [float(value) for value in block[:4]],
            "text": _normalize_text(block[4]),
        }
        for block in page.get_text("blocks", sort=True)
        if len(block) >= 5 and isinstance(block[4], str)
    ]

    images = [
        {
            "xref": info.get("xref"),
            "bbox": [float(value) for value in info.get("bbox", ())],
            "width": info.get("width"),
            "height": info.get("height"),
        }
        for info in page.get_image_info(xrefs=True)
        if info.get("bbox")
    ]
    image_coverage = _image_coverage(page, rect, images)

    links = [
        str(link.get("uri"))
        for link in page.get_links()
        if isinstance(link, dict) and link.get("uri")
    ]

    tables: list[dict[str, Any]] = []
    if extract_tables:
        try:
            found = page.find_tables()
            for table in found.tables:
                rows = table.extract()
                if not rows:
                    continue
                tables.append(
                    {
                        "bbox": [float(value) for value in table.bbox],
                        "rows": [
                            [_normalize_text(str(cell or "")) for cell in row]
                            for row in rows
                        ],
                    }
                )
        except Exception:  # noqa: BLE001 - table extraction is best effort
            tables = []

    return PageExtraction(
        page_number=index + 1,
        width_pt=float(rect.width),
        height_pt=float(rect.height),
        text=text,
        blocks=blocks,
        images=images,
        links=links,
        tables=tables,
        image_count=len(images),
        image_coverage=image_coverage,
        char_count=len(text.strip()),
        text_hash=sha256_text(text) if text.strip() else None,
    )


def render_page_png(path: Path, page_number: int, *, dpi: int) -> bytes:
    """Render one page to PNG bytes."""
    if page_number < 1:
        raise ValueError("page_number must be >= 1")
    try:
        document = fitz.open(path)
    except Exception as exc:  # noqa: BLE001
        raise PdfExtractionError(f"Could not open PDF {path.name}: {exc}") from exc
    try:
        if page_number > document.page_count:
            raise ValueError(f"Page {page_number} is outside the document")
        page = document.load_page(page_number - 1)
        pixmap = page.get_pixmap(dpi=dpi, alpha=False)
        return pixmap.tobytes("png")
    finally:
        document.close()


def image_hash(image: bytes) -> str:
    return sha256_bytes(image)


def _normalize_text(text: str) -> str:
    lines = [line.rstrip() for line in text.replace("\r\n", "\n").replace("\r", "\n").split("\n")]
    cleaned: list[str] = []
    blank = False
    for line in lines:
        if line.strip():
            cleaned.append(line)
            blank = False
        elif not blank:
            cleaned.append("")
            blank = True
    return "\n".join(cleaned).strip()


def _image_coverage(
    page: fitz.Page, rect: fitz.Rect, images: list[dict[str, Any]]
) -> float:
    if not images or rect.is_empty:
        return 0.0
    covered = 0.0
    for image in images:
        bbox = image.get("bbox") or []
        if len(bbox) != 4:
            continue
        try:
            image_rect = fitz.Rect(*bbox) & rect
        except Exception:  # noqa: BLE001
            continue
        if not image_rect.is_empty:
            covered += image_rect.get_area()
    return min(1.0, covered / rect.get_area()) if rect.get_area() else 0.0
