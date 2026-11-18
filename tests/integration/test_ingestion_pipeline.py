from __future__ import annotations

import asyncio
from collections.abc import Sequence
from pathlib import Path
from uuid import UUID, uuid4

import pymupdf
import pytest
from psycopg import AsyncConnection
from psycopg.rows import dict_row

from app.config import Settings
from app.db.pool import close_pool, init_pool
from app.ingestion import registry
from app.ingestion.job_runner import run_ingestion_job
from app.retrieval.embeddings import set_embedder

pytestmark = pytest.mark.integration


class FakeEmbedder:
    model_name = "fake-embedding"
    dim = 1024

    async def encode_documents(self, texts: Sequence[str]) -> list[list[float]]:
        return [[0.001] * self.dim for _ in texts]

    async def encode_query(self, text: str) -> list[float]:
        return [0.001] * self.dim


def _make_pdf(path: Path, marker: str) -> None:
    document = pymupdf.open()
    page = document.new_page(width=400, height=500)
    page.insert_text((40, 60), "1. Test Procedure", fontsize=18)
    page.insert_text(
        (40, 100),
        "Open the isolation valve and verify that pressure remains stable.",
        fontsize=12,
    )
    page.insert_text((40, 130), "Warning: release pressure before servicing.", fontsize=12)
    page.insert_text((40, 160), marker, fontsize=8)
    document.save(path)
    document.close()


def test_ingestion_pipeline_with_fake_providers(
    settings: Settings, migrated_db: bool, tmp_path: Path
) -> None:
    settings.ensure_runtime_dirs()
    pdf_path = tmp_path / "procedure.pdf"
    _make_pdf(pdf_path, uuid4().hex)
    set_embedder(FakeEmbedder())
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
                job = await registry.create_job(
                    conn,
                    UUID(registered["version_id"]),
                    provider="fake",
                    model="fake-vision-model",
                    config={"vision_mode": "all"},
                )
                await conn.commit()

                await run_ingestion_job(job["id"], settings)

                detail = await registry.job_detail(conn, job["id"])
                assert detail is not None
                assert detail["status"] == "completed", detail.get("error_details")
                assert detail["stats"]["chunks"] >= 1

                version = await registry.version_context(
                    conn, UUID(registered["version_id"])
                )
                assert version is not None
                assert version["status"] == "ready"
                assert version["chunk_count"] >= 1

                pages = await registry.list_pages(
                    conn, UUID(registered["version_id"])
                )
                assert pages[0]["processing_status"] == "completed"
                assert pages[0]["analysis_summary"]

                chunks = await registry.list_chunks(
                    conn, UUID(registered["version_id"])
                )
                assert chunks
                assert all(chunk["has_embedding"] for chunk in chunks)

                runs = await (
                    await conn.execute(
                        "SELECT status FROM vision_runs WHERE job_id = %s",
                        (str(job["id"]),),
                    )
                ).fetchall()
                assert runs and runs[0]["status"] == "completed"

                await conn.execute(
                    "DELETE FROM documents WHERE id = %s", (document_id,)
                )
                await conn.commit()
        finally:
            await close_pool()
            set_embedder(None)

    asyncio.run(run())


def test_markdown_ingestion_and_knowledge_rebuild(
    settings: Settings, migrated_db: bool, tmp_path: Path
) -> None:
    settings.ensure_runtime_dirs()
    path = tmp_path / "employee_records.md"
    marker = uuid4().hex
    path.write_text(
        f"""---
source_url: https://www.youtube.com/watch?v=sh-R7SeDCjI
video_id: sh-R7SeDCjI
---

# Depot Incharge Employee Records

Open the employee records screen. Run {marker}.

## Search Employees

1. Open the employee search screen.
2. Enter the employee number.
3. Submit the search.

## Transfer Employee

Select the destination depot and submit the transfer.
""",
        encoding="utf-8",
    )
    set_embedder(FakeEmbedder())
    document_id: str | None = None

    async def run() -> None:
        nonlocal document_id
        await init_pool(settings)
        try:
            async with await AsyncConnection.connect(
                settings.database_url, row_factory=dict_row
            ) as conn:
                registered = await registry.register_document(conn, settings, path)
                document_id = registered["document_id"]
                assert registered["status"] == "registered"
                assert registered["document_type"] == "markdown"
                job = await registry.create_job(
                    conn,
                    UUID(registered["version_id"]),
                    provider="structured",
                    model="markdown-extractor",
                    config={"vision_mode": "none"},
                )
                await conn.commit()

                await run_ingestion_job(job["id"], settings)

                detail = await registry.job_detail(conn, job["id"])
                assert detail is not None
                assert detail["status"] == "completed", detail.get("error_details")
                assert detail["stats"]["chunks"] >= 1
                assert detail["stats"]["knowledge"]["workflows"] >= 1

                version = await registry.version_context(
                    conn, UUID(registered["version_id"])
                )
                assert version is not None
                assert version["status"] == "ready"

                pages = await registry.list_pages(
                    conn, UUID(registered["version_id"])
                )
                assert pages[0]["layout"]["format"] == "markdown"
                assert pages[0]["layout"]["source_url"].endswith("sh-R7SeDCjI")

                workflows = await (
                    await conn.execute(
                        """
                        SELECT title FROM workflows
                        WHERE document_version_id = %s
                        """,
                        (registered["version_id"],),
                    )
                ).fetchall()
                assert workflows

                await conn.execute(
                    "DELETE FROM documents WHERE id = %s", (document_id,)
                )
                await conn.commit()
        finally:
            await close_pool()
            set_embedder(None)

    asyncio.run(run())
