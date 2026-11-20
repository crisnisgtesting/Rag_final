from __future__ import annotations

import asyncio
import json
from typing import Any
from uuid import uuid4

from app.config import Settings
from app.retrieval import knowledge as retrieval_knowledge
from app.retrieval import search


class FakeCursor:
    def __init__(self, response: Any) -> None:
        self._response = response

    async def fetchone(self) -> Any:
        return None

    async def fetchall(self) -> Any:
        return self._response


class FakeConnection:
    def __init__(self, response: Any) -> None:
        self.response = response
        self.queries: list[str] = []
        self.params: list[Any] = []

    async def execute(self, query: str, params: Any = None) -> FakeCursor:
        self.queries.append(query)
        self.params.append(params)
        return FakeCursor(self.response)


class FakeEmbedder:
    model_name = "fake"
    dim = 3

    async def encode_documents(self, texts: Any) -> list[list[float]]:
        return [[1.0, 0.0, 0.0] for _ in texts]

    async def encode_query(self, text: str) -> list[float]:
        return [1.0, 0.0, 0.0]


def _settings() -> Settings:
    return Settings(
        app_env="test",
        databricks_host="https://workspace.databricks.com",
        databricks_client_id="client-id",
        databricks_client_secret="client-secret",
        retrieval_knowledge_boost=False,
        embedding_dimension=3,
    )


def _row(chunk_id: str, embedding: list[float], content: str = "valve procedure") -> dict[str, Any]:
    return {
        "id": chunk_id,
        "document_version_id": str(uuid4()),
        "document_id": str(uuid4()),
        "page_start": 1,
        "page_end": 1,
        "chunk_index": 0,
        "section_number": "4.1",
        "heading": "Valve service",
        "content_type": "procedure",
        "content": content,
        "metadata": json.dumps({"source": "manual"}),
        "title": "Service Manual",
        "filename": "manual.pdf",
        "version_number": 1,
        "embedding": embedding,
    }


def test_filters_use_explicit_placeholders() -> None:
    clauses, params = search._filters(
        True, [uuid4(), uuid4()], ["pdf"], ["procedure"]
    )
    assert "ANY(" not in clauses
    assert "::uuid[]" not in clauses
    assert "IN (%s, %s)" in clauses
    assert len(params) == 4


def test_dense_rows_score_cosine_without_sql() -> None:
    rows = [_row("a", [1.0, 0.0, 0.0]), _row("b", [0.0, 1.0, 0.0])]
    scored = search._dense_rows(_settings(), rows, [1.0, 0.0, 0.0])
    assert [row["id"] for row in scored] == ["a"]
    assert scored[0]["dense_score"] == 1.0


def test_lexical_rows_score_token_overlap_without_tsvector() -> None:
    rows = [_row("a", [1.0, 0.0, 0.0]), _row("b", [0.0, 1.0, 0.0], "unrelated")]
    scored = search._lexical_rows(_settings(), "valve procedure", rows)
    assert [row["id"] for row in scored][0] == "a"
    assert scored[0]["lexical_score"] > scored[1]["lexical_score"]


def test_hybrid_search_uses_delta_scan_only() -> None:
    conn = FakeConnection([_row("a", [1.0, 0.0, 0.0])])
    results = asyncio.run(
        search.hybrid_search(
            conn,
            _settings(),
            FakeEmbedder(),
            "valve procedure",
            top_k=2,
        )
    )
    query = conn.queries[0]
    assert "<=>" not in query
    assert "ts_rank" not in query
    assert "c.embedding" in query
    assert results
    assert results[0].metadata == {"source": "manual"}


def test_matched_knowledge_uses_explicit_like_or_conditions() -> None:
    conn = FakeConnection([])
    asyncio.run(retrieval_knowledge.matched_knowledge(conn, "valve procedure"))
    query = conn.queries[0]
    assert "ILIKE ANY" not in query
    assert "lower(e.normalized_name) LIKE %s" in query
    assert len(conn.params[0]) == 7
