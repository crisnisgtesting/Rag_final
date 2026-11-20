"""Grounded chat endpoints."""

from __future__ import annotations

import json
from typing import Any
from uuid import UUID

from fastapi import APIRouter, Depends, File, HTTPException, Query, Request, UploadFile
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from app.api.deps import get_app_settings
from app.auth.dependencies import CurrentUser, get_current_user, require_admin
from app.chat.provider import ChatConfigurationError, ChatError
from app.chat.service import (
    ChatServiceError,
    answer_question,
    list_sessions,
    list_unanswered,
    resolve_unanswered,
    session_detail,
    stream_answer_events,
    submit_feedback,
)
from app.config import Settings
from app.db.databricks import DatabricksConnection
from app.db.pool import get_db
from app.retrieval.embeddings import EmbeddingError
from app.retrieval.search import RetrievalError
from app.voice.transcribe import (
    TranscriptionConfigurationError,
    TranscriptionError,
    transcribe_audio,
    transcription_health,
)

router = APIRouter(prefix="/chat", tags=["chat"])


class ChatRequest(BaseModel):
    question: str = Field(min_length=1, max_length=4000)
    session_id: UUID | None = None
    top_k: int | None = Field(default=None, ge=1, le=20)
    document_ids: list[UUID] | None = None
    document_types: list[str] | None = None
    content_types: list[str] | None = None


class FeedbackRequest(BaseModel):
    rating: int = Field(ge=-1, le=1)
    comment: str | None = Field(default=None, max_length=4000)


class UnansweredResolution(BaseModel):
    status: str = Field(pattern="^(open|in_review|resolved|ignored)$")
    resolution: str = Field(min_length=1, max_length=4000)


@router.post("")
async def chat(
    payload: ChatRequest,
    conn: DatabricksConnection = Depends(get_db),
    settings: Settings = Depends(get_app_settings),
    user: CurrentUser = Depends(get_current_user),
) -> dict[str, Any]:
    try:
        return await answer_question(
            conn,
            settings,
            user,
            question=payload.question,
            session_id=payload.session_id,
            top_k=payload.top_k,
            document_ids=payload.document_ids,
            document_types=payload.document_types,
            content_types=payload.content_types,
        )
    except ChatServiceError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except ChatConfigurationError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except ChatError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    except EmbeddingError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except RetrievalError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/stream")
async def chat_stream(
    payload: ChatRequest,
    request: Request,
    conn: DatabricksConnection = Depends(get_db),
    settings: Settings = Depends(get_app_settings),
    user: CurrentUser = Depends(get_current_user),
) -> StreamingResponse:
    request_id = getattr(request.state, "request_id", None)

    async def events():
        try:
            async for event in stream_answer_events(
                conn,
                settings,
                user,
                question=payload.question,
                session_id=payload.session_id,
                top_k=payload.top_k,
                document_ids=payload.document_ids,
                document_types=payload.document_types,
                content_types=payload.content_types,
                request_id=request_id,
            ):
                yield (
                    f"event: {event['event']}\n"
                    f"data: {json.dumps(event['data'], default=str)}\n\n"
                )
        except (ChatServiceError, ChatConfigurationError, ChatError,
                EmbeddingError, RetrievalError) as exc:
            yield (
                "event: error\n"
                f"data: {json.dumps({'message': str(exc), 'type': type(exc).__name__})}\n\n"
            )

    return StreamingResponse(
        events(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
        },
    )


@router.post("/responses/{response_id}/feedback")
async def feedback(
    response_id: UUID,
    payload: FeedbackRequest,
    conn: DatabricksConnection = Depends(get_db),
    user: CurrentUser = Depends(get_current_user),
) -> dict[str, Any]:
    try:
        result = await submit_feedback(
            conn,
            user_id=user.id,
            response_id=response_id,
            rating=payload.rating,
            comment=payload.comment,
        )
        return result
    except ChatServiceError as exc:
        await conn.rollback()
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.get("/unanswered")
async def unanswered(
    status: str = Query(default="open"),
    limit: int = Query(default=100, ge=1, le=500),
    conn: DatabricksConnection = Depends(get_db),
    _: CurrentUser = Depends(require_admin),
) -> dict[str, Any]:
    return {"items": await list_unanswered(conn, status=status, limit=limit)}


@router.post("/unanswered/{question_id}/resolve")
async def resolve_question(
    question_id: UUID,
    payload: UnansweredResolution,
    conn: DatabricksConnection = Depends(get_db),
    user: CurrentUser = Depends(require_admin),
) -> dict[str, Any]:
    try:
        return await resolve_unanswered(
            conn,
            question_id,
            status=payload.status,
            resolution=payload.resolution,
            user_id=user.id,
        )
    except ChatServiceError as exc:
        await conn.rollback()
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.get("/voice/capability")
async def voice_capability(
    settings: Settings = Depends(get_app_settings),
    _: CurrentUser = Depends(get_current_user),
) -> dict[str, Any]:
    """Report whether microphone dictation can be transcribed by the service."""
    return await transcription_health(settings)


@router.post("/transcribe")
async def transcribe(
    file: UploadFile = File(...),
    settings: Settings = Depends(get_app_settings),
    _: CurrentUser = Depends(get_current_user),
) -> dict[str, Any]:
    audio = await file.read()
    try:
        return await transcribe_audio(
            settings, audio, filename=file.filename or "audio.webm"
        )
    except TranscriptionConfigurationError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except TranscriptionError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.get("/sessions")
async def get_sessions(
    limit: int = Query(default=50, ge=1, le=200),
    conn: DatabricksConnection = Depends(get_db),
    user: CurrentUser = Depends(get_current_user),
) -> dict[str, Any]:
    return {"items": await list_sessions(conn, user.id, limit=limit)}


@router.get("/sessions/{session_id}")
async def get_session(
    session_id: UUID,
    conn: DatabricksConnection = Depends(get_db),
    user: CurrentUser = Depends(get_current_user),
) -> dict[str, Any]:
    detail = await session_detail(conn, user.id, session_id)
    if detail is None:
        raise HTTPException(status_code=404, detail="Chat session was not found")
    return detail
