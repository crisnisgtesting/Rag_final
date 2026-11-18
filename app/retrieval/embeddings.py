"""BGE-M3 dense embedding service backed by ONNX Runtime.

PyTorch is blocked by the Application Control policy on the development machine,
so this module uses the official BGE-M3 ONNX export instead of
sentence-transformers. The output is the same 1024-dimensional dense embedding.
"""

from __future__ import annotations

import asyncio
import os
import threading
from collections.abc import Sequence
from pathlib import Path
from typing import Any, Protocol

import numpy as np

from app.config import Settings
from app.logging_conf import get_logger

logger = get_logger(__name__)


class Embedder(Protocol):
    model_name: str
    dim: int

    async def encode_documents(self, texts: Sequence[str]) -> list[list[float]]: ...

    async def encode_query(self, text: str) -> list[float]: ...


class EmbeddingError(RuntimeError):
    pass


class OnnxBgeM3Embedder:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.model_name = settings.embedding_model
        self.dim = settings.embedding_dim
        self._session: Any = None
        self._tokenizer: Any = None
        self._pad_id = 1
        self._lock = threading.Lock()

    def _model_dir(self) -> Path:
        if self.settings.hf_hub_offline:
            os.environ.setdefault("HF_HUB_OFFLINE", "1")
        try:
            from huggingface_hub import snapshot_download

            return Path(
                snapshot_download(
                    repo_id=self.model_name,
                    cache_dir=str(self.settings.model_dir),
                    local_files_only=True,
                )
            )
        except Exception:  # noqa: BLE001 - fall back to a direct snapshot scan
            snapshots = (
                self.settings.model_dir
                / f"models--{self.model_name.replace('/', '--')}"
                / "snapshots"
            )
            candidates = sorted(snapshots.glob("*")) if snapshots.exists() else []
            if not candidates:
                raise EmbeddingError(
                    f"Embedding model '{self.model_name}' is not downloaded. "
                    "Run: python scripts/download_models.py --backend onnx"
                ) from None
            return candidates[-1]

    def _load(self) -> tuple[Any, Any]:
        if self._session is not None and self._tokenizer is not None:
            return self._session, self._tokenizer
        with self._lock:
            if self._session is not None and self._tokenizer is not None:
                return self._session, self._tokenizer
            try:
                import onnxruntime as ort
                from tokenizers import Tokenizer
            except ImportError as exc:  # pragma: no cover - dependency error path
                raise EmbeddingError(
                    "onnxruntime/tokenizers is not installed. "
                    "Run: pip install -r requirements-ml.txt"
                ) from exc

            model_dir = self._model_dir()
            model_path = model_dir / "onnx" / "model.onnx"
            tokenizer_path = model_dir / "tokenizer.json"
            if not model_path.is_file() or not tokenizer_path.is_file():
                raise EmbeddingError(
                    f"BGE-M3 ONNX files are incomplete under {model_dir}. "
                    "Run: python scripts/download_models.py --backend onnx"
                )
            logger.info(
                "embedding_model_loading",
                model=self.model_name,
                backend="onnx",
                path=str(model_path),
            )
            options = ort.SessionOptions()
            options.graph_optimization_level = ort.GraphOptimizationLevel.ORT_ENABLE_ALL
            session = ort.InferenceSession(
                str(model_path),
                sess_options=options,
                providers=["CPUExecutionProvider"],
            )
            tokenizer = Tokenizer.from_file(str(tokenizer_path))
            tokenizer.enable_truncation(max_length=self.settings.embedding_max_seq_length)
            pad_token = tokenizer.token_to_id("<pad>")
            self._pad_id = int(pad_token) if pad_token is not None else 1
            self._session = session
            self._tokenizer = tokenizer
            logger.info("embedding_model_loaded", model=self.model_name, dim=self.dim)
            return session, tokenizer

    def _encode_sync(self, texts: Sequence[str]) -> list[list[float]]:
        session, tokenizer = self._load()
        encodings = tokenizer.encode_batch(list(texts))
        if not encodings:
            return []
        max_length = max(len(encoding.ids) for encoding in encodings)
        input_ids = np.full(
            (len(encodings), max_length), self._pad_id, dtype=np.int64
        )
        attention_mask = np.zeros((len(encodings), max_length), dtype=np.int64)
        for row, encoding in enumerate(encodings):
            length = len(encoding.ids)
            input_ids[row, :length] = encoding.ids
            attention_mask[row, :length] = encoding.attention_mask
        outputs = session.run(
            ["sentence_embedding"],
            {"input_ids": input_ids, "attention_mask": attention_mask},
        )
        vectors = np.asarray(outputs[0], dtype=np.float32)
        norms = np.linalg.norm(vectors, axis=1, keepdims=True)
        vectors = vectors / np.maximum(norms, 1e-12)
        return vectors.tolist()

    async def encode_documents(self, texts: Sequence[str]) -> list[list[float]]:
        if not texts:
            return []
        try:
            return await asyncio.to_thread(self._encode_sync, list(texts))
        except EmbeddingError:
            raise
        except Exception as exc:  # noqa: BLE001
            raise EmbeddingError(f"Embedding failed: {type(exc).__name__}") from exc

    async def encode_query(self, text: str) -> list[float]:
        vectors = await self.encode_documents([text])
        return vectors[0]


_embedder: Embedder | None = None


def get_embedder(settings: Settings) -> Embedder:
    global _embedder
    if _embedder is None:
        _embedder = OnnxBgeM3Embedder(settings)
    return _embedder


def set_embedder(embedder: Embedder | None) -> None:
    """Test hook for deterministic embeddings."""
    global _embedder
    _embedder = embedder
