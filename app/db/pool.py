"""Async Databricks SQL connection pool."""

from __future__ import annotations

from collections.abc import AsyncIterator

from app.config import Settings
from app.db.databricks import DatabricksConnection, DatabricksPool
from app.logging_conf import get_logger

logger = get_logger(__name__)

_pool: DatabricksPool | None = None


async def init_pool(settings: Settings) -> DatabricksPool:
    global _pool
    if _pool is not None:
        return _pool
    pool = DatabricksPool(settings)
    await pool.initialize()
    _pool = pool
    logger.info(
        "databricks_sql_pool_opened",
        max_connections=getattr(settings, "databricks_sql_max_connections", 8),
        http_path=settings.databricks_sql_http_path,
    )
    return pool


async def close_pool() -> None:
    global _pool
    if _pool is not None:
        await _pool.close()
        _pool = None
        logger.info("databricks_sql_pool_closed")


def get_pool() -> DatabricksPool:
    if _pool is None:
        raise RuntimeError("Databricks SQL pool has not been initialized")
    return _pool


async def get_db() -> AsyncIterator[DatabricksConnection]:
    pool = get_pool()
    connection = await pool.acquire()
    try:
        yield connection
    finally:
        await connection.release()


async def check_database() -> dict[str, object]:
    return await get_pool().health_check()
