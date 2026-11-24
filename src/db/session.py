"""Async engine and session factory."""
from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker, create_async_engine

from settings import Settings


def make_engine(settings: Settings, **kwargs) -> AsyncEngine:
    return create_async_engine(settings.database_url, pool_pre_ping=True, **kwargs)


def make_session_factory(engine: AsyncEngine) -> async_sessionmaker[AsyncSession]:
    # expire_on_commit=False: an expired attribute would be reloaded on access,
    # which is implicit I/O and fails under asyncio
    return async_sessionmaker(engine, expire_on_commit=False)
