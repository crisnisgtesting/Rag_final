from __future__ import annotations

import asyncio
import json
from typing import Any

import httpx
import pytest

from app.chat.provider import DatabricksChatProvider, build_chat_provider
from app.config import Settings
from app.llm.auth.oauth import clear_global_oauth_cache
from app.llm.base import LLMConfigurationError, LLMMessage, LLMRequestError, LLMResponseError
from app.llm.factory import build_llm_provider
from app.llm.providers.databricks import DatabricksLLMProvider


@pytest.fixture(autouse=True)
def _clear_oauth_cache() -> None:
    clear_global_oauth_cache()


def _settings(**overrides: Any) -> Settings:
    values: dict[str, Any] = {
        "app_env": "test",
        "jwt_secret": "test-secret-0123456789-0123456789",
        "chat_provider": "databricks",
        "vision_provider": "databricks",
        "databricks_host": "https://workspace.databricks.com/",
        "databricks_client_id": "client-id",
        "databricks_client_secret": "client-secret",
        "databricks_oauth_scope": "model-serving",
        "databricks_model_serving_endpoint": "databri-meta--instruct",
        "databricks_model_timeout_seconds": 5.0,
        "databricks_model_max_retries": 0,
        "databricks_model_supports_parameters": False,
    }
    values.update(overrides)
    return Settings(**values)


def _transport(handler) -> httpx.MockTransport:
    return httpx.MockTransport(handler)


def _default_handler(captured: dict[str, Any], response_body: dict[str, Any]):
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/oidc/v1/token"):
            return httpx.Response(
                200,
                json={"access_token": "access-token", "expires_in": 3600},
            )
        captured["url"] = str(request.url)
        captured["headers"] = dict(request.headers)
        captured["json"] = json.loads(request.content)
        return httpx.Response(200, json=response_body)

    return handler


def test_request_construction_uses_messages_only_by_default() -> None:
    captured: dict[str, Any] = {}
    body = {"choices": [{"message": {"content": "Hello back"}}]}
    client = httpx.AsyncClient(transport=_transport(_default_handler(captured, body)))
    settings = _settings()
    provider = DatabricksLLMProvider(settings, client=client)

    async def run() -> None:
        try:
            result = await provider.complete(
                [LLMMessage(role="user", content="Hello")],
                temperature=0.7,
                max_tokens=128,
            )
            assert result.content == "Hello back"
            assert result.provider == "databricks"
            assert result.model == "databri-meta--instruct"
        finally:
            await provider.close()
            await client.aclose()

    asyncio.run(run())
    assert captured["url"] == (
        "https://workspace.databricks.com/serving-endpoints/"
        "databri-meta--instruct/invocations"
    )
    assert captured["headers"]["authorization"] == "Bearer access-token"
    assert captured["json"] == {"messages": [{"role": "user", "content": "Hello"}]}


def test_generation_parameters_are_sent_only_when_supported() -> None:
    captured: dict[str, Any] = {}
    body = {"predictions": [{"text": "Pong"}]}
    client = httpx.AsyncClient(
        transport=_transport(_default_handler(captured, body))
    )
    settings = _settings(databricks_model_supports_parameters=True)
    provider = DatabricksLLMProvider(settings, client=client)

    async def run() -> None:
        try:
            result = await provider.complete(
                [LLMMessage(role="user", content="Ping")],
                temperature=0.2,
                max_tokens=64,
            )
            assert result.content == "Pong"
        finally:
            await provider.close()
            await client.aclose()

    asyncio.run(run())
    assert captured["json"]["temperature"] == 0.2
    assert captured["json"]["max_tokens"] == 64


def test_usage_is_normalized() -> None:
    body = {
        "choices": [{"message": {"content": "Answer"}}],
        "usage": {"prompt_tokens": 3, "completion_tokens": 4, "total_tokens": 7},
    }
    client = httpx.AsyncClient(transport=_transport(_default_handler({}, body)))
    provider = DatabricksLLMProvider(_settings(), client=client)

    async def run() -> None:
        try:
            result = await provider.complete(
                [LLMMessage(role="user", content="Question")],
                temperature=0.2,
                max_tokens=64,
            )
            assert result.usage == {
                "input_tokens": 3,
                "output_tokens": 4,
                "total_tokens": 7,
            }
        finally:
            await provider.close()
            await client.aclose()

    asyncio.run(run())


def test_unexpected_response_is_rejected() -> None:
    client = httpx.AsyncClient(transport=_transport(_default_handler({}, {"unknown": 1})))
    provider = DatabricksLLMProvider(_settings(), client=client)

    async def run() -> None:
        try:
            with pytest.raises(LLMResponseError):
                await provider.complete(
                    [LLMMessage(role="user", content="Question")],
                    temperature=0.2,
                    max_tokens=64,
                )
        finally:
            await provider.close()
            await client.aclose()

    asyncio.run(run())


def test_timeout_is_reported_as_llm_request_error() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/oidc/v1/token"):
            return httpx.Response(
                200, json={"access_token": "access-token", "expires_in": 3600}
            )
        raise httpx.TimeoutException("timed out")

    client = httpx.AsyncClient(transport=_transport(handler))
    provider = DatabricksLLMProvider(_settings(), client=client)

    async def run() -> None:
        try:
            with pytest.raises(LLMRequestError):
                await provider.complete(
                    [LLMMessage(role="user", content="Question")],
                    temperature=0.2,
                    max_tokens=64,
                )
        finally:
            await provider.close()
            await client.aclose()

    asyncio.run(run())


def test_http_error_is_reported_without_body() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/oidc/v1/token"):
            return httpx.Response(
                200, json={"access_token": "access-token", "expires_in": 3600}
            )
        return httpx.Response(400, text="secret body")

    client = httpx.AsyncClient(transport=_transport(handler))
    provider = DatabricksLLMProvider(_settings(), client=client)

    async def run() -> None:
        try:
            with pytest.raises(LLMRequestError) as exc_info:
                await provider.complete(
                    [LLMMessage(role="user", content="Question")],
                    temperature=0.2,
                    max_tokens=64,
                )
            assert exc_info.value.status_code == 400
            assert "secret body" not in str(exc_info.value)
        finally:
            await provider.close()
            await client.aclose()

    asyncio.run(run())


def test_factory_and_chat_adapter_default_to_databricks() -> None:
    settings = _settings()
    llm_provider = build_llm_provider(settings)
    assert isinstance(llm_provider, DatabricksLLMProvider)

    chat_provider = build_chat_provider(settings)
    assert isinstance(chat_provider, DatabricksChatProvider)
    assert chat_provider.model_name == "databri-meta--instruct"


def test_missing_oauth_config_is_rejected() -> None:
    settings = _settings(databricks_client_secret=None)
    with pytest.raises(LLMConfigurationError):
        build_llm_provider(settings)
