from __future__ import annotations

from contextlib import asynccontextmanager
from pathlib import Path
from typing import TYPE_CHECKING, AsyncGenerator

from sqlalchemy import text
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from src.storage.models import Base

if TYPE_CHECKING:
    from alembic.config import Config

_session_factories: dict[int, async_sessionmaker[AsyncSession]] = {}


async def init_db(url: str) -> AsyncEngine:
    engine = create_async_engine(url, echo=False)
    # engine.begin() 开外层 transaction；alembic 内层 context.begin_transaction()
    # 检测 _in_external_transaction → nullcontext → 共享外层 transaction → 外层退出 COMMIT
    # (Round 13 calibration: engine.connect() would auto-begin then ROLLBACK on async with exit
    #  because alembic's nullcontext doesn't commit and no explicit outer commit)
    async with engine.begin() as conn:
        has_alembic = await conn.run_sync(_has_alembic_version_table)
        if has_alembic:
            # 路径 1: 已 in-Alembic 链 → alembic upgrade head（no-op 若已 head）
            await conn.run_sync(_alembic_upgrade_head)
        elif await conn.run_sync(_has_business_tables):
            # 路径 2: pre-Alembic legacy DB（W1 当前状态）→ stamp base + upgrade head
            # stamp base 标记到 migration 链起点之前，让 legacy DB 真正经历首个 migration
            await conn.run_sync(_alembic_stamp_base)
            await conn.run_sync(_alembic_upgrade_head)
        else:
            # 路径 3: 空库 / 测试 fixture → create_all + stamp head（快路径，跳过 migration 链）
            await conn.run_sync(Base.metadata.create_all)
            await conn.run_sync(_alembic_stamp_head)
    # WAL pragma 仍在外层（与原行为一致）
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


# === Alembic helpers (sync, called via conn.run_sync) ===


def _has_alembic_version_table(sync_conn) -> bool:
    """検測 alembic_version 表是否存在（sentinel #1: 已 in-Alembic 链）

    SQLite-specific: sqlite_master 是 SQLite 系统表；本 Iter 仅支持 SQLite。
    """
    result = sync_conn.execute(
        text("SELECT name FROM sqlite_master WHERE type='table' AND name='alembic_version'")
    )
    return result.scalar() is not None


def _has_business_tables(sync_conn) -> bool:
    """検測核心业务表是否存在（sentinel #2: pre-Alembic legacy DB vs 空库）

    用 sessions 作 sentinel（最早创建的核心表，所有 W1 DB 都有此表）。
    SQLite-specific: sqlite_master。
    """
    result = sync_conn.execute(
        text("SELECT name FROM sqlite_master WHERE type='table' AND name='sessions'")
    )
    return result.scalar() is not None


def _alembic_config(sync_conn) -> "Config":
    """構造 Alembic Config，路径锚定到 repo root（避免 cwd 依赖）"""
    from alembic.config import Config
    # database.py 在 src/storage/，parents[2] = repo root
    repo_root = Path(__file__).resolve().parents[2]
    cfg = Config(str(repo_root / "alembic.ini"))
    cfg.attributes["connection"] = sync_conn   # connection 注入
    return cfg


def _alembic_upgrade_head(sync_conn) -> None:
    """復用外层 conn 跑 upgrade head（路径 1 / 2 共用）"""
    from alembic import command
    command.upgrade(_alembic_config(sync_conn), "head")


def _alembic_stamp_head(sync_conn) -> None:
    """空库 create_all 后标记为 head（路径 3 快路径终点）"""
    from alembic import command
    command.stamp(_alembic_config(sync_conn), "head")


def _alembic_stamp_base(sync_conn) -> None:
    """pre-Alembic legacy DB 标记到 migration 链起点之前（路径 2 起点）

    后续 _alembic_upgrade_head 会从 base 跑全部 migration，包括 batch_alter 重建 decision_logs。
    """
    from alembic import command
    command.stamp(_alembic_config(sync_conn), "base")
