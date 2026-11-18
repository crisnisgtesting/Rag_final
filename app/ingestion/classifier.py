"""Heuristics for selective vision analysis of PDF pages."""

from __future__ import annotations

from dataclasses import dataclass

from app.config import Settings
from app.ingestion.pdf_extractor import PageExtraction


@dataclass(frozen=True)
class PageClassification:
    needs_vision: bool
    reasons: tuple[str, ...]
    text_source: str


def classify_page(
    page: PageExtraction, settings: Settings, *, mode: str | None = None
) -> PageClassification:
    mode = mode or settings.ingestion_vision_mode
    if mode == "none":
        return PageClassification(
            needs_vision=False,
            reasons=(),
            text_source=_text_source(page),
        )
    if mode == "all":
        return PageClassification(
            needs_vision=True,
            reasons=("vision mode is 'all'",),
            text_source=_text_source(page),
        )

    reasons: list[str] = []
    if page.char_count < settings.ingestion_vision_low_text_chars and page.image_count:
        reasons.append("low digital text with images")
    if page.image_coverage >= settings.ingestion_vision_min_image_coverage:
        reasons.append("image coverage above threshold")
    if page.tables and (page.image_count or page.char_count < settings.ingestion_vision_low_text_chars):
        reasons.append("table on an image-heavy or low-text page")
    if not page.char_count and page.image_count:
        reasons.append("scanned or image-only page")
    if not page.char_count and not page.image_count:
        reasons.append("page has no extractable text (vector-only or blank)")

    return PageClassification(
        needs_vision=bool(reasons),
        reasons=tuple(reasons),
        text_source=_text_source(page),
    )


def _text_source(page: PageExtraction) -> str:
    if page.char_count == 0:
        return "scanned" if page.image_count else "empty"
    return "digital"
