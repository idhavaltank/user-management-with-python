"""Administrator-only user profile CRUD endpoints.

The router validates transport-level input through generated Pydantic schemas,
then delegates normalization, transactions, and invariants to
``app.services.users``.
"""

from __future__ import annotations

from typing import Literal
from uuid import UUID

from fastapi import APIRouter, Depends, Path, Query, Response, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.auth import AuthContext, require_admin, require_admin_csrf
from app.core.database import get_request_session
from app.generated.schemas import User as UserSchema
from app.generated.schemas import UserCreateRequest, UserListResponse, UserUpdateRequest
from app.services.users import (
    create_user,
    delete_user,
    get_active_user,
    list_users,
    to_user_schema,
    update_user,
)

# All profile mutations use the admin + CSRF dependency.  Reads still require
# an authenticated administrator but do not need a CSRF header.
router = APIRouter(prefix="/api/v1/users", tags=["Users"])


@router.get("", response_model=UserListResponse, operation_id="listUsers")
async def get_users(
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, alias="pageSize", ge=1, le=100),
    search: str | None = Query(default=None, max_length=120),
    sort: Literal["name", "email", "birthDate", "createdAt", "updatedAt"] = "createdAt",
    order: Literal["asc", "desc"] = "desc",
    _: AuthContext = Depends(require_admin),
    session: AsyncSession = Depends(get_request_session),
) -> UserListResponse:
    """Return a paginated, searchable, allowlist-sorted active-user list."""

    users, total = await list_users(session, page, page_size, search, sort, order)
    return UserListResponse(
        items=[to_user_schema(user) for user in users],
        page=page,
        pageSize=page_size,
        total=total,
    )


@router.post(
    "", response_model=UserSchema, status_code=status.HTTP_201_CREATED, operation_id="createUser"
)
async def post_user(
    payload: UserCreateRequest,
    _: AuthContext = Depends(require_admin_csrf),
    session: AsyncSession = Depends(get_request_session),
) -> UserSchema:
    """Create one user profile through the service-layer transaction."""

    return to_user_schema(await create_user(session, payload))


@router.get("/{userId}", response_model=UserSchema, operation_id="getUser")
async def get_user(
    user_id: UUID = Path(alias="userId"),
    _: AuthContext = Depends(require_admin),
    session: AsyncSession = Depends(get_request_session),
) -> UserSchema:
    """Return one active user by UUID."""

    return to_user_schema(await get_active_user(session, user_id))


@router.patch("/{userId}", response_model=UserSchema, operation_id="updateUser")
async def patch_user(
    payload: UserUpdateRequest,
    user_id: UUID = Path(alias="userId"),
    _: AuthContext = Depends(require_admin_csrf),
    session: AsyncSession = Depends(get_request_session),
) -> UserSchema:
    """Apply a partial profile update and return the normalized record."""

    return to_user_schema(await update_user(session, user_id, payload))


@router.delete("/{userId}", status_code=status.HTTP_204_NO_CONTENT, operation_id="deleteUser")
async def remove_user(
    response: Response,
    user_id: UUID = Path(alias="userId"),
    _: AuthContext = Depends(require_admin_csrf),
    session: AsyncSession = Depends(get_request_session),
) -> Response:
    """Soft-delete a user so audit/history data remains recoverable."""

    await delete_user(session, user_id)
    response.status_code = status.HTTP_204_NO_CONTENT
    return response
