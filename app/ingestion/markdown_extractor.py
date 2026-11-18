"""Markdown and plain-text extraction into logical document sections.

Markdown has no physical pages. Each heading starts a logical section/page so
that the rest of the ingestion pipeline can use the same page and chunk storage
as PDFs while preserving the original heading hierarchy.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from app.ingestion.pdf_extractor import DocumentExtraction, PageExtraction
from app.utils.hashing import sha256_text

_HEADING_RE = re.compile(r"^(#{1,6})\s+(.+?)\s*#*\s*$")
_FRONT_MATTER_RE = re.compile(r"\A---\s*\n(.*?)\n---\s*\n", re.DOTALL)
_LINK_RE = re.compile(r"!?\[([^\]]*)\]\(([^)]+)\)")
_INLINE_MARKUP_RE = re.compile(r"(\*\*|__|`|~~)")
_TABLE_RULE_RE = re.compile(r"^\s*\|?[\s:|-]+\|[\s:|-]*$")
_NUMBERED_STEP_RE = re.compile(r"^\s*(\d{1,3})[.)]\s+(.+?)\s*$")
_BULLET_STEP_RE = re.compile(r"^(\s*)[-*+]\s+(.+?)\s*$")
_BOLD_LABEL_RE = re.compile(r"^\*\*([^*:]{2,80}):\*\*\s*(.*)$")
_PLAIN_LABEL_RE = re.compile(r"^([A-Za-z][A-Za-z0-9 /&'()-]{1,60}):\s+(.+)$")


class MarkdownExtractionError(RuntimeError):
    pass


@dataclass
class _Section:
    heading: str
    level: int
    lines: list[str]
    line_start: int
    line_end: int


def extract_markdown(path: Path, *, max_sections: int | None = None) -> DocumentExtraction:
    try:
        raw = path.read_text(encoding="utf-8-sig")
    except UnicodeDecodeError:
        raw = path.read_text(encoding="utf-8", errors="replace")
    except OSError as exc:
        raise MarkdownExtractionError(f"Could not read Markdown {path.name}: {exc}") from exc

    raw = raw.replace("\r\n", "\n").replace("\r", "\n")
    front_matter, body = _split_front_matter(raw)
    lines = body.split("\n")
    front_matter.update(_metadata_lines(lines))
    title = _title(lines, front_matter, path)
    sections = _sections(lines)
    if max_sections is not None:
        sections = sections[:max_sections]

    pages: list[PageExtraction] = []
    toc: list[list[Any]] = []
    counters = [0] * 7
    heading_path: list[str] = [""] * 7
    for section in sections:
        text = _clean_markdown("\n".join([section.heading, *section.lines])).strip()
        if not text:
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
        page_number = len(pages) + 1
        steps = _parse_steps(section.lines)
        layout = {
            "format": "markdown",
            "heading": section.heading,
            "heading_path": [value for value in heading_path if value],
            "level": section.level,
            "section_number": section_number,
            "line_start": section.line_start,
            "line_end": section.line_end,
            "source_url": front_matter.get("source_url"),
            "video_id": front_matter.get("video_id"),
            "steps": steps,
        }
        pages.append(
            PageExtraction(
                page_number=page_number,
                width_pt=0.0,
                height_pt=0.0,
                text=text,
                char_count=len(text),
                text_hash=sha256_text(text),
                layout=layout,
            )
        )
        toc.append([section.level, section.heading, str(page_number)])

    metadata = {
        **front_matter,
        "format": "markdown",
        "title": title,
        "section_count": len(pages),
    }
    return DocumentExtraction(
        page_count=len(pages),
        metadata=metadata,
        toc=toc,
        pages=pages,
    )


def _parse_steps(lines: list[str]) -> list[dict[str, Any]]:
    """Extract procedural steps from a section body.

    Numbered items and bold-labelled bullets are treated as steps. Nested
    bullets are kept as additional detail on the step they belong to.
    """
    steps: list[dict[str, Any]] = []
    for raw in lines:
        line = raw.strip()
        if not line:
            continue
        numbered = _NUMBERED_STEP_RE.match(line)
        if numbered:
            action, description = _split_step_text(numbered.group(2))
            steps.append(
                {
                    "order": len(steps) + 1,
                    "action": action,
                    "description": description,
                }
            )
            continue
        bullet = _BULLET_STEP_RE.match(raw)
        if bullet:
            indent = len(bullet.group(1).expandtabs(4))
            text = bullet.group(2).strip()
            if indent >= 2 and steps:
                detail = text
                previous = steps[-1]
                previous["description"] = (
                    f"{previous['description']} {detail}".strip()
                    if previous["description"]
                    else detail
                )
                continue
            action, description = _split_step_text(text)
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
    bold = _BOLD_LABEL_RE.match(text)
    if bold:
        return bold.group(1).strip(), bold.group(2).strip()
    plain = _PLAIN_LABEL_RE.match(text)
    if plain:
        return plain.group(1).strip(), plain.group(2).strip()
    return text, ""


def _split_front_matter(raw: str) -> tuple[dict[str, str], str]:
    match = _FRONT_MATTER_RE.match(raw)
    if not match:
        return {}, raw
    values: dict[str, str] = {}
    for line in match.group(1).splitlines():
        if ":" not in line:
            continue
        key, value = line.split(":", 1)
        key = key.strip().lower().replace(" ", "_")
        value = value.strip().strip("\"'")
        if key and value:
            values[key] = value
    return values, raw[match.end() :]


def _metadata_lines(lines: list[str]) -> dict[str, str]:
    values: dict[str, str] = {}
    patterns = {
        "source_url": re.compile(r"^\s*(?:source\s*url|url)\s*:\s*(\S+)\s*$", re.IGNORECASE),
        "video_id": re.compile(r"^\s*video\s*id\s*:\s*(\S+)\s*$", re.IGNORECASE),
    }
    for line in lines[:40]:
        for key, pattern in patterns.items():
            match = pattern.match(line)
            if match:
                values[key] = match.group(1).strip().strip("`<>")
    return values


def _title(lines: list[str], front_matter: dict[str, str], path: Path) -> str:
    for line in lines:
        match = _HEADING_RE.match(line)
        if match and len(match.group(1)) == 1:
            return match.group(2).strip()[:300]
    for key in ("title", "source_url", "video_id"):
        if front_matter.get(key) and key == "title":
            return str(front_matter[key])[:300]
    return path.stem.replace("_", " ").replace("-", " ").strip()[:300] or path.name


def _sections(lines: list[str]) -> list[_Section]:
    sections: list[_Section] = []
    current_heading = "Document"
    current_level = 1
    current_start = 1
    buffer: list[str] = []

    def flush(end_line: int) -> None:
        if buffer or current_heading != "Document":
            sections.append(
                _Section(
                    heading=current_heading,
                    level=current_level,
                    lines=buffer.copy(),
                    line_start=current_start,
                    line_end=end_line,
                )
            )
        buffer.clear()

    for index, line in enumerate(lines, start=1):
        match = _HEADING_RE.match(line)
        if match:
            flush(index - 1)
            current_heading = match.group(2).strip()
            current_level = len(match.group(1))
            current_start = index
            continue
        buffer.append(line)
    flush(len(lines))

    cleaned: list[_Section] = []
    for section in sections:
        body = "\n".join(section.lines).strip()
        if section.heading == "Document" and not body:
            continue
        cleaned.append(section)
    return cleaned


def _clean_markdown(text: str) -> str:
    output: list[str] = []
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped or _TABLE_RULE_RE.match(stripped):
            if stripped or (output and output[-1] != ""):
                output.append("")
            continue
        heading = _HEADING_RE.match(stripped)
        if heading:
            output.append(heading.group(2).strip())
            continue
        stripped = re.sub(r"^\s{0,3}>\s?", "", stripped)
        stripped = re.sub(r"^\s*[-*+]\s+", "- ", stripped)
        stripped = re.sub(r"^\s*\d+[.)]\s+", lambda m: m.group(0).strip() + " ", stripped)
        stripped = _LINK_RE.sub(lambda m: m.group(1) or m.group(2), stripped)
        stripped = _INLINE_MARKUP_RE.sub("", stripped)
        stripped = stripped.replace("\\", "")
        if "|" in stripped and stripped.startswith("|"):
            cells = [cell.strip() for cell in stripped.strip("|").split("|")]
            stripped = " | ".join(cells)
        output.append(stripped.rstrip())
    return re.sub(r"\n{3,}", "\n\n", "\n".join(output)).strip()
