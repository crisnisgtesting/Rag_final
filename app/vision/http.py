"""HTTP retry helper shared by Vision provider adapters."""

from __future__ import annotations

import asyncio
import time
from typing import Any

import httpx

from app.logging_conf import get_logger
from app.vision.base import VisionRequestError

logger = get_logger(__name__)

RETRYABLE_STATUS_CODES = {408, 409, 425, 429, 500, 502, 503, 504}


def _sanitize(text: str, limit: int = 500) -> str:
    cleaned = " ".join(text.split())
    return cleaned[:limit]


async def request_with_retry(
    *,
    method: str,
    url: str,
    provider: str,
    timeout: float,
    max_retries: int,
    headers: dict[str, str] | None = None,
    json_body: dict[str, Any] | None = None,
) -> httpx.Response:
    last_error: VisionRequestError | None = None
    async with httpx.AsyncClient(timeout=timeout) as client:
        for attempt in range(max_retries + 1):
            started = time.perf_counter()
            try:
                response = await client.request(
                    method, url, headers=headers, json=json_body
                )
            except (httpx.TimeoutException, httpx.TransportError) as exc:
                last_error = VisionRequestError(
                    f"{provider} request failed: {type(exc).__name__}",
                    provider=provider,
                    retryable=True,
                )
                logger.warning(
                    "vision_request_transport_error",
                    provider=provider,
                    attempt=attempt + 1,
                    error_type=type(exc).__name__,
                    duration_ms=int((time.perf_counter() - started) * 1000),
                )
            else:
                latency_ms = int((time.perf_counter() - started) * 1000)
                if response.status_code < 400:
                    logger.info(
                        "vision_request_ok",
                        provider=provider,
                        attempt=attempt + 1,
                        status_code=response.status_code,
                        latency_ms=latency_ms,
                    )
                    return response

                retryable = response.status_code in RETRYABLE_STATUS_CODES
                last_error = VisionRequestError(
                    (
                        f"{provider} returned HTTP {response.status_code}: "
                        f"{_sanitize(response.text)}"
                    ),
                    provider=provider,
                    retryable=retryable,
                )
                logger.warning(
                    "vision_request_http_error",
                    provider=provider,
                    attempt=attempt + 1,
                    status_code=response.status_code,
                    retryable=retryable,
                    latency_ms=latency_ms,
                )
                if not retryable:
                    raise last_error

            if attempt < max_retries:
                await asyncio.sleep(min(2**attempt, 8))

    assert last_error is not None
    raise last_error
