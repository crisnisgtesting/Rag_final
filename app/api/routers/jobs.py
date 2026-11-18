"""Ingestion job inspection and control endpoints."""

from __future__ import annotations

from typing import Any
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query
from psycopg import AsyncConnection

from app.api.deps import get_app_settings
from app.auth.dependencies import CurrentUser, get_current_user, require_admin
from app.config import Settings
from app.db.pool import get_db
from app.ingestion import registry
from app.ingestion.job_runner import start_job
from app.logging_conf import get_logger

logger = get_logger(__name__)

router = APIRouter(prefix="/jobs", tags=["jobs"])


@router.get("")
async def list_jobs(
    limit: int = Query(default=100, ge=1, le=500),
    conn: AsyncConnection = Depends(get_db),
    _: CurrentUser = Depends(get_current_user),
) -> dict[str, Any]:
    items = await registry.list_jobs(conn, limit=limit)
    return {"items": items}


@router.get("/{job_id}")
async def job_detail(
    job_id: UUID,
    conn: AsyncConnection = Depends(get_db),
    _: CurrentUser = Depends(get_current_user),
) -> dict[str, Any]:
    job = await registry.job_detail(conn, job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="Ingestion job was not found")
    checkpoints = await (
        await conn.execute(
            """
            SELECT checkpoint_key, checkpoint_data, created_at
            FROM job_checkpoints
            WHERE job_id = %s
            ORDER BY created_at
            """,
            (str(job_id),),
        )
    ).fetchall()
    runs = await (
        await conn.execute(
            """
            SELECT id, document_page_id, provider, model, status, latency_ms,
                   usage, error, created_at
            FROM vision_runs
            WHERE job_id = %s
            ORDER BY created_at DESC
            LIMIT 200
            """,
            (str(job_id),),
        )
    ).fetchall()
    return {"job": job, "checkpoints": checkpoints, "vision_runs": runs}


@router.post("/{job_id}/cancel")
async def cancel_job(
    job_id: UUID,
    conn: AsyncConnection = Depends(get_db),
    user: CurrentUser = Depends(require_admin),
) -> dict[str, Any]:
    job = await registry.request_cancel(conn, job_id, actor_user_id=user.id)
    if job is None:
        raise HTTPException(status_code=404, detail="Ingestion job was not found")
    await conn.commit()
    return job


@router.post("/{job_id}/resume")
async def resume_job(
    job_id: UUID,
    conn: AsyncConnection = Depends(get_db),
    settings: Settings = Depends(get_app_settings),
    user: CurrentUser = Depends(require_admin),
) -> dict[str, Any]:
    try:
        job = await registry.resume_job(conn, job_id, actor_user_id=user.id)
        await conn.commit()
    except registry.RegistryError as exc:
        await conn.rollback()
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    start_job(job["id"], settings)
    logger.info("ingestion_resumed", job_id=str(job_id), actor=user.username)
    return job
