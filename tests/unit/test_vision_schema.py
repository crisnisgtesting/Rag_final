from __future__ import annotations

from app.vision.schemas import (
    ContentType,
    VisionPageResult,
    VisualElement,
    WorkflowStep,
)


def test_low_confidence_requires_review() -> None:
    result = VisionPageResult(page_number=1, confidence=0.4)
    assert result.needs_review is True
    assert any("confidence" in reason.lower() for reason in result.review_reasons)


def test_missing_callouts_require_review() -> None:
    result = VisionPageResult(
        page_number=1,
        confidence=0.95,
        needs_review=False,
        visual_elements=[VisualElement(callout_number=1, label="Start")],
        workflow_steps=[
            WorkflowStep(step_number=1, action="Press start", visual_callouts=[1, 2])
        ],
    )
    assert result.needs_review is True
    assert any("callout" in reason.lower() for reason in result.review_reasons)


def test_matching_callouts_do_not_add_review_reasons() -> None:
    result = VisionPageResult(
        page_number=2,
        content_type=ContentType.PROCEDURE,
        confidence=0.9,
        needs_review=False,
        visual_elements=[VisualElement(callout_number=1, label="Start")],
        workflow_steps=[
            WorkflowStep(step_number=1, action="Press start", visual_callouts=[1])
        ],
    )
    assert result.needs_review is False
    assert result.review_reasons == []


def test_confidence_bounds_are_enforced() -> None:
    try:
        VisionPageResult(page_number=1, confidence=1.5)
    except Exception as exc:  # pydantic ValidationError
        assert "confidence" in str(exc)
    else:  # pragma: no cover
        raise AssertionError("confidence above 1.0 should be rejected")
