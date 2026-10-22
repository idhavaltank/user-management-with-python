"""Shared SQLAlchemy declarative base and timestamp behavior."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import DateTime, MetaData, text
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

# Stable names make Alembic diffs and production troubleshooting predictable.
NAMING_CONVENTION = {
    "ix": "ix_%(column_0_label)s",
    "uq": "uq_%(table_name)s_%(column_0_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}


class Base(DeclarativeBase):
    """Base class that supplies metadata to all ORM models and migrations."""

    metadata = MetaData(naming_convention=NAMING_CONVENTION)


class TimestampMixin:
    """Add timezone-aware creation and update timestamps to an ORM entity."""

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("CURRENT_TIMESTAMP")
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=text("CURRENT_TIMESTAMP"),
        onupdate=lambda: datetime.now().astimezone(),
    )
