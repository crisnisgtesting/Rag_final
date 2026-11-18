"""Tests for the microphone dictation helpers."""

from __future__ import annotations

from typing import Any

from frontend.voice import (
    DICTATION_FILENAME,
    audio_fingerprint,
    merge_transcript,
    transcribe_recording,
)


class StubClient:
    def __init__(self, payload: dict[str, Any]) -> None:
        self.payload = payload
        self.calls: list[tuple[str, bytes]] = []

    def transcribe_audio(self, filename: str, content: bytes) -> dict[str, Any]:
        self.calls.append((filename, content))
        return self.payload


def test_fingerprint_is_stable_and_content_sensitive() -> None:
    assert audio_fingerprint(b"abc") == audio_fingerprint(b"abc")
    assert audio_fingerprint(b"abc") != audio_fingerprint(b"abd")


def test_transcribe_recording_sends_the_wav_payload() -> None:
    client = StubClient({"text": "how do I mark attendance"})

    text = transcribe_recording(client, b"audio-bytes")

    assert text == "how do I mark attendance"
    assert client.calls == [(DICTATION_FILENAME, b"audio-bytes")]


def test_transcribe_recording_handles_missing_text() -> None:
    client = StubClient({})
    assert transcribe_recording(client, b"audio-bytes") == ""


def test_transcribe_recording_ignores_empty_audio() -> None:
    client = StubClient({"text": "ignored"})
    assert transcribe_recording(client, b"") == ""
    assert client.calls == []


def test_merge_transcript_uses_speech_when_the_box_is_empty() -> None:
    assert merge_transcript("", "mark attendance", "old") == "mark attendance"


def test_merge_transcript_replaces_an_unedited_previous_transcript() -> None:
    assert merge_transcript("mark attendance", "mark leave", "mark attendance") == (
        "mark leave"
    )


def test_merge_transcript_keeps_manual_edits() -> None:
    merged = merge_transcript("mark attendance for my group", "and leave", "mark attendance")
    assert merged == "mark attendance for my group and leave"


def test_merge_transcript_ignores_an_empty_transcript() -> None:
    assert merge_transcript("kept", "", "kept") == "kept"
