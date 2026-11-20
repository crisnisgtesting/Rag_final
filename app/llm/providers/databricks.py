"""Databricks Model Serving LLM provider."""

from __future__ import annotations

import time
from typing import Any

import httpx

from app.llm.auth.oauth import DatabricksOAuthTokenProvider
from app.llm.base import (
    LLMConfigurationError,
    LLMMessage,
    LLMProvider,
    LLMRequestError,
    LLMResponseError,
    LLMResult,
)
from app.llm.http import request_with_retry


class DatabricksLLMProvider(LLMProvider):
    provider_name = "databricks"

    def __init__(self, settings: Any, *, client: httpx.AsyncClient | None = None) -> None:
        super().__init__(settings)
        host = (settings.databricks_host or "").strip().rstrip("/")
        client_id = (settings.databricks_client_id or "").strip()
        client_secret = (
            settings.databricks_client_secret.get_secret_value()
            if settings.databricks_client_secret is not None
            else ""
        )
        endpoint = (settings.databricks_model_serving_endpoint or "").strip()
        missing = [
            name
            for name, value in (
                ("DATABRICKS_HOST", host),
                ("DATABRICKS_CLIENT_ID", client_id),
                ("DATABRICKS_CLIENT_SECRET", client_secret),
                ("DATABRICKS_MODEL_SERVING_ENDPOINT", endpoint),
            )
            if not value
        ]
        if missing:
            raise LLMConfigurationError(
                "Databricks Model Serving is not configured: missing " + ", ".join(missing)
            )

        self._host = host
        self._endpoint = endpoint
        self._client = client
        self._oauth = DatabricksOAuthTokenProvider(
            host=host,
            client_id=client_id,
            client_secret=client_secret,
            scope=settings.databricks_oauth_scope,
            timeout_seconds=settings.databricks_model_timeout_seconds,
            client=client,
        )

    @property
    def model_name(self) -> str:
        return self._endpoint

    @property
    def invocation_url(self) -> str:
        return f"{self._host}/serving-endpoints/{self._endpoint}/invocations"

    async def complete(
        self,
        messages: list[LLMMessage],
        *,
        temperature: float,
        max_tokens: int,
    ) -> LLMResult:
        payload: dict[str, Any] = {
            "messages": [{"role": message.role, "content": message.content} for message in messages]
        }
        if self.settings.databricks_model_supports_parameters:
            payload["temperature"] = temperature
            payload["max_tokens"] = max_tokens

        started = time.perf_counter()
        response = await self._invoke(payload)
        latency_ms = int((time.perf_counter() - started) * 1000)
        try:
            body = response.json()
        except ValueError as exc:
            raise LLMResponseError(
                "Databricks Model Serving response was not valid JSON"
            ) from exc
        if not isinstance(body, dict):
            raise LLMResponseError("Databricks Model Serving response was not an object")

        content = self._extract_text(body)
        if not content:
            raise LLMResponseError("Databricks Model Serving returned no answer")

        return LLMResult(
            content=content,
            provider=self.provider_name,
            model=self.model_name,
            latency_ms=latency_ms,
            usage=self._extract_usage(body),
        )

    async def healthcheck(self) -> dict[str, Any]:
        started = time.perf_counter()
        try:
            token = await self._oauth.get_token()
            response = await request_with_retry(
                method="GET",
                url=f"{self._host}/api/2.0/serving-endpoints/{self._endpoint}",
                provider=self.provider_name,
                timeout=min(float(self.settings.databricks_model_timeout_seconds), 15.0),
                max_retries=0,
                headers={"Authorization": f"Bearer {token}"},
                client=self._client,
            )
        except Exception as exc:  # noqa: BLE001
            return {
                "configured": True,
                "available": False,
                "status": "unreachable",
                "detail": type(exc).__name__,
                "latency_ms": int((time.perf_counter() - started) * 1000),
            }

        state = response.json().get("state") or {}
        return {
            "configured": True,
            "available": True,
            "status": "ok",
            "detail": state.get("ready") if isinstance(state, dict) else None,
            "latency_ms": int((time.perf_counter() - started) * 1000),
        }

    async def close(self) -> None:
        await self._oauth.close()

    async def _invoke(self, payload: dict[str, Any]) -> httpx.Response:
        headers = {
            "Content-Type": "application/json",
            "Authorization": await self._oauth.authorization_header(),
        }
        try:
            return await request_with_retry(
                method="POST",
                url=self.invocation_url,
                provider=self.provider_name,
                timeout=float(self.settings.databricks_model_timeout_seconds),
                max_retries=int(self.settings.databricks_model_max_retries),
                headers=headers,
                json_body=payload,
                client=self._client,
            )
        except LLMRequestError as exc:
            if exc.status_code != 401:
                raise
            headers["Authorization"] = await self._oauth.authorization_header(
                force_refresh=True
            )
            return await request_with_retry(
                method="POST",
                url=self.invocation_url,
                provider=self.provider_name,
                timeout=float(self.settings.databricks_model_timeout_seconds),
                max_retries=int(self.settings.databricks_model_max_retries),
                headers=headers,
                json_body=payload,
                client=self._client,
            )

    @classmethod
    def _extract_text(cls, body: dict[str, Any]) -> str:
        pieces = cls._collect_text(body)
        return "\n".join(piece.strip() for piece in pieces if piece.strip()).strip()

    @classmethod
    def _collect_text(cls, value: Any) -> list[str]:
        if isinstance(value, str):
            return [value]
        if isinstance(value, list):
            pieces: list[str] = []
            for item in value:
                pieces.extend(cls._collect_text(item))
            return pieces
        if isinstance(value, dict):
            for key in (
                "content",
                "text",
                "answer",
                "response",
                "output",
                "result",
                "choices",
                "predictions",
                "outputs",
                "data",
                "message",
            ):
                if key in value:
                    pieces = cls._collect_text(value[key])
                    if pieces:
                        return pieces
        return []

    @staticmethod
    def _extract_usage(body: dict[str, Any]) -> dict[str, Any]:
        sources: list[Any] = [
            body.get("usage"),
            body.get("usageMetadata"),
            body.get("metadata", {}).get("usage") if isinstance(body.get("metadata"), dict) else None,
        ]
        key_sets = (
            ("input_tokens", "output_tokens", "total_tokens"),
            ("prompt_tokens", "completion_tokens", "total_tokens"),
            ("promptTokenCount", "candidatesTokenCount", "totalTokenCount"),
        )
        for source in sources:
            if not isinstance(source, dict):
                continue
            for input_key, output_key, total_key in key_sets:
                if input_key in source and output_key in source:
                    return {
                        "input_tokens": source.get(input_key),
                        "output_tokens": source.get(output_key),
                        "total_tokens": source.get(total_key),
                    }
        return {}
