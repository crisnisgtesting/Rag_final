"""Download the Phase 2 embedding model into the local model directory."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from huggingface_hub import snapshot_download

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from app.config import get_settings  # noqa: E402


def main() -> None:
    parser = argparse.ArgumentParser(description="Download the BGE-M3 embedding model")
    parser.add_argument("--model", default=None, help="Override EMBEDDING_MODEL")
    parser.add_argument(
        "--backend",
        choices=("onnx", "pytorch"),
        default="onnx",
        help="Model files to download (default: onnx)",
    )
    args = parser.parse_args()

    settings = get_settings()
    model = args.model or settings.embedding_model
    settings.model_dir.mkdir(parents=True, exist_ok=True)
    print(f"Downloading {model} ({args.backend}) into {settings.model_dir}")
    if args.backend == "onnx":
        patterns = [
            "onnx/model.onnx",
            "onnx/model.onnx_data",
            "onnx/config.json",
            "config.json",
            "tokenizer.json",
            "tokenizer_config.json",
            "special_tokens_map.json",
            "sentencepiece.bpe.model",
        ]
        path = snapshot_download(
            repo_id=model,
            cache_dir=str(settings.model_dir),
            allow_patterns=patterns,
            max_workers=2,
        )
    else:
        path = snapshot_download(
            repo_id=model,
            cache_dir=str(settings.model_dir),
            ignore_patterns=["onnx/*", "imgs/*"],
            max_workers=2,
        )
    print(f"Model available at {path}")


if __name__ == "__main__":
    main()
