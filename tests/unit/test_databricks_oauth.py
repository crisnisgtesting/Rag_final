from __future__ import annotations

import asyncio
from urllib.parse import parse_qs

import httpx
import pytest

from app.llm.auth.oauth import (
    DatabricksOAuthTokenProvider,
    OAuthTokenCache,
    OAuthTokenError,
)


def _provider(
    handler,
    *,
    scope: str = "model-serving",
    skew: float = 60.0,
    cache: OAuthTokenCache | None = None,
):
    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    provider = DatabricksOAuthTokenProvider(
        host="https://workspace.databricks.com/",
        client_id="client-id",
        client_secret="client-secret",
        scope=scope,
        refresh_skew_seconds=skew,
        client=client,
        cache=cache if cache is not None else OAuthTokenCache(),
    )
    return provider, client


def test_token_acquisition_uses_client_credentials() -> None:
    seen: dict[str, str] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        body = parse_qs(request.content.decode())
        seen.update({key: values[0] for key, values in body.items()})
        return httpx.Response(
            200,
            json={"access_token": "token-value", "expires_in": 3600},
        )

    async def run() -> None:
        provider, client = _provider(handler)
        try:
            token = await provider.get_token()
            assert token == "token-value"
        finally:
            await provider.close()
            await client.aclose()

    asyncio.run(run())
    assert seen["grant_type"] == "client_credentials"
    assert seen["scope"] == "model-serving"
    assert seen["client_id"] == "client-id"
    assert seen["client_secret"] == "client-secret"


def test_token_caching_avoids_repeated_requests() -> None:
    calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(200, json={"access_token": "cached", "expires_in": 3600})

    async def run() -> None:
        provider, client = _provider(handler)
        try:
            first = await provider.get_token()
            second = await provider.get_token()
            assert first == second == "cached"
        finally:
            await provider.close()
            await client.aclose()

    asyncio.run(run())
    assert calls == 1


def test_token_refresh_can_be_forced() -> None:
    calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(
            200,
            json={"access_token": f"token-{calls}", "expires_in": 3600},
        )

    async def run() -> None:
        provider, client = _provider(handler)
        try:
            assert await provider.get_token() == "token-1"
            assert await provider.get_token(force_refresh=True) == "token-2"
        finally:
            await provider.close()
            await client.aclose()

    asyncio.run(run())
    assert calls == 2


def test_token_cache_is_shared_between_provider_instances() -> None:
    calls = 0
    cache = OAuthTokenCache()

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(200, json={"access_token": "shared", "expires_in": 3600})

    async def run() -> None:
        first, first_client = _provider(handler, cache=cache)
        second, second_client = _provider(handler, cache=cache)
        try:
            assert await first.get_token() == "shared"
            assert await second.get_token() == "shared"
        finally:
            await first.close()
            await second.close()
            await first_client.aclose()
            await second_client.aclose()

    asyncio.run(run())
    assert calls == 1


def test_authentication_failure_is_sanitized() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(401, text="client_secret=bad token=secret")

    async def run() -> None:
        provider, client = _provider(handler)
        try:
            with pytest.raises(OAuthTokenError) as exc_info:
                await provider.get_token()
            message = str(exc_info.value)
            assert "HTTP 401" in message
            assert "client_secret=bad" not in message
            assert "token=secret" not in message
        finally:
            await provider.close()
            await client.aclose()

    asyncio.run(run())
