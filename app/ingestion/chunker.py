"""Structure-aware chunk construction for retrieval."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from app.config import Settings
from app.ingestion.pdf_extractor import PageExtraction
from app.utils.hashing import sha256_text

_SECTION_RE = re.compile(r"^\s*(\d+(?:\.\d+)*)[.)]?\s+")


@dataclass
class PageKnowledge:
    page: PageExtraction
    analysis: dict[str, Any] | None = None


@dataclass
class ChunkDraft:
    page_start: int
    page_end: int
    section_number: str | None
    heading: str | None
    content_type: str
    content: str
    metadata: dict[str, Any] = field(default_factory=dict)

    @property
    def content_hash(self) -> str:
        return sha256_text(self.content)

    @property
    def char_count(self) -> int:
        return len(self.content)

    @property
    def token_estimate(self) -> int:
        return max(1, self.char_count // 4)


def build_chunks(pages: list[PageKnowledge], settings: Settings) -> list[ChunkDraft]:
    chunks: list[ChunkDraft] = []
    seen: set[str] = set()
    for item in sorted(pages, key=lambda value: value.page.page_number):
        page = item.page
        analysis = item.analysis or {}
        body = _page_content(page, analysis)
        if not body.strip():
            continue

        heading = _heading(page, analysis)
        section_number = _section_number(page, analysis, heading)
        content_type = str(
            analysis.get("content_type")
            or page.layout.get("content_type")
            or "unknown"
        )
        prefix_parts = [part for part in (section_number, heading) if part]
        prefix = " - ".join(prefix_parts)
        if prefix:
            body = f"{prefix}\n{body}"

        metadata = {
            "page": page.page_number,
            "heading": heading,
            "section_number": section_number,
            "text_source": _text_source(page, analysis),
            "image_count": page.image_count,
            "image_coverage": page.image_coverage,
            "table_count": len(page.tables),
            "confidence": analysis.get("confidence"),
            "needs_review": analysis.get("needs_review"),
            "vision_provider": analysis.get("_provider"),
            "vision_model": analysis.get("_model"),
            "format": page.layout.get("format"),
            "heading_path": page.layout.get("heading_path"),
            "source_url": page.layout.get("source_url"),
            "video_id": page.layout.get("video_id"),
        }
        for content in _split_text(
            body,
            max_chars=settings.ingestion_chunk_max_chars,
            overlap_chars=settings.ingestion_chunk_overlap_chars,
        ):
            content = content.strip()
            if not content:
                continue
            digest = sha256_text(content)
            if digest in seen:
                continue
            seen.add(digest)
            chunks.append(
                ChunkDraft(
                    page_start=page.page_number,
                    page_end=page.page_number,
                    section_number=section_number,
                    heading=heading,
                    content_type=content_type,
                    content=content,
                    metadata=metadata,
                )
            )
    return chunks


def _page_content(page: PageExtraction, analysis: dict[str, Any]) -> str:
    parts: list[str] = []
    if page.text.strip():
        parts.append(page.text.strip())

    summary = str(analysis.get("summary") or "").strip()
    if summary:
        parts.append(f"Page summary:\n{summary}")

    steps = analysis.get("workflow_steps") or []
    if steps:
        step_lines = ["Procedure:"]
        for step in steps:
            if not isinstance(step, dict):
                continue
            number = step.get("step_number") or len(step_lines)
            action = str(step.get("action") or "").strip()
            element = str(step.get("ui_element") or "").strip()
            description = str(step.get("description") or "").strip()
            line = f"{number}. {action}"
            if element:
                line += f" [{element}]"
            if description:
                line += f": {description}"
            step_lines.append(line)
            for key in ("conditions", "warnings", "exceptions"):
                values = step.get(key) or []
                if values:
                    step_lines.append(f"   {key}: " + "; ".join(str(value) for value in values))
        parts.append("\n".join(step_lines))

    knowledge = analysis.get("domain_knowledge") or []
    if knowledge:
        lines = ["Domain knowledge:"]
        for item in knowledge:
            if not isinstance(item, dict):
                continue
            name = str(item.get("name") or "").strip()
            description = str(item.get("description") or "").strip()
            if name or description:
                lines.append(f"- {name}: {description}".strip())
        if len(lines) > 1:
            parts.append("\n".join(lines))

    for key, title in (
        ("prerequisites", "Prerequisites"),
        ("business_rules", "Business rules"),
        ("validations", "Validations"),
        ("exceptions", "Exceptions"),
        ("warnings", "Warnings"),
    ):
        values = analysis.get(key) or []
        if values:
            parts.append(f"{title}:\n" + "\n".join(f"- {value}" for value in values))

    labels = [
        str(element.get("label") or "").strip()
        for element in (analysis.get("visual_elements") or [])
        if isinstance(element, dict) and str(element.get("label") or "").strip()
    ]
    if labels:
        parts.append("Visual elements: " + ", ".join(dict.fromkeys(labels)))

    for index, table in enumerate(page.tables, start=1):
        rows = table.get("rows") or []
        if not rows:
            continue
        table_lines = [f"Table {index}:"]
        table_lines.extend(" | ".join(str(cell) for cell in row) for row in rows)
        parts.append("\n".join(table_lines))

    return "\n\n".join(part for part in parts if part).strip()


def _heading(page: PageExtraction, analysis: dict[str, Any]) -> str | None:
    if page.layout.get("heading"):
        return str(page.layout["heading"]).strip()[:200] or None
    headings = analysis.get("headings") or []
    if headings:
        heading = str(headings[0]).strip()
        if heading:
            return heading[:200]
    for line in page.text.splitlines()[:10]:
        candidate = line.strip()
        if 3 <= len(candidate) <= 160 and not candidate.endswith((".", ";")):
            return candidate
    return None


def _section_number(
    page: PageExtraction, analysis: dict[str, Any], heading: str | None
) -> str | None:
    value = analysis.get("section_number") or page.layout.get("section_number")
    if value:
        return str(value).strip()
    for line in page.text.splitlines()[:10]:
        match = _SECTION_RE.match(line)
        if match:
            return match.group(1)
    if heading:
        match = _SECTION_RE.match(heading)
        if match:
            return match.group(1)
    return None


def _text_source(page: PageExtraction, analysis: dict[str, Any]) -> str:
    if page.layout.get("format"):
        return str(page.layout["format"])
    if page.char_count:
        return "digital"
    if analysis:
        return "vision"
    return "scanned" if page.image_count else "empty"


def _split_text(text: str, *, max_chars: int, overlap_chars: int) -> list[str]:
    text = re.sub(r"[ \t]+", " ", text.replace("\r\n", "\n").replace("\r", "\n")).strip()
    if not text:
        return []
    if len(text) <= max_chars:
        return [text]

    max_chars = max(400, max_chars)
    overlap_chars = max(0, min(overlap_chars, max_chars // 3))
    paragraphs = re.split(r"\n{2,}", text)
    chunks: list[str] = []
    current = ""

    def flush() -> None:
        nonlocal current
        if current.strip():
            chunks.append(current.strip())
        current = ""

    for paragraph in paragraphs:
        paragraph = paragraph.strip()
        if not paragraph:
            continue
        if len(paragraph) > max_chars:
            flush()
            pieces = _split_long(paragraph, max_chars=max_chars, overlap_chars=overlap_chars)
            chunks.extend(piece for piece in pieces if piece.strip())
            continue
        candidate = f"{current}\n\n{paragraph}".strip() if current else paragraph
        if len(candidate) <= max_chars:
            current = candidate
        else:
            previous = current
            flush()
            tail = previous[-overlap_chars:] if overlap_chars and previous else ""
            current = f"{tail}\n\n{paragraph}".strip() if tail else paragraph
    flush()
    return chunks


def _split_long(text: str, *, max_chars: int, overlap_chars: int) -> list[str]:
    sentences = re.split(r"(?<=[.!?])\s+", text)
    pieces: list[str] = []
    current = ""
    for sentence in sentences:
        sentence = sentence.strip()
        if not sentence:
            continue
        if len(sentence) > max_chars:
            if current:
                pieces.append(current.strip())
                current = ""
            start = 0
            while start < len(sentence):
                end = min(start + max_chars, len(sentence))
                pieces.append(sentence[start:end].strip())
                if end == len(sentence):
                    break
                start = max(end - overlap_chars, start + 1)
            continue
        candidate = f"{current} {sentence}".strip() if current else sentence
        if len(candidate) <= max_chars:
            current = candidate
        else:
            pieces.append(current.strip())
            tail = current[-overlap_chars:] if overlap_chars else ""
            current = f"{tail} {sentence}".strip() if tail else sentence
    if current.strip():
        pieces.append(current.strip())
    return pieces
