from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

from sqlalchemy import event
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from app.db.models import Base

_engine: AsyncEngine | None = None
_session_factory: async_sessionmaker[AsyncSession] | None = None


def configure_sqlite_engine(engine: AsyncEngine) -> None:
    """Apply SQLite pragmas needed by the bot.

    ``foreign_keys`` makes declared foreign keys actually enforced, and
    ``busy_timeout`` lets a writer wait for a short concurrent transaction
    instead of failing with "database is locked".
    """
    if engine.url.get_backend_name() != "sqlite":
        return

    @event.listens_for(engine.sync_engine, "connect")
    def _set_pragma(dbapi_connection: Any, _: Any) -> None:
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.execute("PRAGMA busy_timeout=30000")
        cursor.close()


def init_engine(database_url: str) -> AsyncEngine:
    global _engine, _session_factory
    if _engine is not None:
        return _engine

    _engine = create_async_engine(database_url, echo=False, pool_pre_ping=True)
    configure_sqlite_engine(_engine)
    _session_factory = async_sessionmaker(_engine, expire_on_commit=False)
    return _engine


def get_session_factory() -> async_sessionmaker[AsyncSession]:
    if _session_factory is None:
        raise RuntimeError("Database engine is not initialized")
    return _session_factory


async def init_db(database_url: str) -> None:
    engine = init_engine(database_url)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
        await _ensure_dialog_scope_columns(conn)


async def _ensure_dialog_scope_columns(conn: Any) -> None:
    """Add topic scope columns to an existing ``dialogs`` table.

    ``create_all`` never alters existing tables, so old databases need a tiny
    in-place migration to support dialogs that live in a forum topic.
    """
    if conn.engine.url.get_backend_name() != "sqlite":
        return
    rows = await conn.exec_driver_sql("PRAGMA table_info(dialogs)")
    names = {row[1] for row in rows}
    if "chat_id" not in names:
        await conn.exec_driver_sql("ALTER TABLE dialogs ADD COLUMN chat_id BIGINT")
    if "thread_id" not in names:
        await conn.exec_driver_sql("ALTER TABLE dialogs ADD COLUMN thread_id BIGINT")


@asynccontextmanager
async def session_scope() -> AsyncIterator[AsyncSession]:
    factory = get_session_factory()
    async with factory() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise
