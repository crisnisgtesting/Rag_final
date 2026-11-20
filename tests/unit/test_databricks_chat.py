from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from typing import Any
from uuid import UUID, uuid4

from app.chat import service as chat_service


class FakeCursor:
    def __init__(self, response: Any) -> None:
        self._response = response

    async def fetchone(self) -> Any:
        return self._response if not isinstance(self._response, list) else None

    async def fetchall(self) -> Any:
        return self._response if isinstance(self._response, list) else []


class FakeConnection:
    def __init__(self, *responses: Any) -> None:
        self.responses = list(responses)
        self.queries: list[str] = []
        self.params: list[Any] = []

    async def execute(self, query: str, params: Any = None) -> FakeCursor:
        self.queries.append(query)
        self.params.append(params)
        response = self.responses.pop(0) if self.responses else None
        return FakeCursor(response)

    async def commit(self) -> None:
        return None

    @property
    def all_sql(self) -> str:
        return "\n".join(self.queries)

    @asynccontextmanager
    async def transaction(self):
        yield self


def test_get_or_create_session_uses_generated_uuid() -> None:
    session_id = uuid4()
    conn = FakeConnection(None, {"id": str(session_id), "title": "Question"})
    result = asyncio.run(
        chat_service._get_or_create_session(conn, uuid4(), "Question title", None)
    )
    assert result == (str(session_id), "Question")
    assert "RETURNING" not in conn.all_sql
    assert UUID(str(conn.params[0][0]))


def test_save_exchange_uses_generated_ids_and_no_returning() -> None:
    conn = FakeConnection(None, None, None, None, None)
    result = asyncio.run(
        chat_service._save_exchange(
            conn,
            session_id=uuid4(),
            title="Question",
            user_id=uuid4(),
            language="en",
            question="What is the procedure?",
            answer="Use [S1].",
            citations=[],
            retrieval={"results": 1},
            provider="databricks",
            model="endpoint",
            usage={"tokens": 1},
            latency_ms=10,
            unanswered_reason="no_evidence",
        )
    )
    assert "RETURNING" not in conn.all_sql
    assert UUID(result["message_id"])
    assert UUID(result["response_id"])
    assert "current_timestamp()" in conn.all_sql


def test_submit_feedback_uses_merge_and_no_on_conflict() -> None:
    response_id = uuid4()
    conn = FakeConnection({"id": str(response_id)}, None, {"id": str(uuid4())})
    asyncio.run(
        chat_service.submit_feedback(
            conn,
            user_id=uuid4(),
            response_id=response_id,
            rating=1,
            comment="helpful",
        )
    )
    assert "MERGE INTO feedback" in conn.all_sql
    assert "ON CONFLICT" not in conn.all_sql
    assert "RETURNING" not in conn.all_sql


def test_resolve_unanswered_uses_update_then_select() -> None:
    question_id = uuid4()
    conn = FakeConnection(None, {"id": str(question_id)})
    asyncio.run(
        chat_service.resolve_unanswered(
            conn,
            question_id,
            status="resolved",
            resolution="done",
            user_id=uuid4(),
        )
    )
    assert "RETURNING" not in conn.all_sql
    assert "current_timestamp()" in conn.all_sql


def test_list_sessions_avoids_postgres_filter_clause() -> None:
    conn = FakeConnection([])
    asyncio.run(chat_service.list_sessions(conn, uuid4()))
    assert "FILTER (" not in conn.all_sql
    assert "CASE WHEN m.role = 'assistant'" in conn.all_sql
