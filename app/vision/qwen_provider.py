"""Qwen3-VL provider (local or internal OpenAI-compatible inference server)."""

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


class QwenVisionProvider(VisionProvider):
    provider_name = "qwen"

    def __init__(self, settings: Settings) -> None:
        super().__init__(settings)
        if not settings.qwen_vision_base_url.strip():
            raise VisionConfigurationError(
                "QWEN_VISION_BASE_URL is not configured", provider=self.provider_name
            )
        if not settings.qwen_vision_model.strip():
            raise VisionConfigurationError(
                "QWEN_VISION_MODEL is not configured", provider=self.provider_name
            )

    @property
    def model_name(self) -> str:
        return self.settings.qwen_vision_model

    def _headers(self) -> dict[str, str]:
        headers = {"Content-Type": "application/json"}
        if self.settings.qwen_vision_api_key is not None:
            key = self.settings.qwen_vision_api_key.get_secret_value()
            if key:
                headers["Authorization"] = f"Bearer {key}"
        return headers

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
            "model": self.model_name,
            "messages": [
                {"role": "system", "content": SYSTEM_PROMPT},
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": user_prompt},
                        {
                            "type": "image_url",
                            "image_url": {"url": f"data:{mime};base64,{encoded}"},
                        },
                    ],
                },
            ],
            "temperature": 0.1,
            "max_tokens": self.settings.vision_max_output_tokens,
        }
        started = time.perf_counter()
        response = await request_with_retry(
            method="POST",
            url=f"{self.settings.qwen_vision_base_url.rstrip('/')}/chat/completions",
            provider=self.provider_name,
            timeout=self.settings.vision_timeout_seconds,
            max_retries=self.settings.vision_max_retries,
            headers=self._headers(),
            json_body=payload,
        )
        latency_ms = int((time.perf_counter() - started) * 1000)

        try:
            body = response.json()
            choice = body["choices"][0]
            content = choice["message"]["content"]
        except (ValueError, KeyError, IndexError, TypeError) as exc:
            raise VisionResponseError(
                f"Unexpected Qwen response structure: {type(exc).__name__}",
                provider=self.provider_name,
            ) from exc

        text = self._content_to_text(content)
        result = parse_vision_result(
            text,
            defaults={
                "document_id": metadata.document_id,
                "page_number": metadata.page_number,
                "section_number": metadata.section_number,
            },
        )
        usage_raw = body.get("usage") or {}
        usage = VisionUsage(
            input_tokens=usage_raw.get("prompt_tokens"),
            output_tokens=usage_raw.get("completion_tokens"),
            total_tokens=usage_raw.get("total_tokens"),
        )
        return VisionCallResult(
            result=result,
            provider=self.provider_name,
            model=self.model_name,
            latency_ms=latency_ms,
            usage=usage,
            raw_response=body,
        )

    @staticmethod
    def _content_to_text(content: object) -> str:
        if isinstance(content, str):
            return content
        if isinstance(content, list):
            chunks = [
                part.get("text", "")
                for part in content
                if isinstance(part, dict) and isinstance(part.get("text"), str)
            ]
            return "\n".join(chunk for chunk in chunks if chunk)
        raise VisionResponseError(
            "Qwen response content is not text", provider="qwen"
        )

    async def healthcheck(self) -> ProviderHealth:
        started = time.perf_counter()
        try:
            await request_with_retry(
                method="GET",
                url=f"{self.settings.qwen_vision_base_url.rstrip('/')}/models",
                provider=self.provider_name,
                timeout=min(self.settings.vision_timeout_seconds, 15.0),
                max_retries=0,
                headers=self._headers(),
            )
        except Exception as exc:  # noqa: BLE001 - report failure, never fallback
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
