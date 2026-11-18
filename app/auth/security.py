"""Password hashing and JWT helpers."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

import jwt
from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError, VerifyMismatchError

_password_hasher = PasswordHasher()


def hash_password(password: str) -> str:
    return _password_hasher.hash(password)


def verify_password(password: str, password_hash: str) -> bool:
    try:
        return _password_hasher.verify(password_hash, password)
    except (VerifyMismatchError, VerificationError, InvalidHashError):
        return False


def create_access_token(
    *,
    subject: str,
    roles: list[str],
    secret: str,
    algorithm: str,
    expires_minutes: int,
    extra: dict[str, Any] | None = None,
) -> tuple[str, int]:
    now = datetime.now(UTC)
    expire = now + timedelta(minutes=expires_minutes)
    payload: dict[str, Any] = {
        "sub": subject,
        "roles": roles,
        "iat": int(now.timestamp()),
        "exp": int(expire.timestamp()),
        "type": "access",
    }
    if extra:
        payload.update(extra)
    token = jwt.encode(payload, secret, algorithm=algorithm)
    return token, int((expire - now).total_seconds())


def decode_access_token(token: str, *, secret: str, algorithm: str) -> dict[str, Any]:
    return jwt.decode(token, secret, algorithms=[algorithm], options={"require": ["sub", "exp"]})
