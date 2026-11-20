"""Retrieval and grounded-answer evaluation."""

from __future__ import annotations

import re
import time
from typing import Any
from uuid import UUID, uuid4

from app.auth.dependencies import CurrentUser
from app.chat.provider import ChatProvider
from app.chat.service import answer_question
from app.config import Settings
from app.db.compat import Jsonb, decode_json_value
from app.db.databricks import DatabricksConnection

_EVAL_JSON_FIELDS = ("expected_keywords", "expected_document_ids", "tags")
_RESULT_JSON_FIELDS = ("citations", "metrics")


def _decode_row(row: Any, *fields: str) -> dict[str, Any] | None:
    if row is None:
        return None
    item = dict(row)
    for field in fields:
        if field in item:
            item[field] = decode_json_value(item[field])
    return item


def _decode_rows(rows: Any, *fields: str) -> list[dict[str, Any]]:
    return [item for row in rows if (item := _decode_row(row, *fields)) is not None]


async def list_eval_set(
    conn: DatabricksConnection, *, active_only: bool = True
) -> list[dict[str, Any]]:
    where = "WHERE active = TRUE" if active_only else ""
    rows = await (
        await conn.execute(f"SELECT * FROM eval_set {where} ORDER BY name, created_at")
    ).fetchall()
    return _decode_rows(rows, *_EVAL_JSON_FIELDS)


async def create_eval_case(
    conn: DatabricksConnection,
    *,
    name: str,
    question: str,
    expected_keywords: list[str] | None = None,
    expected_document_ids: list[str] | None = None,
    expected_answer: str | None = None,
    language: str = "en",
    tags: list[str] | None = None,
    user_id: UUID | str | None = None,
) -> dict[str, Any]:
    eval_id = uuid4()
    await conn.execute(
        """
        INSERT INTO eval_set
            (id, name, question, expected_keywords, expected_document_ids,
             expected_answer, language, tags, active, created_by, created_at, updated_at)
        VALUES (%s, %s, %s, from_json(%s, 'ARRAY<STRING>'),
                from_json(%s, 'ARRAY<STRING>'), %s, %s,
                from_json(%s, 'ARRAY<STRING>'), TRUE, %s, current_timestamp(),
                current_timestamp())
        """,
        (
            str(eval_id),
            name,
            question,
            Jsonb(expected_keywords or []),
            Jsonb(expected_document_ids or []),
            expected_answer,
            language,
            Jsonb(tags or []),
            str(user_id) if user_id else None,
        ),
    )
    row = await (
        await conn.execute("SELECT * FROM eval_set WHERE id = %s", (str(eval_id),))
    ).fetchone()
    await conn.commit()
    return _decode_row(row, *_EVAL_JSON_FIELDS)


async def run_evaluation(
    conn: DatabricksConnection,
    settings: Settings,
    user: CurrentUser,
    *,
    name: str | None = None,
    limit: int = 50,
    provider: ChatProvider | None = None,
) -> dict[str, Any]:
    where = "WHERE active = TRUE"
    params: list[Any] = []
    if name:
        where += " AND name = %s"
        params.append(name)
    params.append(min(max(limit, 1), 200))
    case_rows = await (
        await conn.execute(
            f"SELECT * FROM eval_set {where} ORDER BY created_at LIMIT %s", tuple(params)
        )
    ).fetchall()
    cases = _decode_rows(case_rows, *_EVAL_JSON_FIELDS)
    run_id = uuid4()
    results: list[dict[str, Any]] = []
    for case in cases:
        started = time.perf_counter()
        response = await answer_question(
            conn,
            settings,
            user,
            question=str(case["question"]),
            provider=provider,
        )
        latency_ms = int((time.perf_counter() - started) * 1000)
        answer = str(response.get("answer") or "")
        keywords = [
            str(value) for value in decode_json_value(case.get("expected_keywords")) or []
        ]
        matched = [value for value in keywords if _contains(answer, value)]
        keyword_recall = len(matched) / len(keywords) if keywords else 1.0
        expected_docs = {
            str(value) for value in decode_json_value(case.get("expected_document_ids")) or []
        }
        actual_docs = {str(value["document_id"]) for value in response.get("citations") or []}
        document_match = not expected_docs or bool(expected_docs & actual_docs)
        citation_valid = response.get("citation_status") == "valid"
        passed = keyword_recall >= 0.8 and document_match and citation_valid
        result_id = uuid4()
        await conn.execute(
            """
            INSERT INTO eval_results
                (id, run_id, eval_id, response_id, passed, citation_valid,
                 keyword_recall, document_match, answer, citations, metrics,
                 latency_ms, created_at)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s,
                    current_timestamp())
            """,
            (
                str(result_id),
                str(run_id),
                str(case["id"]),
                response.get("response_id"),
                passed,
                citation_valid,
                round(keyword_recall, 4),
                document_match,
                answer,
                Jsonb(response.get("citations") or []),
                Jsonb(
                    {
                        "matched_keywords": matched,
                        "expected_document_ids": sorted(expected_docs),
                        "actual_document_ids": sorted(actual_docs),
                        "citation_status": response.get("citation_status"),
                    }
                ),
                latency_ms,
            ),
        )
        row = await (
            await conn.execute(
                "SELECT * FROM eval_results WHERE id = %s", (str(result_id),)
            )
        ).fetchone()
        result = _decode_row(row, *_RESULT_JSON_FIELDS)
        results.append(result)
    passed_count = sum(1 for row in results if row["passed"])
    return {
        "run_id": str(run_id),
        "cases": len(results),
        "passed": passed_count,
        "pass_rate": round(passed_count / len(results), 4) if results else 0.0,
        "results": results,
    }


async def latest_eval_results(
    conn: DatabricksConnection, *, limit: int = 200
) -> list[dict[str, Any]]:
    rows = await (
        await conn.execute(
            """
            SELECT r.*, e.name, e.question
            FROM eval_results r
            JOIN eval_set e ON e.id = r.eval_id
            ORDER BY r.created_at DESC
            LIMIT %s
            """,
            (min(max(limit, 1), 1000),),
        )
    ).fetchall()
    return _decode_rows(rows, *_RESULT_JSON_FIELDS)


def _contains(answer: str, keyword: str) -> bool:
    return re.search(re.escape(keyword), answer, re.IGNORECASE) is not None
