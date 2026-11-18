"""SQL migration runner.

Migrations are plain, ordered ``.sql`` files tracked in ``schema_migrations``.
Production tables are never created by ad-hoc application startup logic.
"""

from __future__ import annotations

import argparse
import hashlib
import sys
from pathlib import Path

import psycopg

from app.config import get_settings
from app.logging_conf import configure_logging, get_logger

logger = get_logger(__name__)

MIGRATIONS_DIR = Path(__file__).resolve().parent / "migrations"
_SCHEMA_MIGRATIONS_DDL = """
CREATE TABLE IF NOT EXISTS schema_migrations (
    version     TEXT PRIMARY KEY,
    checksum    TEXT NOT NULL,
    applied_at  TIMESTAMPTZ NOT NULL DEFAULT now()
)
"""
_ADVISORY_LOCK_KEY = 0x464D4D  # "FMM"


class MigrationError(RuntimeError):
    pass


def migration_files(migrations_dir: Path = MIGRATIONS_DIR) -> list[Path]:
    return sorted(migrations_dir.glob("*.sql"), key=lambda path: path.name)


def _checksum(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def applied_versions(conn: psycopg.Connection) -> dict[str, str]:
    rows = conn.execute("SELECT version, checksum FROM schema_migrations").fetchall()
    return {row[0]: row[1] for row in rows}


def apply_migrations(
    conn: psycopg.Connection,
    migrations_dir: Path = MIGRATIONS_DIR,
) -> list[str]:
    """Apply pending migrations. Returns the list of newly applied versions.

    Raises ``MigrationError`` if an already-applied migration was modified.
    """
    conn.execute(_SCHEMA_MIGRATIONS_DDL)
    conn.execute("SELECT pg_advisory_lock(%s)", (_ADVISORY_LOCK_KEY,))
    applied_now: list[str] = []
    try:
        applied = applied_versions(conn)
        for path in migration_files(migrations_dir):
            checksum = _checksum(path)
            if path.name in applied:
                if applied[path.name] != checksum:
                    raise MigrationError(
                        f"Migration {path.name} was modified after being applied"
                    )
                continue
            sql = path.read_text(encoding="utf-8")
            with conn.transaction():
                conn.execute(sql)
                conn.execute(
                    "INSERT INTO schema_migrations (version, checksum) VALUES (%s, %s)",
                    (path.name, checksum),
                )
            logger.info("migration_applied", migration=path.name)
            applied_now.append(path.name)
    finally:
        conn.execute("SELECT pg_advisory_unlock(%s)", (_ADVISORY_LOCK_KEY,))
    return applied_now


def migration_status(conn: psycopg.Connection, migrations_dir: Path = MIGRATIONS_DIR) -> dict:
    conn.execute(_SCHEMA_MIGRATIONS_DDL)
    applied = applied_versions(conn)
    files = migration_files(migrations_dir)
    return {
        "applied": [path.name for path in files if path.name in applied],
        "pending": [path.name for path in files if path.name not in applied],
    }


def _main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Apply FMM database migrations")
    parser.add_argument("--status", action="store_true", help="Show migration status only")
    args = parser.parse_args(argv)
    settings = get_settings()
    configure_logging(settings.log_level, json_logs=False)
    try:
        with psycopg.connect(settings.database_url, autocommit=True) as conn:
            if args.status:
                status = migration_status(conn)
                print("applied:", *status["applied"], sep="\n  ")
                print("pending:", *status["pending"], sep="\n  ")
                return 0
            applied = apply_migrations(conn)
            if applied:
                print("Applied migrations:", *applied, sep="\n  ")
            else:
                print("No pending migrations.")
        return 0
    except (psycopg.Error, MigrationError) as exc:
        print(f"Migration failed: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(_main())
