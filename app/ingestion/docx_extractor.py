"""DOCX extraction using the Office Open XML package.

The project avoids heavyweight office dependencies. Paragraphs, headings and
tables are read directly from ``word/document.xml`` and grouped into logical
sections (DOCX has no fixed physical page geometry).
"""

from __future__ import annotations

import re
import zipfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from xml.etree import ElementTree

from app.ingestion.pdf_extractor import DocumentExtraction, PageExtraction
from app.utils.hashing import sha256_text

_W = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"
_HEADING_STYLE_RE = re.compile(r"(?:heading|titre)\s*([1-6])", re.IGNORECASE)
_NUMBERED_STEP_RE = re.compile(r"^\s*(\d{1,3})[.)]\s+(.+?)\s*$")
_LABEL_RE = re.compile(r"^([A-Za-z][A-Za-z0-9 /&'()-]{1,60}):\s+(.+)$")


class DocxExtractionError(RuntimeError):
    pass


@dataclass
class _Block:
    text: str
    style: str = ""
    is_table: bool = False
    rows: list[list[str]] = field(default_factory=list)


@dataclass
class _Section:
    heading: str
    level: int
    blocks: list[_Block]


def extract_docx(path: Path, *, max_sections: int | None = None) -> DocumentExtraction:
    try:
        with zipfile.ZipFile(path) as archive:
            document_xml = archive.read("word/document.xml")
            core_xml = _read_optional(archive, "docProps/core.xml")
    except (OSError, KeyError, zipfile.BadZipFile) as exc:
        raise DocxExtractionError(f"Could not read DOCX {path.name}: {exc}") from exc

    metadata = _core_metadata(core_xml)
    root = ElementTree.fromstring(document_xml)
    body = root.find(f"{_W}body")
    if body is None:
        raise DocxExtractionError(f"DOCX {path.name} has no document body")
    blocks = list(_iter_blocks(body))
    title = str(metadata.get("title") or path.stem.replace("_", " ")).strip()[:300]
    sections = _sections(blocks, title)
    if max_sections is not None:
        sections = sections[:max_sections]

    pages: list[PageExtraction] = []
    toc: list[list[Any]] = []
    counters = [0] * 7
    heading_path: list[str] = [""] * 7
    for section in sections:
        text = _section_text(section)
        if not text.strip():
            continue
        counters[section.level] += 1
        for deeper in range(section.level + 1, 7):
            counters[deeper] = 0
        section_number = ".".join(
            str(counters[level]) for level in range(1, section.level + 1)
        )
        heading_path[section.level - 1] = section.heading
        for level in range(section.level, 7):
            heading_path[level] = ""
        tables = [block.rows for block in section.blocks if block.is_table and block.rows]
        page_number = len(pages) + 1
        steps = _parse_steps(section)
        layout = {
            "format": "docx",
            "heading": section.heading,
            "heading_path": [value for value in heading_path if value],
            "level": section.level,
            "section_number": section_number,
            "tables": [{"rows": rows} for rows in tables],
            "steps": steps,
            "blocks": [
                {"text": block.text, "style": block.style}
                for block in section.blocks
                if not block.is_table
            ],
        }
        pages.append(
            PageExtraction(
                page_number=page_number,
                width_pt=0.0,
                height_pt=0.0,
                text=text,
                tables=[
                    {"rows": rows, "bbox": [0, 0, 0, 0]} for rows in tables
                ],
                char_count=len(text),
                text_hash=sha256_text(text),
                layout=layout,
            )
        )
        toc.append([section.level, section.heading, str(page_number)])

    return DocumentExtraction(
        page_count=len(pages),
        metadata={"format": "docx", "title": title, "section_count": len(pages), **metadata},
        toc=toc,
        pages=pages,
    )


def _read_optional(archive: zipfile.ZipFile, name: str) -> bytes:
    try:
        return archive.read(name)
    except KeyError:
        return b""


def _core_metadata(xml: bytes) -> dict[str, str]:
    if not xml:
        return {}
    try:
        root = ElementTree.fromstring(xml)
    except ElementTree.ParseError:
        return {}
    values: dict[str, str] = {}
    for key, tag in (("title", "title"), ("author", "creator"), ("subject", "subject")):
        element = root.find(f"{{http://purl.org/dc/elements/1.1/}}{tag}")
        if element is not None and element.text and element.text.strip():
            values[key] = element.text.strip()
    return values


def _iter_blocks(body: ElementTree.Element):
    for child in body:
        if child.tag == f"{_W}p":
            text = _paragraph_text(child).strip()
            if text:
                yield _Block(text=text, style=_paragraph_style(child))
        elif child.tag == f"{_W}tbl":
            rows = _table_rows(child)
            if rows:
                yield _Block(
                    text="\n".join(" | ".join(row) for row in rows),
                    style="Table",
                    is_table=True,
                    rows=rows,
                )


def _paragraph_text(paragraph: ElementTree.Element) -> str:
    parts: list[str] = []
    for node in paragraph.iter():
        if node.tag == f"{_W}t" and node.text:
            parts.append(node.text)
        elif node.tag == f"{_W}tab":
            parts.append("\t")
        elif node.tag in {f"{_W}br", f"{_W}cr"}:
            parts.append("\n")
    return "".join(parts)


def _paragraph_style(paragraph: ElementTree.Element) -> str:
    style = paragraph.find(f"{_W}pPr/{_W}pStyle")
    if style is None:
        return ""
    return str(style.get(f"{_W}val") or "")


def _table_rows(table: ElementTree.Element) -> list[list[str]]:
    rows: list[list[str]] = []
    for row in table.findall(f"{_W}tr"):
        cells = []
        for cell in row.findall(f"{_W}tc"):
            paragraphs = [
                _paragraph_text(paragraph).strip()
                for paragraph in cell.findall(f"{_W}p")
            ]
            cells.append(" ".join(value for value in paragraphs if value))
        if cells:
            rows.append(cells)
    return rows


def _sections(blocks: list[_Block], title: str) -> list[_Section]:
    sections: list[_Section] = []
    current = _Section(heading=title, level=1, blocks=[])
    for block in blocks:
        level = _heading_level(block.style)
        if level:
            if current.blocks or current.heading != title:
                sections.append(current)
            current = _Section(heading=block.text, level=level, blocks=[])
        else:
            current.blocks.append(block)
    if current.blocks or current.heading != title:
        sections.append(current)
    return sections


def _heading_level(style: str) -> int | None:
    match = _HEADING_STYLE_RE.search(style)
    if match:
        return max(1, min(6, int(match.group(1))))
    return None


def _parse_steps(section: _Section) -> list[dict[str, Any]]:
    """Collect procedural steps from numbered paragraphs and labelled lines."""
    steps: list[dict[str, Any]] = []
    for block in section.blocks:
        if block.is_table:
            continue
        for raw_line in block.text.splitlines():
            line = raw_line.strip()
            if not line:
                continue
            numbered = _NUMBERED_STEP_RE.match(line)
            if numbered:
                action, description = _split_step_text(numbered.group(2))
            else:
                action, description = _split_step_text(line)
                if not description:
                    continue
            if not action:
                continue
            steps.append(
                {
                    "order": len(steps) + 1,
                    "action": action,
                    "description": description,
                }
            )
    return steps


def _split_step_text(text: str) -> tuple[str, str]:
    text = text.strip()
    plain = _LABEL_RE.match(text)
    if plain:
        return plain.group(1).strip(), plain.group(2).strip()
    return text, ""


def _section_text(section: _Section) -> str:
    parts = [section.heading]
    for block in section.blocks:
        parts.append(block.text)
    return "\n\n".join(part for part in parts if part.strip()).strip()
