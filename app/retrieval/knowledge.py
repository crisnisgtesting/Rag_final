"""Phase 4 knowledge-aware retrieval.

Phase 3 stores reviewable entities, workflows, rules and their source pages and
chunks. This module matches a natural-language query against that knowledge and
returns the chunks that back the matched knowledge so retrieval can promote
them above purely lexical matches.
"""

from __future__ import annotations

import re
from typing import Any

from psycopg import AsyncConnection

from app.logging_conf import get_logger

logger = get_logger(__name__)

_TOKEN_RE = re.compile(r"[a-z0-9]+")
_STOP_WORDS = {
    "about",
    "after",
    "before",
    "does",
    "from",
    "have",
    "how",
    "into",
    "should",
    "that",
    "their",
    "then",
    "there",
    "these",
    "they",
    "this",
    "what",
    "when",
    "where",
    "which",
    "while",
    "with",
    "would",
    "your",
}
_MIN_TOKEN_LENGTH = 4


def query_tokens(query: str, *, limit: int = 8) -> list[str]:
    tokens = [
        token
        for token in _TOKEN_RE.findall(query.lower())
        if len(token) >= _MIN_TOKEN_LENGTH and token not in _STOP_WORDS
    ]
    return list(dict.fromkeys(tokens))[:limit]


async def matched_knowledge(
    conn: AsyncConnection, query: str, *, limit: int = 25
) -> list[dict[str, Any]]:
    """Return the approved knowledge items that match the query tokens."""
    tokens = query_tokens(query)
    if not tokens:
        return []

    patterns = [f"%{token}%" for token in tokens]
    rows = await (
        await conn.execute(
            """
            SELECT 'entity' AS knowledge_type, e.id, e.name AS label,
                   e.description, e.normalized_name AS match_key
            FROM knowledge_entities e
            WHERE e.status = 'approved'
              AND e.normalized_name ILIKE ANY(%s)
            UNION ALL
            SELECT 'workflow', w.id, w.title, w.summary, w.normalized_title
            FROM workflows w
            WHERE w.status = 'approved'
              AND w.normalized_title ILIKE ANY(%s)
            UNION ALL
            SELECT 'rule', r.id, r.rule_type, r.rule_text, lower(r.rule_text)
            FROM knowledge_rules r
            WHERE r.status = 'approved'
              AND r.rule_text ILIKE ANY(%s)
            LIMIT %s
            """,
            (patterns, patterns, patterns, limit),
        )
    ).fetchall()

    matched: list[dict[str, Any]] = []
    for row in rows:
        tokens_hit = [
            token for token in tokens if token in str(row["match_key"] or "").lower()
        ]
        if not tokens_hit:
            continue
        matched.append(
            {
                "knowledge_type": row["knowledge_type"],
                "knowledge_id": str(row["id"]),
                "label": row["label"],
                "description": row["description"],
                "matched_tokens": tokens_hit,
                "strength": len(tokens_hit) / len(tokens),
            }
        )
    matched.sort(key=lambda item: item["strength"], reverse=True)
    return matched


async def knowledge_chunk_boosts(
    conn: AsyncConnection, query: str, *, limit: int = 25
) -> dict[str, float]:
    """Map chunk ids to a boost value based on matched knowledge sources."""
    matched = await matched_knowledge(conn, query, limit=limit)
    if not matched:
        return {}

    boosts: dict[str, float] = {}
    for item in matched:
        rows = await (
            await conn.execute(
                """
                SELECT DISTINCT document_chunk_id
                FROM knowledge_sources
                WHERE knowledge_type = %s AND knowledge_id = %s
                  AND document_chunk_id IS NOT NULL
                """,
                (item["knowledge_type"], item["knowledge_id"]),
            )
        ).fetchall()
        weight = 0.25 * float(item["strength"])
        for row in rows:
            chunk_id = str(row["document_chunk_id"])
            boosts[chunk_id] = max(boosts.get(chunk_id, 0.0), weight)
    return boosts


async def knowledge_summary_for_query(
    conn: AsyncConnection, query: str, *, limit: int = 10
) -> list[dict[str, Any]]:
    """Compact knowledge view used to enrich retrieval metadata."""
    matched = await matched_knowledge(conn, query, limit=limit)
    return [
        {
            "knowledge_type": item["knowledge_type"],
            "label": item["label"],
            "description": (item["description"] or "")[:600],
            "strength": round(item["strength"], 4),
        }
        for item in matched
    ]
