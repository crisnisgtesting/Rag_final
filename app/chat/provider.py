"""Chat completion providers. Gemini is the Phase 2 default."""

from __future__ import annotations

import time
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any

from app.config import Settings
from app.vision.http import request_with_retry


class ChatError(RuntimeError):
    pass


class ChatConfigurationError(ChatError):
    pass


@dataclass
class ChatResult:
    content: str
    provider: str
    model: str
    latency_ms: int
    usage: dict[str, Any] = field(default_factory=dict)


class ChatProvider(ABC):
    provider_name: str

    def __init__(self, settings: Settings) -> None:
        self.settings = settings

    @property
    @abstractmethod
    def model_name(self) -> str:
        raise NotImplementedError

    @abstractmethod
    async def complete(
        self,
        *,
        system: str,
        messages: list[dict[str, str]],
        temperature: float,
        max_tokens: int,
    ) -> ChatResult:
        raise NotImplementedError


class GeminiChatProvider(ChatProvider):
    provider_name = "gemini"

    def __init__(self, settings: Settings) -> None:
        super().__init__(settings)
        key = (
            settings.gemini_api_key.get_secret_value()
            if settings.gemini_api_key is not None
            else ""
        )
        if not key:
            raise ChatConfigurationError("GEMINI_API_KEY is not configured")
        if not settings.effective_chat_model:
            raise ChatConfigurationError(
                "GEMINI_CHAT_MODEL or GEMINI_VISION_MODEL must be configured"
            )

    @property
    def model_name(self) -> str:
        return self.settings.effective_chat_model

    @property
    def _model_path(self) -> str:
        model = self.model_name
        return model[len("models/") :] if model.startswith("models/") else model

    async def complete(
        self,
        *,
        system: str,
        messages: list[dict[str, str]],
        temperature: float,
        max_tokens: int,
    ) -> ChatResult:
        assert self.settings.gemini_api_key is not None
        contents = [
            {
                "role": "model" if message["role"] == "assistant" else "user",
                "parts": [{"text": message["content"]}],
            }
            for message in messages
        ]
        payload = {
            "systemInstruction": {"parts": [{"text": system}]},
            "contents": contents,
            "generationConfig": {
                "temperature": temperature,
                "maxOutputTokens": max_tokens,
            },
        }
        base = self.settings.gemini_api_base_url.rstrip("/")
        url = f"{base}/v1beta/models/{self._model_path}:generateContent"
        started = time.perf_counter()
        response = await request_with_retry(
            method="POST",
            url=url,
            provider=self.provider_name,
            timeout=self.settings.chat_timeout_seconds,
            max_retries=self.settings.vision_max_retries,
            headers={
                "Content-Type": "application/json",
                "x-goog-api-key": self.settings.gemini_api_key.get_secret_value(),
            },
            json_body=payload,
        )
        latency_ms = int((time.perf_counter() - started) * 1000)
        try:
            body = response.json()
            candidate = body["candidates"][0]
            parts = candidate["content"]["parts"]
        except (ValueError, KeyError, IndexError, TypeError) as exc:
            raise ChatError(
                f"Unexpected Gemini chat response: {type(exc).__name__}"
            ) from exc
        text = "\n".join(
            part.get("text", "")
            for part in parts
            if isinstance(part, dict) and isinstance(part.get("text"), str)
        ).strip()
        if not text:
            raise ChatError(
                f"Gemini returned no answer (finishReason={candidate.get('finishReason')})"
            )
        usage_raw = body.get("usageMetadata") or {}
        usage = {
            "input_tokens": usage_raw.get("promptTokenCount"),
            "output_tokens": usage_raw.get("candidatesTokenCount"),
            "total_tokens": usage_raw.get("totalTokenCount"),
        }
        return ChatResult(
            content=text,
            provider=self.provider_name,
            model=self.model_name,
            latency_ms=latency_ms,
            usage=usage,
        )


class QwenChatProvider(ChatProvider):
    provider_name = "qwen"

    def __init__(self, settings: Settings) -> None:
        super().__init__(settings)
        if not settings.qwen_vision_base_url.strip():
            raise ChatConfigurationError("QWEN_VISION_BASE_URL is not configured")
        if not settings.qwen_vision_model.strip():
            raise ChatConfigurationError("QWEN_VISION_MODEL is not configured")

    @property
    def model_name(self) -> str:
        return self.settings.qwen_vision_model

    async def complete(
        self,
        *,
        system: str,
        messages: list[dict[str, str]],
        temperature: float,
        max_tokens: int,
    ) -> ChatResult:
        payload = {
            "model": self.model_name,
            "messages": [{"role": "system", "content": system}, *messages],
            "temperature": temperature,
            "max_tokens": max_tokens,
        }
        headers = {"Content-Type": "application/json"}
        if self.settings.qwen_vision_api_key is not None:
            key = self.settings.qwen_vision_api_key.get_secret_value()
            if key:
                headers["Authorization"] = f"Bearer {key}"
        started = time.perf_counter()
        response = await request_with_retry(
            method="POST",
            url=f"{self.settings.qwen_vision_base_url.rstrip('/')}/chat/completions",
            provider=self.provider_name,
            timeout=self.settings.chat_timeout_seconds,
            max_retries=self.settings.vision_max_retries,
            headers=headers,
            json_body=payload,
        )
        latency_ms = int((time.perf_counter() - started) * 1000)
        try:
            body = response.json()
            text = body["choices"][0]["message"]["content"]
        except (ValueError, KeyError, IndexError, TypeError) as exc:
            raise ChatError(f"Unexpected Qwen chat response: {type(exc).__name__}") from exc
        if not isinstance(text, str) or not text.strip():
            raise ChatError("Qwen returned no answer")
        usage_raw = body.get("usage") or {}
        return ChatResult(
            content=text.strip(),
            provider=self.provider_name,
            model=self.model_name,
            latency_ms=latency_ms,
            usage={
                "input_tokens": usage_raw.get("prompt_tokens"),
                "output_tokens": usage_raw.get("completion_tokens"),
                "total_tokens": usage_raw.get("total_tokens"),
            },
        )


def build_chat_provider(settings: Settings) -> ChatProvider:
    name = (settings.chat_provider or "").strip().lower()
    if name == "gemini":
        return GeminiChatProvider(settings)
    if name == "qwen":
        return QwenChatProvider(settings)
    if name == "databricks":
        raise ChatConfigurationError("Databricks chat is not implemented in Phase 2")
    raise ChatConfigurationError(f"Unsupported chat provider '{settings.chat_provider}'")
