from __future__ import annotations

import zipfile
from pathlib import Path

from app.ingestion.docx_extractor import extract_docx
from app.ingestion.markdown_extractor import extract_markdown


def test_markdown_extracts_sections_and_metadata(tmp_path: Path) -> None:
    path = tmp_path / "employee_records.md"
    path.write_text(
        """---
title: Depot Employee Records
source_url: https://www.youtube.com/watch?v=sh-R7SeDCjI
video_id: sh-R7SeDCjI
---

# Depot Incharge Employee Records

This process uses **employee records** and a [search screen](https://example.com).

## Search Employees

1. Open the employee search screen.
2. Enter the employee number.

| Field | Meaning |
| --- | --- |
| Employee No | Unique identifier |

### Transfer Employee

Select the new depot and submit the transfer.
""",
        encoding="utf-8",
    )

    extraction = extract_markdown(path)

    assert extraction.page_count == 3
    assert extraction.metadata["title"] == "Depot Incharge Employee Records"
    assert extraction.metadata["source_url"].endswith("sh-R7SeDCjI")
    assert [page.layout["section_number"] for page in extraction.pages] == ["1", "1.1", "1.1.1"]
    assert extraction.pages[0].layout["heading_path"] == ["Depot Incharge Employee Records"]
    assert extraction.pages[1].layout["heading_path"] == [
        "Depot Incharge Employee Records",
        "Search Employees",
    ]
    assert "**" not in extraction.pages[0].text
    assert "| --- |" not in extraction.pages[1].text
    steps = extraction.pages[1].layout["steps"]
    assert [step["action"] for step in steps] == [
        "Open the employee search screen.",
        "Enter the employee number.",
    ]


def test_markdown_without_headings_keeps_body_as_one_section(tmp_path: Path) -> None:
    path = tmp_path / "notes.md"
    path.write_text("Plain operational notes.\n\nSecond paragraph.", encoding="utf-8")

    extraction = extract_markdown(path)

    assert extraction.page_count == 1
    assert extraction.pages[0].layout["heading"] == "Document"
    assert extraction.pages[0].text.endswith("Second paragraph.")


def test_docx_extracts_headings_paragraphs_and_tables(tmp_path: Path) -> None:
    path = tmp_path / "attendance_groups.docx"
    document_xml = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">
  <w:body>
    <w:p><w:pPr><w:pStyle w:val="Heading1"/></w:pPr><w:r><w:t>Update Attendance Groups</w:t></w:r></w:p>
    <w:p><w:r><w:t>Open the attendance group master.</w:t></w:r></w:p>
    <w:p><w:pPr><w:pStyle w:val="Heading2"/></w:pPr><w:r><w:t>Assign Employees</w:t></w:r></w:p>
    <w:tbl>
      <w:tr><w:tc><w:p><w:r><w:t>Group</w:t></w:r></w:p></w:tc><w:tc><w:p><w:r><w:t>Employee</w:t></w:r></w:p></w:tc></w:tr>
      <w:tr><w:tc><w:p><w:r><w:t>A</w:t></w:r></w:p></w:tc><w:tc><w:p><w:r><w:t>1001</w:t></w:r></w:p></w:tc></w:tr>
    </w:tbl>
  </w:body>
</w:document>
"""
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("word/document.xml", document_xml)

    extraction = extract_docx(path)

    assert extraction.page_count == 2
    assert extraction.metadata["format"] == "docx"
    assert extraction.pages[0].layout["heading"] == "Update Attendance Groups"
    assert extraction.pages[1].layout["heading"] == "Assign Employees"
    assert extraction.pages[1].layout["heading_path"] == [
        "Update Attendance Groups",
        "Assign Employees",
    ]
    assert extraction.pages[1].tables[0]["rows"] == [["Group", "Employee"], ["A", "1001"]]
