"""Databricks Service Principal OAuth token provider."""

from __future__ import annotations

import asyncio
import hashlib
import threading
import time
from dataclasses import dataclass, field

import httpx

from app.llm.base import LLMConfigurationError, LLMError
from app.logging_conf import get_logger

logger = get_logger(__name__)


class OAuthTokenError(LLMError):
    def __init__(self, message: str, *, scope: str | None = None) -> None:
        super().__init__(message)
        self.scope = scope


@dataclass(frozen=True)
class OAuthToken:
    value: str
    expires_at: float


@dataclass
class OAuthTokenCache:
    _tokens: dict[str, OAuthToken] = field(default_factory=dict, repr=False)
    _state_lock: threading.Lock = field(default_factory=threading.Lock, repr=False)
    _refresh_locks: dict[str, asyncio.Lock] = field(default_factory=dict, repr=False)

    def valid_token(self, key: str) -> OAuthToken | None:
        with self._state_lock:
            token = self._tokens.get(key)
        if token is None:
            return None
        if token.expires_at <= time.monotonic() + 1:
            return None
        return token

    def refresh_lock(self, key: str) -> asyncio.Lock:
        with self._state_lock:
            refresh_lock = self._refresh_locks.get(key)
            if refresh_lock is None:
                refresh_lock = asyncio.Lock()
                self._refresh_locks[key] = refresh_lock
            return refresh_lock

    def set_token(self, key: str, token: OAuthToken) -> None:
        with self._state_lock:
            self._tokens[key] = token

    def clear(self) -> None:
        with self._state_lock:
            self._tokens.clear()
            self._refresh_locks.clear()


_GLOBAL_TOKEN_CACHE = OAuthTokenCache()


def clear_global_oauth_cache() -> None:
    _GLOBAL_TOKEN_CACHE.clear()


class DatabricksOAuthTokenProvider:
    def __init__(
        self,
        *,
        host: str,
        client_id: str,
        client_secret: str,
        scope: str = "model-serving",
        timeout_seconds: float = 30.0,
        refresh_skew_seconds: float = 60.0,
        client: httpx.AsyncClient | None = None,
        cache: OAuthTokenCache | None = None,
    ) -> None:
        clean_host = (host or "").strip().rstrip("/")
        clean_client_id = (client_id or "").strip()
        clean_client_secret = client_secret or ""
        clean_scope = (scope or "").strip()
        missing: list[str] = []
        if not clean_host:
            missing.append("DATABRICKS_HOST")
        if not clean_client_id:
            missing.append("DATABRICKS_CLIENT_ID")
        if not clean_client_secret:
            missing.append("DATABRICKS_CLIENT_SECRET")
        if not clean_scope:
            missing.append("DATABRICKS_OAUTH_SCOPE")
        if missing:
            raise LLMConfigurationError(
                "Databricks OAuth is not configured: missing " + ", ".join(missing)
            )

        self._host = clean_host
        self._client_id = clean_client_id
        self._client_secret = clean_client_secret
        self._scope = clean_scope
        self._timeout_seconds = timeout_seconds
        self._refresh_skew_seconds = refresh_skew_seconds
        self._token_url = f"{clean_host}/oidc/v1/token"
        self._client = client
        self._owns_client = client is None
        self._cache = cache if cache is not None else _GLOBAL_TOKEN_CACHE
        secret_fingerprint = hashlib.sha256(clean_client_secret.encode()).hexdigest()[:12]
        self._cache_key = (
            f"{clean_host}|{clean_client_id}|{self._scope}|{secret_fingerprint}"
        )

    @property
    def scope(self) -> str:
        return self._scope

    async def get_token(self, *, force_refresh: bool = False) -> str:
        if not force_refresh:
            cached = self._cache.valid_token(self._cache_key)
            if cached is not None:
                return cached.value

        refresh_lock = self._cache.refresh_lock(self._cache_key)
        async with refresh_lock:
            if not force_refresh:
                cached = self._cache.valid_token(self._cache_key)
                if cached is not None:
                    return cached.value
            token = await self._request_token()
            self._cache.set_token(self._cache_key, token)
            return token.value

    async def authorization_header(self, *, force_refresh: bool = False) -> str:
        return f"Bearer {await self.get_token(force_refresh=force_refresh)}"

    async def close(self) -> None:
        if self._owns_client and self._client is not None:
            await self._client.aclose()
            self._client = None

    async def _request_token(self) -> OAuthToken:
        payload = {
            "grant_type": "client_credentials",
            "scope": self._scope,
            "client_id": self._client_id,
            "client_secret": self._client_secret,
        }
        headers = {"Content-Type": "application/x-www-form-urlencoded"}
        try:
            if self._client is not None:
                response = await self._client.post(
                    self._token_url,
                    data=payload,
                    headers=headers,
                    timeout=self._timeout_seconds,
                )
            else:
                async with httpx.AsyncClient(
                    timeout=self._timeout_seconds, trust_env=True
                ) as client:
                    response = await client.post(
                        self._token_url,
                        data=payload,
                        headers=headers,
                    )
        except httpx.TimeoutException as exc:
            raise OAuthTokenError(
                "Databricks OAuth token request timed out", scope=self._scope
            ) from exc
        except httpx.TransportError as exc:
            raise OAuthTokenError(
                "Databricks OAuth token request failed: transport error",
                scope=self._scope,
            ) from exc

        if response.status_code >= 400:
            raise OAuthTokenError(
                f"Databricks OAuth token request failed with HTTP {response.status_code}",
                scope=self._scope,
            )

        try:
            body = response.json()
        except ValueError as exc:
            raise OAuthTokenError(
                "Databricks OAuth token response was not valid JSON",
                scope=self._scope,
            ) from exc

        access_token = body.get("access_token")
        expires_in = body.get("expires_in")
        if not isinstance(access_token, str) or not access_token.strip():
            raise OAuthTokenError(
                "Databricks OAuth token response is missing access_token",
                scope=self._scope,
            )
        try:
            lifetime = float(expires_in)
        except (TypeError, ValueError) as exc:
            raise OAuthTokenError(
                "Databricks OAuth token response is missing expires_in",
                scope=self._scope,
            ) from exc

        ttl = max(1.0, lifetime - self._refresh_skew_seconds)
        logger.info(
            "databricks_oauth_token_acquired",
            scope=self._scope,
            expires_in_seconds=int(lifetime),
        )
        return OAuthToken(value=access_token, expires_at=time.monotonic() + ttl)


__all__ = [
    "DatabricksOAuthTokenProvider",
    "OAuthToken",
    "OAuthTokenCache",
    "OAuthTokenError",
    "clear_global_oauth_cache",
]
