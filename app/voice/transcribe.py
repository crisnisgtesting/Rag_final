"""Local speech-to-text using faster-whisper (CTranslate2)."""

from __future__ import annotations

import asyncio
import tempfile
import time
from pathlib import Path
from typing import Any

from app.config import Settings
from app.logging_conf import get_logger

logger = get_logger(__name__)

_MODELS: dict[tuple[str, str, str], Any] = {}


class TranscriptionError(RuntimeError):
    pass


class TranscriptionConfigurationError(TranscriptionError):
    pass


async def transcribe_audio(
    settings: Settings, audio: bytes, *, filename: str = "audio.webm"
) -> dict[str, Any]:
    if settings.transcription_provider == "disabled":
        raise TranscriptionConfigurationError("Voice transcription is disabled")
    if not audio:
        raise TranscriptionError("Audio file is empty")
    if len(audio) > settings.max_audio_mb * 1024 * 1024:
        raise TranscriptionError(
            f"Audio exceeds the {settings.max_audio_mb} MB transcription limit"
        )
    suffix = Path(filename).suffix or ".webm"
    with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as handle:
        handle.write(audio)
        temp_path = Path(handle.name)
    try:
        return await asyncio.to_thread(_transcribe_sync, settings, temp_path)
    finally:
        temp_path.unlink(missing_ok=True)


def _transcribe_sync(settings: Settings, path: Path) -> dict[str, Any]:
    try:
        from faster_whisper import WhisperModel
    except ImportError as exc:
        raise TranscriptionConfigurationError(
            "faster-whisper is not installed; install requirements-ml.txt"
        ) from exc

    key = (settings.whisper_model, settings.whisper_device, settings.whisper_compute_type)
    model = _MODELS.get(key)
    if model is None:
        try:
            # model = WhisperModel(
            #     settings.whisper_model,
            #     device=settings.whisper_device,
            #     compute_type=settings.whisper_compute_type,
            # )
            model = WhisperModel(
                settings.whisper_model,
                device=settings.whisper_device,
                compute_type=settings.whisper_compute_type,
                cpu_threads=4,
                num_workers=1,
            )
        except Exception as exc:  # noqa: BLE001 - model loading is environment-specific
            raise TranscriptionConfigurationError(
                f"Could not load Whisper model '{settings.whisper_model}': {exc}"
            ) from exc
        _MODELS[key] = model

    started = time.perf_counter()
    try:
        segments, info = model.transcribe(
            str(path),
            beam_size=settings.whisper_beam_size,
            vad_filter=True,
        )
        collected = list(segments)
    except Exception as exc:  # noqa: BLE001
        raise TranscriptionError(f"Transcription failed: {exc}") from exc

    text = " ".join(segment.text.strip() for segment in collected).strip()
    if not text:
        raise TranscriptionError("No speech was detected in the audio")
    return {
        "text": text,
        "language": getattr(info, "language", None),
        "language_probability": getattr(info, "language_probability", None),
        "duration_seconds": round(float(getattr(info, "duration", 0) or 0), 3),
        "segments": [
            {
                "start": round(float(segment.start), 3),
                "end": round(float(segment.end), 3),
                "text": segment.text.strip(),
            }
            for segment in collected
        ],
        "latency_ms": int((time.perf_counter() - started) * 1000),
        "model": settings.whisper_model,
    }


async def transcription_health(settings: Settings) -> dict[str, Any]:
    configured = settings.transcription_provider != "disabled"
    installed = False
    if configured:
        try:
            import faster_whisper  # noqa: F401

            installed = True
        except ImportError:
            installed = False
    return {
        "provider": settings.transcription_provider,
        "configured": configured,
        "installed": installed,
        "available": configured and installed,
        "status": "ok" if configured and installed else "unavailable",
        "model": settings.whisper_model,
    }
