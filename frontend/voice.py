"""Microphone dictation helpers for the chat page.

The chat page records the prompt with the browser microphone through
`st.audio_input` and sends the recording to the transcription endpoint. The
helpers here keep the page thin and make the transcript handling testable.
"""

from __future__ import annotations

import hashlib
from typing import Any, Protocol

DICTATION_FILENAME = "dictation.wav"


class TranscriptionClient(Protocol):
    def transcribe_audio(self, filename: str, content: bytes) -> dict[str, Any]: ...


def audio_fingerprint(content: bytes) -> str:
    """Return a stable identifier for a recording so it is transcribed once."""
    return hashlib.sha256(content).hexdigest()


def transcribe_recording(client: TranscriptionClient, content: bytes) -> str:
    """Transcribe recorded audio and return the recognized text."""
    if not content:
        return ""
    result = client.transcribe_audio(DICTATION_FILENAME, content)
    return str(result.get("text") or "").strip()


def merge_transcript(
    existing: str, transcript: str, last_applied: str | None = None
) -> str:
    """Combine the spoken transcript with the question box content.

    A new recording replaces the previous transcript when the user has not
    edited it. If the text was edited, the new speech is appended so that the
    manual correction is preserved.
    """
    existing = (existing or "").strip()
    transcript = (transcript or "").strip()
    if not transcript:
        return existing
    if not existing:
        return transcript
    if last_applied is not None and existing == last_applied.strip():
        return transcript
    return f"{existing} {transcript}"
