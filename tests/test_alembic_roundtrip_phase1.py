"""AC-1: alembic upgrade + downgrade roundtrip — 9 列 add/drop + 3 view create/drop.

Bootstrap 策略：用 init_db (Path 3 = Base.metadata.create_all + _apply_views + stamp head)
直接到 Phase 1 head，避开第一 migration 假设 W1-like fixture 的限制
（fresh DB 跑 alembic upgrade 会因 "no such index ix_sim_orders_session_status" 失败）。

测试覆盖：head → downgrade -1 → R2-7 → upgrade head → Phase 1 head 单步 roundtrip。
对 column 和 view 都断言（PR #42 review fix）。
"""
import os
import subprocess
import sqlite3

import pytest


PHASE1_REV = "61ac4841a55d"
PREV_REV = "eeeee565cb36"
EXPECTED_VIEWS = {"v_cycle_metrics", "v_alert_lifecycle", "v_order_lifecycle"}


def _query_views(db_path: str) -> set[str]:
    conn = sqlite3.connect(db_path)
    return {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='view'")}


@pytest.fixture
async def head_db(tmp_path):
    """Bootstrap fresh DB at current head via init_db (Path 3)."""
    from src.storage.database import init_db
    db_path = tmp_path / "roundtrip.db"
    db_url_async = f"sqlite+aiosqlite:///{db_path}"
    engine = await init_db(db_url_async)
    await engine.dispose()
    env = {**os.environ, "TRADEBOT_DB_URL": db_url_async}
    return str(db_path), env


@pytest.fixture
async def phase1_head_db(tmp_path):
    """Bootstrap fresh DB then explicitly downgrade to Phase 1 head.

    Tests that assert ``downgrade -1`` removes Phase 1 columns/views must
    start from Phase 1 head, not from whatever the current head happens to
    be.  This fixture walks from current head (which may have more migrations
    on top of Phase 1 in future iters) down to PHASE1_REV first, so the
    subsequent ``alembic downgrade -1`` in the test body correctly steps
    Phase 1 → R2-7.  Pattern is forward-compatible with future migrations.
    """
    from src.storage.database import init_db
    db_path = tmp_path / "phase1_roundtrip.db"
    db_url_async = f"sqlite+aiosqlite:///{db_path}"
    engine = await init_db(db_url_async)
    await engine.dispose()
    env = {**os.environ, "TRADEBOT_DB_URL": db_url_async}
    subprocess.run(
        ["alembic", "downgrade", PHASE1_REV],
        check=True, env=env, capture_output=True,
    )
    return str(db_path), env


async def test_head_has_8_agent_cycles_columns(head_db):
    """T5.1: init_db Path 3 后 agent_cycles 含 Phase 1 8 新列。"""
    db, _ = head_db
    conn = sqlite3.connect(db)
    cols = {row[1] for row in conn.execute("PRAGMA table_info(agent_cycles)")}
    expected = {"wall_time_ms", "llm_call_ms", "input_tokens", "output_tokens",
                "cache_read_tokens", "cache_write_tokens", "reasoning_tokens",
                "cache_hit_rate"}
    assert expected.issubset(cols), f"missing: {expected - cols}"


async def test_head_has_trade_actions_alert_id(head_db):
    """T5.2: init_db Path 3 后 trade_actions 含 alert_id 列。"""
    db, _ = head_db
    conn = sqlite3.connect(db)
    cols = {row[1] for row in conn.execute("PRAGMA table_info(trade_actions)")}
    assert "alert_id" in cols


async def test_head_has_3_views(head_db):
    """T5.5 (PR #42 fix): init_db Path 3 后 3 个 view 可用 (production fresh-DB
    path 之前漏建)。"""
    db, _ = head_db
    views = _query_views(db)
    assert EXPECTED_VIEWS.issubset(views), f"missing views: {EXPECTED_VIEWS - views}"


async def test_downgrade_drops_phase1_columns(phase1_head_db):
    """T5.3: downgrade -1 后 Phase 1 9 列全消失（roundtrip clean）。"""
    db, env = phase1_head_db
    subprocess.run(["alembic", "downgrade", "-1"], check=True, env=env, capture_output=True)

    conn = sqlite3.connect(db)
    ac_cols = {row[1] for row in conn.execute("PRAGMA table_info(agent_cycles)")}
    ta_cols = {row[1] for row in conn.execute("PRAGMA table_info(trade_actions)")}

    assert "wall_time_ms" not in ac_cols
    assert "llm_call_ms" not in ac_cols
    assert "input_tokens" not in ac_cols
    assert "cache_hit_rate" not in ac_cols
    assert "alert_id" not in ta_cols


async def test_downgrade_drops_views(phase1_head_db):
    """T5.6 (PR #42 fix): downgrade -1 后 3 view 全 drop。"""
    db, env = phase1_head_db
    subprocess.run(["alembic", "downgrade", "-1"], check=True, env=env, capture_output=True)
    views = _query_views(db)
    assert not (EXPECTED_VIEWS & views), f"views still exist after downgrade: {EXPECTED_VIEWS & views}"


async def test_upgrade_idempotent_after_downgrade(head_db):
    """T5.4: down → up 二次 roundtrip 仍可上 head（防 alembic state 污染）+ view 重建。"""
    db, env = head_db
    subprocess.run(["alembic", "downgrade", "-1"], check=True, env=env, capture_output=True)
    subprocess.run(["alembic", "upgrade", "head"], check=True, env=env, capture_output=True)

    conn = sqlite3.connect(db)
    cols = {row[1] for row in conn.execute("PRAGMA table_info(agent_cycles)")}
    assert "wall_time_ms" in cols   # 二次 upgrade 仍生效

    views = _query_views(db)
    assert EXPECTED_VIEWS.issubset(views), f"views not restored: {EXPECTED_VIEWS - views}"


async def test_head_has_tool_calls_result(head_db):
    """init_db Path 3 后 tool_calls 含 result 列。"""
    db, _ = head_db
    conn = sqlite3.connect(db)
    cols = {row[1] for row in conn.execute("PRAGMA table_info(tool_calls)")}
    assert "result" in cols


async def test_downgrade_drops_result_keeps_views(head_db):
    """downgrade -1（head → 7244c7b7185d）删 result 列、且 3 个 view 仍在
    （downgrade 的 DROP VIEW → drop_column → 重建 view 舞蹈正确性）。"""
    db, env = head_db
    subprocess.run(["alembic", "downgrade", "-1"], check=True, env=env, capture_output=True)
    conn = sqlite3.connect(db)
    cols = {row[1] for row in conn.execute("PRAGMA table_info(tool_calls)")}
    assert "result" not in cols
    views = _query_views(db)
    assert EXPECTED_VIEWS.issubset(views), f"views not restored by downgrade: {EXPECTED_VIEWS - views}"
