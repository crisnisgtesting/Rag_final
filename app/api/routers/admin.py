"""Administrator endpoints: vision provider selection and audit inspection."""

from __future__ import annotations

import json
from typing import Any
from uuid import uuid4

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from pydantic import BaseModel, Field

from app.api.deps import get_app_settings
from app.auth.dependencies import CurrentUser, require_admin
from app.config import Settings
from app.db.compat import Jsonb
from app.db.databricks import DatabricksConnection
from app.db.pool import get_db
from app.evaluation.service import (
    create_eval_case,
    latest_eval_results,
    list_eval_set,
    run_evaluation,
)
from app.logging_conf import get_logger
from app.services.settings_service import (
    get_effective_vision_provider,
    get_vision_configuration,
    set_vision_provider,
)
from app.vision.base import VisionConfigurationError, VisionError
from app.vision.factory import ALLOWED_PROVIDERS, build_provider, normalize_provider_name
from app.vision.schemas import ProviderHealth
from app.voice.transcribe import transcription_health

logger = get_logger(__name__)

router = APIRouter(prefix="/admin", tags=["admin"])


def _decode_json_value(value: Any) -> Any:
    if not isinstance(value, str):
        return value
    try:
        return json.loads(value)
    except json.JSONDecodeError:
        return value


def _decode_row(row: dict[str, Any], *fields: str) -> dict[str, Any]:
    decoded = dict(row)
    for field in fields:
        if field in decoded:
            decoded[field] = _decode_json_value(decoded[field])
    return decoded


class ProviderSummary(BaseModel):
    provider: str
    model: str | None = None
    configured: bool
    is_active: bool
    endpoint: str | None = None
    credential_configured: bool


class VisionConfiguration(BaseModel):
    active_provider: str
    source: str
    environment_default: str
    providers: list[ProviderSummary]


class SetVisionProviderRequest(BaseModel):
    provider: str = Field(min_length=1, max_length=50)


class ProviderTestRequest(BaseModel):
    provider: str | None = None


class EvalCaseRequest(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    question: str = Field(min_length=1, max_length=4000)
    expected_keywords: list[str] = []
    expected_document_ids: list[str] = []
    expected_answer: str | None = None
    language: str = "en"
    tags: list[str] = []


class EvalRunRequest(BaseModel):
    name: str | None = None
    limit: int = Field(default=50, ge=1, le=200)


class VisionEvaluationRequest(BaseModel):
    document_version_id: str
    document_page_id: str | None = None
    provider: str
    model: str
    evaluation_type: str = "manual"
    accuracy_score: float | None = Field(default=None, ge=0, le=1)
    completeness_score: float | None = Field(default=None, ge=0, le=1)
    visual_score: float | None = Field(default=None, ge=0, le=1)
    metrics: dict[str, Any] = {}
    notes: str | None = None


def _model_for(provider: str, settings: Settings) -> str:
    if provider == "qwen":
        return settings.qwen_vision_model
    if provider == "gemini":
        return settings.gemini_vision_model or "unconfigured"
    if provider == "databricks":
        return settings.databricks_vision_endpoint or "unconfigured"
    return "unconfigured"


@router.get("/vision/providers", response_model=VisionConfiguration)
async def list_vision_providers(
    conn: DatabricksConnection = Depends(get_db),
    settings: Settings = Depends(get_app_settings),
    _: CurrentUser = Depends(require_admin),
) -> VisionConfiguration:
    config = await get_vision_configuration(conn, settings)
    return VisionConfiguration.model_validate(config)


@router.put("/settings/vision-provider", response_model=VisionConfiguration)
async def update_vision_provider(
    payload: SetVisionProviderRequest,
    request: Request,
    conn: DatabricksConnection = Depends(get_db),
    settings: Settings = Depends(get_app_settings),
    user: CurrentUser = Depends(require_admin),
) -> VisionConfiguration:
    try:
        await set_vision_provider(
            conn,
            payload.provider,
            actor_user_id=user.id,
        )
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)
        ) from exc

    config = await get_vision_configuration(conn, settings)
    logger.info(
        "vision_provider_changed",
        provider=config["active_provider"],
        actor=user.username,
        request_id=getattr(request.state, "request_id", None),
    )
    return VisionConfiguration.model_validate(config)


@router.post("/vision/test", response_model=ProviderHealth)
async def test_vision_provider(
    payload: ProviderTestRequest,
    conn: DatabricksConnection = Depends(get_db),
    settings: Settings = Depends(get_app_settings),
    _: CurrentUser = Depends(require_admin),
) -> ProviderHealth:
    if payload.provider:
        provider_name = normalize_provider_name(payload.provider)
    else:
        provider_name, _source = await get_effective_vision_provider(conn, settings)

    if provider_name not in ALLOWED_PROVIDERS:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=(
                f"Unsupported vision provider '{payload.provider}'. "
                f"Allowed values: {', '.join(ALLOWED_PROVIDERS)}"
            ),
        )

    try:
        provider = build_provider(provider_name, settings)
    except VisionConfigurationError as exc:
        return ProviderHealth(
            provider=provider_name,
            model=_model_for(provider_name, settings),
            configured=False,
            reachable=False,
            status="not_configured",
            detail=str(exc),
        )
    except VisionError as exc:
        return ProviderHealth(
            provider=provider_name,
            model=_model_for(provider_name, settings),
            configured=True,
            reachable=False,
            status="error",
            detail=str(exc),
        )

    try:
        health = await provider.healthcheck()
    except Exception as exc:  # noqa: BLE001 - report, never fallback
        logger.warning(
            "vision_healthcheck_failed",
            provider=provider_name,
            error=type(exc).__name__,
        )
        return ProviderHealth(
            provider=provider_name,
            model=provider.model_name,
            configured=True,
            reachable=False,
            status="error",
            detail=f"Connectivity test failed: {type(exc).__name__}",
        )
    return health


@router.get("/audit-log")
async def audit_log(
    limit: int = Query(default=100, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
    conn: DatabricksConnection = Depends(get_db),
    _: CurrentUser = Depends(require_admin),
) -> dict[str, Any]:
    rows = await (
        await conn.execute(
            """
            SELECT id, actor_user_id, action, entity_type, entity_id,
                   request_id, metadata, created_at
            FROM audit_log
            ORDER BY created_at DESC
            LIMIT %s OFFSET %s
            """,
            (limit, offset),
        )
    ).fetchall()
    return {
        "items": [_decode_row(row, "metadata") for row in rows],
        "limit": limit,
        "offset": offset,
    }


@router.get("/evals")
async def eval_cases(
    active_only: bool = Query(default=True),
    conn: DatabricksConnection = Depends(get_db),
    _: CurrentUser = Depends(require_admin),
) -> dict[str, Any]:
    return {"items": await list_eval_set(conn, active_only=active_only)}


@router.post("/evals", status_code=status.HTTP_201_CREATED)
async def add_eval_case(
    payload: EvalCaseRequest,
    conn: DatabricksConnection = Depends(get_db),
    user: CurrentUser = Depends(require_admin),
) -> dict[str, Any]:
    return await create_eval_case(
        conn,
        name=payload.name,
        question=payload.question,
        expected_keywords=payload.expected_keywords,
        expected_document_ids=payload.expected_document_ids,
        expected_answer=payload.expected_answer,
        language=payload.language,
        tags=payload.tags,
        user_id=user.id,
    )


@router.post("/evals/run")
async def execute_eval(
    payload: EvalRunRequest,
    conn: DatabricksConnection = Depends(get_db),
    settings: Settings = Depends(get_app_settings),
    user: CurrentUser = Depends(require_admin),
) -> dict[str, Any]:
    return await run_evaluation(
        conn,
        settings,
        user,
        name=payload.name,
        limit=payload.limit,
    )


@router.get("/evals/results")
async def eval_results(
    limit: int = Query(default=200, ge=1, le=1000),
    conn: DatabricksConnection = Depends(get_db),
    _: CurrentUser = Depends(require_admin),
) -> dict[str, Any]:
    return {"items": await latest_eval_results(conn, limit=limit)}


@router.post("/vision/evaluations", status_code=status.HTTP_201_CREATED)
async def add_vision_evaluation(
    payload: VisionEvaluationRequest,
    conn: DatabricksConnection = Depends(get_db),
    user: CurrentUser = Depends(require_admin),
) -> dict[str, Any]:
    evaluation_id = str(uuid4())
    async with conn.transaction():
        await conn.execute(
            """
            INSERT INTO vision_evaluations
                (id, document_version_id, document_page_id, provider, model,
                 evaluation_type, accuracy_score, completeness_score, visual_score,
                 metrics, notes, created_by, created_at)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, current_timestamp())
            """,
            (
                evaluation_id,
                payload.document_version_id,
                payload.document_page_id,
                payload.provider,
                payload.model,
                payload.evaluation_type,
                payload.accuracy_score,
                payload.completeness_score,
                payload.visual_score,
                Jsonb(payload.metrics),
                payload.notes,
                str(user.id),
            ),
        )
    row = await (
        await conn.execute(
            "SELECT * FROM vision_evaluations WHERE id = %s", (evaluation_id,)
        )
    ).fetchone()
    return _decode_row(row, "metrics") if row is not None else {}


@router.get("/operations/health")
async def operations_health(
    conn: DatabricksConnection = Depends(get_db),
    settings: Settings = Depends(get_app_settings),
    _: CurrentUser = Depends(require_admin),
) -> dict[str, Any]:
    counts = await (
        await conn.execute(
            """
            SELECT
                (SELECT count(*) FROM documents) AS documents,
                (SELECT count(*) FROM document_versions WHERE status = 'ready') AS ready_versions,
                (SELECT count(*) FROM ingestion_jobs WHERE status = 'failed') AS failed_jobs,
                (SELECT count(*) FROM ingestion_jobs WHERE status = 'running') AS running_jobs,
                (SELECT count(*) FROM document_chunks WHERE embedding IS NULL) AS missing_embeddings,
                (SELECT count(*) FROM knowledge_conflicts WHERE status = 'open') AS open_conflicts,
                (SELECT count(*) FROM unanswered_questions WHERE status = 'open') AS open_questions,
                (SELECT count(*) FROM request_logs
                 WHERE created_at > current_timestamp() - INTERVAL 1 HOUR)
                    AS requests_last_hour,
                (SELECT count(*) FROM request_logs
                 WHERE created_at > current_timestamp() - INTERVAL 1 HOUR
                   AND status_code >= 500)
                    AS errors_last_hour
            """
        )
    ).fetchone()
    recent_jobs = await (
        await conn.execute(
            """
            SELECT id, status, stage, progress, error_details, created_at, completed_at
            FROM ingestion_jobs ORDER BY created_at DESC LIMIT 10
            """
        )
    ).fetchall()
    voice = await transcription_health(settings)
    return {
        "status": "ok" if int(counts["errors_last_hour"]) == 0 else "degraded",
        "counts": counts,
        "recent_jobs": recent_jobs,
        "voice": voice,
        "providers": {
            "vision": settings.vision_provider,
            "chat": settings.chat_provider,
        },
    }
