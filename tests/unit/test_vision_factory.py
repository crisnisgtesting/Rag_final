from __future__ import annotations

import json

import pytest

from app.vision.base import VisionConfigurationError
from app.vision.factory import (
    ALLOWED_PROVIDERS,
    build_provider,
    provider_summaries,
)


def test_unknown_provider_is_rejected(settings) -> None:
    with pytest.raises(VisionConfigurationError):
        build_provider("does-not-exist", settings)


def test_gemini_without_credentials_is_rejected(settings) -> None:
    with pytest.raises(VisionConfigurationError):
        build_provider("gemini", settings)


def test_fake_provider_is_available_in_tests(settings) -> None:
    provider = build_provider("fake", settings)
    assert provider.model_name == "fake-vision-model"


def test_allowed_providers_are_exactly_the_three(settings) -> None:
    assert ALLOWED_PROVIDERS == ("qwen", "gemini", "databricks")


def test_provider_summaries_never_expose_secrets(tmp_path) -> None:
    from app.config import Settings

    settings = Settings(
        app_env="test",
        jwt_secret="test-secret-0123456789-0123456789",
        gemini_api_key="super-secret-gemini-key",
        gemini_vision_model="gemini-2.0-flash",
        qwen_vision_api_key="super-secret-qwen-key",
        databricks_host="https://example.databricks.com",
        databricks_client_id="databricks-client-id",
        databricks_client_secret="super-secret-databricks-secret",
        databricks_vision_endpoint="my-endpoint",
        model_dir=tmp_path / "models",
        data_dir=tmp_path / "data",
        log_dir=tmp_path / "logs",
    )
    summaries = provider_summaries(settings, active_provider="gemini")
    serialized = json.dumps(summaries)
    assert "super-secret" not in serialized
    assert sum(1 for item in summaries if item["is_active"]) == 1
    gemini = next(item for item in summaries if item["provider"] == "gemini")
    assert gemini["configured"] is True
    assert gemini["credential_configured"] is True
    databricks = next(item for item in summaries if item["provider"] == "databricks")
    assert databricks["configured"] is True
    assert databricks["credential_configured"] is True
