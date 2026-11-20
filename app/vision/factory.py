"""Vision provider factory and non-secret provider summaries.

Selection is explicit. There is no automatic fallback between providers.
"""

from __future__ import annotations

from typing import Any

from app.config import Settings
from app.vision.base import VisionConfigurationError, VisionProvider
from app.vision.databricks_provider import DatabricksVisionProvider
from app.vision.fake_provider import FakeVisionProvider
from app.vision.gemini_provider import GeminiVisionProvider
from app.vision.qwen_provider import QwenVisionProvider

ALLOWED_PROVIDERS = ("qwen", "gemini", "databricks")


def normalize_provider_name(name: str) -> str:
    return (name or "").strip().lower()


def build_provider(
    provider_name: str, settings: Settings, *, model: str | None = None
) -> VisionProvider:
    name = normalize_provider_name(provider_name)
    model = (model or "").strip()
    if model:
        if name == "qwen":
            settings = settings.model_copy(update={"qwen_vision_model": model})
        elif name == "gemini":
            settings = settings.model_copy(update={"gemini_vision_model": model})
        elif name == "databricks":
            settings = settings.model_copy(
                update={"databricks_vision_endpoint": model}
            )
    if name == "qwen":
        return QwenVisionProvider(settings)
    if name == "gemini":
        return GeminiVisionProvider(settings)
    if name == "databricks":
        return DatabricksVisionProvider(settings)
    if name == "fake":
        if settings.is_production:
            raise VisionConfigurationError(
                "The fake vision provider is disabled in production", provider=name
            )
        return FakeVisionProvider(settings)
    raise VisionConfigurationError(
        f"Unsupported vision provider '{provider_name}'. "
        f"Allowed values: {', '.join(ALLOWED_PROVIDERS)}",
        provider=name,
    )


def _qwen_configured(settings: Settings) -> bool:
    return bool(settings.qwen_vision_base_url.strip() and settings.qwen_vision_model.strip())


def _gemini_configured(settings: Settings) -> bool:
    key = (
        settings.gemini_api_key.get_secret_value()
        if settings.gemini_api_key is not None
        else ""
    )
    return bool(key.strip() and (settings.gemini_vision_model or "").strip())


def _databricks_configured(settings: Settings) -> bool:
    secret = (
        settings.databricks_client_secret.get_secret_value()
        if settings.databricks_client_secret is not None
        else ""
    )
    endpoint = (settings.databricks_vision_endpoint or "").strip() or (
        settings.databricks_model_serving_endpoint or ""
    ).strip()
    return bool(
        (settings.databricks_host or "").strip()
        and (settings.databricks_client_id or "").strip()
        and secret.strip()
        and endpoint
    )


def provider_summaries(
    settings: Settings, *, active_provider: str | None = None
) -> list[dict[str, Any]]:
    """Return provider configuration metadata without any credentials."""
    active = normalize_provider_name(active_provider or settings.vision_provider)
    return [
        {
            "provider": "qwen",
            "model": settings.qwen_vision_model,
            "configured": _qwen_configured(settings),
            "is_active": active == "qwen",
            "endpoint": settings.qwen_vision_base_url,
            "credential_configured": bool(
                settings.qwen_vision_api_key
                and settings.qwen_vision_api_key.get_secret_value()
            ),
        },
        {
            "provider": "gemini",
            "model": settings.gemini_vision_model,
            "configured": _gemini_configured(settings),
            "is_active": active == "gemini",
            "endpoint": settings.gemini_api_base_url,
            "credential_configured": bool(
                settings.gemini_api_key
                and settings.gemini_api_key.get_secret_value()
            ),
        },
        {
            "provider": "databricks",
            "model": (settings.databricks_vision_endpoint or "").strip()
            or settings.databricks_model_serving_endpoint,
            "configured": _databricks_configured(settings),
            "is_active": active == "databricks",
            "endpoint": settings.databricks_host,
            "credential_configured": bool(
                settings.databricks_client_id
                and settings.databricks_client_secret
                and settings.databricks_client_secret.get_secret_value()
            ),
        },
    ]
