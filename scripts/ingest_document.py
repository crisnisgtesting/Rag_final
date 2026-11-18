"""Register and ingest a PDF, DOCX or Markdown document.

Examples:
    python scripts/ingest_document.py --file "data/documents/IRWMS.pdf"
    python scripts/ingest_document.py --file "data/documents/guide.md" --approve
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from app.config import get_settings  # noqa: E402
from app.db.pool import close_pool, get_pool, init_pool  # noqa: E402
from app.ingestion import registry  # noqa: E402
from app.ingestion.job_runner import run_ingestion_job  # noqa: E402
from app.services.settings_service import get_effective_vision_provider  # noqa: E402
from app.vision.factory import (  # noqa: E402
    ALLOWED_PROVIDERS,
    normalize_provider_name,
)

if sys.platform == "win32":  # psycopg async requires the selector loop on Windows
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())


def _model_for(provider: str, settings: Any) -> str:
    if provider == "qwen":
        return (settings.qwen_vision_model or "").strip()
    if provider == "gemini":
        return (settings.gemini_vision_model or "").strip()
    if provider == "databricks":
        return (settings.databricks_vision_endpoint or "").strip()
    return ""


async def ingest(args: argparse.Namespace) -> int:
    settings = get_settings()
    path = Path(args.file).resolve()
    if not path.is_file():
        print(f"File not found: {path}", file=sys.stderr)
        return 2

    pool = await init_pool(settings)
    async with pool.connection() as conn:
        registered = await registry.register_document(conn, settings, path)
        await conn.commit()
        version_id = registered["version_id"]
        print(json.dumps({"registration": registered}, indent=2, default=str))
        if registered["status"] == "duplicate":
            print("Document content already exists; skipping ingestion.")
            await close_pool()
            return 0

        document_type = str(registered.get("document_type") or "pdf")
        if document_type != "pdf":
            provider, model = "structured", f"{document_type}-extractor"
        elif args.provider:
            provider = normalize_provider_name(args.provider)
            if provider not in ALLOWED_PROVIDERS:
                await close_pool()
                print(
                    f"Unsupported provider. Allowed: {', '.join(ALLOWED_PROVIDERS)}",
                    file=sys.stderr,
                )
                return 2
        else:
            provider, _source = await get_effective_vision_provider(conn, settings)
        if document_type == "pdf":
            model = (args.model or _model_for(provider, settings)).strip()
        if not model:
            await close_pool()
            print(
                f"No model is configured for vision provider '{provider}'",
                file=sys.stderr,
            )
            return 2

        job = await registry.create_job(
            conn,
            version_id,
            provider=provider,
            model=model,
            config={
                "vision_mode": args.vision_mode,
                "requested_provider": provider,
                "requested_model": model,
            },
        )
        await conn.commit()

    print(
        json.dumps(
            {"job_id": str(job["id"]), "provider": provider, "model": model},
            indent=2,
        )
    )
    await run_ingestion_job(job["id"], settings)

    async with get_pool().connection() as conn:
        detail = await registry.job_detail(conn, job["id"])
        if detail:
            print(json.dumps({"job": detail}, indent=2, default=str))
        if args.approve and detail and detail["status"] == "completed":
            approved = await registry.approve_version(conn, version_id)
            await conn.commit()
            print(json.dumps({"approval": approved}, indent=2, default=str))
    await close_pool()
    return 0 if detail and detail["status"] == "completed" else 1


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--file", required=True, help="Path to a PDF, DOCX or Markdown file")
    parser.add_argument("--provider", default=None, help="Vision provider override")
    parser.add_argument("--model", default=None, help="Vision model override")
    parser.add_argument(
        "--vision-mode",
        choices=("auto", "all", "none"),
        default="auto",
        help="Vision page selection mode (default: auto)",
    )
    parser.add_argument(
        "--approve",
        action="store_true",
        help="Approve the version after a successful run",
    )
    return asyncio.run(ingest(parser.parse_args()))


if __name__ == "__main__":
    raise SystemExit(main())
