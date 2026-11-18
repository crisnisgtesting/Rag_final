"""Thin Streamlit-facing client for the FastAPI service."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import httpx


class ApiError(Exception):
    def __init__(self, message: str, status_code: int | None = None) -> None:
        super().__init__(message)
        self.status_code = status_code


class FmmApiClient:
    def __init__(
        self,
        base_url: str,
        token: str | None = None,
        timeout: float = 30.0,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.token = token
        self.timeout = timeout

    def _headers(self) -> dict[str, str]:
        if self.token:
            return {"Authorization": f"Bearer {self.token}"}
        return {}

    def _request(self, method: str, path: str, **kwargs: Any) -> dict[str, Any]:
        url = f"{self.base_url}{path}"
        try:
            with httpx.Client(timeout=self.timeout) as client:
                response = client.request(
                    method, url, headers=self._headers(), **kwargs
                )
        except httpx.HTTPError as exc:
            raise ApiError(f"Cannot reach the API at {self.base_url}: {exc}") from exc

        if response.status_code >= 400:
            message = f"Request failed ({response.status_code})"
            try:
                body = response.json()
                error = body.get("error") if isinstance(body, dict) else None
                if isinstance(error, dict) and error.get("message"):
                    message = str(error["message"])
                elif isinstance(body, dict) and body.get("detail"):
                    detail = body["detail"]
                    message = detail if isinstance(detail, str) else message
            except ValueError:
                pass
            raise ApiError(message, status_code=response.status_code)

        if not response.content:
            return {}
        try:
            data = response.json()
        except ValueError as exc:
            raise ApiError("The API returned an invalid response") from exc
        return data if isinstance(data, dict) else {"data": data}

    def health_ready(self) -> dict[str, Any]:
        return self._request("GET", "/health/ready")

    def login(self, username: str, password: str) -> dict[str, Any]:
        data = self._request(
            "POST", "/auth/login", json={"username": username, "password": password}
        )
        self.token = data.get("access_token")
        return data

    def me(self) -> dict[str, Any]:
        return self._request("GET", "/auth/me")

    def vision_configuration(self) -> dict[str, Any]:
        return self._request("GET", "/admin/vision/providers")

    def set_vision_provider(self, provider: str) -> dict[str, Any]:
        return self._request(
            "PUT", "/admin/settings/vision-provider", json={"provider": provider}
        )

    def test_vision_provider(self, provider: str | None = None) -> dict[str, Any]:
        payload = {"provider": provider} if provider else {}
        return self._request("POST", "/admin/vision/test", json=payload)

    def audit_log(self, limit: int = 50) -> dict[str, Any]:
        return self._request("GET", "/admin/audit-log", params={"limit": limit})

    def list_documents(self) -> dict[str, Any]:
        return self._request("GET", "/documents")

    def scan_documents(self) -> dict[str, Any]:
        return self._request("POST", "/documents/scan")

    def upload_document(self, filename: str, content: bytes) -> dict[str, Any]:
        suffix = Path(filename).suffix.lower()
        media_type = {
            ".pdf": "application/pdf",
            ".md": "text/markdown",
            ".markdown": "text/markdown",
            ".docx": (
                "application/vnd.openxmlformats-officedocument."
                "wordprocessingml.document"
            ),
        }.get(suffix, "application/octet-stream")
        return self._request(
            "POST",
            "/documents/upload",
            files={"file": (filename, content, media_type)},
        )

    def document_detail(self, document_id: str) -> dict[str, Any]:
        return self._request("GET", f"/documents/{document_id}")

    def version_pages(self, version_id: str, limit: int = 500) -> dict[str, Any]:
        return self._request(
            "GET", f"/documents/versions/{version_id}/pages", params={"limit": limit}
        )

    def version_chunks(self, version_id: str, limit: int = 100) -> dict[str, Any]:
        return self._request(
            "GET", f"/documents/versions/{version_id}/chunks", params={"limit": limit}
        )

    def ingest_version(
        self,
        version_id: str,
        *,
        provider: str | None = None,
        model: str | None = None,
        vision_mode: str = "auto",
    ) -> dict[str, Any]:
        payload: dict[str, Any] = {"vision_mode": vision_mode}
        if provider:
            payload["provider"] = provider
        if model:
            payload["model"] = model
        return self._request(
            "POST", f"/documents/versions/{version_id}/ingest", json=payload
        )

    def approve_version(self, version_id: str) -> dict[str, Any]:
        return self._request("POST", f"/documents/versions/{version_id}/approve")

    def reject_version(self, version_id: str) -> dict[str, Any]:
        return self._request("POST", f"/documents/versions/{version_id}/reject")

    def list_jobs(self, limit: int = 100) -> dict[str, Any]:
        return self._request("GET", "/jobs", params={"limit": limit})

    def job_detail(self, job_id: str) -> dict[str, Any]:
        return self._request("GET", f"/jobs/{job_id}")

    def cancel_job(self, job_id: str) -> dict[str, Any]:
        return self._request("POST", f"/jobs/{job_id}/cancel")

    def resume_job(self, job_id: str) -> dict[str, Any]:
        return self._request("POST", f"/jobs/{job_id}/resume")

    def search(
        self,
        query: str,
        *,
        top_k: int | None = None,
        document_ids: list[str] | None = None,
        document_types: list[str] | None = None,
        content_types: list[str] | None = None,
        include_drafts: bool = False,
    ) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "query": query,
            "include_drafts": include_drafts,
        }
        if top_k is not None:
            payload["top_k"] = top_k
        if document_ids:
            payload["document_ids"] = document_ids
        if document_types:
            payload["document_types"] = document_types
        if content_types:
            payload["content_types"] = content_types
        return self._request("POST", "/search", json=payload)

    def chat(
        self,
        question: str,
        *,
        session_id: str | None = None,
        top_k: int | None = None,
        document_ids: list[str] | None = None,
        document_types: list[str] | None = None,
        content_types: list[str] | None = None,
    ) -> dict[str, Any]:
        payload: dict[str, Any] = {"question": question}
        if session_id:
            payload["session_id"] = session_id
        if top_k is not None:
            payload["top_k"] = top_k
        if document_ids:
            payload["document_ids"] = document_ids
        if document_types:
            payload["document_types"] = document_types
        if content_types:
            payload["content_types"] = content_types
        return self._request("POST", "/chat", json=payload)

    def chat_sessions(self, limit: int = 50) -> dict[str, Any]:
        return self._request("GET", "/chat/sessions", params={"limit": limit})

    def chat_session(self, session_id: str) -> dict[str, Any]:
        return self._request("GET", f"/chat/sessions/{session_id}")

    def chat_stream(
        self,
        question: str,
        *,
        session_id: str | None = None,
        top_k: int | None = None,
        document_ids: list[str] | None = None,
        document_types: list[str] | None = None,
        content_types: list[str] | None = None,
    ) -> list[dict[str, Any]]:
        payload: dict[str, Any] = {"question": question}
        if session_id:
            payload["session_id"] = session_id
        if top_k is not None:
            payload["top_k"] = top_k
        if document_ids:
            payload["document_ids"] = document_ids
        if document_types:
            payload["document_types"] = document_types
        if content_types:
            payload["content_types"] = content_types
        events: list[dict[str, Any]] = []
        url = f"{self.base_url}/chat/stream"
        try:
            with httpx.Client(timeout=self.timeout) as client:
                with client.stream(
                    "POST", url, headers=self._headers(), json=payload
                ) as response:
                    if response.status_code >= 400:
                        response.read()
                        raise ApiError(
                            f"Request failed ({response.status_code})",
                            status_code=response.status_code,
                        )
                    event_name = "message"
                    for line in response.iter_lines():
                        if not line:
                            continue
                        if line.startswith("event:"):
                            event_name = line[len("event:") :].strip() or "message"
                            continue
                        if not line.startswith("data:"):
                            continue
                        raw = line[len("data:") :].strip()
                        if not raw:
                            continue
                        try:
                            payload = json.loads(raw)
                        except ValueError:
                            continue
                        events.append({"event": event_name, "data": payload})
        except httpx.HTTPError as exc:
            raise ApiError(f"Cannot reach the API at {self.base_url}: {exc}") from exc
        return events

    def submit_feedback(
        self, response_id: str, rating: int, comment: str | None = None
    ) -> dict[str, Any]:
        payload: dict[str, Any] = {"rating": rating}
        if comment:
            payload["comment"] = comment
        return self._request(
            "POST", f"/chat/responses/{response_id}/feedback", json=payload
        )

    def unanswered_questions(self, status: str = "open", limit: int = 100) -> dict[str, Any]:
        return self._request(
            "GET", "/chat/unanswered", params={"status": status, "limit": limit}
        )

    def resolve_unanswered(
        self, question_id: str, status: str, resolution: str
    ) -> dict[str, Any]:
        return self._request(
            "POST",
            f"/chat/unanswered/{question_id}/resolve",
            json={"status": status, "resolution": resolution},
        )

    def voice_capability(self) -> dict[str, Any]:
        return self._request("GET", "/chat/voice/capability")

    def transcribe_audio(self, filename: str, content: bytes) -> dict[str, Any]:
        return self._request(
            "POST",
            "/chat/transcribe",
            files={"file": (filename, content, "application/octet-stream")},
            timeout=180.0,
        )

    def knowledge_summary(self, version_id: str | None = None) -> dict[str, Any]:
        params = {"version_id": version_id} if version_id else None
        return self._request("GET", "/knowledge/summary", params=params)

    def knowledge_workflows(
        self,
        version_id: str | None = None,
        status: str | None = None,
        limit: int = 100,
    ) -> dict[str, Any]:
        params: dict[str, Any] = {"limit": limit}
        if version_id:
            params["version_id"] = version_id
        if status:
            params["status"] = status
        return self._request("GET", "/knowledge/workflows", params=params)

    def knowledge_workflow(self, workflow_id: str) -> dict[str, Any]:
        return self._request("GET", f"/knowledge/workflows/{workflow_id}")

    def set_knowledge_status(
        self, workflow_id: str, status: str
    ) -> dict[str, Any]:
        return self._request(
            "POST",
            f"/knowledge/workflows/{workflow_id}/status",
            json={"status": status},
        )

    def knowledge_conflicts(self, status: str = "open", limit: int = 100) -> dict[str, Any]:
        return self._request(
            "GET", "/knowledge/conflicts", params={"status": status, "limit": limit}
        )

    def resolve_conflict(
        self, conflict_id: str, status: str, resolution: str
    ) -> dict[str, Any]:
        return self._request(
            "POST",
            f"/knowledge/conflicts/{conflict_id}/resolve",
            json={"status": status, "resolution": resolution},
        )

    def rebuild_knowledge(
        self, version_id: str, approve: bool = False
    ) -> dict[str, Any]:
        return self._request(
            "POST",
            f"/knowledge/versions/{version_id}/rebuild",
            params={"approve": str(approve).lower()},
        )

    def eval_cases(self, active_only: bool = True) -> dict[str, Any]:
        return self._request(
            "GET", "/admin/evals", params={"active_only": str(active_only).lower()}
        )

    def add_eval_case(
        self,
        name: str,
        question: str,
        expected_keywords: list[str] | None = None,
        expected_answer: str | None = None,
        language: str | None = None,
        tags: list[str] | None = None,
    ) -> dict[str, Any]:
        payload: dict[str, Any] = {"name": name, "question": question}
        if expected_keywords:
            payload["expected_keywords"] = expected_keywords
        if expected_answer:
            payload["expected_answer"] = expected_answer
        if language:
            payload["language"] = language
        if tags:
            payload["tags"] = tags
        return self._request("POST", "/admin/evals", json=payload)

    def run_evals(self, name: str | None = None, limit: int = 20) -> dict[str, Any]:
        payload: dict[str, Any] = {"limit": limit}
        if name:
            payload["name"] = name
        return self._request("POST", "/admin/evals/run", json=payload)

    def eval_results(self, limit: int = 100) -> dict[str, Any]:
        return self._request("GET", "/admin/evals/results", params={"limit": limit})

    def operations_health(self) -> dict[str, Any]:
        return self._request("GET", "/admin/operations/health")
