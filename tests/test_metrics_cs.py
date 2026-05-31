# tests/test_metrics_cs.py
"""Task 8 (B2): MetricsService FIFO pnl_gross ×cs from sessions.contract_size.

Fixtures seed sessions.contract_size=0.01 to verify that a single long roundtrip
of 10 contracts at 100000→101000 yields gross = (1000) × (10 × 0.01) = 100.0.

Also verifies that sessions.contract_size=NULL falls back to 1.0 (legacy rows).
"""
from __future__ import annotations

import pytest
from sqlalchemy import text

from src.storage.database import init_db, get_session
from src.storage.models import Session as SessionModel, TradeAction


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
async def metrics_engine_cs_001(tmp_path):
    """Fresh engine with a session seeded: contract_size=0.01, fee_rate=0.0005.

    Single long roundtrip:
      open  10 contracts @ 100000  (pnl=None → open fill)
      close 10 contracts @ 101000  (pnl IS NOT NULL → close fill)

    Expected with cs=0.01:
      consumed = 10 contracts
      gross = (101000 - 100000) × 10 × 0.01 × sign(+1) = 100.0
    """
    engine = await init_db(f"sqlite+aiosqlite:///{tmp_path}/metrics_cs_001.db")
    sid = "cs-001"
    fee_rate = 0.0005
    amount = 10.0   # contracts
    entry_price = 100000.0
    exit_price = 101000.0
    fee_open = entry_price * amount * fee_rate      # 500.0
    fee_close = exit_price * amount * fee_rate      # 505.0

    async with engine.begin() as conn:
        await conn.execute(text(
            "INSERT INTO sessions "
            "(id, name, symbol, initial_balance, status, created_at, updated_at, "
            " exchange_type, timeframe, scheduler_interval_min, approval_enabled, "
            " token_budget, fee_rate, contract_size) "
            "VALUES (:sid, :sid, 'BTC/USDT:USDT', 10000.0, 'active', "
            "        '2026-01-01 00:00:00', '2026-01-01 00:00:00', "
            "        'simulated', '15m', 15, 1, 500000, :fr, :cs)"
        ), {"sid": sid, "fr": fee_rate, "cs": 0.01})

        # Open fill: pnl=NULL → FIFO treats as open lot
        await conn.execute(text(
            "INSERT INTO trade_actions "
            "(session_id, action, symbol, side, trigger_reason, price, "
            " pnl, fee, amount, entry_price, order_id, created_at) "
            "VALUES (:sid, 'order_filled', 'BTC/USDT:USDT', 'long', 'market', :px, "
            "        NULL, :fee, :amt, NULL, 'o-open', '2026-01-01 00:00:00')"
        ), {"sid": sid, "px": entry_price, "fee": fee_open, "amt": amount})

        # Close fill: pnl IS NOT NULL → FIFO treats as close
        # sign=+1 (long), gross = (101000-100000)*10*1 = 10000 without cs
        # gross = 10000 * 0.01 = 100.0 with cs=0.01
        close_pnl = (exit_price - entry_price) * amount  # 10000.0 (stored for reference; FIFO ignores)
        await conn.execute(text(
            "INSERT INTO trade_actions "
            "(session_id, action, symbol, side, trigger_reason, price, "
            " pnl, fee, amount, entry_price, order_id, created_at) "
            "VALUES (:sid, 'order_filled', 'BTC/USDT:USDT', 'long', 'market', :px, "
            "        :pnl, :fee, :amt, :ep, 'o-close', '2026-01-01 00:00:01')"
        ), {"sid": sid, "px": exit_price, "pnl": close_pnl, "fee": fee_close,
            "amt": amount, "ep": entry_price})

    yield engine
    await engine.dispose()


@pytest.fixture
async def metrics_engine_cs_null(tmp_path):
    """Session with contract_size=NULL → fallback to 1.0.

    Same trade: 10 contracts @ 100000 → 101000.
    With cs=1.0: gross = (101000-100000) × 10 × 1.0 = 10000.0
    """
    engine = await init_db(f"sqlite+aiosqlite:///{tmp_path}/metrics_cs_null.db")
    sid = "cs-null"
    fee_rate = 0.0005
    amount = 10.0
    entry_price = 100000.0
    exit_price = 101000.0
    fee_open = entry_price * amount * fee_rate
    fee_close = exit_price * amount * fee_rate

    async with engine.begin() as conn:
        await conn.execute(text(
            "INSERT INTO sessions "
            "(id, name, symbol, initial_balance, status, created_at, updated_at, "
            " exchange_type, timeframe, scheduler_interval_min, approval_enabled, "
            " token_budget, fee_rate, contract_size) "
            "VALUES (:sid, :sid, 'BTC/USDT:USDT', 10000.0, 'active', "
            "        '2026-01-01 00:00:00', '2026-01-01 00:00:00', "
            "        'simulated', '15m', 15, 1, 500000, :fr, NULL)"
        ), {"sid": sid, "fr": fee_rate})

        await conn.execute(text(
            "INSERT INTO trade_actions "
            "(session_id, action, symbol, side, trigger_reason, price, "
            " pnl, fee, amount, entry_price, order_id, created_at) "
            "VALUES (:sid, 'order_filled', 'BTC/USDT:USDT', 'long', 'market', :px, "
            "        NULL, :fee, :amt, NULL, 'o-open', '2026-01-01 00:00:00')"
        ), {"sid": sid, "px": entry_price, "fee": fee_open, "amt": amount})

        close_pnl = (exit_price - entry_price) * amount
        await conn.execute(text(
            "INSERT INTO trade_actions "
            "(session_id, action, symbol, side, trigger_reason, price, "
            " pnl, fee, amount, entry_price, order_id, created_at) "
            "VALUES (:sid, 'order_filled', 'BTC/USDT:USDT', 'long', 'market', :px, "
            "        :pnl, :fee, :amt, :ep, 'o-close', '2026-01-01 00:00:01')"
        ), {"sid": sid, "px": exit_price, "pnl": close_pnl, "fee": fee_close,
            "amt": amount, "ep": entry_price})

    yield engine
    await engine.dispose()


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_metrics_total_pnl_scales_with_cs(metrics_engine_cs_001):
    """cs=0.01: pnl_gross = (101000-100000) × (10 × 0.01) × sign(+1) = 100.0."""
    from src.services.metrics import MetricsService
    svc = MetricsService(engine=metrics_engine_cs_001, session_id="cs-001", initial_balance=10000.0)
    metrics = await svc.compute()
    assert metrics.total_trades == 1
    assert abs(metrics.total_pnl - 100.0) < 1e-6, (
        f"expected total_pnl=100.0, got {metrics.total_pnl} "
        f"(without cs: would be 10000.0)"
    )


@pytest.mark.asyncio
async def test_metrics_cs_null_fallback(metrics_engine_cs_null):
    """contract_size=NULL falls back to 1.0: gross = (101000-100000) × 10 × 1.0 = 10000.0."""
    from src.services.metrics import MetricsService
    svc = MetricsService(engine=metrics_engine_cs_null, session_id="cs-null", initial_balance=10000.0)
    metrics = await svc.compute()
    assert metrics.total_trades == 1
    assert abs(metrics.total_pnl - 10000.0) < 1e-6, (
        f"expected total_pnl=10000.0 (cs=1.0 fallback), got {metrics.total_pnl}"
    )
