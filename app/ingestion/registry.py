"""Document registration, storage and job records."""

from __future__ import annotations

import re
import shutil
from pathlib import Path
from typing import Any
from uuid import UUID, uuid4

import pymupdf
from psycopg import AsyncConnection
from psycopg.types.json import Jsonb

from app.config import Settings
from app.logging_conf import get_logger
from app.services.audit import record_audit
from app.utils.hashing import sha256_file

logger = get_logger(__name__)


class RegistryError(RuntimeError):
    pass


SUPPORTED_EXTENSIONS = {
    ".pdf": "pdf",
    ".docx": "docx",
    ".md": "markdown",
    ".markdown": "markdown",
}


def safe_filename(name: str) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9._ -]+", "_", Path(name).name).strip(" ._")
    return cleaned[:200] or "document"


def document_type_for(path: Path) -> str:
    document_type = SUPPORTED_EXTENSIONS.get(path.suffix.lower())
    if document_type is None:
        allowed = ", ".join(sorted(SUPPORTED_EXTENSIONS))
        raise RegistryError(f"Unsupported document type. Allowed extensions: {allowed}")
    return document_type


def _title_for(path: Path, document_type: str) -> str:
    if document_type == "pdf":
        try:
            with pymupdf.open(path) as document:
                title = (document.metadata or {}).get("title")
                if isinstance(title, str) and title.strip():
                    return title.strip()[:300]
        except Exception:  # noqa: BLE001 - title is best effort
            pass
    elif document_type == "markdown":
        try:
            for line in path.read_text(encoding="utf-8-sig", errors="replace").splitlines():
                if line.startswith("# "):
                    return line[2:].strip()[:300]
        except OSError:
            pass
    elif document_type == "docx":
        try:
            import zipfile

            with zipfile.ZipFile(path) as archive:
                xml = archive.read("docProps/core.xml").decode("utf-8", "replace")
            match = re.search(r"<dc:title>(.*?)</dc:title>", xml, re.DOTALL)
            if match:
                title = re.sub(r"<[^>]+>", "", match.group(1)).strip()
                if title:
                    return title[:300]
        except Exception:  # noqa: BLE001 - title is best effort
            pass
    return path.stem.replace("_", " ").replace("-", " ").strip()[:300] or path.name


def _storage_path(version_id: UUID, suffix: str = ".pdf") -> str:
    return f"document_versions/{version_id}/source{suffix.lower()}"


def resolve_storage_path(settings: Settings, storage_path: str) -> Path:
    root = settings.data_dir.resolve()
    path = (root / storage_path).resolve()
    if root not in path.parents:
        raise RegistryError("Stored document path escapes the data directory")
    return path


async def register_document(
    conn: AsyncConnection,
    settings: Settings,
    path: Path,
    *,
    document_type: str | None = None,
    actor_user_id: UUID | str | None = None,
) -> dict[str, Any]:
    document_type = document_type or document_type_for(path)
    if document_type not in {"pdf", "docx", "markdown"}:
        raise RegistryError(f"Unsupported document type: {document_type}")
    digest = sha256_file(path)
    existing = await (
        await conn.execute(
            """
            SELECT dv.id, dv.document_id, d.title
            FROM document_versions dv
            JOIN documents d ON d.id = dv.document_id
            WHERE dv.content_hash = %s
            ORDER BY dv.uploaded_at
            LIMIT 1
            """,
            (digest,),
        )
    ).fetchone()
    if existing:
        return {
            "status": "duplicate",
            "filename": path.name,
            "sha256": digest,
            "document_id": str(existing["document_id"]),
            "version_id": str(existing["id"]),
            "title": existing["title"],
            "document_type": document_type,
        }

    title = _title_for(path, document_type)
    filename = safe_filename(path.name)
    version_id = uuid4()
    storage_path = _storage_path(version_id, path.suffix or ".bin")

    async with conn.transaction():
        document = await (
            await conn.execute(
                """
                SELECT id FROM documents
                WHERE lower(filename) = lower(%s)
                ORDER BY created_at DESC
                LIMIT 1
                """,
                (filename,),
            )
        ).fetchone()

        if document:
            document_id = document["id"]
            await conn.execute(
                """
                UPDATE documents
                SET filename = %s, title = %s, document_type = %s,
                    source = coalesce(source, 'inbox'), updated_at = now()
                WHERE id = %s
                """,
                (filename, title, document_type, document_id),
            )
            next_number = await (
                await conn.execute(
                    "SELECT coalesce(max(version_number), 0) + 1 AS n "
                    "FROM document_versions WHERE document_id = %s",
                    (document_id,),
                )
            ).fetchone()
        else:
            document_id = uuid4()
            next_number = {"n": 1}
            await conn.execute(
                """
                INSERT INTO documents
                    (id, title, filename, document_type, source, status, uploaded_by)
                VALUES (%s, %s, %s, %s, 'inbox', 'draft', %s)
                """,
                (
                    str(document_id),
                    title,
                    filename,
                    document_type,
                    str(actor_user_id) if actor_user_id else None,
                ),
            )

        destination = settings.data_dir / storage_path
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(path, destination)

        await conn.execute(
            """
            INSERT INTO document_versions
                (id, document_id, version_number, content_hash, storage_path,
                 status, approval_status, metadata)
            VALUES (%s, %s, %s, %s, %s, 'uploaded', 'draft', %s)
            """,
            (
                str(version_id),
                str(document_id),
                int(next_number["n"]),
                digest,
                storage_path,
                Jsonb(
                    {
                        "original_filename": path.name,
                        "size_bytes": path.stat().st_size,
                        "title": title,
                        "document_type": document_type,
                    }
                ),
            ),
        )
        await record_audit(
            conn,
            actor_user_id=actor_user_id,
            action="document.registered",
            entity_type="document_version",
            entity_id=str(version_id),
            metadata={
                "filename": path.name,
                "document_id": str(document_id),
                "document_type": document_type,
            },
        )

    return {
        "status": "registered",
        "filename": path.name,
        "sha256": digest,
        "document_id": str(document_id),
        "version_id": str(version_id),
        "title": title,
        "document_type": document_type,
    }


async def register_pdf(
    conn: AsyncConnection,
    settings: Settings,
    path: Path,
    *,
    actor_user_id: UUID | str | None = None,
) -> dict[str, Any]:
    return await register_document(
        conn,
        settings,
        path,
        document_type="pdf",
        actor_user_id=actor_user_id,
    )


async def scan_inbox(
    conn: AsyncConnection,
    settings: Settings,
    *,
    actor_user_id: UUID | str | None = None,
) -> dict[str, Any]:
    inbox = settings.data_dir / "documents"
    inbox.mkdir(parents=True, exist_ok=True)
    paths = sorted(
        path
        for path in inbox.iterdir()
        if path.is_file() and path.suffix.lower() in SUPPORTED_EXTENSIONS
    )
    items: list[dict[str, Any]] = []
    for path in paths:
        items.append(
            await register_document(conn, settings, path, actor_user_id=actor_user_id)
        )
    return {
        "scanned": len(items),
        "registered": sum(1 for item in items if item["status"] == "registered"),
        "duplicates": sum(1 for item in items if item["status"] == "duplicate"),
        "items": items,
    }


async def list_documents(conn: AsyncConnection) -> list[dict[str, Any]]:
    rows = await (
        await conn.execute(
            """
            SELECT d.id, d.title, d.filename, d.status, d.created_at, d.updated_at,
                   d.current_version_id,
                   v.id AS latest_version_id, v.version_number,
                   v.status AS version_status, v.approval_status,
                   v.page_count, v.chunk_count, v.indexed_at,
                   j.id AS latest_job_id, j.status AS latest_job_status,
                   j.stage AS latest_job_stage, j.progress AS latest_job_progress
            FROM documents d
            LEFT JOIN LATERAL (
                SELECT * FROM document_versions dv
                WHERE dv.document_id = d.id
                ORDER BY dv.version_number DESC
                LIMIT 1
            ) v ON true
            LEFT JOIN LATERAL (
                SELECT * FROM ingestion_jobs ij
                WHERE ij.document_version_id = v.id
                ORDER BY ij.created_at DESC
                LIMIT 1
            ) j ON true
            ORDER BY d.created_at DESC, d.title
            """
        )
    ).fetchall()
    return rows


async def document_detail(conn: AsyncConnection, document_id: UUID) -> dict[str, Any] | None:
    document = await (
        await conn.execute("SELECT * FROM documents WHERE id = %s", (str(document_id),))
    ).fetchone()
    if document is None:
        return None
    versions = await (
        await conn.execute(
            """
            SELECT id, version_number, content_hash, storage_path, status,
                   approval_status, page_count, chunk_count, indexed_at,
                   approved_at, metadata, uploaded_at
            FROM document_versions
            WHERE document_id = %s
            ORDER BY version_number DESC
            """,
            (str(document_id),),
        )
    ).fetchall()
    return {"document": document, "versions": versions}


async def list_pages(
    conn: AsyncConnection, version_id: UUID, *, limit: int = 500
) -> list[dict[str, Any]]:
    rows = await (
        await conn.execute(
            """
            SELECT id, physical_page_number, printed_page_number,
                   left(coalesce(page_text, ''), 1000) AS page_text_excerpt,
                   page_image_path, content_type, processing_status,
                   vision_provider, vision_model, confidence, needs_review,
                   text_source, text_char_count, image_count, image_coverage,
                   layout,
                   analysis->>'summary' AS analysis_summary
            FROM document_pages
            WHERE document_version_id = %s
            ORDER BY physical_page_number
            LIMIT %s
            """,
            (str(version_id), limit),
        )
    ).fetchall()
    return rows


async def list_chunks(
    conn: AsyncConnection, version_id: UUID, *, limit: int = 100
) -> list[dict[str, Any]]:
    rows = await (
        await conn.execute(
            """
            SELECT id, chunk_index, page_start, page_end, section_number, heading,
                   content_type, left(content, 1200) AS content_excerpt,
                   char_count, token_estimate, embedding_model,
                   embedding IS NOT NULL AS has_embedding
            FROM document_chunks
            WHERE document_version_id = %s
            ORDER BY chunk_index
            LIMIT %s
            """,
            (str(version_id), limit),
        )
    ).fetchall()
    return rows


async def version_context(
    conn: AsyncConnection, version_id: UUID
) -> dict[str, Any] | None:
    row = await (
        await conn.execute(
            """
            SELECT dv.*, d.title, d.filename, d.document_type,
                   d.status AS document_status
            FROM document_versions dv
            JOIN documents d ON d.id = dv.document_id
            WHERE dv.id = %s
            """,
            (str(version_id),),
        )
    ).fetchone()
    return row


async def create_job(
    conn: AsyncConnection,
    version_id: UUID,
    *,
    provider: str,
    model: str,
    config: dict[str, Any],
    actor_user_id: UUID | str | None = None,
) -> dict[str, Any]:
    version = await version_context(conn, version_id)
    if version is None:
        raise RegistryError("Document version was not found")
    existing = await (
        await conn.execute(
            """
            SELECT id, status FROM ingestion_jobs
            WHERE document_version_id = %s AND status IN ('queued', 'running')
            ORDER BY created_at DESC
            LIMIT 1
            """,
            (str(version_id),),
        )
    ).fetchone()
    if existing:
        raise RegistryError(f"An ingestion job is already {existing['status']} for this version")

    async with conn.transaction():
        job = await (
            await conn.execute(
                """
                INSERT INTO ingestion_jobs
                    (document_version_id, job_type, status, selected_vision_provider,
                     selected_vision_model, stage, config)
                VALUES (%s, 'document_ingest', 'queued', %s, %s, 'queued', %s)
                RETURNING id, document_version_id, status, stage, created_at
                """,
                (str(version_id), provider, model, Jsonb(config)),
            )
        ).fetchone()
        await conn.execute(
            "UPDATE document_versions SET status = 'processing' WHERE id = %s",
            (str(version_id),),
        )
        await conn.execute(
            "UPDATE documents SET status = 'processing' WHERE id = %s",
            (str(version["document_id"]),),
        )
        await record_audit(
            conn,
            actor_user_id=actor_user_id,
            action="ingestion.queued",
            entity_type="ingestion_job",
            entity_id=str(job["id"]),
            metadata={"version_id": str(version_id), "provider": provider, "model": model},
        )
    return job


async def list_jobs(conn: AsyncConnection, *, limit: int = 100) -> list[dict[str, Any]]:
    rows = await (
        await conn.execute(
            """
            SELECT j.*, d.title, d.id AS document_id, v.version_number
            FROM ingestion_jobs j
            JOIN document_versions v ON v.id = j.document_version_id
            JOIN documents d ON d.id = v.document_id
            ORDER BY j.created_at DESC
            LIMIT %s
            """,
            (limit,),
        )
    ).fetchall()
    return rows


async def job_detail(conn: AsyncConnection, job_id: UUID) -> dict[str, Any] | None:
    return await (
        await conn.execute(
            """
            SELECT j.*, d.title, d.id AS document_id, v.version_number
            FROM ingestion_jobs j
            JOIN document_versions v ON v.id = j.document_version_id
            JOIN documents d ON d.id = v.document_id
            WHERE j.id = %s
            """,
            (str(job_id),),
        )
    ).fetchone()


async def request_cancel(
    conn: AsyncConnection,
    job_id: UUID,
    *,
    actor_user_id: UUID | str | None = None,
) -> dict[str, Any] | None:
    async with conn.transaction():
        job = await (
            await conn.execute(
                """
                UPDATE ingestion_jobs
                SET cancel_requested = TRUE,
                    status = CASE WHEN status = 'queued' THEN 'cancelled' ELSE status END,
                    completed_at = CASE WHEN status = 'queued' THEN now() ELSE completed_at END
                WHERE id = %s
                RETURNING id, status, cancel_requested
                """,
                (str(job_id),),
            )
        ).fetchone()
        if job is None:
            return None
        await record_audit(
            conn,
            actor_user_id=actor_user_id,
            action="ingestion.cancel_requested",
            entity_type="ingestion_job",
            entity_id=str(job_id),
        )
    return job


async def resume_job(
    conn: AsyncConnection,
    job_id: UUID,
    *,
    actor_user_id: UUID | str | None = None,
) -> dict[str, Any]:
    async with conn.transaction():
        job = await (
            await conn.execute(
                """
                UPDATE ingestion_jobs
                SET status = 'queued', stage = 'queued', progress = 0,
                    cancel_requested = FALSE, error_details = NULL,
                    completed_at = NULL, heartbeat_at = NULL,
                    resume_count = resume_count + 1
                WHERE id = %s
                  AND status IN ('paused', 'failed', 'cancelled')
                RETURNING id, document_version_id, status, stage
                """,
                (str(job_id),),
            )
        ).fetchone()
        if job is None:
            raise RegistryError("Only paused, failed or cancelled jobs can be resumed")
        version = await version_context(conn, job["document_version_id"])
        if version is None:
            raise RegistryError("Document version was not found")
        await conn.execute(
            "UPDATE document_versions SET status = 'processing' WHERE id = %s",
            (str(version["id"]),),
        )
        await conn.execute(
            "UPDATE documents SET status = 'processing' WHERE id = %s",
            (str(version["document_id"]),),
        )
        await record_audit(
            conn,
            actor_user_id=actor_user_id,
            action="ingestion.resumed",
            entity_type="ingestion_job",
            entity_id=str(job_id),
        )
    return job


async def approve_version(
    conn: AsyncConnection,
    version_id: UUID,
    *,
    actor_user_id: UUID | str | None = None,
) -> dict[str, Any]:
    version = await version_context(conn, version_id)
    if version is None:
        raise RegistryError("Document version was not found")
    if version["status"] != "ready":
        raise RegistryError("Only fully processed versions can be approved")
    if version["approval_status"] == "approved":
        async with conn.transaction():
            await conn.execute(
                """
                UPDATE documents
                SET status = 'active', current_version_id = %s
                WHERE id = %s
                """,
                (str(version_id), str(version["document_id"])),
            )
        return {"id": str(version_id), "approval_status": "approved"}

    async with conn.transaction():
        await conn.execute(
            """
            UPDATE document_versions
            SET approval_status = 'superseded', superseded_at = now()
            WHERE document_id = %s AND approval_status = 'approved' AND id <> %s
            """,
            (str(version["document_id"]), str(version_id)),
        )
        await conn.execute(
            """
            UPDATE document_versions
            SET approval_status = 'approved', approved_at = now(), approved_by = %s
            WHERE id = %s
            """,
            (
                str(actor_user_id) if actor_user_id else None,
                str(version_id),
            ),
        )
        await conn.execute(
            """
            UPDATE documents
            SET status = 'active', current_version_id = %s
            WHERE id = %s
            """,
            (str(version_id), str(version["document_id"])),
        )
        await record_audit(
            conn,
            actor_user_id=actor_user_id,
            action="document.approved",
            entity_type="document_version",
            entity_id=str(version_id),
            metadata={"document_id": str(version["document_id"])},
        )
    return {"id": str(version_id), "approval_status": "approved"}


async def reject_version(
    conn: AsyncConnection,
    version_id: UUID,
    *,
    actor_user_id: UUID | str | None = None,
) -> dict[str, Any]:
    version = await version_context(conn, version_id)
    if version is None:
        raise RegistryError("Document version was not found")
    async with conn.transaction():
        await conn.execute(
            "UPDATE document_versions SET approval_status = 'rejected' WHERE id = %s",
            (str(version_id),),
        )
        await conn.execute(
            """
            UPDATE documents
            SET status = 'draft', current_version_id = NULL
            WHERE id = %s AND current_version_id = %s
            """,
            (str(version["document_id"]), str(version_id)),
        )
        await record_audit(
            conn,
            actor_user_id=actor_user_id,
            action="document.rejected",
            entity_type="document_version",
            entity_id=str(version_id),
        )
    return {"id": str(version_id), "approval_status": "rejected"}
