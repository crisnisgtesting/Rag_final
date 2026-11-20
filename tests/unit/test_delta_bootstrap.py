from __future__ import annotations

import asyncio

import pytest

from app.config import Settings
from app.db.bootstrap import (
    DeltaBootstrapError,
    bootstrap_schema,
    build_bootstrap_statements,
    quote_identifier,
    volume_identifiers,
)
from app.db.delta_schema import DELTA_TABLES, SCHEMA_VERSION


def _settings() -> Settings:
    return Settings(
        app_env="test",
        databricks_catalog="prod_operations",
        databricks_schema="rag",
        databricks_volume_path="/Volumes/prod_operations/rag/rag_documents",
    )


def test_quote_identifier_rejects_injection() -> None:
    with pytest.raises(DeltaBootstrapError):
        quote_identifier("rag; DROP TABLE users")


def test_volume_identifiers_from_volume_path() -> None:
    assert volume_identifiers(_settings()) == (
        "prod_operations",
        "rag",
        "rag_documents",
    )


def test_bootstrap_statements_include_catalog_schema_volume_and_tables() -> None:
    statements = build_bootstrap_statements(_settings())
    joined = "\n".join(statements)
    assert "CREATE CATALOG IF NOT EXISTS `prod_operations`" in joined
    assert "CREATE SCHEMA IF NOT EXISTS `prod_operations`.`rag`" in joined
    assert "CREATE VOLUME IF NOT EXISTS `prod_operations`.`rag`.`rag_documents`" in joined
    for table in DELTA_TABLES:
        assert f"`prod_operations`.`rag`.`{table}`" in joined
    assert f"'{SCHEMA_VERSION}'" in joined


def test_bootstrap_schema_executes_all_statements() -> None:
    class FakeCursor:
        async def execute(self, query: str, params: object = None) -> FakeCursor:
            return self

    class FakeConnection:
        def __init__(self) -> None:
            self.queries: list[str] = []
            self.released = False

        async def execute(self, query: str, params: object = None) -> FakeCursor:
            self.queries.append(query)
            return FakeCursor()

        async def release(self) -> None:
            self.released = True

    class FakePool:
        def __init__(self) -> None:
            self.connection = FakeConnection()

        async def acquire(self) -> FakeConnection:
            return self.connection

    pool = FakePool()
    expected = len(build_bootstrap_statements(_settings()))
    result = asyncio.run(bootstrap_schema(_settings(), pool))
    assert len(pool.connection.queries) == expected
    assert pool.connection.released is True
    assert result["statement_count"] == expected
    assert result["tables"] == list(DELTA_TABLES)
