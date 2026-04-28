"""Shared test fixtures for Iter 6+ tests."""
from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock

from src.integrations.exchange.base import FillEvent, Ticker


def make_fill_event(
    *,
    order_id: str = "test-order-1",
    symbol: str = "BTC/USDT:USDT",
    side: str = "buy",
    position_side: str = "long",
    trigger_reason: str = "market",
    fill_price: float = 50000.0,
    amount: float = 0.01,
    fee: float = 0.5,
    pnl: float | None = None,
    timestamp: int = 1700000000000,
    is_full_close: bool = False,
) -> FillEvent:
    """Construct a FillEvent for tests with sensible defaults.

    Defaults to is_full_close=False (open semantics). Tests covering
    full-close scenarios MUST explicitly pass is_full_close=True.
    See spec §4.1 — default in factory not dataclass to force explicit
    intent at call site (silent corruption protection).
    """
    return FillEvent(
        order_id=order_id,
        symbol=symbol,
        side=side,
        position_side=position_side,
        trigger_reason=trigger_reason,
        fill_price=fill_price,
        amount=amount,
        fee=fee,
        pnl=pnl,
        timestamp=timestamp,
        is_full_close=is_full_close,
    )


def make_ticker(
    *,
    symbol: str = "BTC/USDT:USDT",
    last: float = 50000.0,
    bid: float | None = None,
    ask: float | None = None,
    timestamp: int = 1700000000000,
) -> Ticker:
    """Construct a Ticker for sim _process_tick calls."""
    if bid is None:
        bid = last - 5.0
    if ask is None:
        ask = last + 5.0
    return Ticker(
        symbol=symbol, last=last, bid=bid, ask=ask,
        high=last * 1.02, low=last * 0.98,
        base_volume=1000.0, timestamp=timestamp,
    )


def make_sim_exchange(
    initial_balance: float = 10000.0,
    fee_rate: float = 0.0005,
    symbol: str = "BTC/USDT:USDT",
):
    """Construct a SimulatedExchange for tests without async start().

    Mirrors tests/test_simulated_exchange.py:_make_exchange pattern. Pre-populates
    state that start() would normally set, plus a default _latest_ticker so
    create_order paths can run without first injecting a tick.
    """
    from src.integrations.exchange.simulated import SimulatedExchange

    config = MagicMock()
    config.fee_rate = fee_rate
    config.precision = {"BTC/USDT:USDT": 3, "ETH/USDT:USDT": 2}

    ex = SimulatedExchange(
        config=config, db_engine=None, session_id="test-session", symbol=symbol,
    )
    ex._free_usdt = initial_balance
    ex._used_usdt = 0.0
    ex._frozen_usdt = 0.0
    ex._positions = {}
    ex._pending_orders = []
    ex._leverage = {}
    ex._latest_ticker = make_ticker(symbol=symbol)
    ex._running = True
    return ex


def make_okx_exchange():
    """Construct OKXExchange via __new__ (skip ccxt client init for unit tests).

    Manually sets state that BaseExchange.__init__ + OKXExchange.__init__
    would normally set. Use only for pure-function tests on _infer_is_full_close
    or _dispatch_fill_event that don't touch network / ccxt client.
    """
    from src.integrations.exchange.okx import OKXExchange

    ex = OKXExchange.__new__(OKXExchange)
    # BaseExchange.__init__ state (set after Task 1 uplift)
    ex._price_level_alerts = []
    ex._latest_price = None
    ex._alert_service = None
    ex._fill_callback = None
    ex._alert_callback = None  # NEW: parity with OKXExchange.__init__:121 (Task 2 review I1)
    # OKXExchange-specific minimal state for tests
    ex._symbol = "BTC/USDT:USDT"
    ex._sandbox = True
    ex._running = False
    ex._ws_connected = False
    ex._pnl_fetch_timeout = 1.0
    ex._seen_order_ids = {}
    ex._seen_order_ids_max = 10000
    return ex
