"""PostgreSQL engine and session lifecycle primitives."""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.orm import DeclarativeBase

from adaptive_trading_bot.config import Settings


class Base(DeclarativeBase):
    """Declarative base for future, issue-scoped PostgreSQL models."""


def create_engine(settings: Settings) -> AsyncEngine:
    """Create a lazy async PostgreSQL engine from typed settings."""

    return create_async_engine(settings.database_url.unicode_string(), pool_pre_ping=True)


def create_session_factory(engine: AsyncEngine) -> async_sessionmaker[AsyncSession]:
    """Create the application's async session factory."""

    return async_sessionmaker(engine, expire_on_commit=False)


@asynccontextmanager
async def session_scope(
    session_factory: async_sessionmaker[AsyncSession],
) -> AsyncIterator[AsyncSession]:
    """Commit a successful unit of work and roll it back on failure."""

    async with session_factory() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise


async def dispose_engine(engine: AsyncEngine) -> None:
    """Release database pool resources during application shutdown."""

    await engine.dispose()
