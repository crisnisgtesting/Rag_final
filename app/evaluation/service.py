"""Retrieval and grounded-answer evaluation."""

from __future__ import annotations

import re
import time
from typing import Any
from uuid import UUID, uuid4

from psycopg import AsyncConnection
from psycopg.types.json import Jsonb

from app.auth.dependencies import CurrentUser
from app.chat.provider import ChatProvider
from app.chat.service import answer_question
from app.config import Settings


async def list_eval_set(conn: AsyncConnection, *, active_only: bool = True) -> list[dict[str, Any]]:
    where = "WHERE active = TRUE" if active_only else ""
    return await (
        await conn.execute(
            f"SELECT * FROM eval_set {where} ORDER BY name, created_at"
        )
    ).fetchall()


async def create_eval_case(
    conn: AsyncConnection,
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
    row = await (
        await conn.execute(
            """
            INSERT INTO eval_set
                (name, question, expected_keywords, expected_document_ids,
                 expected_answer, language, tags, created_by)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
            RETURNING *
            """,
            (
                name,
                question,
                expected_keywords or [],
                expected_document_ids or [],
                expected_answer,
                language,
                tags or [],
                str(user_id) if user_id else None,
            ),
        )
    ).fetchone()
    await conn.commit()
    return row


async def run_evaluation(
    conn: AsyncConnection,
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
    cases = await (
        await conn.execute(
            f"SELECT * FROM eval_set {where} ORDER BY created_at LIMIT %s", tuple(params)
        )
    ).fetchall()
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
        keywords = [str(value) for value in case["expected_keywords"] or []]
        matched = [value for value in keywords if _contains(answer, value)]
        keyword_recall = len(matched) / len(keywords) if keywords else 1.0
        expected_docs = {str(value) for value in case["expected_document_ids"] or []}
        actual_docs = {str(value["document_id"]) for value in response.get("citations") or []}
        document_match = not expected_docs or bool(expected_docs & actual_docs)
        citation_valid = response.get("citation_status") == "valid"
        passed = keyword_recall >= 0.8 and document_match and citation_valid
        row = await (
            await conn.execute(
                """
                INSERT INTO eval_results
                    (run_id, eval_id, response_id, passed, citation_valid,
                     keyword_recall, document_match, answer, citations, metrics,
                     latency_ms)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                RETURNING *
                """,
                (
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
        ).fetchone()
        results.append(row)
    passed_count = sum(1 for row in results if row["passed"])
    return {
        "run_id": str(run_id),
        "cases": len(results),
        "passed": passed_count,
        "pass_rate": round(passed_count / len(results), 4) if results else 0.0,
        "results": results,
    }


async def latest_eval_results(
    conn: AsyncConnection, *, limit: int = 200
) -> list[dict[str, Any]]:
    return await (
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


def _contains(answer: str, keyword: str) -> bool:
    return re.search(re.escape(keyword), answer, re.IGNORECASE) is not None
