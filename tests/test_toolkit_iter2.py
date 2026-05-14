"""Rendering tests for 3 new tools + get_position enhancement (spec §5.2)."""
from __future__ import annotations
import re
import pytest
from dataclasses import dataclass, field
from unittest.mock import AsyncMock
from src.integrations.exchange.base import OrderBook, OrderBookLevel, Trade, Ticker, Balance, Position, Order


# iter-tool-opt-as-of-header: 14 perception tools now carry inline "@ HH:MM:SS UTC"
# in their first section header. Tests assert via regex rather than byte-equal
# substring to remain deterministic w/o freezing the clock.
_AS_OF_TS = r"@ \d{2}:\d{2}:\d{2} UTC"


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
    # iter-tool-opt-order-book-default: default depth lowered 20 → 15 (sim modal 84%)
    assert "=== Depth (top 15 each side) ===" in result


def test_get_order_book_default_depth_is_15():
    """iter-tool-opt-order-book-default: sim modal 84% justifies depth=15 default.

    sim #8 31 calls: depth=15×26 (84%) / 20×3 (10%) / 30×1 / 10×1. Current default
    20 matched only 10% of agent calls; agent overrode 90% of the time to 15.
    Lineage R2-Next-D §4 GMD (50→30) but stronger — GMD modal cluster was diffuse
    ([10,20,30]~17% each), OB modal is monomodal 84%.
    """
    from src.agent.tools_perception import ORDER_BOOK_DEPTH_DEFAULT
    assert ORDER_BOOK_DEPTH_DEFAULT == 15


@pytest.mark.asyncio
async def test_order_book_empty_insufficient():
    """Empty order book returns 'insufficient data' with depth info."""
    from src.agent.tools_perception import get_order_book
    deps = MockDeps()
    deps.market_data.get_order_book.return_value = OrderBook(
        symbol="BTC/USDT:USDT", bids=[], asks=[], timestamp=0,
    )
    result = await get_order_book(deps, depth=20)
    # R2-8c §4.2.20 — L2 Option D form (inline Error: prefix)
    assert re.search(rf"=== Order Book \(BTC/USDT:USDT {_AS_OF_TS}\) ===", result), result[:200]
    assert "Error: Insufficient data" in result
    assert "requested depth 20" in result
    assert "got 0" in result


@pytest.mark.asyncio
async def test_order_book_service_failure():
    """Exception in service layer → 'temporarily unavailable'."""
    from src.agent.tools_perception import get_order_book
    deps = MockDeps()
    deps.market_data.get_order_book.side_effect = Exception("connection reset")
    result = await get_order_book(deps)
    # R2-8c §4.2.20 — L2 Option D form (inline Error: prefix)
    # iter-tool-opt-error-metadata: exception class name appended in parentheses
    assert re.search(rf"=== Order Book \(BTC/USDT:USDT {_AS_OF_TS}\) ===", result), result[:200]
    assert "Error: Temporarily unavailable (Exception)." in result


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
    # R2-8c §4.2.20 — promoted to explicit sub-section header
    assert "=== Concentrated Levels" in result
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
    """No trades in window → no trades message (L3 empty-state, single section)."""
    from src.agent.tools_perception import get_recent_trades
    deps = MockDeps()
    deps.market_data.get_recent_trades.return_value = []
    result = await get_recent_trades(deps, window_seconds=300)
    # R2-8c §4.2.9: single-section empty-state (NOT Error: prefix).
    assert re.search(rf"=== Recent Trades \(BTC/USDT:USDT, last 300s {_AS_OF_TS}\) ===", result), result[:200]
    assert "No trades in last 300s." in result
    assert "Error:" not in result


@pytest.mark.asyncio
async def test_recent_trades_service_failure():
    """Service failure → inline Error: prefix under tool section (R2-8c §4.2.9 Option D)."""
    from src.agent.tools_perception import get_recent_trades
    deps = MockDeps()
    deps.market_data.get_recent_trades.side_effect = Exception("timeout")
    result = await get_recent_trades(deps)
    # iter-tool-opt-error-metadata: exception class name appended in parentheses
    # review-followup I2: error-path header now carries `last Xs` window field
    assert re.search(rf"=== Recent Trades \(BTC/USDT:USDT, last \d+s {_AS_OF_TS}\) ===", result), result[:200]
    assert "Error: Temporarily unavailable (Exception)." in result


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


@pytest.mark.asyncio
async def test_recent_trades_all_taker_buy():
    """All trades are taker-buy → 100% taker buy / positive net (symmetric to all-sell)."""
    from src.agent.tools_perception import get_recent_trades
    import time
    now_ms = int(time.time() * 1000)
    trades = [Trade(timestamp=now_ms - i * 3000, side="buy", price=64000.0, amount=0.01, trade_id=None)
              for i in range(50)]
    deps = MockDeps()
    deps.market_data.get_recent_trades.return_value = trades
    result = await get_recent_trades(deps, window_seconds=300)
    assert "100% taker buy" in result
    assert "net +" in result  # positive net (all buys)


@pytest.mark.asyncio
async def test_recent_trades_future_timestamp_clock_skew_skipped():
    """Trades with timestamp in the future (server clock ahead of local) are skipped.

    Regression for IndexError: Python floor division on negative age yields
    `-5000 // 60_000 == -1`, which would compute bucket_idx = 5 — out of bounds
    on a 5-element list. NTP-level millisecond drift between OKX server and
    local clock is common in practice; this test pins the skip-on-negative
    behavior so a refactor cannot re-introduce the crash silently.
    """
    from src.agent.tools_perception import get_recent_trades
    import time
    now_ms = int(time.time() * 1000)
    trades = [
        # 5 seconds in the future (server clock ahead) — would crash without guard
        Trade(timestamp=now_ms + 5_000, side="buy", price=64000.0, amount=0.01, trade_id="future"),
        # Normal in-window trade — should still be counted
        Trade(timestamp=now_ms - 30_000, side="sell", price=64000.0, amount=0.02, trade_id="ok"),
    ]
    deps = MockDeps()
    deps.market_data.get_recent_trades.return_value = trades
    # Must NOT raise IndexError
    result = await get_recent_trades(deps, window_seconds=300)
    # Future-timestamped trade was filtered; only the in-window trade counted
    assert "Trade count: 1" in result


@pytest.mark.asyncio
async def test_recent_trades_non_standard_window_label():
    """window_seconds != 300 → fallback label format `bucket {i+1}/N ({start}-{end}s ago)`.

    Spec §5.2 test-coverage clause. Default 300s path uses `t-Xmin` labels; any
    other window must render positional bucket labels with second ranges.
    """
    from src.agent.tools_perception import get_recent_trades
    import time
    now_ms = int(time.time() * 1000)
    # 120s window → 5 buckets × 24s each
    trades = [Trade(timestamp=now_ms - i * 2000, side="buy", price=64000.0, amount=0.01, trade_id=None)
              for i in range(50)]  # ages 0..98000ms, all within 120s window
    deps = MockDeps()
    deps.market_data.get_recent_trades.return_value = trades
    result = await get_recent_trades(deps, window_seconds=120)
    # Non-standard window → fallback label format; no t-Xmin
    assert "t-1min" not in result
    assert "t-2min" not in result
    # Positional label present (exact format: `bucket {i}/5 ({start}-{end}s ago)`)
    assert "bucket 1/5" in result
    assert "bucket 5/5" in result
    # Second-range markers
    assert "s ago)" in result
    # Header reflects 24s bucket duration
    assert "5 × 24s buckets" in result


import pandas as pd


def _make_ohlcv_df(n: int, last_close: float = 64200.0) -> pd.DataFrame:
    """Helper: synthetic OHLCV with gentle trend."""
    return pd.DataFrame([
        {"timestamp": 1700000000000 + i * 60_000,
         "open": last_close - (n - i), "high": last_close - (n - i) + 5,
         "low": last_close - (n - i) - 5, "close": last_close - (n - i - 1),
         "volume": 100.0}
        for i in range(n)
    ])


@pytest.mark.asyncio
async def test_multi_tf_snapshot_typical(mocker):
    """Typical: 4 TFs all with sufficient data → 4 formatted rows + Columns header."""
    from src.agent.tools_perception import get_multi_timeframe_snapshot
    deps = MockDeps()
    deps.market_data.get_ohlcv_dataframe = AsyncMock(side_effect=lambda sym, tf, limit: _make_ohlcv_df(limit))
    deps.technical.compute_indicators = mocker.Mock(return_value={"atr_14": 85.0})
    deps.market_data.get_ticker = AsyncMock(return_value=Ticker(
        symbol="BTC/USDT:USDT", last=64200.0, bid=64199.5, ask=64200.5,
        high=65000.0, low=63000.0, base_volume=1000.0, timestamp=0,
    ))
    result = await get_multi_timeframe_snapshot(deps)
    assert "Multi-TF Snapshot" in result
    # New layout (iter w2r2-next-d Task 5): cycle-opening primary with Last
    # (ticker @ HH:MM:SS UTC) and per-tf rows prefixed [tf] not "tf:".
    assert re.search(r"Last \(ticker @ \d{2}:\d{2}:\d{2} UTC\):", result)
    assert "Columns: Momentum" in result
    for tf in ("5m", "1h", "4h", "1d"):
        assert f"[{tf}]" in result


@pytest.mark.asyncio
async def test_multi_tf_snapshot_custom_tfs(mocker):
    from src.agent.tools_perception import get_multi_timeframe_snapshot
    deps = MockDeps()
    deps.market_data.get_ohlcv_dataframe = AsyncMock(side_effect=lambda sym, tf, limit: _make_ohlcv_df(limit))
    deps.technical.compute_indicators = mocker.Mock(return_value={"atr_14": 85.0})
    deps.market_data.get_ticker = AsyncMock(return_value=Ticker(
        symbol="BTC/USDT:USDT", last=64200.0, bid=64199.5, ask=64200.5,
        high=65000.0, low=63000.0, base_volume=1000.0, timestamp=0,
    ))
    result = await get_multi_timeframe_snapshot(deps, tfs=["1h"])
    # New layout (iter w2r2-next-d Task 5): per-tf rows prefixed [tf]
    assert "[1h]" in result
    assert "[5m]" not in result


@pytest.mark.asyncio
async def test_multi_tf_snapshot_all_fail(mocker):
    """All TFs raise → overall unavailable (sectioned per R2-8c §4.2.3)."""
    from src.agent.tools_perception import get_multi_timeframe_snapshot
    deps = MockDeps()
    deps.market_data.get_ohlcv_dataframe = AsyncMock(side_effect=Exception("down"))
    deps.market_data.get_ticker = AsyncMock(return_value=Ticker(
        symbol="BTC/USDT:USDT", last=64200.0, bid=64199.5, ask=64200.5,
        high=65000.0, low=63000.0, base_volume=1000.0, timestamp=0,
    ))
    result = await get_multi_timeframe_snapshot(deps)
    assert "=== Multi-TF Snapshot (BTC/USDT:USDT) ===" in result
    assert "Error: Temporarily unavailable" in result


@pytest.mark.asyncio
async def test_multi_tf_snapshot_single_tf_failure_isolated(mocker):
    """One TF raises an exception; other TFs render normally (per-TF independent
    degradation via asyncio.gather). Exercises the `isinstance(df_or_err, Exception)`
    branch specifically — distinct from `df.empty or len < slow` (per_tf_insufficient).
    """
    from src.agent.tools_perception import get_multi_timeframe_snapshot
    deps = MockDeps()

    def ohlcv_side(sym, tf, limit):
        if tf == "5m":
            raise Exception("5m endpoint transient failure")
        return _make_ohlcv_df(limit)

    deps.market_data.get_ohlcv_dataframe = AsyncMock(side_effect=ohlcv_side)
    deps.technical.compute_indicators = mocker.Mock(return_value={"atr_14": 85.0})
    deps.market_data.get_ticker = AsyncMock(return_value=Ticker(
        symbol="BTC/USDT:USDT", last=64200.0, bid=64199.5, ask=64200.5,
        high=65000.0, low=63000.0, base_volume=1000.0, timestamp=0,
    ))
    result = await get_multi_timeframe_snapshot(deps)
    # New layout (iter w2r2-next-d Task 5): "[5m]  temporarily unavailable"
    assert "[5m]  temporarily unavailable" in result
    # Other TFs: normal data rendering with [tf] prefix
    for tf in ("[1h]", "[4h]", "[1d]"):
        assert tf in result
    # And they actually rendered data — Mom/Structure/ATR columns present.
    assert "Mom " in result
    assert "ATR " in result
    # Header still present — overall degrade was NOT triggered.
    assert "Multi-TF Snapshot" in result
    assert re.search(r"Last \(ticker @ \d{2}:\d{2}:\d{2} UTC\):", result)


@pytest.mark.asyncio
async def test_multi_tf_snapshot_per_tf_insufficient(mocker):
    """5m has only 30 candles (< 50 needed): that TF shows insufficient, others OK."""
    from src.agent.tools_perception import get_multi_timeframe_snapshot
    deps = MockDeps()

    def ohlcv_side(sym, tf, limit):
        if tf == "5m":
            return _make_ohlcv_df(30)  # insufficient for MA50
        return _make_ohlcv_df(limit)

    deps.market_data.get_ohlcv_dataframe = AsyncMock(side_effect=ohlcv_side)
    deps.technical.compute_indicators = mocker.Mock(return_value={"atr_14": 85.0})
    deps.market_data.get_ticker = AsyncMock(return_value=Ticker(
        symbol="BTC/USDT:USDT", last=64200.0, bid=64199.5, ask=64200.5,
        high=65000.0, low=63000.0, base_volume=1000.0, timestamp=0,
    ))
    result = await get_multi_timeframe_snapshot(deps)
    # New layout: "[5m]  insufficient data (need N candles, got M)"
    assert "[5m]  insufficient data" in result
    assert "[1h]" in result  # still rendered


@pytest.mark.asyncio
async def test_multi_tf_snapshot_ma_entangled(mocker):
    """MA fast ≈ MA slow (diff < 0.1%) → 'MA{fast}: X ≈ MA{slow}: Y' rendering."""
    from src.agent.tools_perception import get_multi_timeframe_snapshot
    deps = MockDeps()
    # Construct a DataFrame where MA50 and MA200 are within 0.1% (constant close → rolling means all equal)
    tight_df = pd.DataFrame([
        {"timestamp": 1700000000000 + i * 60_000,
         "open": 64000.0, "high": 64001.0, "low": 63999.0,
         "close": 64000.0,
         "volume": 100.0}
        for i in range(250)
    ])
    deps.market_data.get_ohlcv_dataframe = AsyncMock(return_value=tight_df)
    deps.technical.compute_indicators = mocker.Mock(return_value={"atr_14": 85.0})
    deps.market_data.get_ticker = AsyncMock(return_value=Ticker(
        symbol="BTC/USDT:USDT", last=64000.0, bid=63999.5, ask=64000.5,
        high=64001.0, low=63999.0, base_volume=1000.0, timestamp=0,
    ))
    result = await get_multi_timeframe_snapshot(deps, tfs=["1h"])
    # New layout (iter w2r2-next-d Task 5): "MA50: X.XX ≈ MA200: Y.YY"
    assert re.search(r"MA50:\s*\d+\.\d+\s*≈\s*MA200:\s*\d+\.\d+", result)


from datetime import datetime, timezone


@pytest.mark.asyncio
async def test_get_position_empty_short_circuit(mocker):
    """No open position → early return (1 IO only, no parallel gather)."""
    from src.agent.tools_perception import get_position
    deps = MockDeps()
    deps.exchange.fetch_positions = AsyncMock(return_value=[])
    result = await get_position(deps)
    # R2-8c §4.2.11 — sectioned empty-state
    # iter-tool-opt-as-of-header: header now carries inline "@ HH:MM:SS UTC"
    assert re.fullmatch(
        rf"=== Position \(BTC/USDT:USDT {_AS_OF_TS}\) ===\nNo open positions\.",
        result,
    ), result
    # Verify other IOs never called
    deps.exchange.fetch_balance.assert_not_called()


@pytest.mark.asyncio
async def test_get_position_enhanced_output(mocker):
    """With position: new Risk exposure + Exit orders sections present."""
    from src.agent.tools_perception import get_position
    deps = MockDeps()
    deps.exchange.fetch_positions = AsyncMock(return_value=[Position(
        symbol="BTC/USDT:USDT", side="long", contracts=0.01,
        entry_price=64000.0, unrealized_pnl=10.0, leverage=3,
        liquidation_price=55000.0, created_at=datetime(2026, 4, 21, 10, 0, tzinfo=timezone.utc),
    )])
    deps.market_data.get_ticker = AsyncMock(return_value=Ticker(
        symbol="BTC/USDT:USDT", last=64100.0, bid=64099.5, ask=64100.5,
        high=65000.0, low=63000.0, base_volume=1000.0, timestamp=0,
    ))
    deps.exchange.fetch_balance = AsyncMock(return_value=Balance(
        total_usdt=10010.0, free_usdt=9796.67, used_usdt=213.33,
    ))
    deps.market_data.get_ohlcv_dataframe = AsyncMock(return_value=_make_ohlcv_df(50, last_close=64100.0))
    deps.technical.compute_indicators = mocker.Mock(return_value={"atr_14": 88.0})
    deps.exchange.fetch_open_orders = AsyncMock(return_value=[
        Order(id="o1", symbol="BTC/USDT:USDT", side="sell", order_type="stop",
              amount=0.01, price=62000.0, status="open"),
        Order(id="o2", symbol="BTC/USDT:USDT", side="sell", order_type="take_profit",
              amount=0.01, price=68000.0, status="open"),
    ])
    deps.exchange.get_contract_size = AsyncMock(return_value=1.0)
    deps.exchange.get_mark_price = AsyncMock(return_value=64100.0)

    result = await get_position(deps)
    # R2-8c §4.2.11 — promoted to explicit section headers
    assert "=== Risk Exposure ===" in result
    assert "Notional value:" in result
    assert "Margin used:" in result
    assert "ATR(1h)" in result
    assert "× ATR(1h)" in result
    assert "=== Exit Orders ===" in result
    assert "Stop loss:" in result
    assert "Take profit:" in result


@pytest.mark.asyncio
async def test_get_position_no_sl_tp_naked_warning(mocker):
    """Position without SL/TP: explicit 'not set' warnings."""
    from src.agent.tools_perception import get_position
    deps = MockDeps()
    deps.exchange.fetch_positions = AsyncMock(return_value=[Position(
        symbol="BTC/USDT:USDT", side="long", contracts=0.01, entry_price=64000.0,
        unrealized_pnl=10.0, leverage=3, liquidation_price=55000.0,
        created_at=datetime(2026, 4, 21, 10, 0, tzinfo=timezone.utc),
    )])
    deps.market_data.get_ticker = AsyncMock(return_value=Ticker(
        symbol="BTC/USDT:USDT", last=64100.0, bid=64099.5, ask=64100.5,
        high=65000.0, low=63000.0, base_volume=1000.0, timestamp=0,
    ))
    deps.exchange.fetch_balance = AsyncMock(return_value=Balance(total_usdt=10010.0, free_usdt=9796.67, used_usdt=213.33))
    deps.market_data.get_ohlcv_dataframe = AsyncMock(return_value=_make_ohlcv_df(50))
    deps.technical.compute_indicators = mocker.Mock(return_value={"atr_14": 88.0})
    deps.exchange.fetch_open_orders = AsyncMock(return_value=[])
    deps.exchange.get_contract_size = AsyncMock(return_value=1.0)
    deps.exchange.get_mark_price = AsyncMock(return_value=64100.0)

    result = await get_position(deps)
    assert "Stop loss: not set" in result
    assert "Take profit: not set" in result


@pytest.mark.asyncio
async def test_get_position_atr_unavailable_degrade(mocker):
    """ATR fetch fails: main sections still shown, ATR-multiple suffix omitted."""
    from src.agent.tools_perception import get_position
    deps = MockDeps()
    deps.exchange.fetch_positions = AsyncMock(return_value=[Position(
        symbol="BTC/USDT:USDT", side="long", contracts=0.01, entry_price=64000.0,
        unrealized_pnl=10.0, leverage=3, liquidation_price=55000.0,
        created_at=datetime(2026, 4, 21, 10, 0, tzinfo=timezone.utc),
    )])
    deps.market_data.get_ticker = AsyncMock(return_value=Ticker(
        symbol="BTC/USDT:USDT", last=64100.0, bid=64099.5, ask=64100.5,
        high=65000.0, low=63000.0, base_volume=1000.0, timestamp=0,
    ))
    deps.exchange.fetch_balance = AsyncMock(return_value=Balance(total_usdt=10010.0, free_usdt=9796.67, used_usdt=213.33))
    deps.market_data.get_ohlcv_dataframe = AsyncMock(side_effect=Exception("no OHLCV"))
    deps.exchange.fetch_open_orders = AsyncMock(return_value=[])
    deps.exchange.get_contract_size = AsyncMock(return_value=1.0)
    deps.exchange.get_mark_price = AsyncMock(return_value=64100.0)

    result = await get_position(deps)
    # R2-8c §4.2.11 — promoted to explicit section header
    assert "=== Risk Exposure ===" in result
    assert "ATR(1h)" not in result  # suffix omitted on ATR failure


@pytest.mark.asyncio
async def test_get_position_multi_tp_sorted(mocker):
    """Multiple TP orders listed all + sorted by price ascending (spec §2.4)."""
    from src.agent.tools_perception import get_position
    deps = MockDeps()
    deps.exchange.fetch_positions = AsyncMock(return_value=[Position(
        symbol="BTC/USDT:USDT", side="long", contracts=0.03, entry_price=64000.0,
        unrealized_pnl=10.0, leverage=3, liquidation_price=55000.0,
        created_at=datetime(2026, 4, 21, 10, 0, tzinfo=timezone.utc),
    )])
    deps.market_data.get_ticker = AsyncMock(return_value=Ticker(
        symbol="BTC/USDT:USDT", last=64100.0, bid=64099.5, ask=64100.5,
        high=65000.0, low=63000.0, base_volume=1000.0, timestamp=0,
    ))
    deps.exchange.fetch_balance = AsyncMock(return_value=Balance(
        total_usdt=10010.0, free_usdt=9796.67, used_usdt=213.33,
    ))
    deps.market_data.get_ohlcv_dataframe = AsyncMock(return_value=_make_ohlcv_df(50, last_close=64100.0))
    deps.technical.compute_indicators = mocker.Mock(return_value={"atr_14": 88.0})
    # Intentionally unsorted (70000 / 66000 / 68000) → expect sorted output (66000 / 68000 / 70000)
    deps.exchange.fetch_open_orders = AsyncMock(return_value=[
        Order(id="tp3", symbol="BTC/USDT:USDT", side="sell", order_type="take_profit",
              amount=0.01, price=70000.0, status="open"),
        Order(id="tp1", symbol="BTC/USDT:USDT", side="sell", order_type="take_profit",
              amount=0.01, price=66000.0, status="open"),
        Order(id="tp2", symbol="BTC/USDT:USDT", side="sell", order_type="take_profit",
              amount=0.01, price=68000.0, status="open"),
    ])
    deps.exchange.get_contract_size = AsyncMock(return_value=1.0)
    deps.exchange.get_mark_price = AsyncMock(return_value=64100.0)

    result = await get_position(deps)
    # All 3 TPs rendered
    tp_lines = [l for l in result.splitlines() if l.startswith("  Take profit:")]
    assert len(tp_lines) == 3, f"Expected 3 TP lines, got {len(tp_lines)}: {tp_lines}"
    # Sorted ascending by price
    idx_low = result.index("66000.00")
    idx_mid = result.index("68000.00")
    idx_high = result.index("70000.00")
    assert idx_low < idx_mid < idx_high, "TP orders must render sorted by price ascending"


@pytest.mark.asyncio
async def test_get_position_filters_none_price_exit_orders(mocker):
    """Defensive: orders with price=None are filtered out (never reach _fmt_exit)."""
    from src.agent.tools_perception import get_position
    deps = MockDeps()
    deps.exchange.fetch_positions = AsyncMock(return_value=[Position(
        symbol="BTC/USDT:USDT", side="long", contracts=0.01, entry_price=64000.0,
        unrealized_pnl=10.0, leverage=3, liquidation_price=55000.0,
        created_at=datetime(2026, 4, 21, 10, 0, tzinfo=timezone.utc),
    )])
    deps.market_data.get_ticker = AsyncMock(return_value=Ticker(
        symbol="BTC/USDT:USDT", last=64100.0, bid=64099.5, ask=64100.5,
        high=65000.0, low=63000.0, base_volume=1000.0, timestamp=0,
    ))
    deps.exchange.fetch_balance = AsyncMock(return_value=Balance(
        total_usdt=10010.0, free_usdt=9796.67, used_usdt=213.33,
    ))
    deps.market_data.get_ohlcv_dataframe = AsyncMock(return_value=_make_ohlcv_df(50))
    deps.technical.compute_indicators = mocker.Mock(return_value={"atr_14": 88.0})
    # Mix: 1 valid stop + 1 None-price stop (simulates a hypothetical upstream bug)
    deps.exchange.fetch_open_orders = AsyncMock(return_value=[
        Order(id="sl_ok", symbol="BTC/USDT:USDT", side="sell", order_type="stop",
              amount=0.01, price=62000.0, status="open"),
        Order(id="sl_bad", symbol="BTC/USDT:USDT", side="sell", order_type="stop",
              amount=0.01, price=None, status="open"),
    ])
    deps.exchange.get_contract_size = AsyncMock(return_value=1.0)
    deps.exchange.get_mark_price = AsyncMock(return_value=64100.0)

    # Should not crash; only the priced order renders
    result = await get_position(deps)
    sl_lines = [l for l in result.splitlines() if l.startswith("  Stop loss:")]
    assert len(sl_lines) == 1, f"Expected 1 valid SL line (None filtered), got {sl_lines}"
    assert "62000.00" in result
    # The None-priced order must not produce any garbage render
    assert "None" not in result


@pytest.mark.asyncio
async def test_get_position_phase2_hard_failure_degradation(mocker):
    """Phase 2 gather hard failure (e.g., fetch_balance timeout) → degrade to
    Position + PnL preserved + Risk Exposure + Exit Orders sections set to
    `(unavailable)` body (R2-8c §4.2.11 sectioned hard-failure form).

    Covers the outer try/except around asyncio.gather in get_position (spec §2.4
    deviation from §3.3 return_exceptions=True — hard failures collapse to a single
    degradation path). Previously code-path had zero test coverage.
    """
    from src.agent.tools_perception import get_position
    deps = MockDeps()
    deps.exchange.fetch_positions = AsyncMock(return_value=[Position(
        symbol="BTC/USDT:USDT", side="long", contracts=0.01, entry_price=64000.0,
        unrealized_pnl=10.0, leverage=3, liquidation_price=55000.0,
        created_at=datetime(2026, 4, 21, 10, 0, tzinfo=timezone.utc),
    )])
    deps.market_data.get_ticker = AsyncMock(return_value=Ticker(
        symbol="BTC/USDT:USDT", last=64100.0, bid=64099.5, ask=64100.5,
        high=65000.0, low=63000.0, base_volume=1000.0, timestamp=0,
    ))
    # Hard failure in one of Phase 2 IOs (ticker/balance/orders/contract_size)
    deps.exchange.fetch_balance = AsyncMock(side_effect=Exception("balance endpoint timeout"))
    deps.market_data.get_ohlcv_dataframe = AsyncMock(return_value=_make_ohlcv_df(50))
    deps.exchange.fetch_open_orders = AsyncMock(return_value=[])
    deps.exchange.get_contract_size = AsyncMock(return_value=1.0)

    result = await get_position(deps)
    # Core position lines preserved (R2-8c sectioned form)
    # iter-tool-opt-as-of-header: header now carries inline "@ HH:MM:SS UTC"
    assert re.search(rf"=== Position \(BTC/USDT:USDT {_AS_OF_TS}\) ===", result), result[:200]
    assert "Side: Long" in result
    assert "64,000.00" in result or "64000.00" in result
    # PnL preserved (Phase-1 data only)
    assert "=== PnL ===" in result
    assert "PnL:" in result
    assert "of initial capital" in result
    # Duration preserved — depends only on p.created_at from Phase-1, must NOT be
    # dropped when Phase-2 IO fails (regression guard for the hard-fail-drops-Duration
    # bug fixed by extracting _render_position_core helper). Position created at
    # 2026-04-21 10:00 UTC; the test runs at "now", which is well past then, so
    # Duration is non-N/A.
    assert "Duration:" in result
    assert "Duration: N/A" not in result
    # Degradation: Risk Exposure + Exit Orders sections present with (unavailable) body
    # iter-tool-opt-error-metadata: exception class name appended (unavailable: ClassName)
    assert "=== Risk Exposure ===\n(unavailable: Exception)" in result
    assert "=== Exit Orders ===\n(unavailable: Exception)" in result
    # Enhanced numeric fields absent (hard-failure collapse)
    assert "Notional value:" not in result
    assert "Stop loss:" not in result
    assert "Take profit:" not in result


@pytest.mark.asyncio
async def test_order_book_all_zero_amounts_insufficient(mocker):
    """Spec §2.1 — total_sum == 0 (all 20×2 levels have amount=0) → insufficient data degradation."""
    from src.agent.tools_perception import get_order_book
    deps = MockDeps()
    deps.market_data.get_order_book.return_value = OrderBook(
        symbol="BTC/USDT:USDT",
        bids=[OrderBookLevel(100.0 - i * 0.1, 0.0) for i in range(20)],
        asks=[OrderBookLevel(101.0 + i * 0.1, 0.0) for i in range(20)],
        timestamp=0,
    )
    result = await get_order_book(deps, depth=20)
    # Should degrade, not raise ZeroDivisionError (R2-8c §4.2.20 Option D form)
    assert "Error: Insufficient data" in result
    # Should not contain Bid share (didn't reach that branch)
    assert "Bid share:" not in result
