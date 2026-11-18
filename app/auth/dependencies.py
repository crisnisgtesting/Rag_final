"""Authentication dependencies and role-based access control."""

from __future__ import annotations

from dataclasses import dataclass
from uuid import UUID

import jwt
from fastapi import Depends, HTTPException, Request, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from psycopg import AsyncConnection

from app.api.deps import get_app_settings
from app.auth.security import decode_access_token
from app.config import Settings
from app.db.pool import get_db
from app.logging_conf import get_logger

logger = get_logger(__name__)

_bearer = HTTPBearer(auto_error=False)


@dataclass(frozen=True)
class CurrentUser:
    id: UUID
    username: str
    email: str | None
    full_name: str | None
    roles: tuple[str, ...]
    is_active: bool

    @property
    def is_admin(self) -> bool:
        return "admin" in self.roles


def _unauthorized(detail: str = "Invalid or expired credentials") -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail=detail,
        headers={"WWW-Authenticate": "Bearer"},
    )


async def load_user(conn: AsyncConnection, user_id: UUID) -> CurrentUser | None:
    row = await (
        await conn.execute(
            """
            SELECT u.id, u.username, u.email, u.full_name, u.is_active,
                   COALESCE(
                       array_agg(ur.role_code) FILTER (WHERE ur.role_code IS NOT NULL),
                       '{}'
                   ) AS roles
            FROM users u
            LEFT JOIN user_roles ur ON ur.user_id = u.id
            WHERE u.id = %s
            GROUP BY u.id
            """,
            (str(user_id),),
        )
    ).fetchone()
    if row is None:
        return None
    return CurrentUser(
        id=row["id"],
        username=row["username"],
        email=row["email"],
        full_name=row["full_name"],
        roles=tuple(row["roles"]),
        is_active=row["is_active"],
    )


async def get_current_user(
    request: Request,
    credentials: HTTPAuthorizationCredentials | None = Depends(_bearer),
    conn: AsyncConnection = Depends(get_db),
    settings: Settings = Depends(get_app_settings),
) -> CurrentUser:
    if credentials is None or credentials.scheme.lower() != "bearer":
        raise _unauthorized("Missing bearer token")
    try:
        payload = decode_access_token(
            credentials.credentials,
            secret=settings.jwt_secret.get_secret_value(),
            algorithm=settings.jwt_algorithm,
        )
    except jwt.PyJWTError as exc:
        logger.info("token_rejected", reason=str(exc))
        raise _unauthorized() from exc

    try:
        user_id = UUID(str(payload["sub"]))
    except (KeyError, ValueError) as exc:
        raise _unauthorized() from exc

    user = await load_user(conn, user_id)
    if user is None or not user.is_active:
        raise _unauthorized("User is inactive or does not exist")

    request.state.user_id = str(user.id)
    request.state.username = user.username
    return user


def require_role(role: str):
    async def _dependency(user: CurrentUser = Depends(get_current_user)) -> CurrentUser:
        if role not in user.roles:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"Role '{role}' is required",
            )
        return user

    return _dependency


require_admin = require_role("admin")
