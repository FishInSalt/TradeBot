"""AC-7: v_order_lifecycle lifetime_seconds / trigger_drift_pct / originated_cycle_id 派生。"""
from datetime import datetime, timezone, timedelta

import pytest
from sqlalchemy import text

from src.storage.models import SimOrder, TradeAction


@pytest.mark.asyncio
async def test_order_lifecycle_lifetime_seconds(db_session):
    """T18.1: filled_at - created_at = lifetime_seconds（julianday 派生）。

    Use clean second-aligned timestamps to avoid julianday float→int CAST 精度
    丢失（datetime.now() 含微秒会让 julianday * 86400 在边界处取 14 而非 15）。
    """
    base = datetime(2026, 5, 1, 12, 0, 0, tzinfo=timezone.utc)
    db_session.add(SimOrder(
        session_id="test-lifetime",
        order_id="order00001",
        symbol="BTC/USDT:USDT", side="buy", position_side="long",
        order_type="market", amount=0.01,
        status="filled", filled_price=80000.0,
        created_at=base,
        filled_at=base + timedelta(seconds=15),
    ))
    await db_session.commit()

    row = (await db_session.execute(text(
        "SELECT lifetime_seconds FROM v_order_lifecycle WHERE order_id='order00001'"
    ))).mappings().one()

    assert row["lifetime_seconds"] == 15


@pytest.mark.asyncio
async def test_order_lifecycle_trigger_drift_pct_signed(db_session):
    """T18.2: stop order 的 trigger_drift_pct 是 signed 浮点（保正负号）。"""
    db_session.add(SimOrder(
        session_id="test-drift-stop",
        order_id="order00002",
        symbol="BTC/USDT:USDT", side="sell", position_side="long",
        order_type="stop", amount=0.01,
        trigger_price=80000.0, filled_price=79900.0,    # 滑点 -0.125%
        status="filled",
    ))
    await db_session.commit()

    row = (await db_session.execute(text(
        "SELECT trigger_drift_pct FROM v_order_lifecycle WHERE order_id='order00002'"
    ))).mappings().one()

    assert row["trigger_drift_pct"] == pytest.approx(-0.125, abs=1e-3)


@pytest.mark.asyncio
async def test_order_lifecycle_drift_pct_null_for_limit(db_session):
    """T18.3: limit order trigger_drift_pct = NULL（filter 掉结构性恒 0 噪音）。"""
    db_session.add(SimOrder(
        session_id="test-drift-limit",
        order_id="order00003",
        symbol="BTC/USDT:USDT", side="buy", position_side="long",
        order_type="limit", amount=0.01,
        trigger_price=80000.0, filled_price=80000.0,
        status="filled",
    ))
    await db_session.commit()

    row = (await db_session.execute(text(
        "SELECT trigger_drift_pct FROM v_order_lifecycle WHERE order_id='order00003'"
    ))).mappings().one()

    assert row["trigger_drift_pct"] is None


@pytest.mark.asyncio
async def test_order_lifecycle_originated_cycle_id(db_session):
    """T18.4: originated_cycle_id 取最早创建 cycle（按 trade_actions.created_at LIMIT 1）。"""
    earlier = datetime(2026, 5, 1, tzinfo=timezone.utc)
    later = datetime(2026, 5, 2, tzinfo=timezone.utc)

    db_session.add(SimOrder(
        session_id="test-origin",
        order_id="order00004",
        symbol="BTC/USDT:USDT", side="buy", position_side="long",
        order_type="market", amount=0.01, status="filled",
        filled_price=80000.0,
    ))
    db_session.add(TradeAction(
        session_id="test-origin",
        cycle_id="orig_cycle",
        action="open_position", order_id="order00004",
        symbol="BTC/USDT:USDT", side="long",
        created_at=earlier,
    ))
    db_session.add(TradeAction(
        session_id="test-origin",
        cycle_id="cancel_cycle",
        action="cancel_order", order_id="order00004",
        symbol="BTC/USDT:USDT",
        created_at=later,
    ))
    await db_session.commit()

    row = (await db_session.execute(text(
        "SELECT originated_cycle_id FROM v_order_lifecycle WHERE order_id='order00004'"
    ))).mappings().one()

    assert row["originated_cycle_id"] == "orig_cycle"
