from __future__ import annotations

import pytest

pytestmark = pytest.mark.integration


def test_liveness(api_client) -> None:
    response = api_client.get("/health/live")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_readiness_reports_database_and_pgvector(api_client) -> None:
    response = api_client.get("/health/ready")
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["status"] == "ready"
    assert body["database"] is True
    assert body["pgvector"] is True


def test_security_headers_and_request_id(api_client) -> None:
    response = api_client.get("/api/version")
    assert response.status_code == 200
    assert response.headers["X-Content-Type-Options"] == "nosniff"
    assert response.headers["X-Frame-Options"] == "DENY"
    assert response.headers["Cache-Control"] == "no-store"
    assert response.headers["X-Request-ID"]


def test_request_id_is_echoed(api_client) -> None:
    response = api_client.get("/api/version", headers={"X-Request-ID": "abc123"})
    assert response.headers["X-Request-ID"] == "abc123"


def test_not_found_uses_error_envelope(api_client) -> None:
    response = api_client.get("/does-not-exist")
    assert response.status_code == 404
    body = response.json()
    assert body["error"]["code"] == "http_error"
    assert body["error"]["request_id"]
