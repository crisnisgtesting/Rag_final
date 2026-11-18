"""ASGI middleware: request IDs, security headers, size limits, request logs."""

from __future__ import annotations

import time
from typing import Any

from starlette.datastructures import Headers, MutableHeaders
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from app.logging_conf import (
    bind_request_context,
    clear_request_context,
    get_logger,
    new_request_id,
)

logger = get_logger(__name__)

_SECURITY_HEADERS = {
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "DENY",
    "Referrer-Policy": "no-referrer",
    "Cache-Control": "no-store",
}


class RequestContextMiddleware:
    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        headers = Headers(scope=scope)
        incoming_request_id = headers.get("x-request-id") or ""
        if (
            incoming_request_id
            and len(incoming_request_id) <= 64
            and all(
                char.isalnum() or char in "-_."
                for char in incoming_request_id
            )
        ):
            request_id = incoming_request_id
        else:
            request_id = new_request_id()
        scope.setdefault("state", {})["request_id"] = request_id
        bind_request_context(request_id)

        async def send_with_headers(message: Message) -> None:
            if message["type"] == "http.response.start":
                response_headers = MutableHeaders(scope=message)
                response_headers["X-Request-ID"] = request_id
                for name, value in _SECURITY_HEADERS.items():
                    response_headers[name] = value
            await send(message)

        try:
            await self.app(scope, receive, send_with_headers)
        finally:
            clear_request_context()


class MaxBodySizeMiddleware:
    def __init__(self, app: ASGIApp, max_bytes: int) -> None:
        self.app = app
        self.max_bytes = max_bytes

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        headers = Headers(scope=scope)
        content_length = headers.get("content-length")
        if content_length is not None:
            try:
                if int(content_length) > self.max_bytes:
                    await _send_json(
                        send,
                        413,
                        {
                            "error": {
                                "code": "payload_too_large",
                                "message": "Request body exceeds the configured limit",
                            }
                        },
                    )
                    return
            except ValueError:
                pass
        await self.app(scope, receive, send)


class RequestLoggingMiddleware:
    def __init__(self, app: ASGIApp, *, exclude_prefixes: tuple[str, ...] = ("/health",)) -> None:
        self.app = app
        self.exclude_prefixes = exclude_prefixes

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        path = scope.get("path", "")
        if any(path.startswith(prefix) for prefix in self.exclude_prefixes):
            await self.app(scope, receive, send)
            return

        started = time.perf_counter()
        status_code: int | None = None
        error_name: str | None = None

        async def send_wrapper(message: Message) -> None:
            nonlocal status_code
            if message["type"] == "http.response.start":
                status_code = message["status"]
            await send(message)

        try:
            await self.app(scope, receive, send_wrapper)
        except Exception as exc:
            error_name = type(exc).__name__
            raise
        finally:
            duration_ms = int((time.perf_counter() - started) * 1000)
            state: dict[str, Any] = scope.get("state") or {}
            await _persist_request_log(
                request_id=str(state.get("request_id") or ""),
                user_id=state.get("user_id"),
                method=scope.get("method", ""),
                endpoint=path,
                status_code=status_code,
                duration_ms=duration_ms,
                error=error_name,
                client_ip=(scope.get("client") or (None, None))[0],
            )


async def _persist_request_log(
    *,
    request_id: str,
    user_id: str | None,
    method: str,
    endpoint: str,
    status_code: int | None,
    duration_ms: int,
    error: str | None,
    client_ip: str | None,
) -> None:
    try:
        from app.db.pool import get_pool

        pool = get_pool()
        async with pool.connection() as conn:
            async with conn.transaction():
                await conn.execute(
                    """
                    INSERT INTO request_logs
                        (request_id, user_id, method, endpoint, status_code,
                         duration_ms, error, client_ip)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                    """,
                    (
                        request_id,
                        user_id,
                        method,
                        endpoint,
                        status_code,
                        duration_ms,
                        error,
                        client_ip,
                    ),
                )
        logger.info(
            "request_completed",
            method=method,
            endpoint=endpoint,
            status_code=status_code,
            duration_ms=duration_ms,
        )
    except Exception as exc:  # noqa: BLE001 - logging must never break requests
        logger.warning("request_log_persist_failed", error=type(exc).__name__)


async def _send_json(send: Send, status_code: int, payload: dict[str, Any]) -> None:
    import json

    body = json.dumps(payload).encode("utf-8")
    await send(
        {
            "type": "http.response.start",
            "status": status_code,
            "headers": [
                (b"content-type", b"application/json"),
                (b"content-length", str(len(body)).encode("ascii")),
            ],
        }
    )
    await send({"type": "http.response.body", "body": body})
