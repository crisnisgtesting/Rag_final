"""Robust parsing of provider JSON responses into VisionPageResult."""

from __future__ import annotations

import json
import re
from typing import Any

from app.vision.schemas import VisionPageResult

_FENCE_RE = re.compile(r"```(?:json)?\s*(.*?)```", re.DOTALL | re.IGNORECASE)


class VisionResponseParseError(ValueError):
    pass


def extract_json_object(text: str) -> dict[str, Any]:
    if not text or not text.strip():
        raise VisionResponseParseError("Provider returned an empty response")

    candidate = text.strip()
    match = _FENCE_RE.search(candidate)
    if match:
        candidate = match.group(1).strip()

    if not candidate.startswith("{"):
        start = candidate.find("{")
        end = candidate.rfind("}")
        if start == -1 or end == -1 or end <= start:
            raise VisionResponseParseError("No JSON object found in provider response")
        candidate = candidate[start : end + 1]

    try:
        data = json.loads(candidate)
    except json.JSONDecodeError as exc:
        raise VisionResponseParseError(f"Invalid JSON from provider: {exc}") from exc

    if not isinstance(data, dict):
        raise VisionResponseParseError("Provider JSON is not an object")

    for wrapper in ("result", "output", "data"):
        nested = data.get(wrapper)
        if isinstance(nested, dict) and (
            "content_type" in nested or "workflow_steps" in nested
        ):
            return nested
    return data


def parse_vision_result(
    text: str,
    *,
    defaults: dict[str, Any] | None = None,
) -> VisionPageResult:
    data = extract_json_object(text)
    defaults = defaults or {}
    for key, value in defaults.items():
        if data.get(key) is None:
            data[key] = value
    try:
        return VisionPageResult.model_validate(data)
    except Exception as exc:  # pydantic ValidationError
        raise VisionResponseParseError(f"Provider JSON failed validation: {exc}") from exc
