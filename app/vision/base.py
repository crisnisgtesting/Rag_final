"""Common Vision provider interface and error types."""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import ClassVar

from app.config import Settings
from app.vision.schemas import (
    PageContext,
    PageMetadata,
    ProviderHealth,
    VisionCallResult,
)


class VisionError(RuntimeError):
    def __init__(
        self,
        message: str,
        *,
        provider: str,
        retryable: bool = False,
    ) -> None:
        super().__init__(message)
        self.provider = provider
        self.retryable = retryable


class VisionConfigurationError(VisionError):
    pass


class VisionRequestError(VisionError):
    pass


class VisionResponseError(VisionError):
    pass


class VisionProvider(ABC):
    provider_name: ClassVar[str]

    def __init__(self, settings: Settings) -> None:
        self.settings = settings

    @property
    @abstractmethod
    def model_name(self) -> str:
        raise NotImplementedError

    @abstractmethod
    async def analyze_page(
        self,
        *,
        image: bytes,
        extracted_text: str,
        page_context: PageContext,
        metadata: PageMetadata,
    ) -> VisionCallResult:
        raise NotImplementedError

    @abstractmethod
    async def healthcheck(self) -> ProviderHealth:
        raise NotImplementedError
