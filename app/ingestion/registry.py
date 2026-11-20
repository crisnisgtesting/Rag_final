"""Document registration, storage and job records."""

from __future__ import annotations

import re
from collections.abc import Mapping
from pathlib import Path
from typing import Any
from uuid import UUID, uuid4

import pymupdf

from app.config import Settings
from app.db.compat import Jsonb, decode_json_value
from app.db.databricks import DatabricksConnection
from app.logging_conf import get_logger
from app.services.audit import record_audit
from app.storage.volume import get_volume_store
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

JSON_FIELDS = {"analysis", "config", "error_details", "layout", "metadata", "stats"}


def _decode_row(row: Any) -> Any:
    if isinstance(row, Mapping):
        return {
            key: decode_json_value(value) if key in JSON_FIELDS else value
            for key, value in row.items()
        }
    return row


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


async def materialize_storage_path(settings: Settings, storage_path: str) -> Path:
    store = get_volume_store()
    relative = storage_path
    if relative.startswith(f"{store.root}/"):
        relative = relative[len(store.root) + 1 :]
    data = await store.read_bytes(relative)
    directory = settings.data_dir / "temporary" / "materialized"
    directory.mkdir(parents=True, exist_ok=True)
    target = directory / f"{uuid4().hex}-{Path(relative).name}"
    target.write_bytes(data)
    return target


async def register_document(
    conn: DatabricksConnection,
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
                    source = coalesce(source, 'inbox'),
                    updated_at = current_timestamp()
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
                    (id, title, filename, document_type, source, status, uploaded_by,
                     created_at, updated_at)
                VALUES (%s, %s, %s, %s, 'inbox', 'draft', %s,
                        current_timestamp(), current_timestamp())
                """,
                (
                    str(document_id),
                    title,
                    filename,
                    document_type,
                    str(actor_user_id) if actor_user_id else None,
                ),
            )

        await get_volume_store().write_bytes(storage_path, path.read_bytes())

        await conn.execute(
            """
            INSERT INTO document_versions
                (id, document_id, version_number, content_hash, storage_path,
                 status, approval_status, metadata, uploaded_at)
            VALUES (%s, %s, %s, %s, %s, 'uploaded', 'draft', %s, current_timestamp())
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
    conn: DatabricksConnection,
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
    conn: DatabricksConnection,
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


async def list_documents(conn: DatabricksConnection) -> list[dict[str, Any]]:
    rows = await (
        await conn.execute(
            """
            WITH ranked_versions AS (
                SELECT dv.*,
                       row_number() OVER (
                           PARTITION BY dv.document_id
                           ORDER BY dv.version_number DESC
                       ) AS version_rank
                FROM document_versions dv
            ),
            ranked_jobs AS (
                SELECT ij.*,
                       row_number() OVER (
                           PARTITION BY ij.document_version_id
                           ORDER BY ij.created_at DESC
                       ) AS job_rank
                FROM ingestion_jobs ij
            )
            SELECT d.id, d.title, d.filename, d.status, d.created_at, d.updated_at,
                   d.current_version_id,
                   v.id AS latest_version_id, v.version_number,
                   v.status AS version_status, v.approval_status,
                   v.page_count, v.chunk_count, v.indexed_at,
                   j.id AS latest_job_id, j.status AS latest_job_status,
                   j.stage AS latest_job_stage, j.progress AS latest_job_progress
            FROM documents d
            LEFT JOIN ranked_versions v
                ON v.document_id = d.id AND v.version_rank = 1
            LEFT JOIN ranked_jobs j
                ON j.document_version_id = v.id AND j.job_rank = 1
            ORDER BY d.created_at DESC, d.title
            """
        )
    ).fetchall()
    return rows


async def document_detail(conn: DatabricksConnection, document_id: UUID) -> dict[str, Any] | None:
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
    return {"document": document, "versions": [_decode_row(row) for row in versions]}


async def list_pages(
    conn: DatabricksConnection, version_id: UUID, *, limit: int = 500
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
                    get_json_object(analysis, '$.summary') AS analysis_summary

            FROM document_pages
            WHERE document_version_id = %s
            ORDER BY physical_page_number
            LIMIT %s
            """,
            (str(version_id), limit),
        )
    ).fetchall()
    return [_decode_row(row) for row in rows]


async def list_chunks(
    conn: DatabricksConnection, version_id: UUID, *, limit: int = 100
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
    conn: DatabricksConnection, version_id: UUID
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
    return _decode_row(row)


async def create_job(
    conn: DatabricksConnection,
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

    job_id = uuid4()
    async with conn.transaction():
        await conn.execute(
            """
            INSERT INTO ingestion_jobs
                (id, document_version_id, job_type, status, selected_vision_provider,
                 selected_vision_model, stage, config, created_at, updated_at)
            VALUES (%s, %s, 'document_ingest', 'queued', %s, %s, 'queued', %s,
                    current_timestamp(), current_timestamp())
            """,
            (str(job_id), str(version_id), provider, model, Jsonb(config)),
        )
        job = await (
            await conn.execute(
                """
                SELECT id, document_version_id, status, stage, created_at
                FROM ingestion_jobs WHERE id = %s
                """,
                (str(job_id),),
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


async def list_jobs(conn: DatabricksConnection, *, limit: int = 100) -> list[dict[str, Any]]:
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
    return [_decode_row(row) for row in rows]


async def job_detail(conn: DatabricksConnection, job_id: UUID) -> dict[str, Any] | None:
    row = await (
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
    return _decode_row(row)


async def request_cancel(
    conn: DatabricksConnection,
    job_id: UUID,
    *,
    actor_user_id: UUID | str | None = None,
) -> dict[str, Any] | None:
    async with conn.transaction():
        await conn.execute(
            """
            UPDATE ingestion_jobs
            SET cancel_requested = TRUE,
                status = CASE WHEN status = 'queued' THEN 'cancelled' ELSE status END,
                completed_at = CASE WHEN status = 'queued'
                                    THEN current_timestamp() ELSE completed_at END
            WHERE id = %s
            """,
            (str(job_id),),
        )
        job = await (
            await conn.execute(
                """
                SELECT id, status, cancel_requested
                FROM ingestion_jobs WHERE id = %s
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
    conn: DatabricksConnection,
    job_id: UUID,
    *,
    actor_user_id: UUID | str | None = None,
) -> dict[str, Any]:
    async with conn.transaction():
        await conn.execute(
            """
            UPDATE ingestion_jobs
            SET status = 'queued', stage = 'queued', progress = 0,
                cancel_requested = FALSE, error_details = NULL,
                completed_at = NULL, heartbeat_at = NULL,
                resume_count = resume_count + 1
            WHERE id = %s
              AND status IN ('paused', 'failed', 'cancelled')
            """,
            (str(job_id),),
        )
        job = await (
            await conn.execute(
                """
                SELECT id, document_version_id, status, stage
                FROM ingestion_jobs WHERE id = %s
                """,
                (str(job_id),),
            )
        ).fetchone()
        if job is None or job["status"] != "queued":
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
    conn: DatabricksConnection,
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
            SET approval_status = 'superseded', superseded_at = current_timestamp()
            WHERE document_id = %s AND approval_status = 'approved' AND id <> %s
            """,
            (str(version["document_id"]), str(version_id)),
        )
        await conn.execute(
            """
            UPDATE document_versions
            SET approval_status = 'approved', approved_at = current_timestamp(), approved_by = %s
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
    conn: DatabricksConnection,
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
