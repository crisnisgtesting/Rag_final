"""Health, readiness and liveness endpoints.

Readiness checks PostgreSQL and pgvector. It never invokes an LLM.
"""

from __future__ import annotations

from fastapi import APIRouter, status
from fastapi.responses import JSONResponse

from app import __version__
from app.db.pool import check_database
from app.logging_conf import get_logger

logger = get_logger(__name__)

router = APIRouter(prefix="/health", tags=["health"])


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
            },
        )

    ready = bool(db.get("database") and db.get("pgvector"))
    payload = {
        "status": "ready" if ready else "not_ready",
        "version": __version__,
        "database": db.get("database", False),
        "server_version": db.get("server_version"),
        "pgvector": db.get("pgvector", False),
        "pgvector_version": db.get("pgvector_version"),
    }
    return JSONResponse(
        status_code=status.HTTP_200_OK if ready else status.HTTP_503_SERVICE_UNAVAILABLE,
        content=payload,
    )


@router.get("")
async def health() -> JSONResponse:
    return await readiness()
