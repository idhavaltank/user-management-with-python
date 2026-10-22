"""Authentication endpoints for browser session management.

The frontend receives an opaque session cookie and keeps the CSRF token in
memory.  Raw session and CSRF values are never stored in PostgreSQL; only
their SHA-256 hashes are persisted by the session model.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from fastapi import APIRouter, Depends, Request, Response, status
from sqlalchemy import func, select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.concurrency import run_in_threadpool

from app.core.auth import AuthContext, get_auth_context, require_csrf
from app.core.config import Settings
from app.core.database import get_request_session
from app.core.http import api_error
from app.core.security import generate_token, hash_token, verify_password
from app.generated.schemas import CsrfResponse, LoginRequest, LoginResponse
from app.generated.schemas import User as UserSchema
from app.models.session import UserSession
from app.models.user import User
from app.services.users import to_user_schema

# Keeping authentication under one versioned router makes the public API
# contract explicit and allows future authentication versions to coexist.
router = APIRouter(prefix="/api/v1/auth", tags=["Authentication"])


def _dummy_password_check(password: str) -> None:
    """Perform a password-hash check when the account does not exist.

    This keeps the response timing for an unknown email closer to the timing
    for a known email with a wrong password, reducing account enumeration risk.
    """

    # A fixed valid hash keeps the missing-account branch comparable to the mismatch branch.
    verify_password(
        password,
        "$argon2id$v=19$m=65536,t=3,p=4$MmJuxX0cn4f7ZAZeb99J7Q$vb8A6pf6RkDFq45nyQg6xfuUY3HqdsntZvlcNW6Hwis",
    )


@router.post("/login", response_model=LoginResponse, operation_id="login")
async def login(
    payload: LoginRequest,
    response: Response,
    request: Request,
    session: AsyncSession = Depends(get_request_session),
) -> LoginResponse:
    """Validate credentials and create a database-backed browser session."""

    settings: Settings = request.app.state.settings
    origin = request.headers.get("origin")
    if origin and origin not in settings.frontend_origins:
        raise api_error(403, "origin_forbidden", "The request origin is not allowed.")
    email = str(payload.email).strip().lower()
    # Email is normalized before lookup so login matches CRUD uniqueness rules.
    user = await session.scalar(
        select(User).where(func.lower(User.email) == email, User.is_active.is_(True))
    )
    valid = bool(user and user.password_hash)
    if valid:
        valid = await run_in_threadpool(
            verify_password, payload.password.get_secret_value(), user.password_hash
        )
    if not valid:
        await run_in_threadpool(_dummy_password_check, payload.password.get_secret_value())
    if not valid:
        raise api_error(401, "invalid_credentials", "Invalid email or password.")

    # The browser receives the raw values, while PostgreSQL stores only hashes.
    session_token = generate_token()
    csrf_token = generate_token()
    now = datetime.now(UTC)
    user_session = UserSession(
        user_id=user.id,
        token_hash=hash_token(session_token),
        csrf_token_hash=hash_token(csrf_token),
        expires_at=now + timedelta(seconds=settings.session_ttl_seconds),
    )
    session.add(user_session)
    try:
        await session.commit()
    except SQLAlchemyError as exc:
        await session.rollback()
        raise api_error(
            503, "dependency_unavailable", "Authentication storage is unavailable."
        ) from exc

    response.set_cookie(
        key=settings.session_cookie_name,
        value=session_token,
        max_age=settings.session_ttl_seconds,
        httponly=True,
        secure=settings.session_cookie_secure,
        samesite=settings.session_cookie_same_site,
        path="/",
    )
    return LoginResponse(user=to_user_schema(user), csrfToken=csrf_token)


@router.post("/logout", status_code=status.HTTP_204_NO_CONTENT, operation_id="logout")
async def logout(
    response: Response,
    request: Request,
    context: AuthContext = Depends(require_csrf),
    session: AsyncSession = Depends(get_request_session),
) -> Response:
    """Revoke the current session and remove its browser cookie."""

    context.session.revoked_at = datetime.now(UTC)
    await session.commit()
    response.delete_cookie(
        key=request.app.state.settings.session_cookie_name,
        httponly=True,
        secure=request.app.state.settings.session_cookie_secure,
        samesite=request.app.state.settings.session_cookie_same_site,
        path="/",
    )
    response.status_code = status.HTTP_204_NO_CONTENT
    return response


@router.get("/me", response_model=UserSchema, operation_id="getCurrentUser")
async def current_user(context: AuthContext = Depends(get_auth_context)) -> UserSchema:
    """Return the authenticated user's public profile."""

    return to_user_schema(context.user)


@router.get("/csrf", response_model=CsrfResponse, operation_id="getCsrfToken")
async def csrf_token(
    context: AuthContext = Depends(get_auth_context),
    session: AsyncSession = Depends(get_request_session),
) -> CsrfResponse:
    """Rotate the CSRF token used by subsequent unsafe requests."""

    token = generate_token()
    context.session.csrf_token_hash = hash_token(token)
    await session.commit()
    return CsrfResponse(csrfToken=token)
