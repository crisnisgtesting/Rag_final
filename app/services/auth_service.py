"""User and authentication service functions."""

from __future__ import annotations

from typing import Any
from uuid import UUID, uuid4

from app.auth.security import hash_password, verify_password
from app.db.databricks import DatabricksConnection

MIN_PASSWORD_LENGTH = 8
_DUMMY_PASSWORD_HASH = hash_password("timing-equalization-dummy-password")


class UserServiceError(ValueError):
    pass


def _validate_password(password: str) -> None:
    if len(password) < MIN_PASSWORD_LENGTH:
        raise UserServiceError(
            f"Password must be at least {MIN_PASSWORD_LENGTH} characters long"
        )


async def get_user_by_username(
    conn: DatabricksConnection, username: str
) -> dict[str, Any] | None:
    return await (
        await conn.execute(
            """
            SELECT id, username, email, full_name, password_hash, is_active
            FROM users
            WHERE lower(username) = lower(%s)
            """,
            (username,),
        )
    ).fetchone()


async def authenticate_user(
    conn: DatabricksConnection, username: str, password: str
) -> dict[str, Any] | None:
    user = await get_user_by_username(conn, username)
    password_hash = user["password_hash"] if user is not None else _DUMMY_PASSWORD_HASH
    password_ok = verify_password(password, password_hash)
    if user is None or not user["is_active"] or not password_ok:
        return None
    async with conn.transaction():
        await conn.execute(
            "UPDATE users SET last_login_at = current_timestamp() WHERE id = %s",
            (str(user["id"]),),
        )
    return user


async def get_user_with_roles(
    conn: DatabricksConnection, user_id: UUID
) -> dict[str, Any] | None:
    row = await (
        await conn.execute(
            """
            SELECT u.id, u.username, u.email, u.full_name, u.is_active,
                   COALESCE(collect_set(ur.role_code), array()) AS roles
            FROM users u
            LEFT JOIN user_roles ur ON ur.user_id = u.id
            WHERE u.id = %s
            GROUP BY u.id, u.username, u.email, u.full_name, u.is_active
            """,
            (str(user_id),),
        )
    ).fetchone()
    if row is None:
        return None
    return {
        "id": UUID(str(row["id"])),
        "username": row["username"],
        "email": row["email"],
        "full_name": row["full_name"],
        "is_active": row["is_active"],
        "roles": list(row["roles"] or []),
    }


async def create_user(
    conn: DatabricksConnection,
    *,
    username: str,
    password: str,
    roles: list[str] | None = None,
    email: str | None = None,
    full_name: str | None = None,
    is_active: bool = True,
) -> UUID:
    username = username.strip()
    if not username:
        raise UserServiceError("Username is required")
    _validate_password(password)
    roles = roles or ["user"]

    async with conn.transaction():
        existing = await (
            await conn.execute(
                "SELECT id FROM users WHERE lower(username) = lower(%s)", (username,)
            )
        ).fetchone()
        if existing is not None:
            raise UserServiceError(f"User '{username}' already exists")

        role_placeholders = ", ".join(["%s"] * len(roles))
        role_rows = await (
            await conn.execute(
                f"SELECT role_code FROM roles WHERE role_code IN ({role_placeholders})",
                tuple(roles),
            )
        ).fetchall()
        known = {row["role_code"] for row in role_rows}
        unknown = set(roles) - known
        if unknown:
            raise UserServiceError(f"Unknown roles: {', '.join(sorted(unknown))}")

        user_id = uuid4()
        await conn.execute(
            """
            INSERT INTO users
                (id, username, email, full_name, password_hash, is_active,
                 created_at, updated_at)
            VALUES (%s, %s, %s, %s, %s, %s, current_timestamp(), current_timestamp())
            """,
            (
                str(user_id),
                username,
                email,
                full_name,
                hash_password(password),
                is_active,
            ),
        )
        for role in roles:
            await conn.execute(
                "INSERT INTO user_roles (user_id, role_code) VALUES (%s, %s)",
                (str(user_id), role),
            )
    return user_id


async def change_password(
    conn: DatabricksConnection, *, user_id: UUID, new_password: str
) -> None:
    _validate_password(new_password)
    async with conn.transaction():
        await conn.execute(
            "UPDATE users SET password_hash = %s WHERE id = %s",
            (hash_password(new_password), str(user_id)),
        )
