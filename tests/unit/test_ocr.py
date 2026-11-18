from __future__ import annotations

from pathlib import Path

import pytest

from app.ingestion.ocr import (
    OcrConfigurationError,
    OcrError,
    OcrResult,
    available_engines,
    build_engine,
    ocr_page_image,
)


class FakeEngine:
    name = "fake"

    def __init__(self, result: OcrResult | Exception) -> None:
        self._result = result

    def read_image(self, image_path: Path) -> OcrResult:
        if isinstance(self._result, Exception):
            raise self._result
        return self._result


async def test_ocr_reads_image_through_engine(tmp_path: Path) -> None:
    image = tmp_path / "page.png"
    image.write_bytes(b"not-a-real-png")
    engine = FakeEngine(OcrResult(text="depot incharge", confidence=0.9, engine="fake"))

    result = await ocr_page_image(engine, image)

    assert result.text == "depot incharge"
    assert result.engine == "fake"


async def test_ocr_wraps_engine_failure(tmp_path: Path) -> None:
    image = tmp_path / "page.png"
    image.write_bytes(b"not-a-real-png")
    engine = FakeEngine(RuntimeError("engine exploded"))

    with pytest.raises(OcrError):
        await ocr_page_image(engine, image)


def test_unsupported_backend_is_rejected() -> None:
    with pytest.raises(OcrConfigurationError):
        build_engine(backend="tesseract")


def test_auto_backend_reports_missing_engine(monkeypatch: pytest.MonkeyPatch) -> None:
    import app.ingestion.ocr as ocr

    monkeypatch.setattr(ocr, "available_engines", lambda: [])
    with pytest.raises(OcrConfigurationError):
        build_engine(backend="auto")


def test_available_engines_is_a_list() -> None:
    assert isinstance(available_engines(), list)
