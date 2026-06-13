"""Migration test: b43e33764d90 rebuilds a stale v_alert_lifecycle on an existing DB.

仿 test_alembic_net_pnl_metrics.py（subprocess alembic + TRADEBOT_DB_URL，无 data/tradebot.db 依赖）。

视图 SQL 本身在 fresh DB 上的行为已由 test_v_alert_lifecycle.py 充分覆盖（array 触发 /
批量 / 双通道 / null-event）。本文件只补唯一未覆盖块：**迁移把 existing DB 上被冻结的
scalar-path 旧视图（生产实况）重建为 array-unnest + delivery 版本**——即漂移修复回归守护。
"""
from __future__ import annotations

import os
import subprocess
import sqlite3

import pytest


PRE_ITER_REV = "8c48305247c3"   # head before this view-rebuild migration (tool_call_result)


# 模拟生产 DB 冻结的 af87432ee6dd(net_pnl) 期 v_alert_lifecycle：标量 `$.type`/`$.alert_id`
# 路径（早于 #71 array-unnest），无 delivery 列 / 无 injected 分支。对数组格式
# trigger_context (`[{...}]`) 标量路径匹配 0 行 → 已触发 alert 被误判为 active。
_STALE_VIEW_SQL = """
CREATE VIEW v_alert_lifecycle AS
WITH registers AS (
  SELECT session_id, alert_id, created_at AS registered_at,
         price AS target_price, reasoning AS register_reasoning
  FROM trade_actions
  WHERE action='add_price_level_alert' AND alert_id IS NOT NULL
),
triggers AS (
  SELECT session_id,
         json_extract(trigger_context, '$.alert_id') AS alert_id,
         created_at AS triggered_at,
         CAST(json_extract(trigger_context, '$.current_price') AS REAL) AS triggered_price
  FROM agent_cycles
  WHERE triggered_by='alert'
    AND json_extract(trigger_context, '$.type')='price_level_alert'
    AND json_extract(trigger_context, '$.alert_id') IS NOT NULL
),
cancels AS (
  SELECT session_id, alert_id, created_at AS cancelled_at, reasoning AS cancel_reasoning
  FROM trade_actions
  WHERE action='cancel_price_level_alert' AND alert_id IS NOT NULL
)
SELECT r.session_id, r.alert_id, r.registered_at, r.target_price, r.register_reasoning,
       t.triggered_at, t.triggered_price, c.cancelled_at, c.cancel_reasoning,
       CASE WHEN t.triggered_at IS NOT NULL THEN 'triggered'
            WHEN c.cancelled_at IS NOT NULL THEN 'cancelled'
            ELSE 'active' END AS final_status
FROM registers r
LEFT JOIN triggers t ON t.session_id=r.session_id AND t.alert_id=r.alert_id
LEFT JOIN cancels  c ON c.session_id=r.session_id AND c.alert_id=r.alert_id
"""

# 数组格式 trigger_context（#71 起的实况）：旧视图标量路径取不到，新视图 json_each 命中。
_ARRAY_TRIGGER_CONTEXT = (
    '[{"type":"price_level_alert","alert_id":"drift01",'
    '"current_price":80050.0,"target_price":80000.0,"direction":"above"}]'
)


@pytest.fixture
async def head_db(tmp_path):
    """Bootstrap fresh DB at current head via init_db (Path 3 — auto-stamps head)."""
    from src.storage.database import init_db
    db_path = tmp_path / "v_alert_rebuild.db"
    db_url_async = f"sqlite+aiosqlite:///{db_path}"
    engine = await init_db(db_url_async)
    await engine.dispose()
    env = {**os.environ, "TRADEBOT_DB_URL": db_url_async}
    return str(db_path), env


def _insert_triggered_alert_array(conn: sqlite3.Connection) -> None:
    """注册一个 alert + 一个数组格式 trigger_context 的 alert-触发 cycle。"""
    conn.execute(
        "INSERT INTO sessions "
        "(id, name, symbol, initial_balance, status, created_at, updated_at, "
        " exchange_type, timeframe, scheduler_interval_min, approval_enabled, token_budget) "
        "VALUES ('drift', 'drift', 'BTC/USDT:USDT', 10000.0, 'active', "
        "'2026-01-01T00:00:00', '2026-01-01T00:00:00', 'simulated', '1h', 60, 0, 500000)"
    )
    conn.execute(
        "INSERT INTO trade_actions (session_id, action, symbol, alert_id, price, created_at) "
        "VALUES ('drift', 'add_price_level_alert', 'BTC/USDT:USDT', 'drift01', 80000.0, "
        "'2026-01-01T00:00:00')"
    )
    conn.execute(
        "INSERT INTO agent_cycles "
        "(session_id, cycle_id, triggered_by, trigger_context, tokens_consumed, created_at) "
        "VALUES ('drift', 'c2', 'alert', ?, 0, '2026-01-01T00:01:00')",
        (_ARRAY_TRIGGER_CONTEXT,),
    )
    conn.commit()


async def test_head_view_has_delivery_and_array_unnest(head_db):
    """At current head: v_alert_lifecycle DDL 含 delivery 列 + array-unnest(json_each) + injected 分支。"""
    db, _ = head_db
    conn = sqlite3.connect(db)
    sql = conn.execute(
        "SELECT sql FROM sqlite_master WHERE type='view' AND name='v_alert_lifecycle'"
    ).fetchone()[0]
    assert "delivery" in sql, f"head view should project delivery; got:\n{sql}"
    assert "json_each" in sql, "head view should unnest array trigger_context via json_each"
    assert "injected_events" in sql, "head view should include injected_events channel"


async def test_migration_rebuilds_stale_view_on_existing_db(head_db):
    """核心回归：existing DB 上冻结的 scalar-path 旧视图经 upgrade 重建为 array-unnest +
    delivery 版本，数组格式触发不再误报 active。"""
    db, env = head_db

    # 1. downgrade 一步到 PRE_ITER_REV（此处 fresh DB 视图本是当前版；下一步覆盖成 stale）
    subprocess.run(
        ["alembic", "downgrade", PRE_ITER_REV],
        check=True, env=env, capture_output=True,
    )

    # 2. 模拟生产漂移：换上 af87432ee6dd 期的 scalar-path 旧视图
    conn = sqlite3.connect(db)
    conn.execute("DROP VIEW IF EXISTS v_alert_lifecycle")
    conn.execute(_STALE_VIEW_SQL)
    conn.commit()

    # 3. 插数组格式 trigger_context 的已触发 alert
    _insert_triggered_alert_array(conn)

    # 4. bug 复现：旧视图标量路径对数组匹配 0 → 已触发 alert 误判 active
    stale_status = conn.execute(
        "SELECT final_status FROM v_alert_lifecycle WHERE alert_id='drift01'"
    ).fetchone()[0]
    assert stale_status == "active", (
        f"stale scalar-path view should MISS the array-format trigger (bug repro); got {stale_status}"
    )
    # 旧视图无 delivery 列 — 选它应抛错
    with pytest.raises(sqlite3.OperationalError):
        conn.execute("SELECT delivery FROM v_alert_lifecycle LIMIT 1")
    conn.close()

    # 5. forward upgrade head → 跑本迁移 upgrade()
    subprocess.run(["alembic", "upgrade", "head"], check=True, env=env, capture_output=True)

    # 6. 修复验证：array-path 命中 → triggered + delivery='wake'；DDL 含 delivery/json_each
    conn = sqlite3.connect(db)
    sql = conn.execute(
        "SELECT sql FROM sqlite_master WHERE type='view' AND name='v_alert_lifecycle'"
    ).fetchone()[0]
    assert "delivery" in sql and "json_each" in sql, "rebuilt view should be the current single-source SQL"
    fixed = conn.execute(
        "SELECT final_status, triggered_price, delivery "
        "FROM v_alert_lifecycle WHERE alert_id='drift01'"
    ).fetchone()
    assert fixed == ("triggered", 80050.0, "wake"), (
        f"rebuilt view should detect the array-format trigger; got {fixed}"
    )
    conn.close()


async def test_downgrade_keeps_view_queryable(head_db):
    """收敛式迁移：downgrade 到 PRE_ITER_REV 后视图仍可查（不回退到 stale，亦不留 dangling）。"""
    db, env = head_db
    subprocess.run(
        ["alembic", "downgrade", PRE_ITER_REV],
        check=True, env=env, capture_output=True,
    )
    conn = sqlite3.connect(db)
    # 视图存在且可 SELECT（downgrade 重建为当前单源，含 delivery）
    sql = conn.execute(
        "SELECT sql FROM sqlite_master WHERE type='view' AND name='v_alert_lifecycle'"
    ).fetchone()[0]
    assert "delivery" in sql, "convergence downgrade re-asserts current SQL (does not revert to stale)"
    conn.execute("SELECT * FROM v_alert_lifecycle LIMIT 1")  # 不抛即 OK
    conn.close()
