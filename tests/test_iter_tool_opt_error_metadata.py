"""iter-tool-opt-error-metadata: 5 perception tools surface exception class name
in error/degradation messages.

Coverage:
  RT-1  get_recent_trades            — except → "Error: Temporarily unavailable (TimeoutError)."
  OB-5  get_order_book               — except → "Error: Temporarily unavailable (ConnectionError)."
  POS-2 get_position                 — except → "(unavailable: <ExceptionClassName>)" in Risk + Exit sections
  EA-5  get_exchange_announcements   — except → "Error: ... temporarily unavailable (<ExceptionClassName>)."
  OO-6  get_open_orders              — current<=0 fallback → "(ticker unavailable)" inline annotation
                                       (if-branch, not exception: no class name; per task spec)

Tool-design principle 1 (fact-provider): error type is part of the fact set
the tool emits; agent triage logic needs the type to choose retry vs. skip.
"""
from __future__ import annotations
import asyncio
from dataclasses import dataclass, field
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock

import pytest

from src.integrations.exchange.base import (
    Balance,
    Order,
    OrderBook,
    OrderBookLevel,
    Position,
    Ticker,
    Trade,
)


@dataclass
class _Deps:
    symbol: str = "BTC/USDT:USDT"
    initial_balance: float = 10000.0
    exchange: AsyncMock = field(default_factory=AsyncMock)
    market_data: AsyncMock = field(default_factory=AsyncMock)
    technical: AsyncMock = field(default_factory=AsyncMock)


# ---------------------------------------------------------------------------
# RT-1 — get_recent_trades surfaces exception class on service failure
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_recent_trades_error_message_includes_exception_class():
    """RT-1: surface exception type (TimeoutError) for agent triage."""
    from src.agent.tools_perception import get_recent_trades

    deps = _Deps()
    deps.market_data.get_recent_trades = AsyncMock(side_effect=asyncio.TimeoutError())

    result = await get_recent_trades(deps)

    assert "=== Recent Trades (BTC/USDT:USDT" in result  # iter-8 may suffix `@ HH:MM:SS UTC`
    assert "Error: Temporarily unavailable" in result
    assert "(TimeoutError)" in result, f"class name missing in: {result!r}"


@pytest.mark.asyncio
async def test_get_recent_trades_error_message_connection_error_class():
    """RT-1 cross-class regression: ConnectionError surfaces distinctly from TimeoutError."""
    from src.agent.tools_perception import get_recent_trades

    deps = _Deps()
    deps.market_data.get_recent_trades = AsyncMock(side_effect=ConnectionError("reset"))

    result = await get_recent_trades(deps)

    assert "(ConnectionError)" in result, f"class name missing in: {result!r}"


# ---------------------------------------------------------------------------
# OB-5 — get_order_book surfaces exception class on service failure
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_order_book_error_message_includes_exception_class():
    """OB-5: surface exception type (ConnectionError) for agent triage."""
    from src.agent.tools_perception import get_order_book

    deps = _Deps()
    deps.market_data.get_order_book = AsyncMock(side_effect=ConnectionError("reset"))

    result = await get_order_book(deps)

    assert "=== Order Book (BTC/USDT:USDT" in result  # iter-8 may suffix `@ HH:MM:SS UTC`
    assert "Error: Temporarily unavailable" in result
    assert "(ConnectionError)" in result, f"class name missing in: {result!r}"


@pytest.mark.asyncio
async def test_get_order_book_error_message_timeout_class():
    """OB-5 cross-class regression: TimeoutError surfaces distinctly."""
    from src.agent.tools_perception import get_order_book

    deps = _Deps()
    deps.market_data.get_order_book = AsyncMock(side_effect=asyncio.TimeoutError())

    result = await get_order_book(deps)

    assert "(TimeoutError)" in result, f"class name missing in: {result!r}"


# ---------------------------------------------------------------------------
# POS-2 — get_position surfaces exception class in degraded sections
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_position_degraded_sections_include_exception_class():
    """POS-2: hard-failure path in Phase-2 gather surfaces exception class in
    `(unavailable: ConnectionError)` body of Risk Exposure + Exit Orders sections."""
    from src.agent.tools_perception import get_position

    deps = _Deps()
    deps.exchange.fetch_positions = AsyncMock(return_value=[Position(
        symbol="BTC/USDT:USDT", side="long", contracts=0.01, entry_price=64000.0,
        unrealized_pnl=10.0, leverage=3, liquidation_price=55000.0,
        created_at=datetime(2026, 4, 21, 10, 0, tzinfo=timezone.utc),
    )])
    deps.market_data.get_ticker = AsyncMock(return_value=Ticker(
        symbol="BTC/USDT:USDT", last=64100.0, bid=64099.5, ask=64100.5,
        high=65000.0, low=63000.0, base_volume=1000.0, timestamp=0,
    ))
    # Hard failure on balance (one of the 5 gather-targets) → except path
    deps.exchange.fetch_balance = AsyncMock(side_effect=ConnectionError("balance down"))
    deps.market_data.get_ohlcv_dataframe = AsyncMock(return_value=None)
    deps.exchange.fetch_open_orders = AsyncMock(return_value=[])
    deps.exchange.get_contract_size = AsyncMock(return_value=1.0)

    result = await get_position(deps)

    # Phase-1 sections preserved
    assert "=== Position (BTC/USDT:USDT" in result  # iter-8 may suffix `@ HH:MM:SS UTC`
    assert "=== PnL ===" in result
    # Degradation surfaces class name in both sections
    assert "=== Risk Exposure ===\n(unavailable: ConnectionError)" in result
    assert "=== Exit Orders ===\n(unavailable: ConnectionError)" in result


@pytest.mark.asyncio
async def test_get_position_degraded_sections_timeout_class():
    """POS-2 cross-class regression: TimeoutError surfaces distinctly from ConnectionError."""
    from src.agent.tools_perception import get_position

    deps = _Deps()
    deps.exchange.fetch_positions = AsyncMock(return_value=[Position(
        symbol="BTC/USDT:USDT", side="long", contracts=0.01, entry_price=64000.0,
        unrealized_pnl=10.0, leverage=3, liquidation_price=55000.0,
        created_at=datetime(2026, 4, 21, 10, 0, tzinfo=timezone.utc),
    )])
    deps.market_data.get_ticker = AsyncMock(side_effect=asyncio.TimeoutError())
    deps.exchange.fetch_balance = AsyncMock(return_value=Balance(
        total_usdt=10000.0, free_usdt=9000.0, used_usdt=1000.0,
    ))
    deps.market_data.get_ohlcv_dataframe = AsyncMock(return_value=None)
    deps.exchange.fetch_open_orders = AsyncMock(return_value=[])
    deps.exchange.get_contract_size = AsyncMock(return_value=1.0)

    result = await get_position(deps)

    assert "(unavailable: TimeoutError)" in result, f"class name missing in: {result!r}"


# ---------------------------------------------------------------------------
# EA-5 — get_exchange_announcements surfaces exception class in error message
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_exchange_announcements_error_message_includes_exception_class():
    """EA-5: surface exception type (TimeoutError) for agent triage."""
    from src.agent.tools_perception import get_exchange_announcements

    deps = MagicMock()
    deps.news = MagicMock()
    deps.news.get_announcements = AsyncMock(side_effect=asyncio.TimeoutError())

    result = await get_exchange_announcements(deps, lookback_hours=24)

    assert "=== Exchange Announcements (past 24h" in result  # iter-8 may suffix `@ HH:MM:SS UTC`
    assert "temporarily unavailable" in result.lower()
    assert "(TimeoutError)" in result, f"class name missing in: {result!r}"


@pytest.mark.asyncio
async def test_get_exchange_announcements_error_message_connection_error_class():
    """EA-5 cross-class regression: ConnectionError surfaces distinctly."""
    from src.agent.tools_perception import get_exchange_announcements

    deps = MagicMock()
    deps.news = MagicMock()
    deps.news.get_announcements = AsyncMock(side_effect=ConnectionError("dns fail"))

    result = await get_exchange_announcements(deps, lookback_hours=24)

    assert "(ConnectionError)" in result, f"class name missing in: {result!r}"


# ---------------------------------------------------------------------------
# OO-6 — get_open_orders fallback when ticker.last <= 0
# ---------------------------------------------------------------------------


def _make_oo_deps(orders: list[Order], ticker_last: float):
    deps = MagicMock()
    deps.symbol = "BTC/USDT:USDT"
    deps.exchange = MagicMock()
    deps.exchange.fetch_open_orders = AsyncMock(return_value=orders)
    deps.market_data = MagicMock()
    deps.market_data.get_ticker = AsyncMock(return_value=Ticker(
        symbol="BTC/USDT:USDT", last=ticker_last,
        bid=max(ticker_last - 1, 0.0), ask=ticker_last + 1,
        high=ticker_last, low=max(ticker_last, 0.0),
        base_volume=0.0, timestamp=0,
    ))
    return deps


@pytest.mark.asyncio
async def test_get_open_orders_fallback_single_order_surfaces_ticker_unavailable():
    """OO-6: ticker.last <= 0 fallback path (single order via _render_single_order)
    surfaces 'ticker unavailable' annotation instead of silently omitting suffix."""
    from src.agent.tools_perception import get_open_orders

    orders = [
        Order(id="lim1", symbol="BTC/USDT:USDT", side="buy", order_type="limit",
              amount=0.01, price=63000.0, status="open"),
    ]
    deps = _make_oo_deps(orders, ticker_last=0.0)

    result = await get_open_orders(deps)

    assert "ticker unavailable" in result, f"fallback hint missing in: {result!r}"
    # Distance percent must not appear since current<=0
    assert "% from current" not in result


@pytest.mark.asyncio
async def test_get_open_orders_fallback_oco_surfaces_ticker_unavailable():
    """OO-6: ticker.last <= 0 fallback path (OCO branch) surfaces 'ticker unavailable'
    annotation for both SL and TP legs."""
    from src.agent.tools_perception import get_open_orders

    orders = [
        Order(id="oco_z", symbol="BTC/USDT:USDT", side="sell",
              order_type="stop", amount=1.0, price=60000.0, status="open",
              is_algo=True),
        Order(id="oco_z", symbol="BTC/USDT:USDT", side="sell",
              order_type="take_profit", amount=1.0, price=80000.0, status="open",
              is_algo=True),
    ]
    deps = _make_oo_deps(orders, ticker_last=0.0)

    result = await get_open_orders(deps)

    # OCO line is single-line; both legs share annotation
    assert "[OCO]" in result
    # The annotation appears at least twice (one per leg) in the OCO row
    assert result.count("ticker unavailable") >= 2, (
        f"expected per-leg ticker-unavailable annotation in: {result!r}"
    )
    assert "% from current" not in result


@pytest.mark.asyncio
async def test_get_open_orders_happy_path_no_ticker_unavailable_annotation():
    """OO-6 negative case: happy path with ticker.last > 0 must NOT surface
    'ticker unavailable' (annotation is fallback-only)."""
    from src.agent.tools_perception import get_open_orders

    orders = [
        Order(id="lim1", symbol="BTC/USDT:USDT", side="buy", order_type="limit",
              amount=0.01, price=63000.0, status="open"),
    ]
    deps = _make_oo_deps(orders, ticker_last=64000.0)

    result = await get_open_orders(deps)

    assert "ticker unavailable" not in result
    # iter-3 promoted distance to `% / Z.Z pts from current` form
    assert "pts from current" in result
