from __future__ import annotations

import re
from typing import Any

from app.config import Settings
from app.db.delta_schema import DELTA_TABLES, SCHEMA_VERSION, TABLE_DDL
from app.db.pool import get_pool
from app.logging_conf import get_logger

logger = get_logger(__name__)

_IDENTIFIER = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


class DeltaBootstrapError(ValueError):
    pass


def quote_identifier(value: str) -> str:
    cleaned = value.strip()
    if not _IDENTIFIER.fullmatch(cleaned):
        raise DeltaBootstrapError(f"Invalid Databricks identifier: {value!r}")
    return f"`{cleaned}`"


def volume_identifiers(settings: Settings) -> tuple[str, str, str]:
    path = (settings.databricks_volume_path or "").strip().strip("/")
    parts = path.split("/")
    if len(parts) >= 4 and parts[0] == "Volumes":
        return parts[1], parts[2], parts[3]
    return (
        settings.databricks_catalog,
        settings.databricks_schema,
        "rag_documents",
    )


def build_bootstrap_statements(settings: Settings) -> list[str]:
    catalog = quote_identifier(settings.databricks_catalog)
    schema = quote_identifier(settings.databricks_schema)
    volume_catalog, volume_schema, volume_name = volume_identifiers(settings)
    qualified_volume = ".".join(
        (
            quote_identifier(volume_catalog),
            quote_identifier(volume_schema),
            quote_identifier(volume_name),
        )
    )

    statements = [
        f"CREATE CATALOG IF NOT EXISTS {catalog}",
        f"CREATE SCHEMA IF NOT EXISTS {catalog}.{schema}",
        f"CREATE VOLUME IF NOT EXISTS {qualified_volume}",
    ]

    for table, ddl in zip(DELTA_TABLES, TABLE_DDL, strict=True):
        qualified_table = f"{catalog}.{schema}.{quote_identifier(table)}"
        statements.append(ddl.format(table=qualified_table).strip())

    roles_table = f"{catalog}.{schema}.{quote_identifier('roles')}"
    statements.append(
        f"""
        MERGE INTO {roles_table} AS target
        USING (
            SELECT 'admin' AS role_code, 'Administrator' AS description
            UNION ALL
            SELECT 'user' AS role_code, 'Standard user' AS description
        ) AS source
        ON target.`role_code` = source.`role_code`
        WHEN MATCHED THEN UPDATE SET `description` = source.`description`
        WHEN NOT MATCHED THEN INSERT (`role_code`, `description`)
            VALUES (source.`role_code`, source.`description`)
        """.strip()
    )

    metadata_table = f"{catalog}.{schema}.{quote_identifier('rag_schema_metadata')}"
    statements.append(
        f"""
        MERGE INTO {metadata_table} AS target
        USING (
            SELECT 'schema_version' AS `key`, '{SCHEMA_VERSION}' AS `value`
        ) AS source
        ON target.`key` = source.`key`
        WHEN MATCHED THEN UPDATE SET
            `value` = source.`value`, `updated_at` = current_timestamp()
        WHEN NOT MATCHED THEN INSERT (`key`, `value`, `updated_at`)
            VALUES (source.`key`, source.`value`, current_timestamp())
        """.strip()
    )
    return statements


async def bootstrap_schema(settings: Settings, pool: Any | None = None) -> dict[str, Any]:
    statements = build_bootstrap_statements(settings)
    database = pool if pool is not None else get_pool()
    connection = await database.acquire()
    try:
        for statement in statements:
            await connection.execute(statement)
    finally:
        await connection.release()

    catalog, schema, volume = volume_identifiers(settings)
    result = {
        "catalog": catalog,
        "schema": schema,
        "volume": volume,
        "schema_version": SCHEMA_VERSION,
        "tables": list(DELTA_TABLES),
        "statement_count": len(statements),
    }
    logger.info(
        "delta_schema_bootstrapped",
        catalog=catalog,
        schema=schema,
        volume=volume,
        tables=len(DELTA_TABLES),
    )
    return result
