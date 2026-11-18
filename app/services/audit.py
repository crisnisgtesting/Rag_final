"""Audit logging service."""

from __future__ import annotations

from typing import Any
from uuid import UUID

from psycopg import AsyncConnection
from psycopg.types.json import Jsonb

from app.logging_conf import get_logger

logger = get_logger(__name__)


async def record_audit(
    conn: AsyncConnection,
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
                (actor_user_id, action, entity_type, entity_id, request_id, metadata)
            VALUES (%s, %s, %s, %s, %s, %s)
            """,
            (
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
