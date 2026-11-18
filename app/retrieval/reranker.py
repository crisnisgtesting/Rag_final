"""Optional cross-encoder reranking for retrieval.

The reranker is opt-in (`RETRIEVAL_RERANKER_ENABLED=1`). When it is disabled,
unavailable, or fails, retrieval keeps the fused hybrid ranking unchanged. No
provider fallback is performed automatically.
"""

from __future__ import annotations

import asyncio
from typing import Any, Protocol

from app.config import Settings
from app.logging_conf import get_logger

logger = get_logger(__name__)


class RerankerError(RuntimeError):
    pass


class Reranker(Protocol):
    model_name: str

    async def rerank(
        self, query: str, documents: list[str], *, top_k: int | None = None
    ) -> list[tuple[int, float]]: ...


class OnnxCrossEncoderReranker:
    """Cross-encoder reranker executed through onnxruntime.

    The model is expected to expose an `onnx/model.onnx` graph with `input_ids`,
    `attention_mask` and `token_type_ids` inputs and a single `logits` output.
    """

    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.model_name = settings.retrieval_reranker_model
        self._session: Any = None
        self._tokenizer: Any = None

    def _model_paths(self) -> tuple[Any, Any]:
        import onnxruntime as ort
        from tokenizers import Tokenizer

        root = (
            self.settings.model_dir
            / f"models--{self.model_name.replace('/', '--')}"
            / "snapshots"
        )
        snapshots = sorted(root.glob("*")) if root.exists() else []
        if not snapshots:
            raise RerankerError(
                f"Reranker model '{self.model_name}' is not available under "
                f"{root}. Download it before enabling the reranker."
            )
        model_dir = snapshots[-1]
        model_path = model_dir / "onnx" / "model.onnx"
        tokenizer_path = model_dir / "tokenizer.json"
        if not model_path.is_file() or not tokenizer_path.is_file():
            raise RerankerError(
                f"Reranker files are incomplete under {model_dir}. "
                "Expected onnx/model.onnx and tokenizer.json."
            )
        options = ort.SessionOptions()
        options.graph_optimization_level = ort.GraphOptimizationLevel.ORT_ENABLE_ALL
        session = ort.InferenceSession(
            str(model_path),
            sess_options=options,
            providers=["CPUExecutionProvider"],
        )
        tokenizer = Tokenizer.from_file(str(tokenizer_path))
        tokenizer.enable_truncation(
            max_length=self.settings.retrieval_reranker_max_length
        )
        return session, tokenizer

    def _load(self) -> tuple[Any, Any]:
        if self._session is None or self._tokenizer is None:
            self._session, self._tokenizer = self._model_paths()
            logger.info("reranker_loaded", model=self.model_name)
        return self._session, self._tokenizer

    def _rerank_sync(
        self, query: str, documents: list[str], top_k: int | None
    ) -> list[tuple[int, float]]:
        import numpy as np

        session, tokenizer = self._load()
        pairs = tokenizer.encode_batch([(query, document) for document in documents])
        if not pairs:
            return []
        max_length = max(len(pair.ids) for pair in pairs)
        input_ids = np.full((len(pairs), max_length), 1, dtype=np.int64)
        attention_mask = np.zeros((len(pairs), max_length), dtype=np.int64)
        token_type_ids = np.zeros((len(pairs), max_length), dtype=np.int64)
        for row, pair in enumerate(pairs):
            length = len(pair.ids)
            input_ids[row, :length] = pair.ids
            attention_mask[row, :length] = pair.attention_mask
            token_type_ids[row, :length] = pair.type_ids
        outputs = session.run(
            None,
            {
                "input_ids": input_ids,
                "attention_mask": attention_mask,
                "token_type_ids": token_type_ids,
            },
        )
        logits = np.asarray(outputs[0], dtype=np.float32).reshape(-1)
        scores = logits.tolist()
        ranked = sorted(enumerate(scores), key=lambda item: item[1], reverse=True)
        return ranked[: top_k] if top_k else ranked

    async def rerank(
        self, query: str, documents: list[str], *, top_k: int | None = None
    ) -> list[tuple[int, float]]:
        if not documents:
            return []
        try:
            return await asyncio.to_thread(self._rerank_sync, query, documents, top_k)
        except RerankerError:
            raise
        except Exception as exc:  # noqa: BLE001
            raise RerankerError(
                f"Reranking failed: {type(exc).__name__}: {exc}"
            ) from exc


class DisabledReranker:
    model_name = "disabled"

    async def rerank(
        self, query: str, documents: list[str], *, top_k: int | None = None
    ) -> list[tuple[int, float]]:
        return [(index, 0.0) for index in range(len(documents))]


_reranker: Reranker | None = None


def get_reranker(settings: Settings) -> Reranker:
    global _reranker
    if _reranker is None:
        if settings.retrieval_reranker_enabled:
            _reranker = OnnxCrossEncoderReranker(settings)
        else:
            _reranker = DisabledReranker()
    return _reranker


def set_reranker(reranker: Reranker | None) -> None:
    """Test hook for deterministic reranking."""
    global _reranker
    _reranker = reranker
