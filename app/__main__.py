"""Run the API with an event loop compatible with psycopg on Windows.

Usage:
    python -m app
"""

from __future__ import annotations

import asyncio
import sys

if sys.platform == "win32":
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

import uvicorn  # noqa: E402

from app.config import get_settings  # noqa: E402


def main() -> None:
    settings = get_settings()
    uvicorn.run(
        "app.main:app",
        host=settings.api_host,
        port=settings.api_port,
        proxy_headers=True,
        forwarded_allow_ips="127.0.0.1",
        access_log=False,
        log_config=None,
    )


if __name__ == "__main__":
    main()
