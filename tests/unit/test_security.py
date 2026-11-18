from __future__ import annotations

import jwt
import pytest

from app.auth.security import (
    create_access_token,
    decode_access_token,
    hash_password,
    verify_password,
)


def test_password_hash_roundtrip() -> None:
    password = "CorrectHorseBatteryStaple9!"
    hashed = hash_password(password)
    assert hashed != password
    assert verify_password(password, hashed) is True
    assert verify_password("wrong-password", hashed) is False


def test_verify_password_rejects_invalid_hash() -> None:
    assert verify_password("anything", "not-a-hash") is False


def test_jwt_roundtrip() -> None:
    token, expires_in = create_access_token(
        subject="00000000-0000-0000-0000-000000000001",
        roles=["admin", "user"],
        secret="unit-test-secret",
        algorithm="HS256",
        expires_minutes=30,
        extra={"username": "alice"},
    )
    payload = decode_access_token(token, secret="unit-test-secret", algorithm="HS256")
    assert payload["sub"] == "00000000-0000-0000-0000-000000000001"
    assert payload["roles"] == ["admin", "user"]
    assert payload["username"] == "alice"
    assert payload["type"] == "access"
    assert 0 < expires_in <= 30 * 60


def test_jwt_rejects_wrong_secret() -> None:
    token, _ = create_access_token(
        subject="abc",
        roles=[],
        secret="secret-one",
        algorithm="HS256",
        expires_minutes=5,
    )
    with pytest.raises(jwt.InvalidTokenError):
        decode_access_token(token, secret="secret-two", algorithm="HS256")
