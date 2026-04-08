from __future__ import annotations

from contextlib import asynccontextmanager
from typing import AsyncGenerator

from sqlalchemy import text
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from src.storage.models import Base

_session_factories: dict[int, async_sessionmaker[AsyncSession]] = {}


async def init_db(url: str) -> AsyncEngine:
    engine = create_async_engine(url, echo=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    # WAL pragma must run outside a transaction
    async with engine.connect() as conn:
        await conn.execute(text("PRAGMA journal_mode=WAL"))
        await conn.commit()
    _session_factories[id(engine)] = async_sessionmaker(engine, expire_on_commit=False)
    return engine


@asynccontextmanager
async def get_session(engine: AsyncEngine) -> AsyncGenerator[AsyncSession, None]:
    factory = _session_factories.get(id(engine))
    if factory is None:
        factory = async_sessionmaker(engine, expire_on_commit=False)
        _session_factories[id(engine)] = factory
    async with factory() as session:
        yield session
