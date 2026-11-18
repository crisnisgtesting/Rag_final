"""Image encoding helpers."""

from __future__ import annotations

import base64


def detect_mime_type(image: bytes) -> str:
    if image.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    if image.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    if image.startswith(b"RIFF") and image[8:12] == b"WEBP":
        return "image/webp"
    return "image/png"


def encode_image_base64(image: bytes) -> tuple[str, str]:
    return detect_mime_type(image), base64.b64encode(image).decode("ascii")


def image_data_url(image: bytes) -> str:
    mime, encoded = encode_image_base64(image)
    return f"data:{mime};base64,{encoded}"
