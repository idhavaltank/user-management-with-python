"""User-profile business rules and transaction boundaries.

Routers deliberately stay thin.  This module owns normalization, pagination,
duplicate-email handling, role restrictions, and the last-administrator
invariant before changes are committed to PostgreSQL.
"""

from __future__ import annotations

from collections.abc import Iterable
from datetime import UTC, date, datetime
from typing import Any
from uuid import UUID

from sqlalchemy import Select, func, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.http import api_error
from app.generated.schemas import (
    Gender as GenderSchema,
)
from app.generated.schemas import (
    HobbyList,
    UserCreateRequest,
    UserUpdateRequest,
)
from app.generated.schemas import (
    User as UserSchema,
)
from app.generated.schemas import (
    UserRole as UserRoleSchema,
)
from app.models.user import Gender, User, UserRole

MIN_BIRTH_DATE = date(1960, 1, 1)
MAX_BIRTH_DATE = date(2000, 1, 1)


def _hobbies(value: HobbyList | None) -> list[str]:
    """Convert generated hobby values into bounded, trimmed unique strings."""

    if value is None:
        return []
    raw = value.root if hasattr(value, "root") else value
    values: Iterable[Any] = raw
    result: list[str] = []
    for item in values:
        text = str(item.root if hasattr(item, "root") else item).strip()
        if text and text not in result:
            result.append(text)
    return result


def _validate_birth_date(value: date | None) -> None:
    """Enforce the inclusive date range retained from the original UI."""

    if value is not None and not MIN_BIRTH_DATE <= value <= MAX_BIRTH_DATE:
        raise api_error(
            422,
            "validation_error",
            "Birth date must be between 1960-01-01 and 2000-01-01.",
        )


def _clean(value: str, field: str) -> str:
    """Trim a required text field and reject whitespace-only input."""

    cleaned = value.strip()
    if not cleaned:
        raise api_error(422, "validation_error", f"{field} must not be blank.")
    return cleaned


def _profile_role(value: UserRoleSchema | None) -> UserRole:
    """Allow CRUD to create ordinary users but keep admin changes privileged."""

    requested_role = UserRole(
        value.value if isinstance(value, UserRoleSchema) else (value or UserRole.USER.value)
    )
    if requested_role is not UserRole.USER:
        raise api_error(
            403,
            "role_management_required",
            "Administrator role changes require a dedicated role-management operation.",
        )
    return requested_role


def to_user_schema(user: User) -> UserSchema:
    """Map an ORM user to the generated public API response schema."""

    return UserSchema(
        id=user.id,
        name=user.name,
        email=user.email,
        gender=user.gender.value if isinstance(user.gender, Gender) else user.gender,
        birthDate=user.birth_date,
        college=user.college,
        address=user.address,
        hobbies=user.hobbies or [],
        customHobbies=user.custom_hobbies or [],
        role=user.role.value if isinstance(user.role, UserRole) else user.role,
        isActive=user.is_active,
        createdAt=user.created_at,
        updatedAt=user.updated_at,
    )


def _user_query() -> Select[tuple[User]]:
    """Start every profile read with the active, non-deleted filter."""

    return select(User).where(User.is_active.is_(True), User.deleted_at.is_(None))


async def list_users(
    session: AsyncSession,
    page: int,
    page_size: int,
    search: str | None,
    sort: str,
    order: str,
) -> tuple[list[User], int]:
    """Return one page of active users and the unpaginated result count."""

    query = _user_query()
    if search:
        pattern = f"%{search.strip()}%"
        query = query.where(or_(User.name.ilike(pattern), User.email.ilike(pattern)))
    total = await session.scalar(select(func.count()).select_from(query.subquery()))
    # The route validates this allowlist, and the map prevents user input from
    # becoming arbitrary SQL identifiers.
    sort_map = {
        "name": User.name,
        "email": User.email,
        "birthDate": User.birth_date,
        "createdAt": User.created_at,
        "updatedAt": User.updated_at,
    }
    sort_column = sort_map[sort]
    query = query.order_by(sort_column.asc() if order == "asc" else sort_column.desc())
    query = query.offset((page - 1) * page_size).limit(page_size)
    result = await session.execute(query)
    return list(result.scalars().all()), int(total or 0)


async def get_active_user(session: AsyncSession, user_id: UUID) -> User:
    """Load one active user or raise the contract's 404 error."""

    user = await session.scalar(_user_query().where(User.id == user_id))
    if user is None:
        raise api_error(404, "not_found", "User not found.")
    return user


async def create_user(session: AsyncSession, payload: UserCreateRequest) -> User:
    """Normalize, validate, insert, and return a new user profile."""

    birth_date = payload.birthDate
    _validate_birth_date(birth_date)
    user = User(
        name=_clean(payload.name, "name"),
        email=str(payload.email).strip().lower(),
        gender=Gender(
            payload.gender.value if isinstance(payload.gender, GenderSchema) else payload.gender
        ),
        birth_date=birth_date,
        college=_clean(payload.college, "college"),
        address=_clean(payload.address, "address"),
        hobbies=_hobbies(payload.hobbies),
        custom_hobbies=_hobbies(payload.customHobbies),
        role=_profile_role(payload.role),
    )
    session.add(user)
    try:
        # The partial lower-case email index is the final concurrency-safe
        # duplicate boundary; translate its database error to HTTP 409.
        await session.commit()
    except IntegrityError as exc:
        await session.rollback()
        raise api_error(409, "email_conflict", "A user with this email already exists.") from exc
    await session.refresh(user)
    return user


async def update_user(session: AsyncSession, user_id: UUID, payload: UserUpdateRequest) -> User:
    """Apply a validated partial update while preserving role boundaries."""

    if not payload.model_fields_set:
        raise api_error(422, "validation_error", "At least one field is required.")
    user = await get_active_user(session, user_id)
    # ``exclude_unset`` distinguishes an omitted field from an explicit value
    # and prevents accidental clearing of unrelated profile fields.
    values = payload.model_dump(exclude_unset=True)
    if "name" in values:
        user.name = _clean(values["name"], "name")
    if "email" in values:
        user.email = str(values["email"]).strip().lower()
    if "gender" in values:
        user.gender = Gender(
            values["gender"].value
            if isinstance(values["gender"], GenderSchema)
            else values["gender"]
        )
    if "birthDate" in values:
        _validate_birth_date(values["birthDate"])
        user.birth_date = values["birthDate"]
    if "college" in values:
        user.college = _clean(values["college"], "college")
    if "address" in values:
        user.address = _clean(values["address"], "address")
    if "hobbies" in values:
        user.hobbies = _hobbies(values["hobbies"])
    if "customHobbies" in values:
        user.custom_hobbies = _hobbies(values["customHobbies"])
    if "role" in values:
        current_role = user.role.value if isinstance(user.role, UserRole) else user.role
        requested_role = UserRole(
            values["role"].value if isinstance(values["role"], UserRoleSchema) else values["role"]
        )
        if requested_role.value != current_role:
            raise api_error(
                403,
                "role_management_required",
                "Administrator role changes require a dedicated role-management operation.",
            )
    user.updated_at = datetime.now(UTC)
    try:
        await session.commit()
    except IntegrityError as exc:
        await session.rollback()
        raise api_error(409, "email_conflict", "A user with this email already exists.") from exc
    await session.refresh(user)
    return user


async def delete_user(session: AsyncSession, user_id: UUID) -> None:
    """Soft-delete a profile and prevent removal of the last active admin."""

    user = await get_active_user(session, user_id)
    current_role = user.role.value if isinstance(user.role, UserRole) else user.role
    if current_role == UserRole.ADMIN.value:
        admin_count = await session.scalar(
            select(func.count())
            .select_from(User)
            .where(
                User.role == UserRole.ADMIN.value,
                User.is_active.is_(True),
                User.deleted_at.is_(None),
            )
        )
        if (admin_count or 0) <= 1:
            raise api_error(
                409,
                "admin_invariant",
                "The last active administrator cannot be deleted.",
            )
    # Keep the row for auditability and make the active-email index reusable
    # only after the record is no longer active.
    user.is_active = False
    user.deleted_at = datetime.now(UTC)
    user.updated_at = datetime.now(UTC)
    await session.commit()
