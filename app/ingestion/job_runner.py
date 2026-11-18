"""Resumable in-process ingestion job runner.

The API is a single process in Phase 2. CPU work is moved to worker threads,
vision calls use the provider's configured concurrency, and job state is durable
in PostgreSQL. An advisory lock prevents two runners from processing one job.
"""

from __future__ import annotations

import asyncio
import json
import time
from datetime import UTC, datetime
from typing import Any
from uuid import UUID

from psycopg import AsyncConnection
from psycopg.types.json import Jsonb

from app.config import Settings, get_settings
from app.db.pool import get_pool
from app.ingestion.chunker import PageKnowledge, build_chunks
from app.ingestion.classifier import classify_page
from app.ingestion.pdf_extractor import (
    DocumentExtraction,
    PageExtraction,
    extract_pdf,
    image_hash,
    render_page_png,
)
from app.ingestion.registry import RegistryError, resolve_storage_path, version_context
from app.logging_conf import get_logger
from app.retrieval.embeddings import EmbeddingError, get_embedder
from app.services.audit import record_audit
from app.utils.hashing import sha256_text
from app.vision.base import VisionConfigurationError, VisionError
from app.vision.factory import build_provider
from app.vision.schemas import PageContext, PageMetadata

logger = get_logger(__name__)

_TASKS: dict[str, asyncio.Task] = {}


class JobRunnerError(RuntimeError):
    pass


def start_job(job_id: UUID | str, settings: Settings | None = None) -> None:
    key = str(job_id)
    existing = _TASKS.get(key)
    if existing is not None and not existing.done():
        return
    task = asyncio.create_task(run_ingestion_job(UUID(key), settings))
    _TASKS[key] = task
    task.add_done_callback(lambda _task, task_key=key: _TASKS.pop(task_key, None))


async def recover_stale_jobs(settings: Settings) -> None:
    if settings.is_test:
        return
    pool = get_pool()
    async with pool.connection() as conn:
        stale = await (
            await conn.execute(
                """
                UPDATE ingestion_jobs
                SET status = 'paused',
                    stage = 'paused',
                    error_details = jsonb_build_object(
                        'reason', 'API restarted while the job was running'
                    )
                WHERE status = 'running'
                  AND (heartbeat_at IS NULL OR heartbeat_at < now() - interval '5 minutes')
                RETURNING id
                """
            )
        ).fetchall()
        await conn.commit()
        for row in stale:
            logger.warning("ingestion_job_paused_after_restart", job_id=str(row["id"]))

        queued = await (
            await conn.execute(
                "SELECT id FROM ingestion_jobs WHERE status = 'queued' ORDER BY created_at"
            )
        ).fetchall()
        await conn.commit()
    for row in queued:
        start_job(row["id"], settings)


async def run_ingestion_job(
    job_id: UUID | str, settings: Settings | None = None
) -> None:
    job_id = UUID(str(job_id))
    settings = settings or get_settings()
    pool = get_pool()
    async with pool.connection() as conn:
        lock_key = f"fmm-ingestion:{job_id}"
        locked = await (
            await conn.execute(
                "SELECT pg_try_advisory_lock(hashtext(%s)) AS locked", (lock_key,)
            )
        ).fetchone()
        await conn.commit()
        if not locked or not locked["locked"]:
            return
        try:
            await _run(conn, settings, job_id)
        finally:
            await _safe_rollback(conn)
            await conn.execute(
                "SELECT pg_advisory_unlock(hashtext(%s))", (lock_key,)
            )
            await conn.commit()


async def _run(conn: AsyncConnection, settings: Settings, job_id: UUID) -> None:
    job = await _get_job(conn, job_id)
    if job is None:
        return
    if job["status"] in {"completed", "cancelled"}:
        return

    version = await version_context(conn, job["document_version_id"])
    if version is None:
        await _fail_job(conn, job_id, "Document version no longer exists")
        return

    config = dict(job["config"] or {})
    stats: dict[str, Any] = dict(job["stats"] or {})
    stats.setdefault("vision_calls", 0)
    stats.setdefault("vision_failures", 0)
    stats.setdefault("input_tokens", 0)
    stats.setdefault("output_tokens", 0)
    stats.setdefault("chunks", 0)

    try:
        source_path = resolve_storage_path(settings, version["storage_path"])
        if not source_path.is_file():
            raise RegistryError("Stored source file is missing")

        await _update_job(
            conn,
            job_id,
            status="running",
            stage="extracting",
            progress=5,
            started_at=datetime.now(UTC),
            heartbeat_at=datetime.now(UTC),
            stats=stats,
        )
        if await _cancel_requested(conn, job_id):
            await _mark_cancelled(conn, job_id, version)
            return

        vision_mode = str(config.get("vision_mode") or settings.ingestion_vision_mode)
        await _extract(
            conn,
            settings,
            job_id,
            version,
            source_path,
            stats,
            vision_mode=vision_mode,
        )
        if await _cancel_requested(conn, job_id):
            await _mark_cancelled(conn, job_id, version)
            return

        if str(version.get("document_type") or "pdf") == "pdf":
            await _reuse_unchanged_analyses(conn, version, stats)

        if vision_mode != "none" and str(version.get("document_type") or "pdf") == "pdf":
            await _analyze_vision_pages(
                conn,
                settings,
                job_id,
                version,
                source_path,
                stats,
                provider_name=str(job["selected_vision_provider"]),
                model_name=str(job["selected_vision_model"]),
            )
            if settings.ingestion_ocr_enabled:
                await _ocr_scanned_pages(conn, settings, version, stats)
        if await _cancel_requested(conn, job_id):
            await _mark_cancelled(conn, job_id, version)
            return

        await _build_and_embed_chunks(conn, settings, job_id, version, stats)
        if await _cancel_requested(conn, job_id):
            await _mark_cancelled(conn, job_id, version)
            return

        from app.knowledge.extractor import rebuild_version_knowledge

        knowledge_stats = await rebuild_version_knowledge(conn, version["id"])
        stats["knowledge"] = knowledge_stats
        await conn.execute(
            "UPDATE ingestion_jobs SET progress = 95, stage = 'knowledge', "
            "heartbeat_at = now(), stats = %s WHERE id = %s",
            (Jsonb(stats), str(job_id)),
        )
        await conn.commit()

        completed_pages = await _count_pages(conn, version["id"], statuses=("completed", "needs_review"))
        failed_pages = await _count_pages(conn, version["id"], statuses=("failed",))
        chunk_count = int(stats.get("chunks") or 0)
        if completed_pages == 0 or chunk_count == 0:
            raise JobRunnerError("No searchable content was produced")

        async with conn.transaction():
            await conn.execute(
                """
                UPDATE document_versions
                SET status = 'ready', page_count = %s, chunk_count = %s, indexed_at = now()
                WHERE id = %s
                """,
                (int(stats.get("pages") or completed_pages), chunk_count, str(version["id"])),
            )
            await conn.execute(
                "UPDATE documents SET status = 'draft' WHERE id = %s",
                (str(version["document_id"]),),
            )
            await conn.execute(
                """
                UPDATE ingestion_jobs
                SET status = 'completed', stage = 'completed', progress = 100,
                    completed_pages = %s, failed_pages = %s, stats = %s,
                    heartbeat_at = now(), completed_at = now(), error_details = NULL
                WHERE id = %s
                """,
                (completed_pages, failed_pages, Jsonb(stats), str(job_id)),
            )
            await record_audit(
                conn,
                actor_user_id=None,
                action="ingestion.completed",
                entity_type="ingestion_job",
                entity_id=str(job_id),
                metadata={
                    "version_id": str(version["id"]),
                    "completed_pages": completed_pages,
                    "failed_pages": failed_pages,
                    "chunks": chunk_count,
                },
            )
        await conn.commit()
        logger.info(
            "ingestion_job_completed",
            job_id=str(job_id),
            completed_pages=completed_pages,
            failed_pages=failed_pages,
            chunks=chunk_count,
        )
    except asyncio.CancelledError:
        await _safe_rollback(conn)
        await _mark_cancelled(conn, job_id, version)
        raise
    except (VisionConfigurationError, VisionError, EmbeddingError) as exc:
        await _safe_rollback(conn)
        await _fail_job(conn, job_id, str(exc), version=version)
    except Exception as exc:  # noqa: BLE001 - persist every job failure
        await _safe_rollback(conn)
        logger.exception("ingestion_job_failed", job_id=str(job_id), error=type(exc).__name__)
        await _fail_job(conn, job_id, f"{type(exc).__name__}: {exc}", version=version)


async def _extract(
    conn: AsyncConnection,
    settings: Settings,
    job_id: UUID,
    version: dict[str, Any],
    source_path,
    stats: dict[str, Any],
    *,
    vision_mode: str,
) -> list[PageExtraction]:
    version_id = version["id"]
    document_type = str(version.get("document_type") or "pdf")
    existing_count = await _count_all_pages(conn, version_id)
    checkpoint = await _get_checkpoint(conn, job_id, "extract")
    if checkpoint and existing_count >= int(version["page_count"] or 0):
        pages = await _load_page_extractions(conn, version_id)
        if pages:
            stats["pages"] = len(pages)
            await conn.execute(
                "UPDATE ingestion_jobs SET total_pages = %s WHERE id = %s",
                (len(pages), str(job_id)),
            )
            await conn.commit()
            return pages

    if document_type in {"markdown", "docx"}:
        return await _extract_structured_document(
            conn, job_id, version, source_path, stats, document_type=document_type
        )

    extraction = await asyncio.to_thread(extract_pdf, source_path, extract_tables=True)
    metadata = dict(version.get("metadata") or {})
    metadata.update(
        {
            "pdf_metadata": extraction.metadata,
            "toc": extraction.toc,
            "extracted_pages": extraction.page_count,
        }
    )

    async with conn.transaction():
        await conn.execute(
            "UPDATE document_versions SET page_count = %s, metadata = %s WHERE id = %s",
            (extraction.page_count, Jsonb(metadata), str(version_id)),
        )
        for page in extraction.pages:
            classification = classify_page(page, settings, mode=vision_mode)
            layout = {
                "blocks": page.blocks,
                "images": page.images,
                "links": page.links,
                "tables": page.tables,
                "classification": {
                    "needs_vision": classification.needs_vision,
                    "reasons": list(classification.reasons),
                    "text_source": classification.text_source,
                },
            }
            status = "pending" if classification.needs_vision else "completed"
            content_type = "table" if page.tables else "unknown"
            await conn.execute(
                """
                INSERT INTO document_pages
                    (document_version_id, physical_page_number, page_text, text_hash,
                     content_type, processing_status, text_source, text_char_count,
                     image_count, image_coverage, width_pt, height_pt, layout)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                ON CONFLICT (document_version_id, physical_page_number) DO UPDATE
                    SET page_text = EXCLUDED.page_text,
                        text_hash = EXCLUDED.text_hash,
                        content_type = EXCLUDED.content_type,
                        text_source = EXCLUDED.text_source,
                        text_char_count = EXCLUDED.text_char_count,
                        image_count = EXCLUDED.image_count,
                        image_coverage = EXCLUDED.image_coverage,
                        width_pt = EXCLUDED.width_pt,
                        height_pt = EXCLUDED.height_pt,
                        layout = EXCLUDED.layout,
                        processing_status = CASE
                            WHEN document_pages.analysis IS NULL THEN EXCLUDED.processing_status
                            ELSE document_pages.processing_status
                        END,
                        updated_at = now()
                """,
                (
                    str(version_id),
                    page.page_number,
                    page.text,
                    page.text_hash,
                    content_type,
                    status,
                    classification.text_source,
                    page.char_count,
                    page.image_count,
                    page.image_coverage,
                    page.width_pt,
                    page.height_pt,
                    Jsonb(layout),
                ),
            )
        await _set_checkpoint(conn, job_id, "extract", {"pages": extraction.page_count})
        await conn.execute(
            "UPDATE ingestion_jobs SET progress = 15, stage = 'extracted', "
            "total_pages = %s, completed_pages = %s, heartbeat_at = now(), "
            "stats = %s WHERE id = %s",
            (
                extraction.page_count,
                sum(
                    1
                    for page in extraction.pages
                    if not classify_page(page, settings, mode=vision_mode).needs_vision
                ),
                Jsonb({**stats, "pages": extraction.page_count}),
                str(job_id),
            ),
        )
    await conn.commit()
    stats["pages"] = extraction.page_count
    return extraction.pages


async def _reuse_unchanged_analyses(
    conn: AsyncConnection,
    version: dict[str, Any],
    stats: dict[str, Any],
) -> int:
    """Carry prior page analyses over to a new version when the page text is
    unchanged.

    Only the analysis content is reused. Rendered page images belong to the
    version that produced them, so the new version re-renders any page it
    still needs to analyze.
    """
    document_id = str(version["document_id"])
    version_id = str(version["id"])
    previous = await (
        await conn.execute(
            """
            SELECT dv.id
            FROM document_versions dv
            WHERE dv.document_id = %s
              AND dv.id <> %s
              AND dv.status IN ('ready', 'approved')
            ORDER BY dv.version_number DESC
            LIMIT 1
            """,
            (document_id, version_id),
        )
    ).fetchone()
    if previous is None:
        return 0

    reused = await (
        await conn.execute(
            """
            UPDATE document_pages current
            SET analysis = previous.analysis,
                analysis_provider = previous.analysis_provider,
                analysis_model = previous.analysis_model,
                analysis_latency_ms = previous.analysis_latency_ms,
                analysis_usage = previous.analysis_usage,
                analyzed_at = previous.analyzed_at,
                content_type = previous.content_type,
                confidence = previous.confidence,
                needs_review = previous.needs_review,
                page_image_path = previous.page_image_path,
                processing_status = CASE
                    WHEN previous.needs_review THEN 'needs_review'
                    ELSE 'completed'
                END,
                updated_at = now()
            FROM document_pages previous
            WHERE current.document_version_id = %s
              AND previous.document_version_id = %s
              AND current.analysis IS NULL
              AND current.text_hash = previous.text_hash
              AND previous.analysis IS NOT NULL
            RETURNING current.id
            """,
            (version_id, str(previous["id"])),
        )
    ).fetchall()
    count = len(reused)
    if count:
        stats["reused_pages"] = int(stats.get("reused_pages", 0)) + count
        await conn.execute(
            "UPDATE ingestion_jobs SET stats = %s WHERE id = %s",
            (Jsonb(stats), str(version["id"])),
        )
        await conn.commit()
        logger.info(
            "page_analyses_reused",
            version_id=version_id,
            previous_version_id=str(previous["id"]),
            pages=count,
        )
    return count


async def _extract_structured_document(
    conn: AsyncConnection,
    job_id: UUID,
    version: dict[str, Any],
    source_path,
    stats: dict[str, Any],
    *,
    document_type: str,
) -> list[PageExtraction]:
    if document_type == "markdown":
        from app.ingestion.markdown_extractor import extract_markdown

        extraction = await asyncio.to_thread(extract_markdown, source_path)
    elif document_type == "docx":
        from app.ingestion.docx_extractor import extract_docx

        extraction = await asyncio.to_thread(extract_docx, source_path)
    else:
        raise JobRunnerError(f"Unsupported structured document type: {document_type}")

    await _store_structured_extraction(
        conn,
        job_id,
        version,
        extraction,
        stats,
        text_source=document_type,
    )
    return extraction.pages


async def _store_structured_extraction(
    conn: AsyncConnection,
    job_id: UUID,
    version: dict[str, Any],
    extraction: DocumentExtraction,
    stats: dict[str, Any],
    *,
    text_source: str,
) -> None:
    version_id = version["id"]
    metadata = dict(version.get("metadata") or {})
    metadata.update(
        {
            "format": text_source,
            "document_metadata": extraction.metadata,
            "toc": extraction.toc,
            "extracted_pages": extraction.page_count,
        }
    )
    async with conn.transaction():
        await conn.execute(
            "DELETE FROM document_pages WHERE document_version_id = %s",
            (str(version_id),),
        )
        await conn.execute(
            "UPDATE document_versions SET page_count = %s, metadata = %s WHERE id = %s",
            (extraction.page_count, Jsonb(metadata), str(version_id)),
        )
        for page in extraction.pages:
            layout = dict(page.layout)
            layout.setdefault("format", text_source)
            content_type = _text_content_type(page.text)
            layout["content_type"] = content_type
            await conn.execute(
                """
                INSERT INTO document_pages
                    (document_version_id, physical_page_number, page_text, text_hash,
                     content_type, processing_status, text_source, text_char_count,
                     image_count, image_coverage, width_pt, height_pt, layout)
                VALUES (%s, %s, %s, %s, %s, 'completed', %s, %s, 0, 0, 0, 0, %s)
                """,
                (
                    str(version_id),
                    page.page_number,
                    page.text,
                    page.text_hash,
                    content_type,
                    text_source,
                    page.char_count,
                    Jsonb(layout),
                ),
            )
        await _set_checkpoint(conn, job_id, "extract", {"pages": extraction.page_count})
        await conn.execute(
            "UPDATE ingestion_jobs SET progress = 15, stage = 'extracted', "
            "total_pages = %s, completed_pages = %s, heartbeat_at = now(), "
            "stats = %s WHERE id = %s",
            (
                extraction.page_count,
                extraction.page_count,
                Jsonb({**stats, "pages": extraction.page_count}),
                str(job_id),
            ),
        )
    await conn.commit()
    stats["pages"] = extraction.page_count


def _text_content_type(text: str) -> str:
    lowered = text.lower()
    if any(marker in lowered for marker in ("| ---", " | ")):
        return "table"
    if "warning" in lowered or "caution" in lowered:
        return "procedure"
    lines = text.splitlines()
    numbered = sum(1 for line in lines if line.strip()[:2].rstrip(".)").isdigit())
    if numbered >= 3:
        return "procedure"
    return "reference"


async def _analyze_vision_pages(
    conn: AsyncConnection,
    settings: Settings,
    job_id: UUID,
    version: dict[str, Any],
    source_path,
    stats: dict[str, Any],
    *,
    provider_name: str,
    model_name: str,
) -> None:
    rows = await _vision_page_rows(conn, version["id"])
    if not rows:
        await conn.execute(
            "UPDATE ingestion_jobs SET progress = 50, stage = 'vision_skipped', "
            "heartbeat_at = now() WHERE id = %s",
            (str(job_id),),
        )
        await conn.commit()
        return

    provider = build_provider(provider_name, settings, model=model_name)
    concurrency = {
        "qwen": settings.vision_concurrency_qwen,
        "gemini": settings.vision_concurrency_gemini,
        "databricks": settings.vision_concurrency_databricks,
    }.get(provider_name, 2)
    semaphore = asyncio.Semaphore(max(1, concurrency))
    render_dpi = settings.ingestion_render_dpi
    render_dir = settings.data_dir / "rendered_pages" / str(version["id"])
    render_dir.mkdir(parents=True, exist_ok=True)

    context_by_page = await _page_context_map(conn, version["id"])
    total = len(rows)
    processed = 0
    batch_size = max(1, settings.ingestion_page_batch_size)

    for start in range(0, total, batch_size):
        if await _cancel_requested(conn, job_id):
            return
        batch = rows[start : start + batch_size]

        async def analyze(row: dict[str, Any]):
            async with semaphore:
                page_number = int(row["physical_page_number"])
                image = await asyncio.to_thread(
                    render_page_png, source_path, page_number, dpi=render_dpi
                )
                image_path = render_dir / f"page-{page_number:04d}.png"
                await asyncio.to_thread(image_path.write_bytes, image)
                context = context_by_page.get(page_number, {})
                metadata = PageMetadata(
                    document_id=str(version["document_id"]),
                    document_title=str(version["title"]),
                    document_version=str(version["version_number"]),
                    page_number=page_number,
                    printed_page_number=row.get("printed_page_number"),
                    section_number=context.get("section_number"),
                    heading=context.get("heading"),
                    source_filename=str(version["filename"]),
                )
                page_context = PageContext(
                    current_heading=context.get("heading"),
                    previous_heading=context.get("previous_heading"),
                    preceding_text=context.get("preceding_text", ""),
                    following_text=context.get("following_text", ""),
                    section_number=context.get("section_number"),
                    document_title=str(version["title"]),
                    document_version=str(version["version_number"]),
                )
                started = time.perf_counter()
                result = await provider.analyze_page(
                    image=image,
                    extracted_text=str(row.get("page_text") or ""),
                    page_context=page_context,
                    metadata=metadata,
                )
                return row, image_path, image, result, int((time.perf_counter() - started) * 1000)

        results = await asyncio.gather(
            *(analyze(row) for row in batch), return_exceptions=True
        )
        for row, outcome in zip(batch, results, strict=False):
            if isinstance(outcome, asyncio.CancelledError):
                raise outcome
            if isinstance(outcome, Exception):
                stats["vision_failures"] = int(stats.get("vision_failures", 0)) + 1
                await _record_vision_failure(
                    conn, job_id, row["id"], provider_name, model_name, outcome
                )
                await conn.execute(
                    "UPDATE document_pages SET processing_status = 'failed', updated_at = now() "
                    "WHERE id = %s",
                    (row["id"],),
                )
                continue

            page_row, image_path, image, result, latency_ms = outcome
            analysis = result.result.model_dump(mode="json")
            analysis["_provider"] = result.provider
            analysis["_model"] = result.model
            usage = result.usage.model_dump(mode="json")
            status = "needs_review" if result.result.needs_review else "completed"
            relative_image = image_path.relative_to(settings.data_dir).as_posix()
            await conn.execute(
                """
                UPDATE document_pages
                SET page_image_path = %s, image_hash = %s, analysis = %s,
                    analysis_provider = %s, analysis_model = %s,
                    analysis_latency_ms = %s, analysis_usage = %s,
                    analyzed_at = now(), content_type = %s, confidence = %s,
                    needs_review = %s, vision_provider = %s, vision_model = %s,
                    processing_status = %s, updated_at = now()
                WHERE id = %s
                """,
                (
                    relative_image,
                    image_hash(image),
                    Jsonb(analysis),
                    result.provider,
                    result.model,
                    latency_ms,
                    Jsonb(usage),
                    str(result.result.content_type.value),
                    result.result.confidence,
                    result.result.needs_review,
                    result.provider,
                    result.model,
                    status,
                    page_row["id"],
                ),
            )
            await conn.execute(
                """
                INSERT INTO vision_runs
                    (job_id, document_page_id, provider, model, status, latency_ms, usage)
                VALUES (%s, %s, %s, %s, 'completed', %s, %s)
                """,
                (
                    str(job_id),
                    page_row["id"],
                    result.provider,
                    result.model,
                    latency_ms,
                    Jsonb(usage),
                ),
            )
            stats["vision_calls"] = int(stats.get("vision_calls", 0)) + 1
            stats["input_tokens"] = int(stats.get("input_tokens", 0)) + int(usage.get("input_tokens") or 0)
            stats["output_tokens"] = int(stats.get("output_tokens", 0)) + int(usage.get("output_tokens") or 0)

        processed += len(batch)
        progress = 15 + int(35 * processed / max(1, total))
        await conn.execute(
            "UPDATE ingestion_jobs SET progress = %s, stage = 'vision', "
            "heartbeat_at = now(), stats = %s WHERE id = %s",
            (progress, Jsonb(stats), str(job_id)),
        )
        await conn.commit()

    await _set_checkpoint(conn, job_id, "vision", {"pages": total})
    await conn.commit()


async def _ocr_scanned_pages(
    conn: AsyncConnection,
    settings: Settings,
    version: dict[str, Any],
    stats: dict[str, Any],
) -> None:
    """Recover text for scanned pages that have no digital text.

    OCR supplements the vision analysis; it never replaces it. When no OCR
    engine is installed the pages keep their vision-only state and the run
    continues.
    """
    from app.ingestion.ocr import (
        OcrConfigurationError,
        OcrError,
        build_engine,
        ocr_page_image,
    )

    rows = await (
        await conn.execute(
            """
            SELECT id, physical_page_number, page_image_path
            FROM document_pages
            WHERE document_version_id = %s
              AND coalesce(text_char_count, 0) = 0
              AND page_image_path IS NOT NULL
            ORDER BY physical_page_number
            """,
            (str(version["id"]),),
        )
    ).fetchall()
    if not rows:
        return

    try:
        engine = build_engine(
            backend=settings.ingestion_ocr_backend,
            lang=settings.ingestion_ocr_language,
            use_gpu=settings.ingestion_ocr_use_gpu,
        )
    except OcrConfigurationError as exc:
        logger.warning("ocr_unavailable", error=str(exc))
        return

    for row in rows:
        image_path = settings.data_dir / str(row["page_image_path"])
        if not image_path.is_file():
            continue
        try:
            result = await ocr_page_image(engine, image_path)
        except OcrError as exc:
            logger.warning("ocr_failed", page=row["physical_page_number"], error=str(exc))
            stats["ocr_failures"] = int(stats.get("ocr_failures", 0)) + 1
            continue
        if not result.text.strip():
            continue
        await conn.execute(
            """
            UPDATE document_pages
            SET page_text = %s,
                text_hash = %s,
                text_char_count = %s,
                text_source = 'ocr',
                updated_at = now()
            WHERE id = %s
            """,
            (
                result.text,
                sha256_text(result.text),
                len(result.text),
                str(row["id"]),
            ),
        )
        stats["ocr_pages"] = int(stats.get("ocr_pages", 0)) + 1
        await conn.execute(
            "UPDATE ingestion_jobs SET stats = %s WHERE id = %s",
            (Jsonb(stats), str(version["id"])),
        )
        await conn.commit()
        logger.info(
            "ocr_page_completed",
            version_id=str(version["id"]),
            page=row["physical_page_number"],
            engine=result.engine,
            confidence=result.confidence,
        )


async def _build_and_embed_chunks(
    conn: AsyncConnection,
    settings: Settings,
    job_id: UUID,
    version: dict[str, Any],
    stats: dict[str, Any],
) -> None:
    version_id = version["id"]
    page_rows = await _all_page_rows(conn, version_id)
    knowledge = [
        PageKnowledge(page=_row_to_extraction(row), analysis=row.get("analysis"))
        for row in page_rows
    ]
    chunks = build_chunks(knowledge, settings)
    if not chunks:
        raise JobRunnerError("Chunking produced no searchable content")

    async with conn.transaction():
        await conn.execute(
            "DELETE FROM document_chunks WHERE document_version_id = %s",
            (str(version_id),),
        )
        for index, chunk in enumerate(chunks):
            await conn.execute(
                """
                INSERT INTO document_chunks
                    (document_version_id, document_id, page_start, page_end,
                     chunk_index, section_number, heading, content_type, content,
                     content_hash, char_count, token_estimate, metadata,
                     embedding_model, embedding_dim)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                """,
                (
                    str(version_id),
                    str(version["document_id"]),
                    chunk.page_start,
                    chunk.page_end,
                    index,
                    chunk.section_number,
                    chunk.heading,
                    chunk.content_type,
                    chunk.content,
                    chunk.content_hash,
                    chunk.char_count,
                    chunk.token_estimate,
                    Jsonb(chunk.metadata),
                    settings.embedding_model,
                    settings.embedding_dim,
                ),
            )
        await conn.execute(
            "UPDATE document_versions SET chunk_count = %s WHERE id = %s",
            (len(chunks), str(version_id)),
        )
        await _set_checkpoint(conn, job_id, "chunk", {"chunks": len(chunks)})
        await conn.execute(
            "UPDATE ingestion_jobs SET progress = 65, stage = 'chunked', "
            "heartbeat_at = now(), stats = %s WHERE id = %s",
            (Jsonb({**stats, "chunks": len(chunks)}), str(job_id)),
        )
    stats["chunks"] = len(chunks)

    embedder = get_embedder(settings)
    rows = await _chunks_without_embeddings(conn, version_id)
    total = len(rows)
    for start in range(0, total, settings.embedding_batch_size):
        if await _cancel_requested(conn, job_id):
            return
        batch = rows[start : start + settings.embedding_batch_size]
        vectors = await embedder.encode_documents([row["content"] for row in batch])
        if len(vectors) != len(batch):
            raise EmbeddingError("Embedding count did not match the chunk batch")
        async with conn.transaction():
            for row, vector in zip(batch, vectors, strict=True):
                if len(vector) != settings.embedding_dim:
                    raise EmbeddingError(
                        f"Embedding dimension {len(vector)} does not match EMBEDDING_DIM"
                    )
                await conn.execute(
                    """
                    UPDATE document_chunks
                    SET embedding = %s::vector, embedding_model = %s,
                        embedding_dim = %s, embedding_updated_at = now()
                    WHERE id = %s
                    """,
                    (
                        json.dumps(vector),
                        settings.embedding_model,
                        settings.embedding_dim,
                        row["id"],
                    ),
                )
        done = min(total, start + len(batch))
        progress = 65 + int(25 * done / max(1, total))
        await conn.execute(
            "UPDATE ingestion_jobs SET progress = %s, stage = 'embedding', "
            "heartbeat_at = now() WHERE id = %s",
            (progress, str(job_id)),
        )
        await conn.commit()
    await _set_checkpoint(conn, job_id, "embed", {"chunks": total})
    await conn.commit()


async def _vision_page_rows(conn: AsyncConnection, version_id: UUID) -> list[dict[str, Any]]:
    return await (
        await conn.execute(
            """
            SELECT id, physical_page_number, printed_page_number, page_text
            FROM document_pages
            WHERE document_version_id = %s
              AND analysis IS NULL
              AND coalesce((layout->'classification'->>'needs_vision')::boolean, false)
              AND processing_status IN ('pending', 'failed')
            ORDER BY physical_page_number
            """,
            (str(version_id),),
        )
    ).fetchall()


async def _page_context_map(conn: AsyncConnection, version_id: UUID) -> dict[int, dict[str, Any]]:
    rows = await (
        await conn.execute(
            """
            SELECT physical_page_number, page_text, layout, analysis
            FROM document_pages
            WHERE document_version_id = %s
            ORDER BY physical_page_number
            """,
            (str(version_id),),
        )
    ).fetchall()
    context: dict[int, dict[str, Any]] = {}
    for index, row in enumerate(rows):
        previous = rows[index - 1] if index > 0 else None
        following = rows[index + 1] if index + 1 < len(rows) else None
        heading = _heading_for(row)
        previous_heading = _heading_for(previous) if previous else None
        context[int(row["physical_page_number"])] = {
            "heading": heading,
            "previous_heading": previous_heading,
            "preceding_text": str(previous.get("page_text") or "")[:4000] if previous else "",
            "following_text": str(following.get("page_text") or "")[:2000] if following else "",
            "section_number": _section_for(row),
        }
    return context


def _heading_for(row: dict[str, Any] | None) -> str | None:
    if not row:
        return None
    analysis = row.get("analysis") or {}
    headings = analysis.get("headings") or []
    if headings:
        return str(headings[0])[:200]
    text = str(row.get("page_text") or "").strip()
    for line in text.splitlines()[:10]:
        candidate = line.strip()
        if 3 <= len(candidate) <= 160 and not candidate.endswith((".", ";")):
            return candidate
    return None


def _section_for(row: dict[str, Any]) -> str | None:
    analysis = row.get("analysis") or {}
    if analysis.get("section_number"):
        return str(analysis["section_number"])
    return None


async def _all_page_rows(conn: AsyncConnection, version_id: UUID) -> list[dict[str, Any]]:
    return await (
        await conn.execute(
            """
            SELECT physical_page_number, page_text, text_hash, content_type, layout,
                   analysis, text_source, image_count, image_coverage,
                   width_pt, height_pt
            FROM document_pages
            WHERE document_version_id = %s
            ORDER BY physical_page_number
            """,
            (str(version_id),),
        )
    ).fetchall()


async def _load_page_extractions(conn: AsyncConnection, version_id: UUID) -> list[PageExtraction]:
    rows = await _all_page_rows(conn, version_id)
    return [_row_to_extraction(row) for row in rows]


def _row_to_extraction(row: dict[str, Any]) -> PageExtraction:
    layout = row.get("layout") or {}
    text = str(row.get("page_text") or "")
    return PageExtraction(
        page_number=int(row["physical_page_number"]),
        width_pt=float(row.get("width_pt") or layout.get("width_pt") or 0),
        height_pt=float(row.get("height_pt") or layout.get("height_pt") or 0),
        text=text,
        blocks=layout.get("blocks") or [],
        images=layout.get("images") or [],
        links=layout.get("links") or [],
        tables=layout.get("tables") or [],
        image_count=int(row.get("image_count") or 0),
        image_coverage=float(row.get("image_coverage") or 0),
        char_count=len(text.strip()),
        text_hash=row.get("text_hash"),
        layout=layout,
    )


async def _chunks_without_embeddings(conn: AsyncConnection, version_id: UUID) -> list[dict[str, Any]]:
    return await (
        await conn.execute(
            """
            SELECT id, content FROM document_chunks
            WHERE document_version_id = %s AND embedding IS NULL
            ORDER BY chunk_index
            """,
            (str(version_id),),
        )
    ).fetchall()


async def _record_vision_failure(
    conn: AsyncConnection,
    job_id: UUID,
    page_id: str,
    provider: str,
    model: str,
    exc: BaseException,
) -> None:
    message = f"{type(exc).__name__}: {exc}"[:2000]
    await conn.execute(
        """
        INSERT INTO vision_runs
            (job_id, document_page_id, provider, model, status, error)
        VALUES (%s, %s, %s, %s, 'failed', %s)
        """,
        (str(job_id), page_id, provider, model, message),
    )
    await conn.commit()


async def _get_job(conn: AsyncConnection, job_id: UUID) -> dict[str, Any] | None:
    return await (
        await conn.execute("SELECT * FROM ingestion_jobs WHERE id = %s", (str(job_id),))
    ).fetchone()


async def _update_job(conn: AsyncConnection, job_id: UUID, **fields: Any) -> None:
    allowed = {
        "status",
        "stage",
        "progress",
        "started_at",
        "heartbeat_at",
        "stats",
        "completed_at",
    }
    assignments: list[str] = []
    values: list[Any] = []
    for key, value in fields.items():
        if key not in allowed:
            raise ValueError(f"Unsupported job field: {key}")
        assignments.append(f"{key} = %s")
        values.append(Jsonb(value) if key == "stats" else value)
    if not assignments:
        return
    values.append(str(job_id))
    await conn.execute(
        f"UPDATE ingestion_jobs SET {', '.join(assignments)} WHERE id = %s",
        tuple(values),
    )
    await conn.commit()


async def _cancel_requested(conn: AsyncConnection, job_id: UUID) -> bool:
    row = await (
        await conn.execute(
            "SELECT cancel_requested, status FROM ingestion_jobs WHERE id = %s",
            (str(job_id),),
        )
    ).fetchone()
    return bool(row and (row["cancel_requested"] or row["status"] == "cancelled"))


async def _mark_cancelled(
    conn: AsyncConnection, job_id: UUID, version: dict[str, Any]
) -> None:
    async with conn.transaction():
        await conn.execute(
            "UPDATE ingestion_jobs SET status = 'cancelled', stage = 'cancelled', "
            "completed_at = now(), heartbeat_at = now() WHERE id = %s",
            (str(job_id),),
        )
        await conn.execute(
            "UPDATE document_versions SET status = 'uploaded' WHERE id = %s",
            (str(version["id"]),),
        )
        await conn.execute(
            "UPDATE documents SET status = 'draft' WHERE id = %s",
            (str(version["document_id"]),),
        )
    await conn.commit()


async def _fail_job(
    conn: AsyncConnection,
    job_id: UUID,
    message: str,
    *,
    version: dict[str, Any] | None = None,
) -> None:
    async with conn.transaction():
        await conn.execute(
            "UPDATE ingestion_jobs SET status = 'failed', stage = 'failed', "
            "completed_at = now(), heartbeat_at = now(), error_details = %s "
            "WHERE id = %s",
            (Jsonb({"message": message[:4000]}), str(job_id)),
        )
        if version:
            await conn.execute(
                "UPDATE document_versions SET status = 'failed' WHERE id = %s",
                (str(version["id"]),),
            )
            await conn.execute(
                "UPDATE documents SET status = 'draft' WHERE id = %s",
                (str(version["document_id"]),),
            )
    await conn.commit()
    logger.error("ingestion_job_failed", job_id=str(job_id), message=message[:500])


async def _get_checkpoint(
    conn: AsyncConnection, job_id: UUID, key: str
) -> dict[str, Any] | None:
    row = await (
        await conn.execute(
            "SELECT checkpoint_data FROM job_checkpoints WHERE job_id = %s AND checkpoint_key = %s",
            (str(job_id), key),
        )
    ).fetchone()
    return row["checkpoint_data"] if row else None


async def _set_checkpoint(
    conn: AsyncConnection, job_id: UUID, key: str, data: dict[str, Any]
) -> None:
    await conn.execute(
        """
        INSERT INTO job_checkpoints (job_id, checkpoint_key, checkpoint_data)
        VALUES (%s, %s, %s)
        ON CONFLICT (job_id, checkpoint_key) DO UPDATE
            SET checkpoint_data = EXCLUDED.checkpoint_data,
                created_at = now()
        """,
        (str(job_id), key, Jsonb(data)),
    )


async def _count_pages(
    conn: AsyncConnection, version_id: UUID, *, statuses: tuple[str, ...]
) -> int:
    row = await (
        await conn.execute(
            "SELECT count(*) AS n FROM document_pages "
            "WHERE document_version_id = %s AND processing_status = ANY(%s)",
            (str(version_id), list(statuses)),
        )
    ).fetchone()
    return int(row["n"])


async def _count_all_pages(conn: AsyncConnection, version_id: UUID) -> int:
    row = await (
        await conn.execute(
            "SELECT count(*) AS n FROM document_pages WHERE document_version_id = %s",
            (str(version_id),),
        )
    ).fetchone()
    return int(row["n"])


async def _safe_rollback(conn: AsyncConnection) -> None:
    try:
        await conn.rollback()
    except Exception:  # noqa: BLE001 - the connection may already be closed
        pass
