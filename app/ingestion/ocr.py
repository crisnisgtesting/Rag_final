"""Optional OCR for pages without extractable text.

Scanned pages have no digital text, so the vision provider is the primary
understanding path. OCR is an optional supplement that recovers the machine
readable text of a scanned page so that lexical retrieval and chunking can use
it.

The OCR engine is optional and detected at runtime. Nothing here is a stub: if
an engine is installed it is used, and if none is installed the caller receives
a clear configuration error instead of a silent no-op.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol

from app.logging_conf import get_logger

logger = get_logger(__name__)


class OcrConfigurationError(RuntimeError):
    """No usable OCR engine is installed or configured."""


class OcrError(RuntimeError):
    """The OCR engine failed while reading a page."""


@dataclass(frozen=True)
class OcrResult:
    text: str
    confidence: float
    engine: str


class OcrEngine(Protocol):
    name: str

    def read_image(self, image_path: Path) -> OcrResult: ...


class PaddleOcrEngine:
    """PaddleOCR engine. Requires the optional `paddleocr` package."""

    name = "paddleocr"

    def __init__(self, *, lang: str = "en", use_gpu: bool = False) -> None:
        try:
            from paddleocr import PaddleOCR  # noqa: PLC0415 - optional dependency
        except ImportError as exc:  # pragma: no cover - dependency error path
            raise OcrConfigurationError(
                "PaddleOCR is not installed. Install it with "
                "'pip install paddleocr paddlepaddle' or disable "
                "INGESTION_OCR_ENABLED."
            ) from exc
        self._ocr = PaddleOCR(use_angle_cls=True, lang=lang, show_log=False)
        self._use_gpu = use_gpu

    def read_image(self, image_path: Path) -> OcrResult:
        try:
            raw = self._ocr.ocr(str(image_path), cls=True)
        except Exception as exc:  # noqa: BLE001 - engine errors are opaque
            raise OcrError(f"OCR failed for {image_path.name}: {exc}") from exc
        return _paddle_result(raw, self.name)


def _paddle_result(raw: Any, engine: str) -> OcrResult:
    lines: list[str] = []
    scores: list[float] = []
    for page_result in raw or []:
        if not page_result:
            continue
        for entry in page_result:
            try:
                text, score = entry[0][0], float(entry[0][1])
            except (IndexError, TypeError, ValueError):
                continue
            cleaned = str(text).strip()
            if cleaned:
                lines.append(cleaned)
                scores.append(score)
    confidence = sum(scores) / len(scores) if scores else 0.0
    return OcrResult(
        text="\n".join(lines),
        confidence=round(confidence, 4),
        engine=engine,
    )


def available_engines() -> list[str]:
    """Return the OCR engines that can be used on this host."""
    engines: list[str] = []
    try:
        import paddleocr  # noqa: F401,PLC0415 - optional dependency probe
    except ImportError:
        pass
    else:
        engines.append(PaddleOcrEngine.name)
    return engines


def build_engine(
    *, backend: str = "auto", lang: str = "en", use_gpu: bool = False
) -> OcrEngine:
    """Build the requested OCR engine, or the best available one."""
    if backend not in {"auto", "paddleocr"}:
        raise OcrConfigurationError(f"Unsupported OCR backend: {backend}")
    if backend == "paddleocr":
        return PaddleOcrEngine(lang=lang, use_gpu=use_gpu)
    engines = available_engines()
    if not engines:
        raise OcrConfigurationError(
            "No OCR engine is installed. Install paddleocr or set "
            "INGESTION_OCR_ENABLED=false."
        )
    return PaddleOcrEngine(lang=lang, use_gpu=use_gpu)


async def ocr_page_image(
    engine: OcrEngine, image_path: Path
) -> OcrResult:
    """Run OCR on a rendered page image without blocking the event loop."""
    try:
        return await asyncio.to_thread(engine.read_image, image_path)
    except OcrError:
        raise
    except Exception as exc:  # noqa: BLE001
        raise OcrError(f"OCR failed for {image_path.name}: {exc}") from exc
