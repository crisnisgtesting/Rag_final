from __future__ import annotations

import asyncio
from collections.abc import Sequence
from pathlib import Path
from uuid import UUID

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


def _make_pdf(path: Path, title: str) -> None:
    """Build a PDF whose page text is identical across versions.

    Only the document metadata changes, so the file bytes and the content hash
    differ while every page keeps the same extracted text.
    """
    document = pymupdf.open()
    page = document.new_page(width=400, height=500)
    page.insert_text((40, 60), "1. Test Procedure", fontsize=18)
    page.insert_text(
        (40, 100),
        "Open the isolation valve and verify that pressure remains stable.",
        fontsize=12,
    )
    document.set_metadata({"title": title})
    document.save(path)
    document.close()


def test_unchanged_pages_reuse_previous_analyses(
    settings: Settings, migrated_db: bool, tmp_path: Path
) -> None:
    """A new version with identical page text must not re-run vision analysis."""
    settings.ensure_runtime_dirs()
    first_dir = tmp_path / "v1"
    second_dir = tmp_path / "v2"
    first_dir.mkdir()
    second_dir.mkdir()
    first_path = first_dir / "procedure.pdf"
    second_path = second_dir / "procedure.pdf"
    _make_pdf(first_path, "Revision 1")
    _make_pdf(second_path, "Revision 2")
    set_embedder(FakeEmbedder())
    document_id: str | None = None

    async def run() -> None:
        nonlocal document_id
        await init_pool(settings)
        try:
            async with await AsyncConnection.connect(
                settings.database_url, row_factory=dict_row
            ) as conn:
                first = await registry.register_pdf(conn, settings, first_path)
                document_id = first["document_id"]
                first_job = await registry.create_job(
                    conn,
                    UUID(first["version_id"]),
                    provider="fake",
                    model="fake-vision-model",
                    config={"vision_mode": "all"},
                )
                await conn.commit()
                await run_ingestion_job(first_job["id"], settings)
                first_detail = await registry.job_detail(conn, first_job["id"])
                assert first_detail is not None
                assert first_detail["status"] == "completed", first_detail.get(
                    "error_details"
                )
                assert first_detail["stats"]["vision_calls"] == 1

                second = await registry.register_document(
                    conn, settings, second_path, document_type="pdf"
                )
                assert second["status"] == "registered"
                assert second["document_id"] == first["document_id"]
                second_job = await registry.create_job(
                    conn,
                    UUID(second["version_id"]),
                    provider="fake",
                    model="fake-vision-model",
                    config={"vision_mode": "all"},
                )
                await conn.commit()
                await run_ingestion_job(second_job["id"], settings)

                second_detail = await registry.job_detail(conn, second_job["id"])
                assert second_detail is not None
                assert second_detail["status"] == "completed", second_detail.get(
                    "error_details"
                )
                assert second_detail["stats"].get("reused_pages") == 1
                assert second_detail["stats"]["vision_calls"] == 0

                pages = await registry.list_pages(
                    conn, UUID(second["version_id"])
                )
                assert pages[0]["analysis_summary"]

                await conn.execute(
                    "DELETE FROM documents WHERE id = %s", (document_id,)
                )
                await conn.commit()
        finally:
            await close_pool()
            set_embedder(None)

    asyncio.run(run())
