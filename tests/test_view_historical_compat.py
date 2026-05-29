"""AC-8: 历史 sim 数据兼容性 — sim #1-#8 在三个 view 上 SELECT * 不 raise.

历史数据特征：
- agent_cycles 8 新列全 NULL（P1+P2 列在本 iter 加；老数据没有源头）
- trade_actions.alert_id 全 NULL（X 方案前老 trade_actions 没此列）
- trigger_context JSON 全无 alert_id key（PriceLevelAlertInfo 加字段是本 iter）
- v_alert_lifecycle 完全过滤掉历史 alert（IS NOT NULL filter）
- v_cycle_metrics / v_order_lifecycle 应能完整 SELECT
"""
import pytest
from sqlalchemy import text


@pytest.mark.asyncio
async def test_v_cycle_metrics_select_on_historical(db_engine_with_real_db):
    """T19.1: 现有 data/tradebot.db SELECT * FROM v_cycle_metrics 不 raise。"""
    async with db_engine_with_real_db.connect() as conn:
        result = await conn.execute(text(
            "SELECT * FROM v_cycle_metrics LIMIT 10"
        ))
        rows = result.mappings().all()

    for row in rows:
        assert "wall_time_ms" in row
        assert "cache_hit_rate_derived" in row


@pytest.mark.asyncio
async def test_v_alert_lifecycle_filters_null_alert_id(db_engine_with_real_db):
    """T19.2: v_alert_lifecycle 不泄漏 NULL alert_id 行（registers CTE IS NOT NULL 过滤）。

    sim #1-#8 历史 trade_actions 的 alert_id 列在本 iter 之前不存在；upgrade 后该列
    是 NULL，被 registers CTE 的 `WHERE alert_id IS NOT NULL` 过滤，不进 view。

    断言 view 内每行 alert_id 均非 NULL —— 此为 schema-level 不变量，恒成立。
    （不用 `COUNT(*)==0`：那依赖"本地 DB 恰无带 alert_id 的 run"这一脆弱快照假设，
    X 方案后新 sim run 的 alert_id 非 NULL，会合法地让总行数 > 0 而误判失败。）
    """
    async with db_engine_with_real_db.connect() as conn:
        result = await conn.execute(text(
            "SELECT COUNT(*) AS c FROM v_alert_lifecycle WHERE alert_id IS NULL"
        ))
        null_leak = result.scalar_one()

    assert null_leak == 0


@pytest.mark.asyncio
async def test_v_order_lifecycle_select_on_historical(db_engine_with_real_db):
    """T19.3: 历史 sim_orders 在 v_order_lifecycle 上 SELECT 不 raise。"""
    async with db_engine_with_real_db.connect() as conn:
        result = await conn.execute(text(
            "SELECT * FROM v_order_lifecycle LIMIT 10"
        ))
        rows = result.mappings().all()

    for row in rows:
        if row["order_type"] == "limit":
            assert row["trigger_drift_pct"] is None


@pytest.mark.asyncio
async def test_v_cycle_metrics_historical_8_new_cols_null(db_engine_with_real_db):
    """T19.4: 历史 cycle 的 8 新列全 NULL（不破坏 view 但 cache_hit_rate_derived 也 NULL）。

    PR #42 review v4 I-1 修订: 用 wall_time_ms IS NULL 作 invariant 而非
    硬编码日期 — 后者今后任何 sim 写入都会让 filter 漂移失效。
    """
    async with db_engine_with_real_db.connect() as conn:
        result = await conn.execute(text(
            "SELECT cycle_id, wall_time_ms, input_tokens, cache_hit_rate_derived "
            "FROM v_cycle_metrics "
            "WHERE wall_time_ms IS NULL "       # invariant: 没 Phase 1 instrumentation 的 row
            "LIMIT 5"
        ))
        rows = result.mappings().all()

    for row in rows:
        assert row["wall_time_ms"] is None
        assert row["input_tokens"] is None
        assert row["cache_hit_rate_derived"] is None
