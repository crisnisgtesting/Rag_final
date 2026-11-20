from __future__ import annotations

import json

import pytest

pytestmark = pytest.mark.integration


def test_login_and_me(api_client, admin_account, auth_headers) -> None:
    me = api_client.get("/auth/me", headers=auth_headers)
    assert me.status_code == 200, me.text
    body = me.json()
    assert body["username"] == admin_account["username"]
    assert "admin" in body["roles"]


def test_login_rejects_wrong_password(api_client, admin_account) -> None:
    response = api_client.post(
        "/auth/login",
        json={"username": admin_account["username"], "password": "wrong-password"},
    )
    assert response.status_code == 401
    assert response.json()["error"]["code"] == "http_error"


def test_me_requires_token(api_client) -> None:
    response = api_client.get("/auth/me")
    assert response.status_code in (401, 403)


def test_voice_capability_reports_transcription_state(
    api_client, auth_headers
) -> None:
    response = api_client.get("/chat/voice/capability", headers=auth_headers)
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["provider"] in {"local", "disabled"}
    assert body["status"] in {"ok", "unavailable"}
    assert isinstance(body["available"], bool)


def test_voice_capability_requires_token(api_client) -> None:
    response = api_client.get("/chat/voice/capability")
    assert response.status_code in (401, 403)


def test_vision_configuration_hides_secrets(api_client, auth_headers) -> None:
    response = api_client.get("/admin/vision/providers", headers=auth_headers)
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["active_provider"] in {"qwen", "gemini", "databricks"}
    assert body["environment_default"] == "databricks"
    assert len(body["providers"]) == 3
    text = json.dumps(body)
    assert "api_key" not in text.lower()
    assert "token" not in text.lower()


def test_vision_configuration_requires_admin(api_client) -> None:
    response = api_client.get("/admin/vision/providers")
    assert response.status_code in (401, 403)


def test_switch_provider_and_audit(
    api_client, auth_headers, clean_vision_setting
) -> None:
    response = api_client.put(
        "/admin/settings/vision-provider",
        json={"provider": "gemini"},
        headers=auth_headers,
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["active_provider"] == "gemini"
    assert body["source"] == "database"

    log = api_client.get("/admin/audit-log", headers=auth_headers)
    assert log.status_code == 200
    actions = [item["action"] for item in log.json()["items"]]
    assert "settings.update" in actions


def test_unknown_provider_is_rejected(
    api_client, auth_headers, clean_vision_setting
) -> None:
    response = api_client.put(
        "/admin/settings/vision-provider",
        json={"provider": "nope"},
        headers=auth_headers,
    )
    assert response.status_code == 400
    assert response.json()["error"]["code"] == "http_error"


def test_gemini_without_credentials_is_not_configured(
    api_client, auth_headers
) -> None:
    response = api_client.post(
        "/admin/vision/test", json={"provider": "gemini"}, headers=auth_headers
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["configured"] is False
    assert body["reachable"] is False
    assert body["status"] == "not_configured"
