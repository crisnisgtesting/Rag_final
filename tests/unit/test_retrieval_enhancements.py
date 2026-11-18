from __future__ import annotations

from app.retrieval.knowledge import query_tokens
from app.retrieval.reranker import DisabledReranker, RerankerError


def test_query_tokens_drops_stop_words_and_short_tokens() -> None:
    tokens = query_tokens("How do I update an attendance group for a depot?")
    assert "attendance" in tokens
    assert "group" in tokens
    assert "how" not in tokens
    assert "do" not in tokens
    assert len(tokens) == len(set(tokens))


def test_query_tokens_returns_empty_for_stop_word_only_query() -> None:
    assert query_tokens("what is it") == []


async def test_disabled_reranker_preserves_order() -> None:
    ranked = await DisabledReranker().rerank("query", ["a", "b", "c"])
    assert ranked == [(0, 0.0), (1, 0.0), (2, 0.0)]


async def test_disabled_reranker_handles_empty_documents() -> None:
    assert await DisabledReranker().rerank("query", []) == []


def test_reranker_error_is_explicit() -> None:
    assert issubclass(RerankerError, RuntimeError)
