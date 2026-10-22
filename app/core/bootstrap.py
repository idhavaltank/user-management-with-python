"""Optional first-administrator bootstrap used by local and test startup."""

from __future__ import annotations

from sqlalchemy import func, select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.config import Settings
from app.core.security import hash_password
from app.core.telemetry import Telemetry
from app.models.user import Gender, User, UserRole


async def ensure_bootstrap_admin(
    settings: Settings,
    session_factory: async_sessionmaker[AsyncSession],
    telemetry: Telemetry,
) -> None:
    """Create the configured administrator once, if bootstrap settings exist.

    The operation is idempotent: an existing email is left unchanged. This is
    useful for local startup while avoiding accidental password overwrites on
    every application restart.
    """

    if not settings.bootstrap_admin_email or not settings.bootstrap_admin_password:
        # Deployments without bootstrap credentials use a separate provisioning
        # workflow to create their first administrator.
        return

    try:
        async with session_factory() as session:
            email = settings.bootstrap_admin_email.strip().lower()
            # Case-insensitive lookup matches the active-email uniqueness rule.
            existing = await session.scalar(select(User).where(func.lower(User.email) == email))
            if existing is not None:
                return
            # Hash before persistence; the raw bootstrap password never enters
            # the database or telemetry.
            session.add(
                User(
                    name=settings.bootstrap_admin_name,
                    email=email,
                    password_hash=hash_password(settings.bootstrap_admin_password),
                    gender=Gender.MEN,
                    birth_date=settings.bootstrap_admin_birth_date,
                    college="System",
                    address="System",
                    hobbies=[],
                    custom_hobbies=[],
                    role=UserRole.ADMIN,
                )
            )
            await session.commit()
            telemetry.info("bootstrap.admin.created", {"email": email})
    except SQLAlchemyError as exc:
        telemetry.error("bootstrap.admin.skipped", {"reason": "database_unavailable"}, error=exc)
