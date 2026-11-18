"""Normalized Vision LLM output contract.

Every provider adapter must return these models. Provider-specific payloads and
response formats are normalized inside the adapters.
"""

from __future__ import annotations

from datetime import UTC, datetime
from enum import Enum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, model_validator


class ContentType(str, Enum):
    PROCEDURE = "procedure"
    DOMAIN_KNOWLEDGE = "domain_knowledge"
    REFERENCE = "reference"
    OVERVIEW = "overview"
    FORM = "form"
    TABLE = "table"
    DIAGRAM = "diagram"
    MIXED = "mixed"
    UNKNOWN = "unknown"


class ElementType(str, Enum):
    BUTTON = "button"
    INPUT = "input"
    MENU = "menu"
    LINK = "link"
    TAB = "tab"
    CHECKBOX = "checkbox"
    DROPDOWN = "dropdown"
    TABLE = "table"
    ICON = "icon"
    LABEL = "label"
    IMAGE = "image"
    OTHER = "other"


class VisualElement(BaseModel):
    model_config = ConfigDict(extra="ignore")

    callout_number: int | None = None
    element_type: ElementType = ElementType.OTHER
    label: str = ""
    action: str | None = None
    bounding_box: dict[str, float] | None = None
    notes: str | None = None


class WorkflowStep(BaseModel):
    model_config = ConfigDict(extra="ignore")

    step_number: int = Field(ge=1)
    action: str
    ui_element: str | None = None
    description: str = ""
    visual_callouts: list[int] = Field(default_factory=list)
    conditions: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    exceptions: list[str] = Field(default_factory=list)


class DomainKnowledgeItem(BaseModel):
    model_config = ConfigDict(extra="ignore")

    entity_type: str | None = None
    name: str
    description: str = ""
    attributes: dict[str, Any] = Field(default_factory=dict)


class SourceReference(BaseModel):
    model_config = ConfigDict(extra="ignore")

    page_number: int | None = None
    section_number: str | None = None
    quote: str | None = None
    screenshot_reference: str | None = None


class VisionPageResult(BaseModel):
    model_config = ConfigDict(extra="ignore")

    document_id: str | None = None
    page_number: int
    section_number: str | None = None
    headings: list[str] = Field(default_factory=list)
    content_type: ContentType = ContentType.UNKNOWN
    summary: str = ""
    domain_knowledge: list[DomainKnowledgeItem] = Field(default_factory=list)
    workflow_steps: list[WorkflowStep] = Field(default_factory=list)
    visual_elements: list[VisualElement] = Field(default_factory=list)
    prerequisites: list[str] = Field(default_factory=list)
    business_rules: list[str] = Field(default_factory=list)
    validations: list[str] = Field(default_factory=list)
    exceptions: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    mentioned_not_documented: list[str] = Field(default_factory=list)
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)
    needs_review: bool = True
    review_reasons: list[str] = Field(default_factory=list)
    source_references: list[SourceReference] = Field(default_factory=list)

    @model_validator(mode="after")
    def _flag_consistency_issues(self) -> VisionPageResult:
        callouts = {
            element.callout_number
            for element in self.visual_elements
            if element.callout_number is not None
        }
        referenced = {
            callout
            for step in self.workflow_steps
            for callout in step.visual_callouts
        }
        unmatched = sorted(referenced - callouts)
        if unmatched:
            self.needs_review = True
            self.review_reasons = [
                *self.review_reasons,
                f"Workflow steps reference callouts not present on the page: {unmatched}",
            ]
        if self.confidence < 0.6:
            self.needs_review = True
            if "Low visual interpretation confidence" not in self.review_reasons:
                self.review_reasons.append("Low visual interpretation confidence")
        return self


class PageMetadata(BaseModel):
    document_id: str | None = None
    document_title: str
    document_version: str | None = None
    page_number: int
    printed_page_number: str | None = None
    section_number: str | None = None
    heading: str | None = None
    source_filename: str | None = None
    language: str | None = None
    extra: dict[str, Any] = Field(default_factory=dict)


class PageContext(BaseModel):
    current_heading: str | None = None
    previous_heading: str | None = None
    preceding_text: str = ""
    following_text: str = ""
    section_number: str | None = None
    document_title: str | None = None
    document_version: str | None = None
    workflow_context: str | None = None


class VisionUsage(BaseModel):
    input_tokens: int | None = None
    output_tokens: int | None = None
    total_tokens: int | None = None
    estimated_cost: float | None = None


class VisionCallResult(BaseModel):
    result: VisionPageResult
    provider: str
    model: str
    latency_ms: int
    usage: VisionUsage = Field(default_factory=VisionUsage)
    raw_response: dict[str, Any] | None = Field(default=None, exclude=True)


class ProviderHealth(BaseModel):
    provider: str
    model: str
    configured: bool
    reachable: bool
    status: str
    detail: str | None = None
    latency_ms: int | None = None
    checked_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
