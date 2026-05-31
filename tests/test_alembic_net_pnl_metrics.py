"""Iter net-pnl-metrics migration test (仿 test_alembic_p4.py pattern, no data/tradebot.db dep)."""
from __future__ import annotations

import os
import subprocess
import sqlite3

import pytest


PRE_ITER_REV = "4ee6c95d0430"   # alembic head before this iter (P4 prompt snapshot)


@pytest.fixture
async def head_db(tmp_path):
    """Bootstrap fresh DB at current head via init_db (Path 3 — auto-stamps head)."""
    from src.storage.database import init_db
    db_path = tmp_path / "net_pnl_metrics.db"
    db_url_async = f"sqlite+aiosqlite:///{db_path}"
    engine = await init_db(db_url_async)
    await engine.dispose()
    env = {**os.environ, "TRADEBOT_DB_URL": db_url_async}
    return str(db_path), env


@pytest.fixture
async def pre_iter_head_db(tmp_path):
    """Bootstrap fresh DB then explicitly downgrade to pre-iter head — forward
    upgrade exercises this iter's migration.upgrade() path."""
    from src.storage.database import init_db
    db_path = tmp_path / "pre_iter_net_pnl.db"
    db_url_async = f"sqlite+aiosqlite:///{db_path}"
    engine = await init_db(db_url_async)
    await engine.dispose()
    env = {**os.environ, "TRADEBOT_DB_URL": db_url_async}
    subprocess.run(
        ["alembic", "downgrade", PRE_ITER_REV],
        check=True, env=env, capture_output=True,
    )
    return str(db_path), env


async def test_head_has_entry_price_amount_columns(head_db):
    """At current head: trade_actions 有 entry_price + amount 两列."""
    db, _ = head_db
    conn = sqlite3.connect(db)
    cols = {row[1] for row in conn.execute("PRAGMA table_info(trade_actions)")}
    assert "entry_price" in cols, f"entry_price missing; have {sorted(cols)}"
    assert "amount" in cols, f"amount missing; have {sorted(cols)}"


async def test_head_view_uses_pnl_pct_of_notional_path(head_db):
    """v_cycle_metrics DDL 引用 $.position.pnl_pct_of_notional."""
    db, _ = head_db
    conn = sqlite3.connect(db)
    sql = conn.execute(
        "SELECT sql FROM sqlite_master WHERE type='view' AND name='v_cycle_metrics'"
    ).fetchone()[0]
    assert "pnl_pct_of_notional" in sql, (
        f"view DDL should reference pnl_pct_of_notional; got:\n{sql}"
    )


async def test_upgrade_preserves_pre_iter_legacy_rows(pre_iter_head_db):
    """Real `alembic upgrade head`: pre-iter trade_actions 行 entry_price/amount=NULL after migration.

    Verifies upgrade() runs forward + does NOT backfill (spec §6.11 by design).
    """
    db, env = pre_iter_head_db

    # Pre-condition: at PRE_ITER_REV, trade_actions has no entry_price/amount cols
    conn = sqlite3.connect(db)
    cols_before = {row[1] for row in conn.execute("PRAGMA table_info(trade_actions)")}
    assert "amount" not in cols_before, (
        f"Pre-condition: amount should not exist at pre-iter head; cols: {sorted(cols_before)}"
    )
    assert "entry_price" not in cols_before

    # Insert legacy row at pre-iter schema (no new columns).
    # Must enumerate all 12 NOT NULL Session cols (Python defaults bypass via raw SQL;
    # per tests/test_alembic_migration.py:262 pattern).
    conn.execute("""
        INSERT INTO sessions
        (id, name, symbol, initial_balance, status, created_at, updated_at,
         exchange_type, timeframe, scheduler_interval_min, approval_enabled, token_budget)
        VALUES ('legacy-test', 'legacy', 'BTC/USDT:USDT', 10000.0, 'active',
                '2026-01-01T00:00:00', '2026-01-01T00:00:00',
                'simulated', '15m', 15, 1, 500000)
    """)
    # trade_actions: session_id + action + symbol NOT NULL; created_at default _utcnow Python-side
    conn.execute(
        "INSERT INTO trade_actions (session_id, action, symbol, price, pnl, fee, created_at) "
        "VALUES ('legacy-test', 'order_filled', 'BTC/USDT:USDT', 50000.0, 10.0, 0.25, '2026-01-01T00:00:00')"
    )
    conn.commit()
    conn.close()

    # Forward upgrade — runs this iter's migration.upgrade()
    subprocess.run(["alembic", "upgrade", "head"], check=True, env=env, capture_output=True)

    # Post-condition: new columns exist, legacy row has NULL
    conn = sqlite3.connect(db)
    cols_after = {row[1] for row in conn.execute("PRAGMA table_info(trade_actions)")}
    assert "amount" in cols_after, f"upgrade did not add amount; cols: {sorted(cols_after)}"
    assert "entry_price" in cols_after, f"upgrade did not add entry_price; cols: {sorted(cols_after)}"

    legacy = conn.execute(
        "SELECT entry_price, amount FROM trade_actions WHERE session_id='legacy-test'"
    ).fetchone()
    assert legacy == (None, None), (
        f"legacy row should preserve NULL after upgrade (no backfill); got {legacy}"
    )

    # v_cycle_metrics view must be present after upgrade (rebuilt by migration)
    view_sql = conn.execute(
        "SELECT sql FROM sqlite_master WHERE type='view' AND name='v_cycle_metrics'"
    ).fetchone()[0]
    assert "pnl_pct_of_notional" in view_sql, "view should reference new JSON path after upgrade"


async def test_downgrade_drops_new_columns_and_restores_view(head_db):
    """alembic downgrade to pre-iter rev: 2 columns removed; view recreated with
    pre-iter JSON path.

    Downgrades to PRE_ITER_REV (af87432ee6dd's down_revision) rather than relative
    `-1`: later migrations may stack on top of net_pnl_metrics (e.g. the
    sim-exec-cs contract_size column), so `-1` would only undo the topmost
    migration and leave entry_price/amount intact. Targeting the explicit
    pre-iter rev keeps this test pinned to the net_pnl_metrics downgrade regardless
    of head.
    """
    db, env = head_db
    subprocess.run(["alembic", "downgrade", PRE_ITER_REV], check=True, env=env, capture_output=True)

    conn = sqlite3.connect(db)
    cols = {row[1] for row in conn.execute("PRAGMA table_info(trade_actions)")}
    assert "amount" not in cols, f"downgrade should drop amount; cols: {sorted(cols)}"
    assert "entry_price" not in cols

    view_sql = conn.execute(
        "SELECT sql FROM sqlite_master WHERE type='view' AND name='v_cycle_metrics'"
    ).fetchone()[0]
    assert "pnl_pct_of_notional" not in view_sql, (
        "downgrade should restore pre-iter view DDL (no pnl_pct_of_notional)"
    )
    assert "$.position.pnl_pct'" in view_sql, "downgrade should restore '$.position.pnl_pct' JSON path"
