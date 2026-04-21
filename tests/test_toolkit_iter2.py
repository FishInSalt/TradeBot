"""Rendering tests for 3 new tools + get_position enhancement (spec §5.2)."""
from __future__ import annotations
import pytest
from dataclasses import dataclass, field
from unittest.mock import AsyncMock
from src.integrations.exchange.base import OrderBook, OrderBookLevel, Trade, Ticker, Balance, Position, Order


@dataclass
class MockDeps:
    symbol: str = "BTC/USDT:USDT"
    initial_balance: float = 10000.0
    exchange: AsyncMock = field(default_factory=AsyncMock)
    market_data: AsyncMock = field(default_factory=AsyncMock)
    technical: AsyncMock = field(default_factory=AsyncMock)


@pytest.mark.asyncio
async def test_order_book_typical_output_format():
    """Typical order book renders best bid/ask, cumulative depth, bid share, concentrated levels."""
    from src.agent.tools_perception import get_order_book
    deps = MockDeps()
    deps.market_data.get_order_book.return_value = OrderBook(
        symbol="BTC/USDT:USDT",
        bids=[
            OrderBookLevel(64190.5, 0.024), OrderBookLevel(64190.0, 0.156),
            *[OrderBookLevel(64190.0 - i * 0.5, 0.1) for i in range(2, 20)],
        ],
        asks=[
            OrderBookLevel(64200.5, 0.032), OrderBookLevel(64201.0, 0.089),
            *[OrderBookLevel(64200.5 + i * 0.5, 0.1) for i in range(2, 20)],
        ],
        timestamp=0,
    )
    result = await get_order_book(deps)
    assert "Order Book" in result
    assert "Best bid:" in result
    assert "Best ask:" in result
    assert "Spread:" in result
    assert "Bid share:" in result
    assert "Depth (top 20 each side)" in result


@pytest.mark.asyncio
async def test_order_book_empty_insufficient():
    """Empty order book returns 'insufficient data' with depth info."""
    from src.agent.tools_perception import get_order_book
    deps = MockDeps()
    deps.market_data.get_order_book.return_value = OrderBook(
        symbol="BTC/USDT:USDT", bids=[], asks=[], timestamp=0,
    )
    result = await get_order_book(deps, depth=20)
    assert "insufficient data" in result
    assert "requested depth 20" in result
    assert "got 0" in result


@pytest.mark.asyncio
async def test_order_book_service_failure():
    """Exception in service layer → 'temporarily unavailable'."""
    from src.agent.tools_perception import get_order_book
    deps = MockDeps()
    deps.market_data.get_order_book.side_effect = Exception("connection reset")
    result = await get_order_book(deps)
    assert "temporarily unavailable" in result


@pytest.mark.asyncio
async def test_order_book_bid_side_heavy():
    """Bid total >> ask total: output shows bid share > 55%."""
    from src.agent.tools_perception import get_order_book
    deps = MockDeps()
    deps.market_data.get_order_book.return_value = OrderBook(
        symbol="BTC/USDT:USDT",
        bids=[OrderBookLevel(100.0 - i * 0.1, 2.0) for i in range(20)],   # total 40
        asks=[OrderBookLevel(100.1 + i * 0.1, 0.5) for i in range(20)],   # total 10
        timestamp=0,
    )
    result = await get_order_book(deps)
    # bids 40 / (40+10) = 80%
    assert "Bid share: 80" in result or "Bid share: 80.0" in result


@pytest.mark.asyncio
async def test_order_book_no_concentrated_levels():
    """All levels have uniform amount → median ≈ amount → no level > 3× median → Concentrated section absent."""
    from src.agent.tools_perception import get_order_book
    deps = MockDeps()
    deps.market_data.get_order_book.return_value = OrderBook(
        symbol="BTC/USDT:USDT",
        bids=[OrderBookLevel(100.0 - i * 0.1, 1.0) for i in range(20)],  # all 1.0
        asks=[OrderBookLevel(100.1 + i * 0.1, 1.0) for i in range(20)],  # all 1.0
        timestamp=0,
    )
    result = await get_order_book(deps)
    # Main sections present
    assert "Best bid:" in result
    assert "Bid share:" in result
    # But no concentrated section when no level exceeds 3× median
    assert "Concentrated levels" not in result


@pytest.mark.asyncio
async def test_order_book_concentrated_truncation_to_10():
    """When > 10 levels exceed 3× median, output truncates to top-10 by amount.

    Data shape: 14 tiny (0.001) + 6 huge (10.0) per side (14+6 = 20 total).
    Sorted → median is between [9th, 10th] which are both tiny → median = 0.001.
    Threshold = 0.001 × 3 = 0.003. All 6 huge levels per side pass → 12 total concentrated.
    12 > 10 → truncation to top-10 kicks in.
    """
    from src.agent.tools_perception import get_order_book
    deps = MockDeps()
    bids = [OrderBookLevel(100.0 - i * 0.1, 0.001 if i < 14 else 10.0) for i in range(20)]
    asks = [OrderBookLevel(100.1 + i * 0.1, 0.001 if i < 14 else 10.0) for i in range(20)]
    deps.market_data.get_order_book.return_value = OrderBook(
        symbol="BTC/USDT:USDT", bids=bids, asks=asks, timestamp=0,
    )
    result = await get_order_book(deps)
    assert "Concentrated levels" in result
    # Count rendered concentrated rows (each starts with "  Bid  " or "  Ask  ")
    concentrated_lines = [l for l in result.splitlines() if l.startswith("  Bid  ") or l.startswith("  Ask  ")]
    assert len(concentrated_lines) <= 10, f"Expected ≤ 10 truncated rows, got {len(concentrated_lines)}"


@pytest.mark.asyncio
async def test_recent_trades_typical():
    """Typical: 5 buckets, total + count + avg size."""
    from src.agent.tools_perception import get_recent_trades
    import time
    now_ms = int(time.time() * 1000)
    trades = []
    # Distribute trades into known buckets — 100 trades evenly across 5 minutes
    for i in range(100):
        age = i * 3000  # 0 to 297s
        trades.append(Trade(timestamp=now_ms - age, side="buy" if i % 3 == 0 else "sell",
                            price=64000.0, amount=0.01, trade_id=None))
    deps = MockDeps()
    deps.market_data.get_recent_trades.return_value = trades
    result = await get_recent_trades(deps, window_seconds=300)
    assert "Recent Trades" in result
    assert "last 300s" in result
    assert "5 × 60s buckets" in result
    assert "Total:" in result
    assert "Trade count: 100" in result
    assert "Avg size:" in result


@pytest.mark.asyncio
async def test_recent_trades_empty_cold_market():
    """No trades in window → no trades message."""
    from src.agent.tools_perception import get_recent_trades
    deps = MockDeps()
    deps.market_data.get_recent_trades.return_value = []
    result = await get_recent_trades(deps, window_seconds=300)
    assert "no trades in last 300s" in result


@pytest.mark.asyncio
async def test_recent_trades_service_failure():
    from src.agent.tools_perception import get_recent_trades
    deps = MockDeps()
    deps.market_data.get_recent_trades.side_effect = Exception("timeout")
    result = await get_recent_trades(deps)
    assert "temporarily unavailable" in result


@pytest.mark.asyncio
async def test_recent_trades_partial_coverage_double_condition():
    """When n>=95% of max AND oldest age < 95% window → partial coverage flagged."""
    from src.agent.tools_perception import get_recent_trades, RECENT_TRADES_MAX_FETCH
    import time
    now_ms = int(time.time() * 1000)
    # Fill up to limit, oldest 200s ago → 200/300 = 67% of window
    trades = [Trade(timestamp=now_ms - int((i / RECENT_TRADES_MAX_FETCH) * 200_000),
                    side="buy", price=64000.0, amount=0.01, trade_id=None)
              for i in range(RECENT_TRADES_MAX_FETCH)]
    deps = MockDeps()
    deps.market_data.get_recent_trades.return_value = trades
    result = await get_recent_trades(deps, window_seconds=300)
    assert "partial coverage" in result


@pytest.mark.asyncio
async def test_recent_trades_all_taker_sell():
    """All trades are taker-sell → 0% taker buy / 100% taker sell / negative net."""
    from src.agent.tools_perception import get_recent_trades
    import time
    now_ms = int(time.time() * 1000)
    trades = [Trade(timestamp=now_ms - i * 3000, side="sell", price=64000.0, amount=0.01, trade_id=None)
              for i in range(50)]
    deps = MockDeps()
    deps.market_data.get_recent_trades.return_value = trades
    result = await get_recent_trades(deps, window_seconds=300)
    assert "0% taker buy" in result
    assert "net -" in result  # negative net (all sells)
