"""Structured logging configuration.

Logs are emitted as JSON in production and as a human readable stream in
development. Request identifiers are propagated through structlog contextvars.
"""

from __future__ import annotations

import logging
import sys
import uuid

import structlog


def configure_logging(level: str = "INFO", *, json_logs: bool | None = None) -> None:
    if json_logs is None:
        json_logs = True

    numeric_level = getattr(logging, level.upper(), logging.INFO)
    logging.basicConfig(
        format="%(message)s",
        stream=sys.stdout,
        level=numeric_level,
        force=True,
    )

    shared_processors = [
        structlog.contextvars.merge_contextvars,
        structlog.processors.add_log_level,
        structlog.processors.TimeStamper(fmt="iso", utc=True),
        structlog.processors.StackInfoRenderer(),
        structlog.processors.format_exc_info,
    ]

    renderer: structlog.types.Processor
    if json_logs:
        renderer = structlog.processors.JSONRenderer()
    else:
        renderer = structlog.dev.ConsoleRenderer(colors=False)

    structlog.configure(
        processors=[*shared_processors, renderer],
        wrapper_class=structlog.make_filtering_bound_logger(numeric_level),
        logger_factory=structlog.PrintLoggerFactory(),
        cache_logger_on_first_use=True,
    )


def new_request_id() -> str:
    return uuid.uuid4().hex


def bind_request_context(request_id: str, **extra: object) -> None:
    structlog.contextvars.bind_contextvars(request_id=request_id, **extra)


def clear_request_context() -> None:
    structlog.contextvars.clear_contextvars()


def get_logger(name: str | None = None) -> structlog.stdlib.BoundLogger:
    return structlog.get_logger(name) if name else structlog.get_logger()
