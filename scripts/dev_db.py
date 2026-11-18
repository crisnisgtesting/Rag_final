"""Start/stop a self-contained PostgreSQL 16 + pgvector instance for development.

This uses the PostgreSQL binaries bundled with the ``pgserver`` development
dependency. Production uses an authorized PostgreSQL 16 server configured via
DATABASE_URL; nothing here is used in production.
"""

from __future__ import annotations

import argparse
import os
import re
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_PGDATA = REPO_ROOT / ".pgdata"
DEFAULT_PORT = 55432
DEFAULT_DATABASE = "fmm_dev"

_IDENTIFIER_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


def _bin_dir() -> Path:
    try:
        from pgserver._commands import POSTGRES_BIN_PATH
    except ImportError as exc:  # pragma: no cover - dev convenience
        raise SystemExit(
            "pgserver is not installed. Run: pip install -r requirements-dev.txt"
        ) from exc
    return Path(POSTGRES_BIN_PATH)


def _exe(name: str) -> str:
    return f"{name}.exe" if os.name == "nt" else name


def _run(args: list[str]) -> int:
    result = subprocess.run(args, text=True)
    return result.returncode


def _pg_ctl(pgdata: Path, extra: list[str], *, quiet: bool = False) -> int:
    args = [str(_bin_dir() / _exe("pg_ctl")), "-D", str(pgdata), *extra]
    if quiet:
        # Detach the server's inherited handles so callers are not held open
        # by the long-lived postgres process (notably on Windows).
        result = subprocess.run(
            args,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        return result.returncode
    return _run(args)


def init(pgdata: Path) -> None:
    if (pgdata / "PG_VERSION").exists():
        print(f"Data directory already initialized: {pgdata}")
        return
    pgdata.mkdir(parents=True, exist_ok=True)
    code = _run(
        [
            str(_bin_dir() / _exe("initdb")),
            "-D",
            str(pgdata),
            "--auth=trust",
            "--auth-local=trust",
            "--encoding=UTF8",
            "-U",
            "postgres",
        ]
    )
    if code != 0:
        raise SystemExit("initdb failed")


def is_running(pgdata: Path) -> bool:
    result = subprocess.run(
        [str(_bin_dir() / _exe("pg_ctl")), "-D", str(pgdata), "status"],
        text=True,
        capture_output=True,
    )
    return result.returncode == 0


def start(pgdata: Path, port: int, database: str) -> None:
    init(pgdata)
    if not is_running(pgdata):
        log_file = pgdata / "postgres-server.log"
        code = _pg_ctl(
            pgdata,
            [
                "-w",
                "-o",
                f'-p {port} -h 127.0.0.1',
                "-l",
                str(log_file),
                "start",
            ],
            quiet=True,
        )
        if code != 0:
            raise SystemExit(f"postgres failed to start; see {log_file}")
    _ensure_database(port, database)
    print(f"PostgreSQL running on 127.0.0.1:{port} (database: {database})")
    print(f"DATABASE_URL=postgresql://postgres@127.0.0.1:{port}/{database}")


def stop(pgdata: Path) -> None:
    if not (pgdata / "PG_VERSION").exists():
        print("No development data directory; nothing to stop.")
        return
    _pg_ctl(pgdata, ["-m", "fast", "stop"])


def status(pgdata: Path) -> None:
    if not (pgdata / "PG_VERSION").exists():
        print("Development database is not initialized.")
        return
    _pg_ctl(pgdata, ["status"])


def reset(pgdata: Path) -> None:
    stop(pgdata)
    if pgdata.exists():
        import shutil

        shutil.rmtree(pgdata)
    print(f"Removed {pgdata}")


def _ensure_database(port: int, database: str) -> None:
    if not _IDENTIFIER_RE.match(database):
        raise SystemExit(f"Invalid database name: {database}")
    import psycopg

    with psycopg.connect(
        f"postgresql://postgres@127.0.0.1:{port}/postgres", autocommit=True
    ) as conn:
        exists = conn.execute(
            "SELECT 1 FROM pg_database WHERE datname = %s", (database,)
        ).fetchone()
        if not exists:
            conn.execute(f'CREATE DATABASE "{database}"')
            print(f"Created database {database}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=["start", "stop", "status", "reset", "url"])
    parser.add_argument(
        "--pgdata",
        type=Path,
        default=Path(os.environ.get("FMM_DEV_PGDATA", DEFAULT_PGDATA)),
    )
    parser.add_argument(
        "--port", type=int, default=int(os.environ.get("FMM_DEV_PGPORT", DEFAULT_PORT))
    )
    parser.add_argument(
        "--database", default=os.environ.get("FMM_DEV_PGDATABASE", DEFAULT_DATABASE)
    )
    parser.add_argument("--yes", action="store_true", help="Confirm destructive reset")
    args = parser.parse_args(argv)

    pgdata = args.pgdata.resolve()
    if args.command == "start":
        start(pgdata, args.port, args.database)
    elif args.command == "stop":
        stop(pgdata)
    elif args.command == "status":
        status(pgdata)
    elif args.command == "reset":
        if not args.yes:
            print("Refusing to delete data without --yes", file=sys.stderr)
            return 2
        reset(pgdata)
    elif args.command == "url":
        print(
            f"postgresql://postgres@127.0.0.1:{args.port}/{args.database}"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
