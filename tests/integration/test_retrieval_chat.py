from __future__ import annotations

import asyncio
from collections.abc import Sequence
from pathlib import Path
from uuid import UUID

import pymupdf
import pytest
from psycopg import AsyncConnection
from psycopg.rows import dict_row

from app.auth.dependencies import CurrentUser
from app.chat.provider import ChatProvider, ChatResult
from app.chat.service import answer_question, session_detail
from app.config import Settings
from app.db.pool import close_pool, init_pool
from app.ingestion import registry
from app.ingestion.job_runner import run_ingestion_job
from app.retrieval.embeddings import set_embedder
from app.retrieval.search import hybrid_search

pytestmark = pytest.mark.integration


class FakeEmbedder:
    model_name = "fake-embedding"
    dim = 1024

    async def encode_documents(self, texts: Sequence[str]) -> list[list[float]]:
        return [[0.002] * self.dim for _ in texts]

    async def encode_query(self, text: str) -> list[float]:
        return [0.002] * self.dim


class FakeChatProvider(ChatProvider):
    provider_name = "fake"
    model_name = "fake-chat"

    def __init__(self) -> None:
        self.prompts: list[list[dict[str, str]]] = []

    async def complete(
        self,
        *,
        system: str,
        messages: list[dict[str, str]],
        temperature: float,
        max_tokens: int,
    ) -> ChatResult:
        assert "numbered sources" in system
        self.prompts.append(messages)
        return ChatResult(
            content="Open the isolation valve before servicing [S1].",
            provider=self.provider_name,
            model=self.model_name,
            latency_ms=5,
            usage={"input_tokens": 10, "output_tokens": 8},
        )


def _make_pdf(path: Path) -> None:
    document = pymupdf.open()
    page = document.new_page(width=400, height=500)
    page.insert_text((40, 60), "1. Valve Procedure", fontsize=18)
    page.insert_text(
        (40, 100),
        "Open the isolation valve and verify that pressure remains stable.",
        fontsize=12,
    )
    page.insert_text(
        (40, 130), "Warning: release pressure before servicing.", fontsize=12
    )
    document.save(path)
    document.close()


def test_hybrid_search_and_grounded_chat(
    settings: Settings,
    migrated_db: bool,
    admin_account: dict[str, str],
    tmp_path: Path,
) -> None:
    settings.ensure_runtime_dirs()
    pdf_path = tmp_path / "valve_procedure.pdf"
    _make_pdf(pdf_path)
    set_embedder(FakeEmbedder())
    chat_provider = FakeChatProvider()
    document_id: str | None = None

    async def run() -> None:
        nonlocal document_id
        await init_pool(settings)
        try:
            async with await AsyncConnection.connect(
                settings.database_url, row_factory=dict_row
            ) as conn:
                registered = await registry.register_pdf(conn, settings, pdf_path)
                document_id = registered["document_id"]
                version_id = UUID(registered["version_id"])
                job = await registry.create_job(
                    conn,
                    version_id,
                    provider="fake",
                    model="fake-vision-model",
                    config={"vision_mode": "none"},
                )
                await conn.commit()
                await run_ingestion_job(job["id"], settings)

                detail = await registry.job_detail(conn, job["id"])
                assert detail is not None
                assert detail["status"] == "completed", detail.get("error_details")

                await registry.approve_version(conn, version_id)
                await conn.commit()

                await conn.execute(
                    "UPDATE documents SET status = 'draft', current_version_id = NULL "
                    "WHERE id = %s",
                    (document_id,),
                )
                await conn.commit()
                await registry.approve_version(conn, version_id)
                document = await (
                    await conn.execute(
                        "SELECT status, current_version_id FROM documents WHERE id = %s",
                        (document_id,),
                    )
                ).fetchone()
                assert document["status"] == "active"
                assert str(document["current_version_id"]) == str(version_id)

                results = await hybrid_search(
                    conn,
                    settings,
                    FakeEmbedder(),
                    "isolation valve",
                    document_ids=[UUID(document_id)],
                )
                assert results
                assert any("isolation valve" in item.content for item in results)
                assert all(item.document_id == document_id for item in results)

                user = CurrentUser(
                    id=UUID(admin_account["id"]),
                    username=admin_account["username"],
                    email=None,
                    full_name=None,
                    roles=("admin",),
                    is_active=True,
                )
                response = await answer_question(
                    conn,
                    settings,
                    user,
                    question="How do I isolate the valve?",
                    document_ids=[UUID(document_id)],
                    provider=chat_provider,
                )
                assert response["answer"].endswith("[S1].")
                assert response["citations"]
                assert response["citations"][0]["document_id"] == document_id
                assert response["provider"] == "fake"
                assert response["model"] == "fake-chat"

                prompt = chat_provider.prompts[0][-1]["content"]
                assert "[S1]" in prompt
                assert "isolation valve" in prompt

                session = await session_detail(
                    conn, user.id, UUID(response["session_id"])
                )
                assert session is not None
                assert [message["role"] for message in session["messages"]] == [
                    "user",
                    "assistant",
                ]
                assert session["messages"][1]["citations"]
        finally:
            if document_id:
                async with await AsyncConnection.connect(
                    settings.database_url, row_factory=dict_row
                ) as conn:
                    await conn.execute(
                        "DELETE FROM documents WHERE id = %s", (document_id,)
                    )
                    await conn.commit()
            await close_pool()
            set_embedder(None)

    asyncio.run(run())
