from __future__ import annotations

import asyncio
import json
from contextlib import asynccontextmanager
from typing import Any
from uuid import UUID

from app.services.audit import record_audit
from app.services.auth_service import create_user, get_user_with_roles
from app.services.settings_service import get_setting, set_setting


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

    @asynccontextmanager
    async def transaction(self):
        yield self


def test_create_user_uses_app_generated_uuid_and_no_postgres_sql() -> None:
    conn = FakeConnection(None, [{"role_code": "user"}])
    user_id = create_user_sync(conn)
    assert isinstance(user_id, UUID)
    joined = "\n".join(conn.queries)
    assert "RETURNING" not in joined
    assert "ANY(" not in joined
    assert "INSERT INTO users" in joined
    assert "current_timestamp()" in joined


def create_user_sync(conn: FakeConnection) -> UUID:
    return asyncio.run(create_user(conn, username="alice", password="Password123!"))


def test_get_user_with_roles_uses_delta_collect_set() -> None:
    conn = FakeConnection(
        {
            "id": "0f8fad5b-d9cb-469f-a165-70867728950e",
            "username": "alice",
            "email": None,
            "full_name": None,
            "is_active": True,
            "roles": ["user"],
        }
    )
    row = asyncio.run(
        get_user_with_roles(
            conn, UUID("0f8fad5b-d9cb-469f-a165-70867728950e")
        )
    )
    assert row is not None
    assert row["id"] == UUID("0f8fad5b-d9cb-469f-a165-70867728950e")
    assert "collect_set" in conn.queries[0]


def test_setting_round_trip_uses_merge_and_decodes_json() -> None:
    write_conn = FakeConnection()
    asyncio.run(set_setting(write_conn, "vision.provider", {"provider": "databricks"}))
    assert "MERGE INTO app_settings" in write_conn.queries[0]
    assert "ON CONFLICT" not in write_conn.queries[0]
    assert write_conn.params[0][1].value == {"provider": "databricks"}

    read_conn = FakeConnection({"value": json.dumps({"provider": "databricks"})})
    assert asyncio.run(get_setting(read_conn, "vision.provider")) == {
        "provider": "databricks"
    }


def test_audit_insert_generates_id_and_timestamp() -> None:
    conn = FakeConnection()
    asyncio.run(
        record_audit(
            conn,
            actor_user_id=None,
            action="test.action",
            metadata={"ok": True},
        )
    )
    query = conn.queries[0]
    assert "INSERT INTO audit_log" in query
    assert "current_timestamp()" in query
    assert isinstance(conn.params[0][0], int)
    assert conn.params[0][-1].value == {"ok": True}
