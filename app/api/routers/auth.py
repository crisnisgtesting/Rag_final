"""Authentication endpoints."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request, status

from app.api.deps import get_app_settings
from app.auth.dependencies import CurrentUser, get_current_user
from app.auth.rate_limit import SlidingWindowLimiter
from app.auth.schemas import LoginRequest, TokenResponse, UserOut
from app.auth.security import create_access_token
from app.config import Settings
from app.db.databricks import DatabricksConnection
from app.db.pool import get_db
from app.logging_conf import get_logger
from app.services.auth_service import authenticate_user, get_user_with_roles

logger = get_logger(__name__)

router = APIRouter(prefix="/auth", tags=["auth"])


def _limiter(request: Request) -> SlidingWindowLimiter:
    return request.app.state.login_limiter


@router.post("/login", response_model=TokenResponse)
async def login(
    payload: LoginRequest,
    request: Request,
    conn: DatabricksConnection = Depends(get_db),
    settings: Settings = Depends(get_app_settings),
    limiter: SlidingWindowLimiter = Depends(_limiter),
) -> TokenResponse:
    client_ip = request.client.host if request.client else "unknown"
    limiter_key = f"{client_ip}:{payload.username.strip().lower()}"
    allowed, retry_after = await limiter.check(limiter_key)
    if not allowed:
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="Too many login attempts. Try again later.",
            headers={"Retry-After": str(retry_after)},
        )

    user = await authenticate_user(conn, payload.username.strip(), payload.password)
    if user is None:
        await limiter.record_failure(limiter_key)
        logger.info("login_failed", username=payload.username.strip())
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid username or password",
            headers={"WWW-Authenticate": "Bearer"},
        )

    await limiter.reset(limiter_key)
    full_user = await get_user_with_roles(conn, user["id"])
    assert full_user is not None
    roles = list(full_user["roles"])

    token, expires_in = create_access_token(
        subject=str(full_user["id"]),
        roles=roles,
        secret=settings.jwt_secret.get_secret_value(),
        algorithm=settings.jwt_algorithm,
        expires_minutes=settings.access_token_expire_minutes,
        extra={"username": full_user["username"]},
    )
    logger.info("login_success", user_id=str(full_user["id"]))
    return TokenResponse(
        access_token=token,
        expires_in=expires_in,
        user=UserOut(
            id=full_user["id"],
            username=full_user["username"],
            email=full_user["email"],
            full_name=full_user["full_name"],
            roles=roles,
            is_active=full_user["is_active"],
        ),
    )


@router.get("/me", response_model=UserOut)
async def me(user: CurrentUser = Depends(get_current_user)) -> UserOut:
    return UserOut(
        id=user.id,
        username=user.username,
        email=user.email,
        full_name=user.full_name,
        roles=list(user.roles),
        is_active=user.is_active,
    )
