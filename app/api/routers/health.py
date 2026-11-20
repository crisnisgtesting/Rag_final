"""Health, readiness and liveness endpoints.

Readiness checks current storage and reports Databricks configuration status. It never invokes an LLM.
"""

from __future__ import annotations

from dataclasses import asdict
from typing import Any

from fastapi import APIRouter, status
from fastapi.responses import JSONResponse

from app import __version__
from app.config import get_settings
from app.db.pool import check_database
from app.logging_conf import get_logger
from app.retrieval.ai_search import get_ai_search_status

logger = get_logger(__name__)

router = APIRouter(prefix="/health", tags=["health"])


def _databricks_status() -> dict[str, Any]:
    settings = get_settings()
    client_secret = (
        settings.databricks_client_secret.get_secret_value()
        if settings.databricks_client_secret is not None
        else ""
    )
    configured = bool(
        settings.databricks_host
        and settings.databricks_client_id
        and client_secret
        and settings.databricks_model_serving_endpoint
    )
    return {
        "configured": configured,
        "model_serving_endpoint": settings.databricks_model_serving_endpoint,
        "oauth_scope": settings.databricks_oauth_scope,
        "ai_search": asdict(get_ai_search_status(settings)),
    }


@router.get("/live")
async def liveness() -> dict[str, str]:
    return {"status": "ok"}


@router.get("/ready")
async def readiness() -> JSONResponse:
    try:
        db = await check_database()
    except Exception as exc:  # noqa: BLE001
        logger.warning("readiness_database_error", error=type(exc).__name__)
        return JSONResponse(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            content={
                "status": "not_ready",
                "version": __version__,
                "database": False,
                "pgvector": False,
                "detail": "database unavailable",
                "databricks": _databricks_status(),
            },
        )

    ready = bool(db.get("database"))
    payload = {
        "status": "ready" if ready else "not_ready",
        "version": __version__,
        "database": db.get("database", False),
        "server_version": db.get("server_version"),
        "pgvector": False,
        "pgvector_version": None,
        "databricks": _databricks_status(),
    }
    return JSONResponse(
        status_code=status.HTTP_200_OK if ready else status.HTTP_503_SERVICE_UNAVAILABLE,
        content=payload,
    )


@router.get("")
async def health() -> JSONResponse:
    return await readiness()
