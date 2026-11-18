"""Rebuild Phase 3 knowledge from existing page analyses.

Examples:
    python scripts/build_knowledge.py
    python scripts/build_knowledge.py --approve
    python scripts/build_knowledge.py --version <uuid>
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from app.config import get_settings  # noqa: E402
from app.db.pool import close_pool, get_pool, init_pool  # noqa: E402
from app.knowledge.extractor import rebuild_version_knowledge  # noqa: E402
from app.knowledge.service import approve_version_knowledge  # noqa: E402

if sys.platform == "win32":
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())


async def build(args: argparse.Namespace) -> int:
    settings = get_settings()
    await init_pool(settings)
    try:
        async with get_pool().connection() as conn:
            if args.version:
                versions = [{"id": args.version}]
            else:
                versions = await (
                    await conn.execute(
                        """
                        SELECT id FROM document_versions
                        WHERE status = 'ready'
                        ORDER BY uploaded_at
                        """
                    )
                ).fetchall()
            results = []
            for version in versions:
                stats = await rebuild_version_knowledge(
                    conn,
                    version["id"],
                    mark_approved=args.approve,
                )
                await conn.commit()
                if args.approve:
                    await approve_version_knowledge(conn, version["id"])
                    await conn.commit()
                results.append({"version_id": str(version["id"]), "stats": stats})
                print(json.dumps(results[-1], default=str))
            print(json.dumps({"versions": len(results), "results": results}, default=str))
    finally:
        await close_pool()
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--version", default=None, help="Rebuild one document version UUID")
    parser.add_argument(
        "--approve",
        action="store_true",
        help="Approve rebuilt knowledge instead of leaving it proposed",
    )
    return asyncio.run(build(parser.parse_args()))


if __name__ == "__main__":
    raise SystemExit(main())
