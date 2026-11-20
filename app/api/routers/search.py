"""Retrieval debugging and search API."""

from __future__ import annotations

from typing import Any
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from app.api.deps import get_app_settings
from app.auth.dependencies import CurrentUser, get_current_user
from app.config import Settings
from app.db.databricks import DatabricksConnection
from app.db.pool import get_db
from app.retrieval.embeddings import EmbeddingError, get_embedder
from app.retrieval.search import RetrievalError, hybrid_search

router = APIRouter(prefix="/search", tags=["search"])


class SearchRequest(BaseModel):
    query: str = Field(min_length=1, max_length=4000)
    top_k: int | None = Field(default=None, ge=1, le=50)
    document_ids: list[UUID] | None = None
    document_types: list[str] | None = None
    content_types: list[str] | None = None
    include_drafts: bool = False


@router.post("")
async def search(
    payload: SearchRequest,
    conn: DatabricksConnection = Depends(get_db),
    settings: Settings = Depends(get_app_settings),
    user: CurrentUser = Depends(get_current_user),
) -> dict[str, Any]:
    if payload.include_drafts and not user.is_admin:
        raise HTTPException(
            status_code=403, detail="Only administrators can search draft versions"
        )
    try:
        results = await hybrid_search(
            conn,
            settings,
            get_embedder(settings),
            payload.query,
            top_k=payload.top_k,
            approved_only=not payload.include_drafts,
            document_ids=payload.document_ids,
            document_types=payload.document_types,
            content_types=payload.content_types,
        )
    except EmbeddingError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except RetrievalError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"query": payload.query, "items": [item.as_dict() for item in results]}
