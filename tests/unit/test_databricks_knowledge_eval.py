from __future__ import annotations

import asyncio
import json
from contextlib import asynccontextmanager
from typing import Any
from uuid import UUID, uuid4

from app.evaluation import service as evaluation_service
from app.knowledge import service as knowledge_service
from app.knowledge import extractor


class FakeCursor:
    def __init__(self, response: Any) -> None:
        self._response = response

    async def fetchone(self) -> Any:
        if isinstance(self._response, list):
            return None
        return self._response

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

    async def rollback(self) -> None:
        return None

    @asynccontextmanager
    async def transaction(self):
        yield self

    @property
    def all_sql(self) -> str:
        return "\n".join(self.queries)


def _entity_insert_params(conn: FakeConnection) -> Any:
    for query, params in zip(conn.queries, conn.params):
        if "INSERT INTO knowledge_entities" in query:
            return params
    raise AssertionError("knowledge_entities insert was not issued")


def test_set_knowledge_status_uses_update_then_select() -> None:
    item_id = uuid4()
    conn = FakeConnection(
        None,
        None,
        None,
        None,
        {"id": str(item_id), "metadata": json.dumps({"source": "manual"})},
        None,
    )
    row = asyncio.run(
        knowledge_service.set_knowledge_status(
            conn,
            knowledge_type="workflow",
            knowledge_id=item_id,
            status="approved",
            actor_user_id=uuid4(),
        )
    )
    assert "RETURNING" not in conn.all_sql
    assert "current_timestamp()" in conn.all_sql
    assert "SELECT * FROM workflows WHERE id = %s" in conn.all_sql
    assert row["metadata"] == {"source": "manual"}


def test_manual_override_merges_metadata_without_json_concat() -> None:
    workflow_id = uuid4()
    conn = FakeConnection(
        {"id": str(workflow_id), "summary": "old", "metadata": json.dumps({"a": 1})},
        None,
        {"id": str(workflow_id), "metadata": json.dumps({"a": 1, "b": 2})},
        None,
    )
    row = asyncio.run(
        knowledge_service.store_manual_knowledge_override(
            conn,
            workflow_id=workflow_id,
            actor_user_id=uuid4(),
            metadata={"b": 2},
        )
    )
    assert "metadata ||" not in conn.all_sql
    assert "RETURNING" not in conn.all_sql
    assert row["metadata"] == {"a": 1, "b": 2}


def test_workflow_detail_decodes_json_columns() -> None:
    workflow_id = uuid4()
    conn = FakeConnection(
        {
            "id": str(workflow_id),
            "title": "Procedure",
            "metadata": json.dumps({"reviewed": True}),
        },
        [
            {
                "id": str(uuid4()),
                "conditions": json.dumps(["if locked"]),
                "warnings": json.dumps(["check pressure"]),
            }
        ],
        [{"id": str(uuid4()), "bounding_box": json.dumps({"x": 1})}],
        [{"id": str(uuid4()), "metadata": json.dumps({"rule": "r1"})}],
        [{"id": str(uuid4()), "metadata": json.dumps({"source": "s1"})}],
    )
    detail = asyncio.run(knowledge_service.workflow_detail(conn, workflow_id))
    assert detail["workflow"]["metadata"] == {"reviewed": True}
    assert detail["steps"][0]["conditions"] == ["if locked"]
    assert detail["visuals"][0]["bounding_box"] == {"x": 1}
    assert detail["rules"][0]["metadata"] == {"rule": "r1"}
    assert detail["sources"][0]["metadata"] == {"source": "s1"}


def test_list_workflows_uses_aggregate_joins_without_lateral() -> None:
    conn = FakeConnection([])
    asyncio.run(knowledge_service.list_workflows(conn, limit=5))
    assert "LATERAL" not in conn.all_sql
    assert "step_counts.step_count" in conn.all_sql
    assert "visual_counts.visual_count" in conn.all_sql


def test_rebuild_version_knowledge_decodes_page_json_and_generates_ids() -> None:
    version_id = uuid4()
    document_id = uuid4()
    page_id = uuid4()
    conn = FakeConnection(
        {
            "id": str(version_id),
            "document_id": str(document_id),
            "title": "Manual",
            "filename": "manual.pdf",
            "document_type": "manual",
        },
        [
            {
                "id": str(page_id),
                "physical_page_number": 1,
                "page_text": "Valve instructions",
                "content_type": "procedure",
                "layout": json.dumps({"format": "markdown", "section_number": "4"}),
                "analysis": json.dumps(
                    {
                        "confidence": 0.9,
                        "domain_knowledge": [
                            {
                                "name": "Valve",
                                "entity_type": "component",
                                "description": "Controls flow",
                                "attributes": {"code": "V-1"},
                            }
                        ],
                    }
                ),
                "confidence": 0.9,
                "page_image_path": "/Volumes/page-1.png",
            }
        ],
    )
    stats = asyncio.run(extractor.rebuild_version_knowledge(conn, version_id))
    params = _entity_insert_params(conn)
    assert stats["entities"] == 1
    assert params[4] == "Valve"
    assert json.loads(params[7].value) == {"code": "V-1"}
    assert UUID(str(params[0]))
    assert "current_timestamp()" in conn.all_sql
    assert "ON CONFLICT" not in conn.all_sql
    assert "now()" not in conn.all_sql
    assert "->>" not in conn.all_sql
    assert "RETURNING" not in conn.all_sql


def test_create_eval_case_uses_generated_id_and_array_json() -> None:
    eval_id = uuid4()
    conn = FakeConnection(
        None,
        {
            "id": str(eval_id),
            "name": "basic",
            "expected_keywords": ["valve"],
            "expected_document_ids": ["doc-1"],
            "tags": ["smoke"],
        },
    )
    row = asyncio.run(
        evaluation_service.create_eval_case(
            conn,
            name="basic",
            question="What is a valve?",
            expected_keywords=["valve"],
            expected_document_ids=["doc-1"],
            tags=["smoke"],
            user_id=uuid4(),
        )
    )
    assert "RETURNING" not in conn.all_sql
    assert "from_json(%s, 'ARRAY<STRING>')" in conn.all_sql
    assert "current_timestamp()" in conn.all_sql
    assert UUID(str(conn.params[0][0]))
    assert row["expected_keywords"] == ["valve"]
    assert row["tags"] == ["smoke"]


def test_run_evaluation_stores_results_without_returning(
    monkeypatch: Any,
) -> None:
    async def fake_answer(conn, settings, user, *, question, provider=None):
        return {
            "answer": "Use the valve procedure.",
            "citations": [{"document_id": "doc-1"}],
            "citation_status": "valid",
            "response_id": str(uuid4()),
        }

    monkeypatch.setattr(evaluation_service, "answer_question", fake_answer)
    conn = FakeConnection(
        [
            {
                "id": str(uuid4()),
                "question": "What is the procedure?",
                "expected_keywords": json.dumps(["valve"]),
                "expected_document_ids": json.dumps(["doc-1"]),
            }
        ],
        None,
        {
            "id": str(uuid4()),
            "passed": True,
            "citations": json.dumps([{"document_id": "doc-1"}]),
            "metrics": json.dumps({"matched_keywords": ["valve"]}),
        },
    )
    result = asyncio.run(
        evaluation_service.run_evaluation(conn, object(), object(), limit=1)
    )
    assert "RETURNING" not in conn.all_sql
    assert "current_timestamp()" in conn.all_sql
    assert result["passed"] == 1
    assert result["results"][0]["citations"] == [{"document_id": "doc-1"}]
    assert result["results"][0]["metrics"]["matched_keywords"] == ["valve"]


def test_latest_eval_results_decodes_json_columns() -> None:
    conn = FakeConnection(
        [
            {
                "id": str(uuid4()),
                "citations": json.dumps([{"marker": "S1"}]),
                "metrics": json.dumps({"pass_rate": 1.0}),
            }
        ]
    )
    rows = asyncio.run(evaluation_service.latest_eval_results(conn, limit=3))
    assert rows[0]["citations"] == [{"marker": "S1"}]
    assert rows[0]["metrics"] == {"pass_rate": 1.0}
