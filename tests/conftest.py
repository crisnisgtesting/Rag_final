"""Shared pytest fixtures.

Unit tests run without a database. Integration tests are skipped unless a
PostgreSQL 16 + pgvector instance is reachable (start it with
``python scripts/dev_db.py start``).
"""

from __future__ import annotations

import asyncio
import os
import sys
from pathlib import Path
from uuid import uuid4

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

if sys.platform == "win32":
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

import psycopg  # noqa: E402
from psycopg.rows import dict_row  # noqa: E402

from app.config import Settings  # noqa: E402

TEST_DATABASE_URL = os.environ.get(
    "TEST_DATABASE_URL", "postgresql://postgres@127.0.0.1:55432/fmm_dev"
)
TEST_JWT_SECRET = "test-secret-0123456789-0123456789-0123456789"


def database_reachable(url: str) -> bool:
    try:
        with psycopg.connect(url, connect_timeout=3) as conn:
            conn.execute("SELECT 1")
        return True
    except Exception:  # noqa: BLE001
        return False


@pytest.fixture(scope="session")
def database_url() -> str:
    return TEST_DATABASE_URL


@pytest.fixture(scope="session")
def migrated_db(database_url: str) -> bool:
    if not database_reachable(database_url):
        pytest.skip(
            "PostgreSQL test database is not reachable. "
            "Run: python scripts/dev_db.py start"
        )
    from app.db.migrate import apply_migrations

    with psycopg.connect(database_url, autocommit=True) as conn:
        apply_migrations(conn)
    return True


@pytest.fixture
def settings(tmp_path: Path) -> Settings:
    return Settings(
        app_env="test",
        database_url=TEST_DATABASE_URL,
        jwt_secret=TEST_JWT_SECRET,
        vision_provider="qwen",
        gemini_api_key=None,
        gemini_vision_model=None,
        qwen_vision_api_key=None,
        databricks_host=None,
        databricks_client_id=None,
        databricks_client_secret=None,
        databricks_sql_http_path=None,
        databricks_vision_endpoint=None,
        model_dir=tmp_path / "models",
        data_dir=tmp_path / "data",
        log_dir=tmp_path / "logs",
    )


@pytest.fixture
def api_client(settings: Settings, migrated_db: bool):
    from fastapi.testclient import TestClient

    from app.main import create_app

    application = create_app(settings)
    with TestClient(application) as client:
        yield client


@pytest.fixture
def admin_account(settings: Settings, migrated_db: bool) -> dict[str, str]:
    from app.services.auth_service import create_user

    username = f"test_admin_{uuid4().hex[:10]}"
    password = "TestPassword123!"

    async def _create() -> str:
        async with await psycopg.AsyncConnection.connect(
            settings.database_url, row_factory=dict_row
        ) as conn:
            user_id = await create_user(
                conn,
                username=username,
                password=password,
                roles=["admin"],
                email=f"{username}@example.test",
                full_name="Test Administrator",
            )
        return str(user_id)

    user_id = asyncio.run(_create())
    yield {"id": user_id, "username": username, "password": password}

    with psycopg.connect(settings.database_url, autocommit=True) as conn:
        conn.execute("DELETE FROM users WHERE id = %s", (user_id,))


@pytest.fixture
def auth_headers(
    api_client, admin_account: dict[str, str]
) -> dict[str, str]:
    response = api_client.post(
        "/auth/login",
        json={
            "username": admin_account["username"],
            "password": admin_account["password"],
        },
    )
    assert response.status_code == 200, response.text
    token = response.json()["access_token"]
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture
def clean_vision_setting(database_url: str, migrated_db: bool):
    def _clear() -> None:
        with psycopg.connect(database_url, autocommit=True) as conn:
            conn.execute("DELETE FROM app_settings WHERE key = 'vision.provider'")

    _clear()
    yield
    _clear()
