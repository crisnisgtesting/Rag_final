"""Hybrid dense + lexical retrieval with reciprocal rank fusion."""

from __future__ import annotations

import json
from dataclasses import dataclass, replace
from typing import Any
from uuid import UUID

from psycopg import AsyncConnection

from app.config import Settings
from app.logging_conf import get_logger
from app.retrieval.embeddings import Embedder, EmbeddingError
from app.retrieval.knowledge import knowledge_chunk_boosts
from app.retrieval.reranker import Reranker, get_reranker

logger = get_logger(__name__)


class RetrievalError(RuntimeError):
    pass


@dataclass(frozen=True)
class RetrievedChunk:
    id: str
    document_id: str
    version_id: str
    document_title: str
    filename: str
    version_number: int
    chunk_index: int
    page_start: int
    page_end: int
    section_number: str | None
    heading: str | None
    content_type: str
    content: str
    metadata: dict[str, Any]
    score: float = 0.0
    dense_score: float = 0.0
    lexical_score: float = 0.0

    def as_dict(self) -> dict[str, Any]:
        return {
            "chunk_id": self.id,
            "document_id": self.document_id,
            "version_id": self.version_id,
            "document_title": self.document_title,
            "filename": self.filename,
            "version_number": self.version_number,
            "chunk_index": self.chunk_index,
            "page_start": self.page_start,
            "page_end": self.page_end,
            "section_number": self.section_number,
            "heading": self.heading,
            "content_type": self.content_type,
            "content": self.content,
            "metadata": self.metadata,
            "score": round(self.score, 6),
            "dense_score": round(self.dense_score, 6),
            "lexical_score": round(self.lexical_score, 6),
        }


async def hybrid_search(
    conn: AsyncConnection,
    settings: Settings,
    embedder: Embedder,
    query: str,
    *,
    top_k: int | None = None,
    approved_only: bool = True,
    document_ids: list[UUID] | None = None,
    document_types: list[str] | None = None,
    content_types: list[str] | None = None,
    reranker: Reranker | None = None,
) -> list[RetrievedChunk]:
    query = query.strip()
    if not query:
        raise RetrievalError("Search query cannot be empty")

    top_k = max(1, min(top_k or settings.retrieval_top_k, 50))
    filters, filter_params = _filters(
        approved_only, document_ids, document_types, content_types
    )

    vector: list[float] | None
    try:
        vector = await embedder.encode_query(query)
    except EmbeddingError as exc:
        logger.warning("dense_retrieval_unavailable", error=str(exc))
        vector = None

    dense = await _dense_rows(conn, settings, vector, filters, filter_params)
    lexical = await _lexical_rows(conn, settings, query, filters, filter_params)
    fused = _fuse(dense, lexical, k=settings.retrieval_rrf_k)
    if not fused and vector is None:
        raise RetrievalError(
            "No retrieval backend is available. Install the embedding model or "
            "ensure the approved corpus contains indexable text."
        )

    candidates = fused[: settings.retrieval_rerank_candidates]
    candidates = await _apply_knowledge_boost(conn, settings, query, candidates)
    candidates = await _apply_reranker(
        conn, settings, query, candidates, reranker=reranker
    )
    return candidates[:top_k]


async def _apply_knowledge_boost(
    conn: AsyncConnection,
    settings: Settings,
    query: str,
    candidates: list[RetrievedChunk],
) -> list[RetrievedChunk]:
    """Promote chunks that back matched Phase 3 knowledge."""
    if not settings.retrieval_knowledge_boost:
        return candidates
    try:
        boosts = await knowledge_chunk_boosts(conn, query)
    except Exception as exc:  # noqa: BLE001 - retrieval must not fail on the boost
        logger.warning("knowledge_boost_unavailable", error=str(exc))
        return candidates
    if not boosts:
        return candidates
    boosted: list[RetrievedChunk] = []
    for chunk in candidates:
        bonus = boosts.get(chunk.id, 0.0)
        if not bonus:
            boosted.append(chunk)
            continue
        boosted.append(
            replace(
                chunk,
                score=chunk.score + bonus,
                metadata={**chunk.metadata, "knowledge_boost": round(bonus, 6)},
            )
        )
    return sorted(boosted, key=lambda item: item.score, reverse=True)


async def _apply_reranker(
    conn: AsyncConnection,
    settings: Settings,
    query: str,
    candidates: list[RetrievedChunk],
    *,
    reranker: Reranker | None,
) -> list[RetrievedChunk]:
    """Optionally reorder candidates with a cross-encoder reranker."""
    if not candidates:
        return candidates
    active = reranker or get_reranker(settings)
    if getattr(active, "model_name", "disabled") == "disabled":
        return candidates
    try:
        ranked = await active.rerank(
            query,
            [chunk.content for chunk in candidates],
            top_k=len(candidates),
        )
    except Exception as exc:  # noqa: BLE001 - reranking is best effort
        logger.warning("reranker_unavailable", error=str(exc))
        return candidates
    reordered: list[RetrievedChunk] = []
    for index, score in ranked:
        if 0 <= index < len(candidates):
            chunk = candidates[index]
            reordered.append(
                replace(
                    chunk,
                    metadata={
                        **chunk.metadata,
                        "rerank_score": round(float(score), 6),
                        "reranker_model": active.model_name,
                    },
                )
            )
    return reordered or candidates


def _filters(
    approved_only: bool,
    document_ids: list[UUID] | None,
    document_types: list[str] | None = None,
    content_types: list[str] | None = None,
) -> tuple[str, list[Any]]:
    clauses: list[str] = []
    params: list[Any] = []
    if approved_only:
        clauses.append("dv.approval_status = 'approved'")
        clauses.append("d.status = 'active'")
    else:
        clauses.append("dv.status = 'ready'")
    if document_ids:
        clauses.append("c.document_id = ANY(%s::uuid[])")
        params.append([str(value) for value in document_ids])
    if document_types:
        clauses.append("d.document_type = ANY(%s::text[])")
        params.append([str(value) for value in document_types])
    if content_types:
        clauses.append("c.content_type = ANY(%s::text[])")
        params.append([str(value) for value in content_types])
    return " AND ".join(clauses), params


async def _dense_rows(
    conn: AsyncConnection,
    settings: Settings,
    vector: list[float] | None,
    filters: str,
    filter_params: list[Any],
) -> list[dict[str, Any]]:
    if vector is None:
        return []
    literal = json.dumps(vector)
    limit = max(1, settings.retrieval_dense_candidates)
    rows = await (
        await conn.execute(
            f"""
            SELECT {_CHUNK_SELECT},
                   1 - (c.embedding <=> %s::vector) AS dense_score
            FROM document_chunks c
            JOIN document_versions dv ON dv.id = c.document_version_id
            JOIN documents d ON d.id = c.document_id
            WHERE {filters}
              AND c.embedding IS NOT NULL
            ORDER BY c.embedding <=> %s::vector
            LIMIT %s
            """,
            (literal, *filter_params, literal, limit),
        )
    ).fetchall()
    return rows


async def _lexical_rows(
    conn: AsyncConnection,
    settings: Settings,
    query: str,
    filters: str,
    filter_params: list[Any],
) -> list[dict[str, Any]]:
    limit = max(1, settings.retrieval_lexical_candidates)
    rows = await (
        await conn.execute(
            f"""
            SELECT {_CHUNK_SELECT},
                   ts_rank_cd(c.tsv, plainto_tsquery('simple', %s)) AS lexical_score
            FROM document_chunks c
            JOIN document_versions dv ON dv.id = c.document_version_id
            JOIN documents d ON d.id = c.document_id
            WHERE {filters}
              AND c.tsv @@ plainto_tsquery('simple', %s)
            ORDER BY lexical_score DESC
            LIMIT %s
            """,
            (query, *filter_params, query, limit),
        )
    ).fetchall()
    return rows


def _fuse(
    dense: list[dict[str, Any]], lexical: list[dict[str, Any]], *, k: int
) -> list[RetrievedChunk]:
    scores: dict[str, float] = {}
    dense_scores: dict[str, float] = {}
    lexical_scores: dict[str, float] = {}
    rows: dict[str, dict[str, Any]] = {}

    for rank, row in enumerate(dense, start=1):
        key = str(row["id"])
        rows[key] = row
        dense_scores[key] = float(row.get("dense_score") or 0)
        scores[key] = scores.get(key, 0.0) + 1.0 / (k + rank)

    for rank, row in enumerate(lexical, start=1):
        key = str(row["id"])
        rows.setdefault(key, row)
        lexical_scores[key] = float(row.get("lexical_score") or 0)
        scores[key] = scores.get(key, 0.0) + 1.0 / (k + rank)

    results = [
        _row_to_chunk(
            row,
            score=scores[key],
            dense_score=dense_scores.get(key, 0.0),
            lexical_score=lexical_scores.get(key, 0.0),
        )
        for key, row in rows.items()
    ]
    return sorted(results, key=lambda item: item.score, reverse=True)


def _row_to_chunk(
    row: dict[str, Any], *, score: float, dense_score: float, lexical_score: float
) -> RetrievedChunk:
    return RetrievedChunk(
        id=str(row["id"]),
        document_id=str(row["document_id"]),
        version_id=str(row["document_version_id"]),
        document_title=str(row["title"]),
        filename=str(row["filename"]),
        version_number=int(row["version_number"]),
        chunk_index=int(row["chunk_index"]),
        page_start=int(row["page_start"]),
        page_end=int(row["page_end"]),
        section_number=row.get("section_number"),
        heading=row.get("heading"),
        content_type=str(row.get("content_type") or "unknown"),
        content=str(row["content"]),
        metadata=dict(row.get("metadata") or {}),
        score=score,
        dense_score=dense_score,
        lexical_score=lexical_score,
    )


_CHUNK_SELECT = """
c.id, c.document_version_id, c.document_id, c.page_start, c.page_end,
c.chunk_index, c.section_number, c.heading, c.content_type, c.content,
c.metadata, d.title, d.filename, dv.version_number
"""
