from __future__ import annotations

from app.auth.rate_limit import SlidingWindowLimiter


async def test_allows_until_limit_then_blocks() -> None:
    limiter = SlidingWindowLimiter(max_attempts=2, window_seconds=60)
    assert (await limiter.check("k"))[0] is True
    await limiter.record_failure("k")
    await limiter.record_failure("k")
    allowed, retry_after = await limiter.check("k")
    assert allowed is False
    assert retry_after >= 1


async def test_reset_clears_failures() -> None:
    limiter = SlidingWindowLimiter(max_attempts=1, window_seconds=60)
    await limiter.record_failure("k")
    assert (await limiter.check("k"))[0] is False
    await limiter.reset("k")
    assert (await limiter.check("k"))[0] is True


async def test_keys_are_independent() -> None:
    limiter = SlidingWindowLimiter(max_attempts=1, window_seconds=60)
    await limiter.record_failure("a")
    assert (await limiter.check("a"))[0] is False
    assert (await limiter.check("b"))[0] is True
