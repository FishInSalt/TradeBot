"""Alembic environment — async engine + connection injection + path normalization.

Spec §4.3 / §3.2.
"""
from __future__ import annotations

import os
from logging.config import fileConfig
from pathlib import Path

from sqlalchemy import create_engine, pool
from alembic import context

from src.storage.models import Base   # 必需：autogenerate / alembic check 依赖

# === 顶层 module setup ===
config = context.config
# fileConfig 重置 root logger 级别，打断 pytest caplog 等 logging capture 工具。
# 仅在 CLI 直调路径（无 connection 注入）时才配置 logging；programmatic 路径跳过，
# 保留调用者的 logging 状态不变。
# disable_existing_loggers=False: 默认值 True 会清空 pytest caplog 的 handler，
# 即使本路径仅 CLI 触发，migration 测试用 alembic_cfg_factory 不注入 connection
# 也走此分支 → 全套测试 caplog-用户测试会被 wipe handler 后失败（PR #28 review 发现）
if config.config_file_name is not None and config.attributes.get("connection") is None:
    fileConfig(config.config_file_name, disable_existing_loggers=False)

# 关键：必须显式赋值，autogenerate / alembic check 全靠这一行
target_metadata = Base.metadata


def _resolved_sync_url() -> str:
    """CLI 直调路径：从 env var 或 settings 派生 sync URL，路径锚定到 repo root（与 app.py:434-438 normalization 一致）"""
    from src.config import load_settings
    # 优先 env var（CI override / ad-hoc 测试路径；实测 load_settings 不处理 database.url env_overrides 故必走此路径）
    env_url = os.getenv("TRADEBOT_DB_URL")
    if env_url:
        async_url = env_url
    else:
        # env_overrides={} 跳过 dotenv 读取（alembic 上下文不需要 OKX_* env vars）
        # path 锚定到 repo_root（避免 alembic CLI 从非 repo_root 启动时 cwd-relative path 失败）
        repo_root = Path(__file__).resolve().parents[1]
        async_url = load_settings(
            path=repo_root / "config" / "settings.yaml",
            env_overrides={},
        ).database.url
    # 同步化 (sqlite+aiosqlite → sqlite)
    sync_url = async_url.replace("sqlite+aiosqlite:", "sqlite:")
    # Path normalization: 相对路径 → 绝对路径（锚定 repo root，alembic/env.py 在 repo_root/alembic/env.py）
    if sync_url.startswith("sqlite:///") and not sync_url.startswith("sqlite:////"):
        relative_path = sync_url[len("sqlite:///"):]
        if not Path(relative_path).is_absolute():
            repo_root = Path(__file__).resolve().parents[1]   # alembic/env.py → parents[1] = repo root
            sync_url = f"sqlite:///{(repo_root / relative_path).as_posix()}"
    return sync_url


def run_migrations_online() -> None:
    # 优先读 init_db 注入的 connection；CLI 直调 (alembic upgrade head) 时为 None
    connectable = config.attributes.get("connection", None)
    if connectable is None:
        # CLI 直调路径：自建 engine + connection（path normalization 见 _resolved_sync_url）
        sync_engine = create_engine(_resolved_sync_url(), poolclass=pool.NullPool)
        with sync_engine.begin() as conn:    # 外层开 transaction，alembic 内层 nullcontext 共享 (Round 13)
            do_run_migrations(conn)
    else:
        # init_db 注入路径：复用外层 sync_conn
        do_run_migrations(connectable)


def do_run_migrations(connection) -> None:
    context.configure(
        connection=connection,
        target_metadata=target_metadata,
        # render_as_batch=True 让 SQLite 自动用 batch_alter（add column 等不需，alter type 必需）
        render_as_batch=True,
    )
    # alembic 检测外层 transaction (init_db engine.begin() 或 CLI sync_engine.begin()) → nullcontext → 共享
    with context.begin_transaction():
        context.run_migrations()


# === 入口 ===
if context.is_offline_mode():
    # offline 模式（生成 SQL 脚本不实际运行）— 简化版，本项目 production 不用 offline
    raise NotImplementedError("Offline mode not supported; use online migrations")
else:
    run_migrations_online()
