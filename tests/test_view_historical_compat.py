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
async def test_v_alert_lifecycle_filters_historical(db_engine_with_real_db):
    """T19.2: v_alert_lifecycle 在历史 DB 上返回 0 行（NULL alert_id 全过滤）。

    sim #1-#8 历史 trade_actions 的 alert_id 列在本 iter 之前不存在；
    upgrade 后该列是 NULL，被 WHERE alert_id IS NOT NULL 过滤掉。
    """
    async with db_engine_with_real_db.connect() as conn:
        result = await conn.execute(text(
            "SELECT COUNT(*) AS c FROM v_alert_lifecycle"
        ))
        count = result.scalar_one()

    assert count == 0


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
    """T19.4: 历史 cycle 的 8 新列全 NULL（不破坏 view 但 cache_hit_rate_derived 也 NULL）。"""
    async with db_engine_with_real_db.connect() as conn:
        result = await conn.execute(text(
            "SELECT cycle_id, wall_time_ms, input_tokens, cache_hit_rate_derived "
            "FROM v_cycle_metrics "
            "WHERE created_at < '2026-05-09'"
            " LIMIT 5"
        ))
        rows = result.mappings().all()

    for row in rows:
        assert row["wall_time_ms"] is None
        assert row["input_tokens"] is None
        assert row["cache_hit_rate_derived"] is None
