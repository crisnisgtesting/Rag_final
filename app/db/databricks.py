from __future__ import annotations

import asyncio
import re
from collections.abc import Mapping, Sequence
from contextlib import asynccontextmanager
from typing import Any

from app.db.compat import DictRow, Jsonb, serialize_json_value
from app.llm.auth.oauth import DatabricksOAuthTokenProvider

_NAMED_PARAMETER = re.compile(r"%\((?P<name>[A-Za-z_][A-Za-z0-9_]*)\)s")


class DatabricksParameterError(ValueError):
    pass


def _normalize_parameters(
    query: str, params: Any
) -> tuple[str, list[Any] | dict[str, Any] | None]:
    if params is None:
        return query, None

    if isinstance(params, Mapping):
        normalized_query = _NAMED_PARAMETER.sub(lambda match: f":{match.group('name')}", query)
        normalized = {
            str(key): serialize_json_value(value) for key, value in params.items()
        }
        return normalized_query, normalized

    if not isinstance(params, Sequence) or isinstance(params, str | bytes):
        raise DatabricksParameterError("SQL parameters must be a sequence or mapping")

    normalized_values: list[Any] = []
    for value in params:
        if isinstance(value, list | tuple) and not isinstance(value, Jsonb):
            raise DatabricksParameterError(
                "Databricks SQL requires scalar parameters; convert list parameters "
                "to Delta ARRAY literals or JSON strings in the query"
            )
        normalized_values.append(serialize_json_value(value))
    return query.replace("%s", "?"), normalized_values


class DatabricksCursor:
    def __init__(self, cursor: Any) -> None:
        self._cursor = cursor
        self._columns: list[str] = []

    @property
    def rowcount(self) -> int:
        return int(getattr(self._cursor, "rowcount", -1) or -1)

    @property
    def description(self) -> tuple[Any, ...]:
        return tuple(getattr(self._cursor, "description", ()) or ())

    async def execute(self, query: str, params: Any = None) -> DatabricksCursor:
        normalized_query, normalized_params = _normalize_parameters(query, params)
        await asyncio.to_thread(self._cursor.execute, normalized_query, normalized_params)
        self._columns = [column[0] for column in self.description]
        return self

    async def executemany(self, query: str, params: Sequence[Sequence[Any]]) -> DatabricksCursor:
        normalized_query, _ = _normalize_parameters(query, None)
        normalized_rows = []
        for row in params:
            _, normalized = _normalize_parameters(query, row)
            normalized_rows.append(normalized)
        await asyncio.to_thread(self._cursor.executemany, normalized_query, normalized_rows)
        self._columns = []
        return self

    async def fetchone(self) -> DictRow | tuple[Any, ...] | None:
        row = await asyncio.to_thread(self._cursor.fetchone)
        return self._wrap_row(row)

    async def fetchall(self) -> list[DictRow | tuple[Any, ...]]:
        rows = await asyncio.to_thread(self._cursor.fetchall) or []
        return [self._wrap_row(row) for row in rows]

    async def fetchmany(self, size: int | None = None) -> list[DictRow | tuple[Any, ...]]:
        rows = await asyncio.to_thread(self._cursor.fetchmany, size) or []
        return [self._wrap_row(row) for row in rows]

    async def close(self) -> None:
        await asyncio.to_thread(self._cursor.close)

    def _wrap_row(self, row: Any) -> DictRow | tuple[Any, ...] | None:
        if row is None or not self._columns:
            return row
        if isinstance(row, Mapping):
            return DictRow(tuple(row.values()), list(row.keys()))
        return DictRow(tuple(row), self._columns)


class DatabricksConnection:
    def __init__(self, connection: Any, pool: DatabricksPool) -> None:
        self._connection = connection
        self._pool = pool
        self._cursors: list[DatabricksCursor] = []
        self._transaction_depth = 0
        self._released = False

    def cursor(self) -> DatabricksCursor:
        cursor = DatabricksCursor(self._connection.cursor())
        self._cursors.append(cursor)
        return cursor

    async def execute(self, query: str, params: Any = None) -> DatabricksCursor:
        return await self.cursor().execute(query, params)

    async def commit(self) -> None:
        return None

    async def rollback(self) -> None:
        return None

    @asynccontextmanager
    async def transaction(self):
        self._transaction_depth += 1
        try:
            yield self
        except Exception:
            if self._transaction_depth == 1:
                await self.rollback()
            raise
        else:
            if self._transaction_depth == 1:
                await self.commit()
        finally:
            self._transaction_depth -= 1

    async def release(self) -> None:
        if self._released:
            return
        self._released = True
        for cursor in self._cursors:
            try:
                await cursor.close()
            except Exception:
                pass
        self._cursors.clear()
        await self._pool.release(self)

    async def close(self) -> None:
        await self.release()

    async def close_physical(self) -> None:
        await asyncio.to_thread(self._connection.close)


class DatabricksPool:
    def __init__(self, settings: Any) -> None:
        host = (getattr(settings, "databricks_host", None) or "").strip().rstrip("/")
        http_path = (getattr(settings, "databricks_sql_http_path", None) or "").strip()
        client_id = (getattr(settings, "databricks_client_id", None) or "").strip()
        secret = settings.databricks_secret_for("sql") or getattr(
            settings, "databricks_client_secret", None
        )
        client_secret = (
            secret.get_secret_value() if secret is not None else ""
        )
        missing = [
            name
            for name, value in (
                ("DATABRICKS_HOST", host),
                ("DATABRICKS_HTTP_PATH", http_path),
                ("DATABRICKS_CLIENT_ID", client_id),
                ("DATABRICKS_SQL_CLIENT_SECRET", client_secret),
            )
            if not value
        ]
        if missing:
            raise RuntimeError(
                "Databricks SQL is not configured: missing " + ", ".join(missing)
            )

        self._host = host
        self._http_path = http_path
        self._max_connections = max(
            1, int(getattr(settings, "databricks_sql_max_connections", 8) or 8)
        )
        self._semaphore = asyncio.Semaphore(self._max_connections)
        self._idle: asyncio.Queue[DatabricksConnection] = asyncio.Queue()
        self._closed = False
        self._oauth = DatabricksOAuthTokenProvider(
            host=host,
            client_id=client_id,
            client_secret=client_secret,
            scope=getattr(settings, "databricks_sql_oauth_scope", "sql") or "sql",
        )

    async def initialize(self) -> None:
        return None

    async def acquire(self) -> DatabricksConnection:
        if self._closed:
            raise RuntimeError("Databricks SQL pool is closed")
        await self._semaphore.acquire()
        try:
            return self._idle.get_nowait()
        except asyncio.QueueEmpty:
            pass
        try:
            return await self._connect()
        except Exception:
            self._semaphore.release()
            raise

    async def release(self, connection: DatabricksConnection) -> None:
        if self._closed:
            await connection.close_physical()
            self._semaphore.release()
            return
        self._idle.put_nowait(connection)
        self._semaphore.release()

    async def health_check(self) -> dict[str, Any]:
        connection = await self.acquire()
        try:
            cursor = await connection.execute("SELECT 1 AS ok")
            row = await cursor.fetchone()
            ok = bool(row and row["ok"] == 1)
            return {
                "database": ok,
                "server_version": "databricks-sql",
                "databricks_sql": ok,
                "pgvector": False,
                "pgvector_version": None,
            }
        finally:
            await connection.release()

    async def close(self) -> None:
        self._closed = True
        while not self._idle.empty():
            connection = self._idle.get_nowait()
            await connection.close_physical()
        await self._oauth.close()

    async def _connect(self) -> DatabricksConnection:
        try:
            from databricks import sql
        except ImportError as exc:  # pragma: no cover - dependency checked at runtime
            raise RuntimeError(
                "databricks-sql-connector is required for Databricks SQL access"
            ) from exc

        token = await self._oauth.get_token()
        connection = await asyncio.to_thread(
            sql.connect,
            server_hostname=self._host,
            http_path=self._http_path,
            access_token=token,
        )
        return DatabricksConnection(connection, self)


__all__ = [
    "DatabricksConnection",
    "DatabricksCursor",
    "DatabricksParameterError",
    "DatabricksPool",
    "Jsonb",
]
