from __future__ import annotations

import asyncio
import json

import pytest

from app.config import Settings
from app.db.compat import DictRow, Jsonb
from app.db.databricks import (
    DatabricksParameterError,
    DatabricksPool,
    _normalize_parameters,
)


def test_positional_parameters_use_qmark_style() -> None:
    query, params = _normalize_parameters(
        "SELECT * FROM t WHERE id = %s AND metadata = %s",
        ["id-1", Jsonb({"a": 1})],
    )
    assert query == "SELECT * FROM t WHERE id = ? AND metadata = ?"
    assert params == ["id-1", json.dumps({"a": 1}, separators=(",", ":"))]


def test_named_parameters_use_colon_style() -> None:
    query, params = _normalize_parameters(
        "SELECT * FROM t WHERE id = %(id)s", {"id": "id-1"}
    )
    assert query == "SELECT * FROM t WHERE id = :id"
    assert params == {"id": "id-1"}


def test_list_parameters_are_rejected_for_native_sql() -> None:
    with pytest.raises(DatabricksParameterError):
        _normalize_parameters("SELECT * FROM t WHERE id IN %s", [["a", "b"]])


def test_dict_row_supports_mapping_access() -> None:
    row = DictRow(("id-1", 3), ["id", "count"])
    assert row["id"] == "id-1"
    assert row[1] == 3
    assert row.get("missing") is None
    assert dict(row) == {"id": "id-1", "count": 3}


def test_pool_health_check_with_fake_connection() -> None:
    class FakeCursor:
        def __init__(self) -> None:
            self.description = (("ok",),)

        async def execute(self, query: str, params: object) -> FakeCursor:
            self.description = (("ok",),)
            return self

        async def fetchone(self) -> DictRow:
            return DictRow((1,), ["ok"])

        async def close(self) -> None:
            return None

    class FakeConnection:
        def cursor(self) -> FakeCursor:
            return FakeCursor()

        async def execute(self, query: str, params: object = None) -> FakeCursor:
            return await self.cursor().execute(query, params)

        async def release(self) -> None:
            return None

    settings = Settings(
        app_env="test",
        databricks_host="https://workspace.databricks.com",
        databricks_client_id="client-id",
        databricks_client_secret="client-secret",
        databricks_sql_http_path="/sql/1.0/warehouses/example",
    )
    pool = DatabricksPool(settings)

    async def fake_connect() -> FakeConnection:
        return FakeConnection()

    pool._connect = fake_connect  # type: ignore[method-assign]
    result = asyncio.run(pool.health_check())
    assert result["database"] is True
    assert result["server_version"] == "databricks-sql"
    assert result["pgvector"] is False
