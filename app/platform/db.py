from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from functools import lru_cache
from pathlib import Path
from typing import AsyncIterator

from alembic import command
from alembic.config import Config
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker, create_async_engine

from app.platform.config import get_settings


def _resolve_project_root() -> Path:
    for candidate in (Path.cwd(), *Path(__file__).resolve().parents):
        if (candidate / "alembic.ini").exists() and (candidate / "alembic").is_dir():
            return candidate
    return Path.cwd()


@lru_cache(maxsize=1)
def get_engine() -> AsyncEngine:
    settings = get_settings()
    return create_async_engine(
        settings.database_url,
        pool_pre_ping=True,
        pool_size=settings.database_pool_size,
        max_overflow=settings.database_max_overflow,
    )


@lru_cache(maxsize=1)
def get_session_factory() -> async_sessionmaker[AsyncSession]:
    return async_sessionmaker(get_engine(), expire_on_commit=False)


@asynccontextmanager
async def get_session() -> AsyncIterator[AsyncSession]:
    session_factory = get_session_factory()
    async with session_factory() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise


async def init_db() -> None:
    engine = get_engine()
    async with engine.connect() as connection:
        await connection.execute(text("SELECT 1"))


async def close_db() -> None:
    engine = get_engine()
    await engine.dispose()


def _run_migrations(database_url: str) -> None:
    project_root = _resolve_project_root()
    config = Config(str(project_root / "alembic.ini"))
    config.set_main_option("script_location", str(project_root / "alembic"))
    config.set_main_option("sqlalchemy.url", database_url)
    command.upgrade(config, "head")


async def run_migrations() -> None:
    settings = get_settings()
    await asyncio.to_thread(_run_migrations, settings.database_url)


async def assert_no_stale_teacher_turn_columns() -> None:
    engine = get_engine()
    async with engine.connect() as connection:
        result = await connection.execute(
            text(
                """
                SELECT column_name
                FROM information_schema.columns
                WHERE table_schema = 'public'
                  AND table_name = 'teacher_turns'
                  AND column_name IN ('surface', 'mode')
                ORDER BY column_name
                """
            )
        )
        stale_columns = [str(row[0]) for row in result.fetchall()]
    if stale_columns:
        joined = ", ".join(stale_columns)
        raise RuntimeError(
            "Detected stale teacher_turns columns in the local dev database: "
            f"{joined}. Rebuild the disposable Postgres volume from the current baseline "
            "before continuing."
        )
