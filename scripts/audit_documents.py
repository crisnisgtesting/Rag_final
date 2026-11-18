"""Audit PDFs in the inbox before ingestion.

Reports duplicates, page counts, digital-text coverage, image-heavy/scanned pages
and an estimate of selective vision calls. Read-only: writes nothing to the DB.

Usage:
    python scripts/audit_documents.py
    python scripts/audit_documents.py --json
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from app.config import get_settings  # noqa: E402
from app.ingestion.classifier import classify_page  # noqa: E402
from app.ingestion.pdf_extractor import PdfExtractionError, extract_pdf  # noqa: E402
from app.utils.hashing import sha256_file  # noqa: E402


def audit(inbox: Path) -> dict:
    settings = get_settings()
    files = sorted(inbox.glob("*.pdf"))
    hashes: dict[str, str] = {}
    documents = []
    for path in files:
        digest = sha256_file(path)
        if digest in hashes:
            documents.append(
                {
                    "file": path.name,
                    "duplicate_of": hashes[digest],
                    "sha256": digest,
                }
            )
            continue
        hashes[digest] = path.name
        try:
            extraction = extract_pdf(path, extract_tables=True)
        except PdfExtractionError as exc:
            documents.append({"file": path.name, "error": str(exc), "sha256": digest})
            continue

        text_chars = sum(page.char_count for page in extraction.pages)
        image_pages = sum(1 for page in extraction.pages if page.image_count)
        scanned_pages = sum(
            1 for page in extraction.pages if page.char_count == 0 and page.image_count
        )
        vision_pages = sum(
            1 for page in extraction.pages if classify_page(page, settings).needs_vision
        )
        documents.append(
            {
                "file": path.name,
                "sha256": digest,
                "size_mb": round(path.stat().st_size / (1024 * 1024), 2),
                "pages": extraction.page_count,
                "text_chars": text_chars,
                "text_pages": sum(1 for page in extraction.pages if page.char_count),
                "image_pages": image_pages,
                "scanned_pages": scanned_pages,
                "vision_pages_auto": vision_pages,
                "tables": sum(len(page.tables) for page in extraction.pages),
                "title": extraction.metadata.get("title"),
            }
        )

    unique = [doc for doc in documents if "duplicate_of" not in doc and "error" not in doc]
    return {
        "inbox": str(inbox),
        "files": len(files),
        "unique_documents": len(unique),
        "duplicates": len(files) - len(unique),
        "pages": sum(doc["pages"] for doc in unique),
        "text_pages": sum(doc["text_pages"] for doc in unique),
        "image_pages": sum(doc["image_pages"] for doc in unique),
        "scanned_pages": sum(doc["scanned_pages"] for doc in unique),
        "vision_pages_auto": sum(doc["vision_pages_auto"] for doc in unique),
        "documents": documents,
    }


def main(argv: list[str] | None = None) -> int:
    settings = get_settings()
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--inbox",
        type=Path,
        default=settings.data_dir / "documents",
        help="Directory containing PDFs (default: data/documents)",
    )
    parser.add_argument("--json", action="store_true", help="Print machine-readable JSON")
    args = parser.parse_args(argv)

    report = audit(args.inbox)
    if args.json:
        print(json.dumps(report, indent=2, default=str))
        return 0

    print(f"Inbox: {report['inbox']}")
    print(f"Files: {report['files']}  Unique: {report['unique_documents']}  "
          f"Duplicates: {report['duplicates']}")
    print(
        f"Pages: {report['pages']}  Text pages: {report['text_pages']}  "
        f"Image pages: {report['image_pages']}  Scanned: {report['scanned_pages']}  "
        f"Estimated vision pages: {report['vision_pages_auto']}"
    )
    print()
    for doc in report["documents"]:
        if "duplicate_of" in doc:
            print(f"SKIP duplicate  {doc['file']}  (same as {doc['duplicate_of']})")
        elif "error" in doc:
            print(f"ERROR          {doc['file']}  {doc['error']}")
        else:
            print(
                f"{doc['file']:<50} pages={doc['pages']:>3} "
                f"text={doc['text_pages']:>3} image={doc['image_pages']:>3} "
                f"scanned={doc['scanned_pages']:>3} vision={doc['vision_pages_auto']:>3} "
                f"tables={doc['tables']:>3}"
            )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
