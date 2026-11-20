"""LLM provider factory."""

from __future__ import annotations

from typing import Any

import httpx

from app.llm.base import LLMConfigurationError, LLMProvider
from app.llm.providers.databricks import DatabricksLLMProvider


def build_llm_provider(
    settings: Any, *, provider: str | None = None, client: httpx.AsyncClient | None = None
) -> LLMProvider:
    name = (provider or getattr(settings, "chat_provider", "databricks") or "").strip().lower()
    if name == "databricks":
        return DatabricksLLMProvider(settings, client=client)
    raise LLMConfigurationError(
        f"Unsupported LLM provider '{name or provider}'. Databricks is the only supported provider."
    )
