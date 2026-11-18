"""Pydantic request/response models for authentication."""

from __future__ import annotations

from uuid import UUID

from pydantic import BaseModel, Field


class LoginRequest(BaseModel):
    username: str = Field(min_length=1, max_length=200)
    password: str = Field(min_length=1, max_length=512)


class UserOut(BaseModel):
    id: UUID
    username: str
    email: str | None = None
    full_name: str | None = None
    roles: list[str]
    is_active: bool


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    expires_in: int
    user: UserOut
