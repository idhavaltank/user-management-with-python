"""FastAPI dependencies for session authentication and authorization.

Authentication verifies the opaque cookie against a hashed database value.
Authorization is deliberately separate: an authenticated user is not
automatically an administrator, and unsafe methods additionally require CSRF
validation.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from hmac import compare_digest
from uuid import UUID

from fastapi import Depends, Request
from fastapi.security import APIKeyCookie, APIKeyHeader
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings
from app.core.database import get_request_session
from app.core.http import api_error
from app.core.security import hash_token
from app.models.session import UserSession
from app.models.user import User, UserRole


@dataclass(frozen=True)
class AuthContext:
    """The validated user and session made available to protected endpoints."""

    user: User
    session: UserSession


# ``auto_error=False`` lets the dependency return our consistent API error
# shape instead of FastAPI's default security error response.
session_cookie = APIKeyCookie(
    name="session_id", scheme_name="sessionCookie", auto_error=False
)
csrf_header = APIKeyHeader(
    name="X-CSRF-Token", scheme_name="csrfToken", auto_error=False
)


async def get_auth_context(
    request: Request,
    raw_token: str | None = Depends(session_cookie),
    session: AsyncSession = Depends(get_request_session),
) -> AuthContext:
    """Resolve and validate the current browser session."""

    settings: Settings = request.app.state.settings
    if raw_token is None and settings.session_cookie_name != "session_id":
        raw_token = request.cookies.get(settings.session_cookie_name)
    if not raw_token:
        raise api_error(401, "authentication_required", "Authentication is required.")

    # Compare hashes in the database rather than storing or querying raw
    # bearer values. The joined user check also makes deactivated users fail.
    result = await session.execute(
        select(UserSession, User)
        .join(User, User.id == UserSession.user_id)
        .where(UserSession.token_hash == hash_token(raw_token))
    )
    row = result.one_or_none()
    now = datetime.now(UTC)
    if row is None:
        raise api_error(401, "invalid_session", "The session is invalid or expired.")
    user_session, user = row
    if (
        user_session.revoked_at is not None
        or user_session.expires_at <= now
        or not user.is_active
        or user.deleted_at is not None
    ):
        raise api_error(401, "invalid_session", "The session is invalid or expired.")

    request.state.auth_context = AuthContext(user=user, session=user_session)
    return request.state.auth_context


async def require_admin(context: AuthContext = Depends(get_auth_context)) -> AuthContext:
    """Require an authenticated user with the administrator role."""

    role = context.user.role.value if isinstance(context.user.role, UserRole) else context.user.role
    if role != UserRole.ADMIN.value:
        raise api_error(403, "forbidden", "Administrator access is required.")
    return context


async def require_csrf(
    supplied: str | None = Depends(csrf_header),
    context: AuthContext = Depends(get_auth_context),
) -> AuthContext:
    """Require the CSRF token that was issued for the current session."""

    if not supplied or not compare_digest(hash_token(supplied), context.session.csrf_token_hash):
        raise api_error(403, "csrf_failed", "A valid CSRF token is required.")
    return context


async def require_admin_csrf(
    context: AuthContext = Depends(require_csrf),
) -> AuthContext:
    """Require both a valid CSRF token and administrator authorization."""

    role = context.user.role.value if isinstance(context.user.role, UserRole) else context.user.role
    if role != UserRole.ADMIN.value:
        raise api_error(403, "forbidden", "Administrator access is required.")
    return context


def user_id(context: AuthContext) -> UUID:
    """Return the authenticated user's UUID for callers that need it."""

    return context.user.id
