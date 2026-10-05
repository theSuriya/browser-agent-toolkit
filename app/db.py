"""Async SQLAlchemy engine, session factory and the declarative base.

The engine is created per application (not at import time) so tests can point each
app instance at its own database. Everything the service persists — organizations,
users, API keys, plans, subscriptions, usage events, jobs, artifacts and the audit
log — lives behind this layer.

``sqlite+aiosqlite`` is the development and test default; set ``DATABASE_URL`` to a
``postgresql+asyncpg://`` DSN in production. Schema is created with ``create_all`` for
development and tests only; production runs Alembic migrations (see ``migrations/``).
"""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import Request
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase


class Base(DeclarativeBase):
    """Declarative base for every model."""


def make_engine(database_url: str) -> AsyncEngine:
    # check_same_thread is a SQLite-only concern; asyncpg ignores unknown args, so
    # only pass it for SQLite URLs.
    connect_args = {"check_same_thread": False} if database_url.startswith("sqlite") else {}
    return create_async_engine(database_url, pool_pre_ping=True, connect_args=connect_args)


def make_sessionmaker(engine: AsyncEngine) -> async_sessionmaker[AsyncSession]:
    return async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)


async def init_models(engine: AsyncEngine) -> None:
    """Create any missing tables (development and tests only)."""
    # Import models so they are registered on Base.metadata before create_all.
    from app import models  # noqa: F401

    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)


def get_sessionmaker(request: Request) -> async_sessionmaker[AsyncSession]:
    return request.app.state.sessionmaker


async def get_session(request: Request) -> AsyncIterator[AsyncSession]:
    """FastAPI dependency: one session per request, rolled back on error."""
    factory: async_sessionmaker[AsyncSession] = request.app.state.sessionmaker
    async with factory() as session:
        try:
            yield session
        except Exception:
            await session.rollback()
            raise


@asynccontextmanager
async def session_scope(factory: async_sessionmaker[AsyncSession]) -> AsyncIterator[AsyncSession]:
    """Open a session outside a request (background tasks, middleware)."""
    async with factory() as session:
        try:
            yield session
        except Exception:
            await session.rollback()
            raise
