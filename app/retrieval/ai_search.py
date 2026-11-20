"""Databricks AI Search configuration status."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class AISearchStatus:
    endpoint: str | None
    index: str | None
    scope: str
    configured: bool
    index_configured: bool
    endpoint_type_verified: bool
    available: bool
    fallback_active: bool
    reason: str | None


def get_ai_search_status(settings: Any) -> AISearchStatus:
    endpoint = (getattr(settings, "databricks_vector_search_endpoint", None) or "").strip() or None
    index = (getattr(settings, "databricks_vector_search_index", None) or "").strip() or None
    scope = (getattr(settings, "databricks_ai_search_oauth_scope", None) or "ai-search").strip()
    host = (getattr(settings, "databricks_host", None) or "").strip()
    client_id = (getattr(settings, "databricks_client_id", None) or "").strip()
    client_secret = getattr(settings, "databricks_client_secret", None)
    secret_value = (
        client_secret.get_secret_value()
        if hasattr(client_secret, "get_secret_value")
        else str(client_secret or "")
    ).strip()
    credentials_configured = bool(host and client_id and secret_value)

    configured = bool(endpoint and credentials_configured)
    index_configured = bool(index)
    endpoint_type_verified = False
    available = configured and index_configured and endpoint_type_verified
    fallback_active = not available

    if not endpoint:
        reason = "AI Search endpoint is not configured"
    elif not credentials_configured:
        reason = "Databricks OAuth credentials are not configured"
    elif not index_configured:
        reason = "AI Search index is not configured"
    else:
        reason = "AI Search endpoint type has not been verified"

    return AISearchStatus(
        endpoint=endpoint,
        index=index,
        scope=scope,
        configured=configured,
        index_configured=index_configured,
        endpoint_type_verified=endpoint_type_verified,
        available=available,
        fallback_active=fallback_active,
        reason=reason,
    )
