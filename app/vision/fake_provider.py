"""Deterministic fake Vision provider used only in development and tests."""

from __future__ import annotations

from app.config import Settings
from app.vision.base import VisionProvider, VisionRequestError
from app.vision.schemas import (
    ContentType,
    PageContext,
    PageMetadata,
    ProviderHealth,
    VisionCallResult,
    VisionPageResult,
)


class FakeVisionProvider(VisionProvider):
    provider_name = "fake"

    def __init__(
        self,
        settings: Settings,
        *,
        result: VisionPageResult | None = None,
        fail: bool = False,
    ) -> None:
        super().__init__(settings)
        self._result = result
        self._fail = fail

    @property
    def model_name(self) -> str:
        return "fake-vision-model"

    async def analyze_page(
        self,
        *,
        image: bytes,
        extracted_text: str,
        page_context: PageContext,
        metadata: PageMetadata,
    ) -> VisionCallResult:
        if self._fail:
            raise VisionRequestError(
                "Fake vision provider failure requested by test",
                provider=self.provider_name,
                retryable=True,
            )
        result = self._result or VisionPageResult(
            document_id=metadata.document_id,
            page_number=metadata.page_number,
            section_number=metadata.section_number,
            headings=[metadata.heading] if metadata.heading else [],
            content_type=ContentType.UNKNOWN,
            summary="Deterministic fake provider output for tests.",
            confidence=0.99,
            needs_review=False,
        )
        return VisionCallResult(
            result=result,
            provider=self.provider_name,
            model=self.model_name,
            latency_ms=1,
        )

    async def healthcheck(self) -> ProviderHealth:
        return ProviderHealth(
            provider=self.provider_name,
            model=self.model_name,
            configured=True,
            reachable=not self._fail,
            status="ok" if not self._fail else "unreachable",
        )
