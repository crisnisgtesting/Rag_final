"""FastAPI application factory."""

from __future__ import annotations

import asyncio
import sys
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from app import __version__
from app.api.routers import admin, auth, chat, documents, health, jobs, knowledge, search
from app.auth.rate_limit import SlidingWindowLimiter
from app.config import Settings, get_settings
from app.db.pool import close_pool, init_pool
from app.logging_conf import configure_logging, get_logger
from app.middleware import (
    MaxBodySizeMiddleware,
    RequestContextMiddleware,
    RequestLoggingMiddleware,
)
from app.vision.base import VisionConfigurationError, VisionError

if sys.platform == "win32":  # psycopg async requires the selector loop on Windows
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

logger = get_logger(__name__)


def _build_lifespan(settings: Settings):
    @asynccontextmanager
    async def lifespan(app: FastAPI):
        configure_logging(settings.log_level, json_logs=settings.is_production)
        settings.ensure_runtime_dirs()
        app.state.settings = settings
        app.state.login_limiter = SlidingWindowLimiter(
            settings.auth_max_login_attempts, settings.auth_login_window_seconds
        )
        logger.info(
            "application_starting",
            app_env=settings.app_env,
            vision_provider=settings.vision_provider,
        )
        await init_pool(settings)
        try:
            from app.ingestion.job_runner import recover_stale_jobs

            try:
                await recover_stale_jobs(settings)
            except Exception as exc:  # noqa: BLE001 - startup must not be blocked
                logger.warning("job_recovery_failed", error=type(exc).__name__)
            yield
        finally:
            await close_pool()
            logger.info("application_stopped")

    return lifespan


def _error_payload(request: Request, code: str, message: str) -> dict:
    return {
        "error": {
            "code": code,
            "message": message,
            "request_id": getattr(request.state, "request_id", None),
        }
    }


def _safe_validation_errors(exc: RequestValidationError) -> list[dict]:
    safe = []
    for error in exc.errors():
        safe.append(
            {
                "loc": [str(part) for part in error.get("loc", [])],
                "msg": error.get("msg", "Invalid value"),
                "type": error.get("type", "value_error"),
            }
        )
    return safe


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or get_settings()
    configure_logging(settings.log_level, json_logs=settings.is_production)

    app = FastAPI(
        title=settings.app_name,
        version=__version__,
        lifespan=_build_lifespan(settings),
        docs_url="/docs" if not settings.is_production else None,
        redoc_url=None,
    )
    app.state.settings = settings
    app.state.login_limiter = SlidingWindowLimiter(
        settings.auth_max_login_attempts, settings.auth_login_window_seconds
    )

    app.add_middleware(RequestLoggingMiddleware)
    app.add_middleware(
        MaxBodySizeMiddleware, max_bytes=settings.max_upload_mb * 1024 * 1024
    )
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,
        allow_credentials=False,
        allow_methods=["*"],
        allow_headers=["*"],
        expose_headers=["X-Request-ID"],
    )
    app.add_middleware(RequestContextMiddleware)

    app.include_router(health.router)
    app.include_router(auth.router)
    app.include_router(admin.router)
    app.include_router(documents.router)
    app.include_router(jobs.router)
    app.include_router(search.router)
    app.include_router(knowledge.router)
    app.include_router(chat.router)

    @app.get("/", tags=["system"])
    async def root() -> dict:
        return {
            "name": settings.app_name,
            "version": __version__,
            "environment": settings.app_env,
            "phase": 2,
        }

    @app.get("/api/version", tags=["system"])
    async def version() -> dict:
        return {"version": __version__, "environment": settings.app_env}

    @app.exception_handler(StarletteHTTPException)
    async def http_exception_handler(
        request: Request, exc: StarletteHTTPException
    ) -> JSONResponse:
        message = exc.detail if isinstance(exc.detail, str) else "Request failed"
        return JSONResponse(
            status_code=exc.status_code,
            content=_error_payload(request, "http_error", message),
            headers=exc.headers,
        )

    @app.exception_handler(RequestValidationError)
    async def validation_exception_handler(
        request: Request, exc: RequestValidationError
    ) -> JSONResponse:
        return JSONResponse(
            status_code=422,
            content={
                "error": {
                    "code": "validation_error",
                    "message": "Invalid request",
                    "request_id": getattr(request.state, "request_id", None),
                    "details": _safe_validation_errors(exc),
                }
            },
        )

    @app.exception_handler(VisionConfigurationError)
    async def vision_config_exception_handler(
        request: Request, exc: VisionConfigurationError
    ) -> JSONResponse:
        return JSONResponse(
            status_code=503,
            content=_error_payload(
                request, "vision_not_configured", str(exc)
            )
            | {"provider": exc.provider},
        )

    @app.exception_handler(VisionError)
    async def vision_exception_handler(
        request: Request, exc: VisionError
    ) -> JSONResponse:
        logger.warning(
            "vision_error",
            provider=exc.provider,
            retryable=exc.retryable,
            error=type(exc).__name__,
        )
        return JSONResponse(
            status_code=502,
            content=_error_payload(request, "vision_error", str(exc))
            | {"provider": exc.provider, "retryable": exc.retryable},
        )

    @app.exception_handler(Exception)
    async def unhandled_exception_handler(
        request: Request, exc: Exception
    ) -> JSONResponse:
        logger.exception(
            "unhandled_exception",
            error=type(exc).__name__,
            request_id=getattr(request.state, "request_id", None),
        )
        return JSONResponse(
            status_code=500,
            content=_error_payload(
                request, "internal_error", "An internal error occurred"
            ),
        )

    return app


app = create_app()
