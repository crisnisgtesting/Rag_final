"""HTTP helpers for LLM provider adapters."""

from __future__ import annotations

import asyncio
import time
from typing import Any

import httpx

from app.llm.base import LLMRequestError
from app.logging_conf import get_logger

logger = get_logger(__name__)

RETRYABLE_STATUS_CODES = {408, 409, 425, 429, 500, 502, 503, 504}


def _sanitize_error_type(exc: Exception) -> str:
    return type(exc).__name__


async def request_with_retry(
    *,
    method: str,
    url: str,
    provider: str,
    timeout: float,
    max_retries: int,
    headers: dict[str, str] | None = None,
    json_body: dict[str, Any] | None = None,
    client: httpx.AsyncClient | None = None,
) -> httpx.Response:
    last_error: LLMRequestError | None = None
    owns_client = client is None
    active_client = client or httpx.AsyncClient(timeout=timeout, trust_env=True)

    try:
        for attempt in range(max_retries + 1):
            started = time.perf_counter()
            try:
                response = await active_client.request(
                    method, url, headers=headers, json=json_body
                )
            except (httpx.TimeoutException, httpx.TransportError) as exc:
                last_error = LLMRequestError(
                    f"{provider} request failed: {_sanitize_error_type(exc)}",
                    provider=provider,
                    retryable=True,
                )
                logger.warning(
                    "llm_request_transport_error",
                    provider=provider,
                    attempt=attempt + 1,
                    error_type=_sanitize_error_type(exc),
                    duration_ms=int((time.perf_counter() - started) * 1000),
                )
            else:
                latency_ms = int((time.perf_counter() - started) * 1000)
                if response.status_code < 400:
                    logger.info(
                        "llm_request_ok",
                        provider=provider,
                        attempt=attempt + 1,
                        status_code=response.status_code,
                        latency_ms=latency_ms,
                    )
                    return response

                retryable = response.status_code in RETRYABLE_STATUS_CODES
                last_error = LLMRequestError(
                    f"{provider} returned HTTP {response.status_code}",
                    provider=provider,
                    status_code=response.status_code,
                    retryable=retryable,
                )
                logger.warning(
                    "llm_request_http_error",
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
    finally:
        if owns_client:
            await active_client.aclose()
