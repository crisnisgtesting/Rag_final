"""Async PostgreSQL connection pool (psycopg 3)."""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Any

from psycopg import AsyncConnection
from psycopg.rows import dict_row
from psycopg_pool import AsyncConnectionPool

from app.config import Settings
from app.logging_conf import get_logger

logger = get_logger(__name__)

_pool: AsyncConnectionPool | None = None


async def init_pool(settings: Settings) -> AsyncConnectionPool:
    global _pool
    if _pool is not None:
        return _pool
    pool = AsyncConnectionPool(
        conninfo=settings.database_url,
        min_size=1,
        max_size=10,
        open=False,
        kwargs={"row_factory": dict_row},
        name="fmm-pool",
    )
    await pool.open(wait=True, timeout=20)
    _pool = pool
    logger.info("database_pool_opened", min_size=1, max_size=10)
    return pool


async def close_pool() -> None:
    global _pool
    if _pool is not None:
        await _pool.close()
        _pool = None
        logger.info("database_pool_closed")


def get_pool() -> AsyncConnectionPool:
    if _pool is None:
        raise RuntimeError("Database pool has not been initialized")
    return _pool


async def get_db() -> AsyncIterator[AsyncConnection[Any]]:
    pool = get_pool()
    async with pool.connection() as conn:
        yield conn


async def check_database() -> dict[str, Any]:
    """Readiness probe: verifies connectivity and that pgvector is installed."""
    result: dict[str, Any] = {
        "database": False,
        "server_version": None,
        "pgvector": False,
        "pgvector_version": None,
    }
    pool = get_pool()
    async with pool.connection() as conn:
        row = await (await conn.execute("SHOW server_version")).fetchone()
        result["server_version"] = row["server_version"] if row else None
        result["database"] = True
        ext = await (
            await conn.execute(
                "SELECT extversion FROM pg_extension WHERE extname = 'vector'"
            )
        ).fetchone()
        if ext:
            result["pgvector"] = True
            result["pgvector_version"] = ext["extversion"]
    return result
