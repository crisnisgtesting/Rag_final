"""Re-render stored page images at the configured DPI.

Page images are produced during vision analysis. When the render DPI changes,
this script regenerates the images for an approved version without repeating
the provider calls, so the stored page images match the current setting.

Examples:
    python scripts/rerender_pages.py --version <uuid>
    python scripts/rerender_pages.py --all --dpi 200
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path
from uuid import UUID

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from app.config import get_settings  # noqa: E402
from app.db.pool import close_pool, get_pool, init_pool  # noqa: E402
from app.ingestion.pdf_extractor import image_hash, render_page_png  # noqa: E402
from app.ingestion.registry import resolve_storage_path  # noqa: E402
from app.logging_conf import configure_logging  # noqa: E402

if sys.platform == "win32":
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())


async def rerender(args: argparse.Namespace) -> int:
    settings = get_settings()
    configure_logging(settings.log_level, json_logs=False)
    dpi = args.dpi or settings.ingestion_render_dpi
    await init_pool(settings)
    results: list[dict[str, object]] = []
    try:
        async with get_pool().connection() as conn:
            if args.version:
                versions = [
                    {"id": args.version, "filename": args.version},
                ]
            else:
                versions = await (
                    await conn.execute(
                        """
                        SELECT dv.id, d.filename
                        FROM document_versions dv
                        JOIN documents d ON d.id = dv.document_id
                        WHERE d.document_type = 'pdf'
                          AND dv.status = 'ready'
                        ORDER BY d.filename
                        """
                    )
                ).fetchall()

            for version in versions:
                version_id = UUID(str(version["id"]))
                context = await (
                    await conn.execute(
                        """
                        SELECT dv.id, dv.document_id, dv.storage_path, dv.version_number,
                               d.filename, d.title
                        FROM document_versions dv
                        JOIN documents d ON d.id = dv.document_id
                        WHERE dv.id = %s
                        """,
                        (str(version_id),),
                    )
                ).fetchone()
                if context is None:
                    print(f"Version {version_id} was not found", file=sys.stderr)
                    continue

                source_path = resolve_storage_path(settings, context["storage_path"])
                if not source_path.is_file():
                    print(
                        f"Source file missing for {context['filename']}",
                        file=sys.stderr,
                    )
                    continue

                pages = await (
                    await conn.execute(
                        """
                        SELECT id, physical_page_number, page_image_path
                        FROM document_pages
                        WHERE document_version_id = %s AND page_image_path IS NOT NULL
                        ORDER BY physical_page_number
                        """,
                        (str(version_id),),
                    )
                ).fetchall()

                render_dir = settings.data_dir / "rendered_pages" / str(version_id)
                render_dir.mkdir(parents=True, exist_ok=True)
                rendered = 0
                for page in pages:
                    image = await asyncio.to_thread(
                        render_page_png,
                        source_path,
                        int(page["physical_page_number"]),
                        dpi=dpi,
                    )
                    target = render_dir / f"page-{int(page['physical_page_number']):04d}.png"
                    await asyncio.to_thread(target.write_bytes, image)
                    await conn.execute(
                        "UPDATE document_pages SET image_hash = %s, updated_at = now() "
                        "WHERE id = %s",
                        (image_hash(image), str(page["id"])),
                    )
                    rendered += 1
                await conn.commit()

                results.append(
                    {
                        "version_id": str(version_id),
                        "filename": context["filename"],
                        "dpi": dpi,
                        "pages": rendered,
                    }
                )
                print(json.dumps(results[-1], default=str))
    finally:
        await close_pool()
    print(json.dumps({"versions": len(results), "results": results}, default=str))
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--version", default=None, help="Document version UUID")
    parser.add_argument("--all", action="store_true", help="Every ready PDF version")
    parser.add_argument("--dpi", type=int, default=None, help="Override the render DPI")
    args = parser.parse_args()
    if not args.version and not args.all:
        parser.error("Pass --version <uuid> or --all")
    return asyncio.run(rerender(args))


if __name__ == "__main__":
    raise SystemExit(main())
