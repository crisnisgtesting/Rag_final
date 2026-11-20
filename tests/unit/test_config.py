from __future__ import annotations

import pytest
from pydantic import ValidationError

from app.config import Settings


def test_production_requires_strong_jwt_secret() -> None:
    with pytest.raises(ValidationError):
        Settings(app_env="production", jwt_secret="short")


def test_production_accepts_strong_secret() -> None:
    settings = Settings(
        app_env="production",
        jwt_secret="x" * 48,
        databricks_host="https://example.databricks.com",
        databricks_client_id="client-id",
        databricks_client_secret="client-secret",
        databricks_sql_http_path="/sql/1.0/warehouses/example",
    )
    assert settings.is_production is True


def test_production_requires_databricks_sql_http_path() -> None:
    with pytest.raises(ValidationError):
        Settings(
            app_env="production",
            jwt_secret="x" * 48,
            databricks_host="https://example.databricks.com",
            databricks_client_id="client-id",
            databricks_client_secret="client-secret",
        )


def test_blank_provider_credentials_become_none() -> None:
    settings = Settings(
        app_env="test",
        gemini_api_key="   ",
        databricks_client_id="",
        databricks_client_secret="   ",
        databricks_host=" ",
        gemini_vision_model="",
    )
    assert settings.gemini_api_key is None
    assert settings.databricks_client_id is None
    assert settings.databricks_client_secret is None
    assert settings.databricks_host is None
    assert settings.gemini_vision_model is None


def test_cors_origins_accept_comma_separated_string() -> None:
    settings = Settings(
        app_env="test",
        cors_origins="http://a.local, http://b.local",
    )
    assert settings.cors_origins == ["http://a.local", "http://b.local"]


def test_relative_paths_are_resolved() -> None:
    settings = Settings(app_env="test", model_dir="models", data_dir="data")
    assert settings.model_dir.is_absolute()
    assert settings.data_dir.is_absolute()
