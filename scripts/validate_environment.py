"""Environment validation for development and deployment.

Checks Python version, dependencies, configuration, directories, database
connectivity and pgvector. Never prints secrets.
"""

from __future__ import annotations

import importlib
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

REQUIRED_MODULES = [
    "fastapi",
    "uvicorn",
    "psycopg",
    "psycopg_pool",
    "pydantic",
    "pydantic_settings",
    "jwt",
    "argon2",
    "httpx",
    "streamlit",
    "structlog",
    "pymupdf",
    "onnxruntime",
    "tokenizers",
    "numpy",
]


def _check_python() -> tuple[bool, str]:
    version = sys.version_info
    ok = version >= (3, 11) and version < (3, 12)
    return ok, f"Python {version.major}.{version.minor}.{version.micro}"


def _check_modules() -> tuple[bool, str]:
    missing = []
    for name in REQUIRED_MODULES:
        try:
            importlib.import_module(name)
        except ImportError:
            missing.append(name)
    if missing:
        return False, f"Missing modules: {', '.join(missing)}"
    return True, "All required modules import successfully"


def _check_config() -> tuple[bool, list[str]]:
    from app.config import ENV_FILE, get_settings

    notes: list[str] = []
    ok = True
    if not ENV_FILE.exists():
        return False, [f"Environment file not found: {ENV_FILE}"]
    try:
        settings = get_settings()
    except Exception as exc:  # noqa: BLE001
        return False, [f"Configuration error: {exc}"]

    notes.append(f"APP_ENV={settings.app_env}")
    notes.append(f"VISION_PROVIDER={settings.vision_provider}")
    for directory in (settings.model_dir, settings.data_dir, settings.log_dir):
        if directory.exists():
            notes.append(f"Directory OK: {directory}")
        else:
            ok = False
            notes.append(f"Missing directory: {directory}")
    return ok, notes


def _check_database() -> tuple[bool, str]:
    import psycopg

    from app.config import get_settings

    settings = get_settings()
    try:
        with psycopg.connect(settings.database_url, connect_timeout=5) as conn:
            row = conn.execute("SHOW server_version").fetchone()
            version = row[0] if row else "unknown"
            ext = conn.execute(
                "SELECT extversion FROM pg_extension WHERE extname = 'vector'"
            ).fetchone()
            if ext is None:
                return False, f"PostgreSQL {version} reachable, but pgvector is not installed"
            return True, f"PostgreSQL {version} reachable, pgvector {ext[0]}"
    except Exception as exc:  # noqa: BLE001
        return False, f"Database connection failed: {type(exc).__name__}: {exc}"


def main() -> int:
    results: list[tuple[str, bool, str]] = []

    ok, detail = _check_python()
    results.append(("Python 3.11", ok, detail))

    ok, detail = _check_modules()
    results.append(("Python dependencies", ok, detail))

    config_ok, notes = _check_config()
    results.append(("Configuration", config_ok, " | ".join(notes)))

    if config_ok:
        ok, detail = _check_database()
        results.append(("Database + pgvector", ok, detail))
    else:
        results.append(("Database + pgvector", False, "Skipped: configuration invalid"))

    print("FMM environment validation")
    print("=" * 60)
    for name, ok, detail in results:
        print(f"[{'OK' if ok else 'FAIL'}] {name}: {detail}")

    return 0 if all(ok for _, ok, _ in results) else 1


if __name__ == "__main__":
    raise SystemExit(main())
