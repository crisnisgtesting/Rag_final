"""Audit logging service."""

from __future__ import annotations

import time
from typing import Any
from uuid import UUID

from app.db.compat import Jsonb
from app.db.databricks import DatabricksConnection
from app.logging_conf import get_logger

logger = get_logger(__name__)


async def record_audit(
    conn: DatabricksConnection,
    *,
    actor_user_id: UUID | str | None,
    action: str,
    entity_type: str | None = None,
    entity_id: str | None = None,
    request_id: str | None = None,
    metadata: dict[str, Any] | None = None,
) -> None:
    async with conn.transaction():
        await conn.execute(
            """
            INSERT INTO audit_log
                (id, actor_user_id, action, entity_type, entity_id, request_id,
                 metadata, created_at)
            VALUES (%s, %s, %s, %s, %s, %s, %s, current_timestamp())
            """,
            (
                time.time_ns(),
                str(actor_user_id) if actor_user_id else None,
                action,
                entity_type,
                entity_id,
                request_id,
                Jsonb(metadata or {}),
            ),
        )
    logger.info(
        "audit_event",
        action=action,
        entity_type=entity_type,
        entity_id=entity_id,
        actor_user_id=str(actor_user_id) if actor_user_id else None,
    )
