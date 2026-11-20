"""Document registry, ingestion queue and approval endpoints."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Literal
from uuid import UUID, uuid4

from fastapi import APIRouter, Depends, File, HTTPException, Query, UploadFile, status
from pydantic import BaseModel

from app.api.deps import get_app_settings
from app.auth.dependencies import CurrentUser, get_current_user, require_admin
from app.config import Settings
from app.db.databricks import DatabricksConnection
from app.db.pool import get_db
from app.ingestion import registry
from app.ingestion.job_runner import start_job
from app.logging_conf import get_logger
from app.services.settings_service import get_effective_vision_provider
from app.vision.factory import ALLOWED_PROVIDERS, normalize_provider_name

logger = get_logger(__name__)

router = APIRouter(prefix="/documents", tags=["documents"])


class IngestRequest(BaseModel):
    provider: str | None = None
    model: str | None = None
    vision_mode: Literal["auto", "all", "none"] = "auto"


def _model_for(provider: str, settings: Settings) -> str:
    if provider == "qwen":
        return (settings.qwen_vision_model or "").strip()
    if provider == "gemini":
        return (settings.gemini_vision_model or "").strip()
    if provider == "databricks":
        return (settings.databricks_vision_endpoint or "").strip()
    return ""


def _registry_error(exc: registry.RegistryError, status_code: int = 400) -> HTTPException:
    return HTTPException(status_code=status_code, detail=str(exc))


@router.get("")
async def list_documents(
    conn: DatabricksConnection = Depends(get_db),
    _: CurrentUser = Depends(get_current_user),
) -> dict[str, Any]:
    items = await registry.list_documents(conn)
    return {"items": items}


@router.post("/scan")
async def scan_documents(
    conn: DatabricksConnection = Depends(get_db),
    settings: Settings = Depends(get_app_settings),
    user: CurrentUser = Depends(require_admin),
) -> dict[str, Any]:
    result = await registry.scan_inbox(conn, settings, actor_user_id=user.id)
    await conn.commit()
    return result


@router.post("/upload", status_code=status.HTTP_201_CREATED)
async def upload_document(
    file: UploadFile = File(...),
    conn: DatabricksConnection = Depends(get_db),
    settings: Settings = Depends(get_app_settings),
    user: CurrentUser = Depends(require_admin),
) -> dict[str, Any]:
    filename = Path(file.filename or "").name
    if Path(filename).suffix.lower() not in registry.SUPPORTED_EXTENSIONS:
        allowed = ", ".join(sorted(registry.SUPPORTED_EXTENSIONS))
        raise HTTPException(
            status_code=400, detail=f"Unsupported file type. Allowed: {allowed}"
        )
    contents = await file.read()
    if not contents:
        raise HTTPException(status_code=400, detail="Uploaded file is empty")
    if len(contents) > settings.max_upload_mb * 1024 * 1024:
        raise HTTPException(
            status_code=413,
            detail=f"File exceeds the {settings.max_upload_mb} MB upload limit",
        )
    temp_path = settings.data_dir / "temporary" / f"{uuid4()}-{filename}"
    temp_path.parent.mkdir(parents=True, exist_ok=True)
    temp_path.write_bytes(contents)
    try:
        result = await registry.register_document(
            conn, settings, temp_path, actor_user_id=user.id
        )
        await conn.commit()
    finally:
        temp_path.unlink(missing_ok=True)
    return result


@router.get("/{document_id}")
async def document_detail(
    document_id: UUID,
    conn: DatabricksConnection = Depends(get_db),
    _: CurrentUser = Depends(get_current_user),
) -> dict[str, Any]:
    detail = await registry.document_detail(conn, document_id)
    if detail is None:
        raise HTTPException(status_code=404, detail="Document was not found")
    return detail


@router.get("/versions/{version_id}/pages")
async def version_pages(
    version_id: UUID,
    limit: int = Query(default=500, ge=1, le=2000),
    conn: DatabricksConnection = Depends(get_db),
    _: CurrentUser = Depends(get_current_user),
) -> dict[str, Any]:
    version = await registry.version_context(conn, version_id)
    if version is None:
        raise HTTPException(status_code=404, detail="Document version was not found")
    pages = await registry.list_pages(conn, version_id, limit=limit)
    return {"version": version, "items": pages}


@router.get("/versions/{version_id}/chunks")
async def version_chunks(
    version_id: UUID,
    limit: int = Query(default=100, ge=1, le=500),
    conn: DatabricksConnection = Depends(get_db),
    _: CurrentUser = Depends(get_current_user),
) -> dict[str, Any]:
    version = await registry.version_context(conn, version_id)
    if version is None:
        raise HTTPException(status_code=404, detail="Document version was not found")
    chunks = await registry.list_chunks(conn, version_id, limit=limit)
    return {"version": version, "items": chunks}


@router.post("/versions/{version_id}/ingest", status_code=status.HTTP_202_ACCEPTED)
async def ingest_version(
    version_id: UUID,
    payload: IngestRequest,
    conn: DatabricksConnection = Depends(get_db),
    settings: Settings = Depends(get_app_settings),
    user: CurrentUser = Depends(require_admin),
) -> dict[str, Any]:
    version = await registry.version_context(conn, version_id)
    if version is None:
        raise HTTPException(status_code=404, detail="Document version was not found")
    if version["status"] == "ready" and version["approval_status"] == "approved":
        raise HTTPException(
            status_code=409,
            detail="Version is already approved and does not need re-ingestion",
        )

    if payload.provider:
        provider = normalize_provider_name(payload.provider)
        if provider not in ALLOWED_PROVIDERS:
            raise HTTPException(
                status_code=400,
                detail=f"Unsupported provider. Allowed: {', '.join(ALLOWED_PROVIDERS)}",
            )
    else:
        provider, _source = await get_effective_vision_provider(conn, settings)

    model = (payload.model or _model_for(provider, settings)).strip()
    if not model:
        raise HTTPException(
            status_code=400,
            detail=f"No model is configured for vision provider '{provider}'",
        )

    try:
        job = await registry.create_job(
            conn,
            version_id,
            provider=provider,
            model=model,
            config={
                "vision_mode": payload.vision_mode,
                "requested_provider": provider,
                "requested_model": model,
            },
            actor_user_id=user.id,
        )
        await conn.commit()
    except registry.RegistryError as exc:
        await conn.rollback()
        raise _registry_error(exc, 409) from exc

    start_job(job["id"], settings)
    logger.info(
        "ingestion_started",
        job_id=str(job["id"]),
        version_id=str(version_id),
        provider=provider,
        actor=user.username,
    )
    return {"job": job}


@router.post("/versions/{version_id}/approve")
async def approve_version(
    version_id: UUID,
    conn: DatabricksConnection = Depends(get_db),
    user: CurrentUser = Depends(require_admin),
) -> dict[str, Any]:
    try:
        result = await registry.approve_version(
            conn, version_id, actor_user_id=user.id
        )
        await conn.commit()
        return result
    except registry.RegistryError as exc:
        await conn.rollback()
        raise _registry_error(exc, 409) from exc


@router.post("/versions/{version_id}/reject")
async def reject_version(
    version_id: UUID,
    conn: DatabricksConnection = Depends(get_db),
    user: CurrentUser = Depends(require_admin),
) -> dict[str, Any]:
    try:
        result = await registry.reject_version(conn, version_id, actor_user_id=user.id)
        await conn.commit()
        return result
    except registry.RegistryError as exc:
        await conn.rollback()
        raise _registry_error(exc, 409) from exc
