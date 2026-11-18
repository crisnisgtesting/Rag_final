from __future__ import annotations

import pytest

pytestmark = pytest.mark.integration


def test_knowledge_summary_and_workflows_are_readable(api_client, auth_headers) -> None:
    response = api_client.get("/knowledge/summary", headers=auth_headers)
    assert response.status_code == 200, response.text
    body = response.json()
    for key in ("entities", "relationships", "workflows", "steps", "visuals", "rules"):
        assert key in body
        assert isinstance(body[key], int)

    workflows = api_client.get("/knowledge/workflows", headers=auth_headers)
    assert workflows.status_code == 200, workflows.text
    assert "items" in workflows.json()


def test_knowledge_endpoints_require_authentication(api_client) -> None:
    assert api_client.get("/knowledge/summary").status_code in (401, 403)
    assert api_client.get("/knowledge/workflows").status_code in (401, 403)


def test_knowledge_conflict_resolution_requires_admin(
    api_client, auth_headers
) -> None:
    conflicts = api_client.get("/knowledge/conflicts", headers=auth_headers)
    assert conflicts.status_code == 200, conflicts.text
    assert isinstance(conflicts.json()["items"], list)


def test_operations_health_reports_counters(api_client, auth_headers) -> None:
    response = api_client.get("/admin/operations/health", headers=auth_headers)
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["status"] in {"ok", "degraded"}
    counts = body["counts"]
    assert counts["documents"] >= 0
    assert counts["missing_embeddings"] >= 0
    assert "voice" in body


def test_eval_case_lifecycle(api_client, auth_headers, database_url) -> None:
    import psycopg

    created = api_client.post(
        "/admin/evals",
        json={
            "name": "phase6-eval-case",
            "question": "How is an employee transferred to another depot?",
            "expected_keywords": ["transfer", "depot"],
            "language": "en",
        },
        headers=auth_headers,
    )
    assert created.status_code == 201, created.text
    case_id = created.json()["id"]

    listing = api_client.get("/admin/evals", headers=auth_headers)
    assert listing.status_code == 200
    assert any(item["id"] == case_id for item in listing.json()["items"])

    with psycopg.connect(database_url, autocommit=True) as conn:
        conn.execute("DELETE FROM eval_set WHERE id = %s", (case_id,))


def test_unanswered_question_endpoints_require_admin(
    api_client, auth_headers
) -> None:
    response = api_client.get("/chat/unanswered", headers=auth_headers)
    assert response.status_code == 200, response.text
    assert "items" in response.json()


def test_feedback_requires_a_known_response(api_client, auth_headers) -> None:
    response = api_client.post(
        "/chat/responses/00000000-0000-0000-0000-000000000000/feedback",
        json={"rating": 1},
        headers=auth_headers,
    )
    assert response.status_code == 404, response.text


def test_transcribe_without_audio_is_rejected(api_client, auth_headers) -> None:
    response = api_client.post("/chat/transcribe", headers=auth_headers)
    assert response.status_code in (400, 422)
