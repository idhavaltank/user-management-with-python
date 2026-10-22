"""Async SQLAlchemy engine, session-factory, and database health helpers."""

from __future__ import annotations

from collections.abc import AsyncIterator

from fastapi import Request
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from app.core.config import Settings


def create_engine(settings: Settings) -> AsyncEngine:
    """Create the process-wide async engine from the configured URL."""

    # ``pool_pre_ping`` prevents stale pooled connections from failing the
    # first request after a database restart.
    return create_async_engine(settings.database_url, pool_pre_ping=True)


def create_session_factory(engine: AsyncEngine) -> async_sessionmaker[AsyncSession]:
    """Create request-scoped sessions that do not expire returned ORM data."""

    return async_sessionmaker(engine, expire_on_commit=False)


async def check_database(engine: AsyncEngine) -> bool:
    """Run a minimal query used by the readiness endpoint."""

    try:
        async with engine.connect() as connection:
            await connection.execute(text("SELECT 1"))
    except (ConnectionError, OSError, SQLAlchemyError):
        return False
    return True


async def get_session(
    session_factory: async_sessionmaker[AsyncSession],
) -> AsyncIterator[AsyncSession]:
    """Yield and close a session for callers that inject a factory directly."""

    async with session_factory() as session:
        yield session


async def get_request_session(request: Request) -> AsyncIterator[AsyncSession]:
    """Yield the application session for one HTTP request."""

    session_factory = request.app.state.session_factory
    if session_factory is None:
        raise RuntimeError("Database session factory is not configured")
    async with session_factory() as session:
        yield session
