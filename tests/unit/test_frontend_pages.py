"""Smoke-render the Streamlit pages with AppTest.

The pages require a signed-in user and a running API, so the test injects a
session token and stubs the API client. It only asserts that each page renders
without raising.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

FRONTEND_ROOT = Path(__file__).resolve().parents[2] / "frontend"
PAGES = [
    "streamlit_app.py",
    "pages/1_Admin_Vision.py",
    "pages/2_System_Status.py",
    "pages/3_Documents.py",
    "pages/4_Chat.py",
    "pages/5_Knowledge.py",
    "pages/6_Quality.py",
    "pages/7_Search.py",
]


class StubClient:
    def __init__(self) -> None:
        self.calls: list[str] = []

    def _record(self, name: str) -> None:
        self.calls.append(name)

    def health_ready(self) -> dict[str, Any]:
        self._record("health_ready")
        return {"status": "ready", "database": "ok"}

    def me(self) -> dict[str, Any]:
        return {"username": "tester", "roles": ["admin"], "full_name": "Tester"}

    def list_documents(self) -> dict[str, Any]:
        self._record("list_documents")
        return {"items": []}

    def list_jobs(self, limit: int = 100) -> dict[str, Any]:
        return {"items": []}

    def chat_sessions(self, limit: int = 50) -> dict[str, Any]:
        return {"items": []}

    def knowledge_summary(self, version_id: str | None = None) -> dict[str, Any]:
        return {"entities": 0, "workflows": 0, "steps": 0, "open_conflicts": 0}

    def knowledge_workflows(self, **kwargs: Any) -> dict[str, Any]:
        return {"items": []}

    def knowledge_conflicts(self, **kwargs: Any) -> dict[str, Any]:
        return {"items": []}

    def unanswered_questions(self, **kwargs: Any) -> dict[str, Any]:
        return {"items": []}

    def operations_health(self) -> dict[str, Any]:
        return {"status": "ok", "counts": {"documents": 0}, "recent_jobs": []}

    def eval_cases(self, active_only: bool = True) -> dict[str, Any]:
        return {"items": []}

    def eval_results(self, limit: int = 100) -> dict[str, Any]:
        return {"items": []}

    def vision_configuration(self) -> dict[str, Any]:
        return {"active_provider": "gemini", "providers": []}

    def audit_log(self, limit: int = 50) -> dict[str, Any]:
        return {"items": []}

    def voice_capability(self) -> dict[str, Any]:
        self._record("voice_capability")
        return {
            "provider": "local",
            "configured": True,
            "installed": True,
            "available": True,
            "status": "ok",
            "model": "small",
        }


@pytest.fixture
def stub_client(monkeypatch: pytest.MonkeyPatch) -> StubClient:
    import frontend.ui as ui

    client = StubClient()
    monkeypatch.setattr(ui, "get_client", lambda: client)
    return client


@pytest.mark.parametrize("page", PAGES)
def test_page_renders_without_exception(page: str, stub_client: StubClient) -> None:
    from streamlit.testing.v1 import AppTest

    path = FRONTEND_ROOT / page
    app = AppTest.from_file(str(path), default_timeout=30)
    app.session_state["token"] = "stub-token"
    app.session_state["user"] = {
        "username": "tester",
        "roles": ["admin"],
        "full_name": "Tester",
    }
    app.run()
    assert not app.exception, [str(item.value) for item in app.exception]


def test_dictated_question_is_prefilled_in_the_question_box(
    stub_client: StubClient,
) -> None:
    """A recognized recording must land in the question box."""
    from streamlit.testing.v1 import AppTest

    app = AppTest.from_file(str(FRONTEND_ROOT / "pages/4_Chat.py"), default_timeout=30)
    app.session_state["token"] = "stub-token"
    app.session_state["user"] = {
        "username": "tester",
        "roles": ["admin"],
        "full_name": "Tester",
    }
    app.session_state["voice_transcript"] = "mark daily attendance for my group"
    app.run()

    assert not app.exception, [str(item.value) for item in app.exception]
    question = next(
        item for item in app.text_area if item.key == "chat_question"
    )
    assert question.value == "mark daily attendance for my group"
    assert "voice_transcript" not in app.session_state
