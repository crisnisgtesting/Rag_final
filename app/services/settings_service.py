"""Non-secret application settings stored in Databricks Delta.

Secrets (API keys, PATs, database credentials) are never stored here.
"""

from __future__ import annotations

import json
from typing import Any
from uuid import UUID

from app.config import Settings
from app.db.compat import Jsonb
from app.db.databricks import DatabricksConnection
from app.services.audit import record_audit
from app.vision.factory import ALLOWED_PROVIDERS, normalize_provider_name, provider_summaries

VISION_PROVIDER_KEY = "vision.provider"


async def get_setting(conn: DatabricksConnection, key: str) -> Any | None:
    row = await (
        await conn.execute("SELECT value FROM app_settings WHERE key = %s", (key,))
    ).fetchone()
    if row is None:
        return None
    value = row["value"]
    if isinstance(value, str):
        try:
            return json.loads(value)
        except json.JSONDecodeError:
            return value
    return value


async def set_setting(
    conn: DatabricksConnection,
    key: str,
    value: Any,
    *,
    actor_user_id: UUID | None = None,
) -> None:
    async with conn.transaction():
        await conn.execute(
            """
            MERGE INTO app_settings AS target
            USING (
                SELECT %s AS `key`, %s AS `value`, %s AS `updated_by`,
                       current_timestamp() AS `updated_at`
            ) AS source
            ON target.`key` = source.`key`
            WHEN MATCHED THEN UPDATE SET
                `value` = source.`value`,
                `updated_by` = source.`updated_by`,
                `updated_at` = source.`updated_at`
            WHEN NOT MATCHED THEN INSERT (`key`, `value`, `updated_by`, `updated_at`)
                VALUES (source.`key`, source.`value`, source.`updated_by`,
                        source.`updated_at`)
            """,
            (key, Jsonb(value), str(actor_user_id) if actor_user_id else None),
        )
        await record_audit(
            conn,
            actor_user_id=actor_user_id,
            action="settings.update",
            entity_type="app_setting",
            entity_id=key,
            metadata={"key": key},
        )


async def get_effective_vision_provider(
    conn: DatabricksConnection, settings: Settings
) -> tuple[str, str]:
    """Return (provider_name, source) where source is 'database' or 'environment'."""
    value = await get_setting(conn, VISION_PROVIDER_KEY)
    if isinstance(value, dict):
        provider = normalize_provider_name(str(value.get("provider", "")))
        if provider in ALLOWED_PROVIDERS:
            return provider, "database"
    return normalize_provider_name(settings.vision_provider), "environment"


async def set_vision_provider(
    conn: DatabricksConnection,
    provider: str,
    *,
    actor_user_id: UUID | None = None,
) -> str:
    name = normalize_provider_name(provider)
    if name not in ALLOWED_PROVIDERS:
        raise ValueError(
            f"Unsupported vision provider '{provider}'. "
            f"Allowed values: {', '.join(ALLOWED_PROVIDERS)}"
        )
    await set_setting(conn, VISION_PROVIDER_KEY, {"provider": name}, actor_user_id=actor_user_id)
    return name


async def get_vision_configuration(
    conn: DatabricksConnection, settings: Settings
) -> dict[str, Any]:
    active, source = await get_effective_vision_provider(conn, settings)
    return {
        "active_provider": active,
        "source": source,
        "environment_default": normalize_provider_name(settings.vision_provider),
        "providers": provider_summaries(settings, active_provider=active),
    }
