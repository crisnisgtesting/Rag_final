from __future__ import annotations

import psycopg
import pytest

pytestmark = pytest.mark.integration

REQUIRED_TABLES = {
    "schema_migrations",
    "roles",
    "users",
    "user_roles",
    "api_tokens",
    "audit_log",
    "documents",
    "document_versions",
    "document_pages",
    "document_chunks",
    "vision_runs",
    "chat_sessions",
    "chat_messages",
    "app_settings",
    "ingestion_jobs",
    "job_checkpoints",
    "request_logs",
    "knowledge_entities",
    "knowledge_relationships",
    "workflows",
    "workflow_steps",
    "visual_references",
    "knowledge_rules",
    "knowledge_sources",
    "knowledge_conflicts",
    "page_markdown",
    "chat_responses",
    "response_sources",
    "feedback",
    "unanswered_questions",
    "vision_evaluations",
    "eval_set",
    "eval_results",
}


def test_required_extensions_are_installed(database_url: str, migrated_db: bool) -> None:
    with psycopg.connect(database_url) as conn:
        rows = conn.execute("SELECT extname FROM pg_extension").fetchall()
    extensions = {row[0] for row in rows}
    assert {"plpgsql", "vector"} <= extensions


def test_core_tables_exist(database_url: str, migrated_db: bool) -> None:
    with psycopg.connect(database_url) as conn:
        rows = conn.execute(
            "SELECT tablename FROM pg_tables WHERE schemaname = 'public'"
        ).fetchall()
    tables = {row[0] for row in rows}
    missing = REQUIRED_TABLES - tables
    assert not missing, f"Missing tables: {sorted(missing)}"


def test_default_roles_are_seeded(database_url: str, migrated_db: bool) -> None:
    with psycopg.connect(database_url) as conn:
        rows = conn.execute("SELECT role_code FROM roles ORDER BY role_code").fetchall()
    assert [row[0] for row in rows] == ["admin", "user"]


def test_all_migrations_are_applied(database_url: str, migrated_db: bool) -> None:
    from app.db.migrate import migration_status

    with psycopg.connect(database_url, autocommit=True) as conn:
        status = migration_status(conn)
    assert status["applied"], "No migrations were applied"
    assert status["pending"] == [], f"Pending migrations: {status['pending']}"
