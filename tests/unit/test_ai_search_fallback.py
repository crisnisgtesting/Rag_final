from __future__ import annotations

from app.config import Settings
from app.retrieval.ai_search import get_ai_search_status


def _settings(**overrides: object) -> Settings:
    values: dict[str, object] = {
        "app_env": "test",
        "jwt_secret": "test-secret-0123456789-0123456789",
        "databricks_host": "https://workspace.databricks.com",
        "databricks_client_id": "client-id",
        "databricks_client_secret": "client-secret",
        "databricks_vector_search_endpoint": "tcms_knowledge_base",
        "databricks_vector_search_index": "",
        "databricks_ai_search_oauth_scope": "ai-search",
    }
    values.update(overrides)
    return Settings(**values)


def test_missing_index_keeps_local_fallback_active() -> None:
    status = get_ai_search_status(_settings())
    assert status.configured is True
    assert status.index_configured is False
    assert status.available is False
    assert status.fallback_active is True
    assert status.reason == "AI Search index is not configured"


def test_missing_oauth_credentials_disable_ai_search() -> None:
    status = get_ai_search_status(_settings(databricks_client_secret=None))
    assert status.configured is False
    assert status.available is False
    assert status.fallback_active is True
    assert status.reason == "Databricks OAuth credentials are not configured"


def test_index_does_not_make_search_available_before_type_verification() -> None:
    status = get_ai_search_status(_settings(databricks_vector_search_index="cris_chunks"))
    assert status.index_configured is True
    assert status.endpoint_type_verified is False
    assert status.available is False
    assert status.fallback_active is True


def test_local_bge_m3_defaults_are_preserved() -> None:
    settings = _settings()
    assert settings.embedding_model == "BAAI/bge-m3"
    assert settings.embedding_dimension == 1024
    assert settings.embedding_dim == 1024
    assert settings.embedding_device == "cpu"


def test_legacy_embedding_dim_env_name_is_supported() -> None:
    settings = Settings(app_env="test", EMBEDDING_DIM=512)
    assert settings.embedding_dimension == 512
