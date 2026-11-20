"""Knowledge organization, review and export endpoints."""

from __future__ import annotations

from typing import Any, Literal
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import PlainTextResponse
from pydantic import BaseModel, Field

from app.auth.dependencies import CurrentUser, get_current_user, require_admin
from app.db.databricks import DatabricksConnection
from app.db.pool import get_db
from app.ingestion import registry
from app.knowledge import service
from app.knowledge.extractor import rebuild_version_knowledge

router = APIRouter(prefix="/knowledge", tags=["knowledge"])


class StatusRequest(BaseModel):
    status: Literal["proposed", "approved", "rejected", "superseded"]


class ResolutionRequest(BaseModel):
    status: Literal["resolved", "ignored", "open"] = "resolved"
    resolution: str = Field(min_length=1, max_length=4000)


class CorrectionRequest(BaseModel):
    summary: str | None = Field(default=None, max_length=8000)
    metadata: dict[str, Any] | None = None


@router.get("/summary")
async def summary(
    version_id: UUID | None = None,
    conn: DatabricksConnection = Depends(get_db),
    _: CurrentUser = Depends(get_current_user),
) -> dict[str, Any]:
    return await service.knowledge_summary(conn, version_id=version_id)


@router.get("/workflows")
async def workflows(
    version_id: UUID | None = None,
    status: str | None = Query(default=None),
    limit: int = Query(default=100, ge=1, le=500),
    conn: DatabricksConnection = Depends(get_db),
    _: CurrentUser = Depends(get_current_user),
) -> dict[str, Any]:
    items = await service.list_workflows(
        conn, version_id=version_id, status=status, limit=limit
    )
    return {"items": items}


@router.get("/workflows/{workflow_id}")
async def workflow_detail(
    workflow_id: UUID,
    conn: DatabricksConnection = Depends(get_db),
    _: CurrentUser = Depends(get_current_user),
) -> dict[str, Any]:
    detail = await service.workflow_detail(conn, workflow_id)
    if detail is None:
        raise HTTPException(status_code=404, detail="Workflow was not found")
    return detail


@router.get("/workflows/{workflow_id}/export", response_class=PlainTextResponse)
async def export_workflow(
    workflow_id: UUID,
    conn: DatabricksConnection = Depends(get_db),
    _: CurrentUser = Depends(get_current_user),
) -> str:
    markdown = await service.export_workflow_markdown(conn, workflow_id)
    if markdown is None:
        raise HTTPException(status_code=404, detail="Workflow was not found")
    return markdown


@router.patch("/workflows/{workflow_id}")
async def correct_workflow(
    workflow_id: UUID,
    payload: CorrectionRequest,
    conn: DatabricksConnection = Depends(get_db),
    user: CurrentUser = Depends(require_admin),
) -> dict[str, Any]:
    try:
        row = await service.store_manual_knowledge_override(
            conn,
            workflow_id=workflow_id,
            actor_user_id=user.id,
            summary=payload.summary,
            metadata=payload.metadata,
        )
        await conn.commit()
        return row
    except ValueError as exc:
        await conn.rollback()
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.post("/workflows/{workflow_id}/status")
async def workflow_status(
    workflow_id: UUID,
    payload: StatusRequest,
    conn: DatabricksConnection = Depends(get_db),
    user: CurrentUser = Depends(require_admin),
) -> dict[str, Any]:
    try:
        row = await service.set_knowledge_status(
            conn,
            knowledge_type="workflow",
            knowledge_id=workflow_id,
            status=payload.status,
            actor_user_id=user.id,
        )
        await conn.commit()
        return row
    except ValueError as exc:
        await conn.rollback()
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.post("/versions/{version_id}/rebuild")
async def rebuild(
    version_id: UUID,
    approve: bool = Query(default=False),
    conn: DatabricksConnection = Depends(get_db),
    user: CurrentUser = Depends(require_admin),
) -> dict[str, Any]:
    try:
        stats = await rebuild_version_knowledge(
            conn,
            version_id,
            mark_approved=approve,
            actor_user_id=user.id,
        )
        await conn.commit()
        return {"version_id": str(version_id), "stats": stats}
    except ValueError as exc:
        await conn.rollback()
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.post("/versions/{version_id}/approve")
async def approve(
    version_id: UUID,
    conn: DatabricksConnection = Depends(get_db),
    user: CurrentUser = Depends(require_admin),
) -> dict[str, Any]:
    version = await registry.version_context(conn, version_id)
    if version is None:
        raise HTTPException(status_code=404, detail="Document version was not found")
    result = await service.approve_version_knowledge(
        conn, version_id, actor_user_id=user.id
    )
    await conn.commit()
    return result


@router.get("/conflicts")
async def conflicts(
    status: str = Query(default="open"),
    limit: int = Query(default=100, ge=1, le=500),
    conn: DatabricksConnection = Depends(get_db),
    _: CurrentUser = Depends(get_current_user),
) -> dict[str, Any]:
    return {"items": await service.list_conflicts(conn, status=status, limit=limit)}


@router.post("/conflicts/{conflict_id}/resolve")
async def resolve(
    conflict_id: UUID,
    payload: ResolutionRequest,
    conn: DatabricksConnection = Depends(get_db),
    user: CurrentUser = Depends(require_admin),
) -> dict[str, Any]:
    try:
        row = await service.resolve_conflict(
            conn,
            conflict_id,
            status=payload.status,
            resolution=payload.resolution,
            actor_user_id=user.id,
        )
        await conn.commit()
        return row
    except ValueError as exc:
        await conn.rollback()
        raise HTTPException(status_code=404, detail=str(exc)) from exc
