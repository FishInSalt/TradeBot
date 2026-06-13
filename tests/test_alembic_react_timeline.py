"""webui-cycle-react-timeline migration test (仿 test_alembic_net_pnl_metrics.py)."""
from __future__ import annotations

import os
import subprocess
import sqlite3

import pytest

PRE_ITER_REV = "b43e33764d90"   # alembic head before this iter（实查，见 plan Task 1 Step 3）


@pytest.fixture
async def head_db(tmp_path):
    from src.storage.database import init_db
    db_path = tmp_path / "react_timeline.db"
    db_url_async = f"sqlite+aiosqlite:///{db_path}"
    engine = await init_db(db_url_async)
    await engine.dispose()
    env = {**os.environ, "TRADEBOT_DB_URL": db_url_async}
    return str(db_path), env


@pytest.fixture
async def pre_iter_head_db(tmp_path):
    from src.storage.database import init_db
    db_path = tmp_path / "pre_iter_react.db"
    db_url_async = f"sqlite+aiosqlite:///{db_path}"
    engine = await init_db(db_url_async)
    await engine.dispose()
    env = {**os.environ, "TRADEBOT_DB_URL": db_url_async}
    subprocess.run(["alembic", "downgrade", PRE_ITER_REV], check=True, env=env, capture_output=True)
    return str(db_path), env


async def test_head_has_react_steps_and_tool_call_id(head_db):
    db, _ = head_db
    conn = sqlite3.connect(db)
    ac_cols = {row[1] for row in conn.execute("PRAGMA table_info(agent_cycles)")}
    tc_cols = {row[1] for row in conn.execute("PRAGMA table_info(tool_calls)")}
    assert "react_steps" in ac_cols, f"react_steps missing; have {sorted(ac_cols)}"
    assert "tool_call_id" in tc_cols, f"tool_call_id missing; have {sorted(tc_cols)}"


async def test_upgrade_preserves_legacy_null(pre_iter_head_db):
    db, env = pre_iter_head_db
    conn = sqlite3.connect(db)
    ac_before = {row[1] for row in conn.execute("PRAGMA table_info(agent_cycles)")}
    assert "react_steps" not in ac_before
    # legacy session + cycle at pre-iter schema
    conn.execute("""
        INSERT INTO sessions
        (id, name, symbol, initial_balance, status, created_at, updated_at,
         exchange_type, timeframe, scheduler_interval_min, approval_enabled, token_budget)
        VALUES ('legacy-test', 'legacy', 'BTC/USDT:USDT', 10000.0, 'active',
                '2026-01-01T00:00:00', '2026-01-01T00:00:00',
                'simulated', '15m', 15, 1, 500000)
    """)
    # raw sqlite3 INSERT 不套 ORM default：agent_cycles 的 NOT NULL 且无 server_default 列
    # 必须显式给值——tokens_consumed(models.py, default=0 仅 Python 端) 是其一，漏给会
    # IntegrityError。execution_status 有 server_default="ok" 故可省，此处仍显式给。
    conn.execute(
        "INSERT INTO agent_cycles (session_id, cycle_id, triggered_by, tokens_consumed, execution_status, created_at) "
        "VALUES ('legacy-test', 'c1', 'scheduled', 0, 'ok', '2026-01-01T00:00:00')"
    )
    conn.commit()
    conn.close()

    subprocess.run(["alembic", "upgrade", "head"], check=True, env=env, capture_output=True)

    conn = sqlite3.connect(db)
    ac_after = {row[1] for row in conn.execute("PRAGMA table_info(agent_cycles)")}
    assert "react_steps" in ac_after
    legacy = conn.execute(
        "SELECT react_steps FROM agent_cycles WHERE session_id='legacy-test'"
    ).fetchone()
    assert legacy == (None,), f"legacy row should preserve NULL (no backfill); got {legacy}"


async def test_downgrade_drops_columns_and_restores_view(head_db):
    db, env = head_db
    subprocess.run(["alembic", "downgrade", PRE_ITER_REV], check=True, env=env, capture_output=True)
    conn = sqlite3.connect(db)
    ac_cols = {row[1] for row in conn.execute("PRAGMA table_info(agent_cycles)")}
    tc_cols = {row[1] for row in conn.execute("PRAGMA table_info(tool_calls)")}
    assert "react_steps" not in ac_cols, f"downgrade should drop react_steps; cols: {sorted(ac_cols)}"
    assert "tool_call_id" not in tc_cols, f"downgrade should drop tool_call_id; cols: {sorted(tc_cols)}"
    # v_cycle_metrics 必须在 downgrade 后仍可查（view 已重建）
    cnt = conn.execute("SELECT count(*) FROM v_cycle_metrics").fetchone()[0]
    assert cnt == 0  # 空库可查即证明 view 重建成功
