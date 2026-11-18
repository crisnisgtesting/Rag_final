"""Create the first administrator (or any) user.

Usage:
    python scripts/create_admin.py --username admin
    python scripts/create_admin.py --username admin --password '...'
"""

from __future__ import annotations

import argparse
import asyncio
import getpass
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

import psycopg  # noqa: E402
from psycopg.rows import dict_row  # noqa: E402

from app.config import get_settings  # noqa: E402
from app.logging_conf import configure_logging  # noqa: E402
from app.services.auth_service import UserServiceError, create_user  # noqa: E402


async def _create(args: argparse.Namespace) -> int:
    settings = get_settings()
    configure_logging(settings.log_level, json_logs=False)
    password = args.password
    if not password:
        password = getpass.getpass("Password: ")
        confirm = getpass.getpass("Confirm password: ")
        if password != confirm:
            print("Passwords do not match.", file=sys.stderr)
            return 1

    async with await psycopg.AsyncConnection.connect(
        settings.database_url, row_factory=dict_row
    ) as conn:
        try:
            user_id = await create_user(
                conn,
                username=args.username,
                password=password,
                roles=args.role,
                email=args.email,
                full_name=args.full_name,
            )
        except UserServiceError as exc:
            print(f"Could not create user: {exc}", file=sys.stderr)
            return 1
    print(f"Created user '{args.username}' with roles {', '.join(args.role)} (id={user_id})")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--username", required=True)
    parser.add_argument("--password", default=None)
    parser.add_argument("--email", default=None)
    parser.add_argument("--full-name", default=None)
    parser.add_argument(
        "--role", action="append", default=None, help="Repeatable; defaults to admin"
    )
    args = parser.parse_args(argv)
    if args.role is None:
        args.role = ["admin"]

    if sys.platform == "win32":
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
    return asyncio.run(_create(args))


if __name__ == "__main__":
    raise SystemExit(main())
