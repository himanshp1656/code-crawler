from __future__ import annotations

import os
from contextlib import asynccontextmanager
from typing import AsyncGenerator

from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

DEFAULT_POSTGRES_DSN = "postgresql://postgres:postgres@localhost:5432/code_crawler"

_engine = None
_session_factory: async_sessionmaker[AsyncSession] | None = None


def _to_async_dsn(dsn: str) -> str:
    """Ensure the DSN uses the asyncpg driver."""
    if dsn.startswith("postgresql://"):
        return dsn.replace("postgresql://", "postgresql+asyncpg://", 1)
    return dsn


def configure(dsn: str | None = None) -> None:
    """Initialise the async engine and session factory. Call once at startup."""
    global _engine, _session_factory

    raw_dsn = dsn or os.getenv("POSTGRES_DSN", DEFAULT_POSTGRES_DSN)
    async_dsn = _to_async_dsn(raw_dsn)

    _engine = create_async_engine(
        async_dsn,
        pool_size=5,
        max_overflow=10,
        echo=False,
    )
    _session_factory = async_sessionmaker(
        _engine,
        expire_on_commit=False,
    )


def _get_factory() -> async_sessionmaker[AsyncSession]:
    if _session_factory is None:
        configure()
    assert _session_factory is not None
    return _session_factory


async def get_session() -> AsyncGenerator[AsyncSession, None]:
    """FastAPI dependency — yields a session, auto-closes on exit."""
    async with _get_factory()() as session:
        yield session


@asynccontextmanager
async def get_session_context() -> AsyncGenerator[AsyncSession, None]:
    """Context manager for use outside FastAPI DI (e.g. Temporal activities)."""
    async with _get_factory()() as session:
        yield session
