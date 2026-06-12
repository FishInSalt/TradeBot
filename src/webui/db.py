"""只读 SQLite 连接（mode=ro）。spike 实测可读 live WAL 库的未 checkpoint 帧；
禁用 immutable（会返回陈旧数据）。见 spec §3。"""
from __future__ import annotations

import os

from sqlalchemy import event
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine


def make_readonly_engine(db_path: str) -> AsyncEngine:
    """指向 db_path 的只读 async engine。mode=ro + busy_timeout + query_only。

    db_path: SQLite 文件绝对/相对路径（非 URL）。不调 init_db、不跑 migration。
    """
    abspath = os.path.abspath(db_path)
    url = f"sqlite+aiosqlite:///file:{abspath}?mode=ro&uri=true"
    engine = create_async_engine(url, echo=False)

    @event.listens_for(engine.sync_engine, "connect")
    def _set_pragmas(dbapi_conn, _record):  # noqa: ANN001
        cur = dbapi_conn.cursor()
        cur.execute("PRAGMA busy_timeout=3000")
        cur.execute("PRAGMA query_only=ON")
        cur.close()

    return engine
