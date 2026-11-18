from __future__ import annotations

import pytest

from app.vision.parsing import (
    VisionResponseParseError,
    extract_json_object,
    parse_vision_result,
)


def test_extracts_fenced_json() -> None:
    text = '```json\n{"page_number": 4, "summary": "ok"}\n```'
    data = extract_json_object(text)
    assert data["page_number"] == 4


def test_extracts_json_surrounded_by_prose() -> None:
    text = 'Here is the result:\n{"page_number": 2, "summary": "s"}\nDone.'
    data = extract_json_object(text)
    assert data["page_number"] == 2


def test_unwraps_common_wrappers() -> None:
    text = '{"result": {"page_number": 7, "content_type": "procedure"}}'
    data = extract_json_object(text)
    assert data["page_number"] == 7


def test_parse_result_applies_defaults_without_overriding_values() -> None:
    text = '{"page_number": 3, "content_type": "overview", "summary": "x"}'
    result = parse_vision_result(
        text, defaults={"page_number": 99, "document_id": "doc-1"}
    )
    assert result.page_number == 3
    assert result.document_id == "doc-1"


def test_invalid_json_raises() -> None:
    with pytest.raises(VisionResponseParseError):
        parse_vision_result("not json at all")


def test_empty_response_raises() -> None:
    with pytest.raises(VisionResponseParseError):
        extract_json_object("   ")


def test_schema_validation_failure_is_wrapped() -> None:
    with pytest.raises(VisionResponseParseError):
        parse_vision_result('{"content_type": "procedure"}')
