"""Databricks Model Serving Vision provider."""

from __future__ import annotations

import time

from app.config import Settings
from app.vision.base import VisionConfigurationError, VisionProvider, VisionResponseError
from app.vision.http import request_with_retry
from app.vision.image_utils import image_data_url
from app.vision.parsing import parse_vision_result
from app.vision.prompts import SYSTEM_PROMPT, build_user_prompt
from app.vision.schemas import (
    PageContext,
    PageMetadata,
    ProviderHealth,
    VisionCallResult,
    VisionUsage,
)


class DatabricksVisionProvider(VisionProvider):
    provider_name = "databricks"

    def __init__(self, settings: Settings) -> None:
        super().__init__(settings)
        token = (
            settings.databricks_token.get_secret_value()
            if settings.databricks_token is not None
            else ""
        )
        missing = [
            name
            for name, value in (
                ("DATABRICKS_HOST", settings.databricks_host),
                ("DATABRICKS_TOKEN", token),
                ("DATABRICKS_VISION_ENDPOINT", settings.databricks_vision_endpoint),
            )
            if not (value or "").strip()
        ]
        if missing:
            raise VisionConfigurationError(
                f"Databricks is not fully configured: missing {', '.join(missing)}",
                provider=self.provider_name,
            )
        if settings.databricks_vision_payload_style != "openai_chat":
            raise VisionConfigurationError(
                "Only the OpenAI-compatible chat payload is currently verified for "
                "Databricks Model Serving vision endpoints",
                provider=self.provider_name,
            )

    @property
    def model_name(self) -> str:
        return self.settings.databricks_vision_endpoint or ""

    @property
    def _host(self) -> str:
        assert self.settings.databricks_host is not None
        return self.settings.databricks_host.rstrip("/")

    def _headers(self) -> dict[str, str]:
        assert self.settings.databricks_token is not None
        return {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {self.settings.databricks_token.get_secret_value()}",
        }

    async def analyze_page(
        self,
        *,
        image: bytes,
        extracted_text: str,
        page_context: PageContext,
        metadata: PageMetadata,
    ) -> VisionCallResult:
        user_prompt = build_user_prompt(
            extracted_text=extracted_text,
            page_context=page_context,
            metadata=metadata,
        )
        payload = {
            "messages": [
                {"role": "system", "content": SYSTEM_PROMPT},
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": user_prompt},
                        {
                            "type": "image_url",
                            "image_url": {"url": image_data_url(image)},
                        },
                    ],
                },
            ],
            "temperature": 0.1,
            "max_tokens": self.settings.vision_max_output_tokens,
        }
        assert self.settings.databricks_vision_endpoint is not None
        url = (
            f"{self._host}/serving-endpoints/"
            f"{self.settings.databricks_vision_endpoint}/invocations"
        )
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
        except ValueError as exc:
            raise VisionResponseError(
                "Databricks response was not valid JSON", provider=self.provider_name
            ) from exc

        text = self._extract_text(body)
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
    def _extract_text(body: dict) -> str:
        try:
            choice = body["choices"][0]
            content = choice["message"]["content"]
            if isinstance(content, str):
                return content
            if isinstance(content, list):
                return "\n".join(
                    part.get("text", "")
                    for part in content
                    if isinstance(part, dict) and isinstance(part.get("text"), str)
                )
        except (KeyError, IndexError, TypeError):
            pass

        predictions = body.get("predictions")
        if isinstance(predictions, list) and predictions:
            first = predictions[0]
            if isinstance(first, str):
                return first
            if isinstance(first, dict):
                for key in ("text", "content", "response"):
                    value = first.get(key)
                    if isinstance(value, str):
                        return value
        raise VisionResponseError(
            "Unexpected Databricks response structure", provider="databricks"
        )

    async def healthcheck(self) -> ProviderHealth:
        started = time.perf_counter()
        assert self.settings.databricks_vision_endpoint is not None
        url = (
            f"{self._host}/api/2.0/serving-endpoints/"
            f"{self.settings.databricks_vision_endpoint}"
        )
        try:
            response = await request_with_retry(
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

        detail: str | None = None
        try:
            state = response.json().get("state") or {}
            detail = state.get("ready") or state.get("config_update")
        except ValueError:
            detail = None
        return ProviderHealth(
            provider=self.provider_name,
            model=self.model_name,
            configured=True,
            reachable=True,
            status="ok",
            detail=detail,
            latency_ms=int((time.perf_counter() - started) * 1000),
        )
