from __future__ import annotations

import asyncio
import json
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any
from uuid import UUID, uuid4

from app.api.routers.jobs import _decode_fields
from app.config import Settings
from app.ingestion import job_runner, registry


def _settings(tmp_path: Path | None = None) -> Settings:
    return Settings(
        app_env="test",
        databricks_host="https://workspace.databricks.com",
        databricks_client_id="client-id",
        databricks_client_secret="client-secret",
        databricks_volume_path="/Volumes/prod_operations/rag/rag_documents",
        data_dir=tmp_path or Path("./data"),
    )


class FakeCursor:
    def __init__(self, response: Any) -> None:
        self._response = response

    async def fetchone(self) -> Any:
        return self._response if not isinstance(self._response, list) else None

    async def fetchall(self) -> Any:
        return self._response if isinstance(self._response, list) else []


class FakeConnection:
    def __init__(self, *responses: Any) -> None:
        self.responses = list(responses)
        self.queries: list[str] = []
        self.params: list[Any] = []

    async def execute(self, query: str, params: Any = None) -> FakeCursor:
        self.queries.append(query)
        self.params.append(params)
        response = self.responses.pop(0) if self.responses else None
        return FakeCursor(response)

    async def commit(self) -> None:
        return None

    async def rollback(self) -> None:
        return None

    @asynccontextmanager
    async def transaction(self):
        yield self


class FakeVolumeStore:
    root = "/Volumes/prod_operations/rag/rag_documents"

    def __init__(self, data: bytes = b"") -> None:
        self.data = data
        self.writes: list[tuple[str, bytes]] = []

    async def write_bytes(self, relative_path: str, data: bytes) -> str:
        self.writes.append((relative_path, data))
        return f"/Volumes/prod_operations/rag/rag_documents/{relative_path}"

    async def read_bytes(self, relative_path: str) -> bytes:
        return self.data


def test_registry_list_documents_uses_window_functions() -> None:
    conn = FakeConnection([])
    rows = asyncio.run(registry.list_documents(conn))
    assert rows == []
    query = conn.queries[0]
    assert "row_number() OVER" in query
    assert "LEFT JOIN LATERAL" not in query


def test_registry_create_job_uses_generated_id_and_no_returning() -> None:
    version_id = uuid4()
    document_id = uuid4()
    conn = FakeConnection(
        {"id": str(version_id), "document_id": str(document_id)},
        None,
        None,
        {"id": str(uuid4()), "document_version_id": str(version_id)},
    )
    job = asyncio.run(
        registry.create_job(
            conn,
            version_id,
            provider="databricks",
            model="databri-meta--instruct",
            config={"vision_mode": "auto"},
        )
    )
    assert job is not None
    joined = "\n".join(conn.queries)
    assert "RETURNING" not in joined
    assert "current_timestamp()" in joined
    assert UUID(str(conn.params[1][0]))


def test_register_document_writes_to_volume(tmp_path: Path, monkeypatch: Any) -> None:
    store = FakeVolumeStore()
    monkeypatch.setattr(registry, "get_volume_store", lambda: store)
    source = tmp_path / "manual.md"
    source.write_text("# Service Manual\n\nBody", encoding="utf-8")
    conn = FakeConnection(None, None)
    result = asyncio.run(
        registry.register_document(conn, _settings(tmp_path), source)
    )
    assert result["status"] == "registered"
    assert len(store.writes) == 1
    assert store.writes[0][1] == source.read_bytes()
    assert store.writes[0][0].startswith("document_versions/")


def test_materialize_storage_path_reads_from_volume(
    tmp_path: Path, monkeypatch: Any
) -> None:
    store = FakeVolumeStore(data=b"stored")
    monkeypatch.setattr(registry, "get_volume_store", lambda: store)
    path = asyncio.run(
        registry.materialize_storage_path(
            _settings(tmp_path), "document_versions/v1/source.pdf"
        )
    )
    assert path.read_bytes() == b"stored"
    assert path.parent == tmp_path / "temporary" / "materialized"


def test_set_checkpoint_uses_merge_with_generated_id() -> None:
    conn = FakeConnection()
    asyncio.run(job_runner._set_checkpoint(conn, uuid4(), "extract", {"pages": 4}))
    query = conn.queries[0]
    assert "MERGE INTO job_checkpoints" in query
    assert "ON CONFLICT" not in query
    assert isinstance(conn.params[0][-1], int)
    assert conn.params[0][2].value == {"pages": 4}


def test_count_pages_expands_status_placeholders() -> None:
    conn = FakeConnection({"n": 2})
    count = asyncio.run(
        job_runner._count_pages(conn, uuid4(), statuses=("completed", "failed"))
    )
    assert count == 2
    assert "processing_status IN (%s, %s)" in conn.queries[0]
    assert conn.params[0] == (str(conn.params[0][0]), "completed", "failed")


def test_vision_page_rows_uses_get_json_object() -> None:
    conn = FakeConnection([])
    rows = asyncio.run(job_runner._vision_page_rows(conn, uuid4()))
    assert rows == []
    query = conn.queries[0]
    assert "get_json_object(layout" in query
    assert "->>" not in query


def test_run_ingestion_job_claims_status_without_advisory_lock(
    monkeypatch: Any,
) -> None:
    job_id = uuid4()
    conn = FakeConnection(None, {"id": str(job_id)})

    class FakePool:
        @asynccontextmanager
        async def connection(self):
            yield conn

    async def fake_run(_conn: Any, _settings: Any, _job_id: UUID) -> None:
        return None

    monkeypatch.setattr(job_runner, "get_pool", lambda: FakePool())
    monkeypatch.setattr(job_runner, "_run", fake_run)
    asyncio.run(job_runner.run_ingestion_job(job_id, _settings()))
    joined = "\n".join(conn.queries)
    assert "pg_try_advisory_lock" not in joined
    assert "status = 'queued'" in joined
    assert "SELECT id FROM ingestion_jobs WHERE id = %s AND status = 'running'" in joined


def test_jobs_router_decodes_json_result_fields() -> None:
    decoded = _decode_fields(
        {"error": json.dumps({"message": "bad"}), "usage": json.dumps({"tokens": 3})},
        {"error", "usage"},
    )
    assert decoded == {"error": {"message": "bad"}, "usage": {"tokens": 3}}
