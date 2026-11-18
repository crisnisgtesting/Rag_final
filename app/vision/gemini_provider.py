"""Gemini Vision provider (official Gemini API)."""

from __future__ import annotations

import time

from app.config import Settings
from app.vision.base import VisionConfigurationError, VisionProvider, VisionResponseError
from app.vision.http import request_with_retry
from app.vision.image_utils import encode_image_base64
from app.vision.parsing import parse_vision_result
from app.vision.prompts import SYSTEM_PROMPT, build_user_prompt
from app.vision.schemas import (
    PageContext,
    PageMetadata,
    ProviderHealth,
    VisionCallResult,
    VisionUsage,
)


class GeminiVisionProvider(VisionProvider):
    provider_name = "gemini"

    def __init__(self, settings: Settings) -> None:
        super().__init__(settings)
        key = (
            settings.gemini_api_key.get_secret_value()
            if settings.gemini_api_key is not None
            else ""
        )
        if not key:
            raise VisionConfigurationError(
                "GEMINI_API_KEY is not configured", provider=self.provider_name
            )
        if not (settings.gemini_vision_model or "").strip():
            raise VisionConfigurationError(
                "GEMINI_VISION_MODEL is not configured", provider=self.provider_name
            )

    @property
    def model_name(self) -> str:
        return (self.settings.gemini_vision_model or "").strip()

    @property
    def _model_path(self) -> str:
        model = self.model_name
        return model[len("models/") :] if model.startswith("models/") else model

    def _headers(self) -> dict[str, str]:
        assert self.settings.gemini_api_key is not None
        return {
            "Content-Type": "application/json",
            "x-goog-api-key": self.settings.gemini_api_key.get_secret_value(),
        }

    async def analyze_page(
        self,
        *,
        image: bytes,
        extracted_text: str,
        page_context: PageContext,
        metadata: PageMetadata,
    ) -> VisionCallResult:
        mime, encoded = encode_image_base64(image)
        user_prompt = build_user_prompt(
            extracted_text=extracted_text,
            page_context=page_context,
            metadata=metadata,
        )
        payload = {
            "systemInstruction": {"parts": [{"text": SYSTEM_PROMPT}]},
            "contents": [
                {
                    "role": "user",
                    "parts": [
                        {"text": user_prompt},
                        {"inlineData": {"mimeType": mime, "data": encoded}},
                    ],
                }
            ],
            "generationConfig": {
                "temperature": 0.1,
                "maxOutputTokens": self.settings.vision_max_output_tokens,
                "responseMimeType": "application/json",
            },
        }
        base = self.settings.gemini_api_base_url.rstrip("/")
        url = f"{base}/v1beta/models/{self._model_path}:generateContent"
        started = time.perf_counter()
        response = await request_with_retry(
            method="POST",
            url=url,
            provider=self.provider_name,
            timeout=self.settings.vision_timeout_seconds,
            max_retries=self.settings.vision_max_retries,
            headers=self._headers(),
            json_body=payload,
        )
        latency_ms = int((time.perf_counter() - started) * 1000)

        try:
            body = response.json()
            candidate = body["candidates"][0]
            parts = candidate["content"]["parts"]
        except (ValueError, KeyError, IndexError, TypeError) as exc:
            try:
                body = response.json()
            except ValueError:
                body = {}
            candidates = body.get("candidates") or []
            finish_reason = candidates[0].get("finishReason") if candidates else None
            block_reason = (body.get("promptFeedback") or {}).get("blockReason")
            detail = finish_reason or block_reason or type(exc).__name__
            raise VisionResponseError(
                f"Unexpected Gemini response structure: {detail}",
                provider=self.provider_name,
            ) from exc

        text = "\n".join(
            part.get("text", "")
            for part in parts
            if isinstance(part, dict) and isinstance(part.get("text"), str)
        )
        if not text.strip():
            finish_reason = candidate.get("finishReason")
            raise VisionResponseError(
                f"Gemini returned no text (finishReason={finish_reason})",
                provider=self.provider_name,
            )

        result = parse_vision_result(
            text,
            defaults={
                "document_id": metadata.document_id,
                "page_number": metadata.page_number,
                "section_number": metadata.section_number,
            },
        )
        usage_raw = body.get("usageMetadata") or {}
        usage = VisionUsage(
            input_tokens=usage_raw.get("promptTokenCount"),
            output_tokens=usage_raw.get("candidatesTokenCount"),
            total_tokens=usage_raw.get("totalTokenCount"),
        )
        return VisionCallResult(
            result=result,
            provider=self.provider_name,
            model=self.model_name,
            latency_ms=latency_ms,
            usage=usage,
            raw_response=body,
        )

    async def healthcheck(self) -> ProviderHealth:
        started = time.perf_counter()
        base = self.settings.gemini_api_base_url.rstrip("/")
        url = f"{base}/v1beta/models/{self._model_path}"
        try:
            await request_with_retry(
                method="GET",
                url=url,
                provider=self.provider_name,
                timeout=min(self.settings.vision_timeout_seconds, 15.0),
                max_retries=0,
                headers=self._headers(),
            )
        except Exception as exc:  # noqa: BLE001
            return ProviderHealth(
                provider=self.provider_name,
                model=self.model_name,
                configured=True,
                reachable=False,
                status="unreachable",
                detail=str(exc),
                latency_ms=int((time.perf_counter() - started) * 1000),
            )
        return ProviderHealth(
            provider=self.provider_name,
            model=self.model_name,
            configured=True,
            reachable=True,
            status="ok",
            latency_ms=int((time.perf_counter() - started) * 1000),
        )
