"""Fact-only regression: ensure new/enhanced tools don't emit banned subjective words (spec §3.5)."""
from __future__ import annotations
import re
import pytest
from unittest.mock import AsyncMock
from dataclasses import dataclass, field
from src.integrations.exchange.base import OrderBook, OrderBookLevel, Trade, Ticker, Balance, Position, Order

FACT_ONLY_BANNED_WORDS_RE = [
    r"\bwall\b", r"\baggressive\b", r"\bbullish\b", r"\bbearish\b",
    r"\boverbought\b", r"\boversold\b", r"\bdry powder\b",
    r"\brisk[- ]on\b", r"\brisk[- ]off\b",
    r"\bbull market\b", r"\bbear market\b",
    r"\bpressure\b", r"\brally\b", r"\bplunge\b",
    r"\bsurge\b", r"\bcrash\b", r"\bpump\b", r"\bdump\b",
]
FACT_ONLY_BANNED_PHRASES_RE = [
    r"\bstrong support\b", r"\bstrong resistance\b",
    r"\bweak support\b", r"\bweak resistance\b",
    r"\btrend\s+(up|down|flat)\b",
]


def _scan(output: str) -> list[str]:
    """Return list of banned pattern hits after stripping Columns: header lines."""
    lines = [l for l in output.splitlines() if not l.startswith("Columns:")]
    scrubbed = "\n".join(lines)
    hits = []
    for pat in FACT_ONLY_BANNED_WORDS_RE + FACT_ONLY_BANNED_PHRASES_RE:
        if re.search(pat, scrubbed, re.IGNORECASE):
            hits.append(pat)
    return hits


@dataclass
class MockDeps:
    symbol: str = "BTC/USDT:USDT"
    initial_balance: float = 10000.0
    exchange: AsyncMock = field(default_factory=AsyncMock)
    market_data: AsyncMock = field(default_factory=AsyncMock)
    technical: AsyncMock = field(default_factory=AsyncMock)


@pytest.mark.asyncio
async def test_order_book_fact_only_4_scenarios():
    """Typical / bid-heavy / empty / service-failure all fact-only."""
    from src.agent.tools_perception import get_order_book
    outputs = []
    deps = MockDeps()

    # Scenario 1: typical
    deps.market_data.get_order_book = AsyncMock(return_value=OrderBook(
        symbol="BTC/USDT:USDT",
        bids=[OrderBookLevel(100 - i * 0.1, 1.0) for i in range(20)],
        asks=[OrderBookLevel(101 + i * 0.1, 1.0) for i in range(20)],
        timestamp=0,
    ))
    outputs.append(await get_order_book(deps))

    # Scenario 2: bid-heavy (extreme)
    deps.market_data.get_order_book = AsyncMock(return_value=OrderBook(
        symbol="BTC/USDT:USDT",
        bids=[OrderBookLevel(100 - i * 0.1, 5.0) for i in range(20)],
        asks=[OrderBookLevel(101 + i * 0.1, 0.1) for i in range(20)],
        timestamp=0,
    ))
    outputs.append(await get_order_book(deps))

    # Scenario 3: empty
    deps.market_data.get_order_book = AsyncMock(return_value=OrderBook(
        symbol="BTC/USDT:USDT", bids=[], asks=[], timestamp=0,
    ))
    outputs.append(await get_order_book(deps))

    # Scenario 4: failure
    deps.market_data.get_order_book = AsyncMock(side_effect=Exception("down"))
    outputs.append(await get_order_book(deps))

    combined = "\n".join(outputs)
    hits = _scan(combined)
    assert not hits, f"Banned words in get_order_book outputs: {hits}\n{combined}"


@pytest.mark.asyncio
async def test_recent_trades_fact_only_4_scenarios():
    from src.agent.tools_perception import get_recent_trades
    import time
    now_ms = int(time.time() * 1000)
    deps = MockDeps()
    outputs = []

    # S1: typical
    deps.market_data.get_recent_trades = AsyncMock(return_value=[
        Trade(timestamp=now_ms - i * 3000, side="buy" if i % 2 == 0 else "sell",
              price=64000.0, amount=0.01, trade_id=None) for i in range(50)
    ])
    outputs.append(await get_recent_trades(deps))

    # S2: all buy
    deps.market_data.get_recent_trades = AsyncMock(return_value=[
        Trade(timestamp=now_ms - i * 3000, side="buy", price=64000.0, amount=0.01, trade_id=None)
        for i in range(50)
    ])
    outputs.append(await get_recent_trades(deps))

    # S3: cold
    deps.market_data.get_recent_trades = AsyncMock(return_value=[])
    outputs.append(await get_recent_trades(deps))

    # S4: fail
    deps.market_data.get_recent_trades = AsyncMock(side_effect=Exception("x"))
    outputs.append(await get_recent_trades(deps))

    hits = _scan("\n".join(outputs))
    assert not hits, f"Banned words in get_recent_trades outputs: {hits}"


@pytest.mark.asyncio
async def test_multi_tf_snapshot_fact_only(mocker):
    """Spec §5.3 clause: 4 scenarios — typical / MA entangled (at) / per-TF insufficient / all-fail."""
    from src.agent.tools_perception import get_multi_timeframe_snapshot
    import pandas as pd
    deps = MockDeps()
    outputs = []

    # Scenario 1: typical
    df = pd.DataFrame([{"timestamp": 0, "open": 64000, "high": 64100, "low": 63900,
                        "close": 64050, "volume": 100.0} for _ in range(250)])
    deps.market_data.get_ohlcv_dataframe = AsyncMock(return_value=df)
    deps.technical.compute_indicators = mocker.Mock(return_value={"atr_14": 85.0})
    deps.exchange.fetch_ticker = AsyncMock(return_value=Ticker(
        symbol="BTC/USDT:USDT", last=64050.0, bid=64049.5, ask=64050.5,
        high=65000.0, low=63000.0, base_volume=1000.0, timestamp=0,
    ))
    outputs.append(await get_multi_timeframe_snapshot(deps))

    # Scenario 2: MA entangled — flat close → diff_pct<0.1 → "MA at MA"
    flat_df = pd.DataFrame([{"timestamp": 0, "open": 64000, "high": 64001, "low": 63999,
                             "close": 64000, "volume": 100.0} for _ in range(250)])
    deps.market_data.get_ohlcv_dataframe = AsyncMock(return_value=flat_df)
    outputs.append(await get_multi_timeframe_snapshot(deps, tfs=["1h"]))

    # Scenario 3: per-TF insufficient — 5m returns 30 candles
    def _partial_side(sym, tf, limit):
        if tf == "5m":
            return pd.DataFrame([{"timestamp": 0, "open": 64000, "high": 64100, "low": 63900,
                                  "close": 64050, "volume": 100.0} for _ in range(30)])
        return df
    deps.market_data.get_ohlcv_dataframe = AsyncMock(side_effect=_partial_side)
    outputs.append(await get_multi_timeframe_snapshot(deps))

    # Scenario 4: all TF fail (after ticker OK)
    deps.market_data.get_ohlcv_dataframe = AsyncMock(side_effect=Exception("x"))
    outputs.append(await get_multi_timeframe_snapshot(deps))

    hits = _scan("\n".join(outputs))
    assert not hits, f"Banned words in get_multi_timeframe_snapshot outputs: {hits}"


@pytest.mark.asyncio
async def test_get_position_fact_only(mocker):
    from src.agent.tools_perception import get_position
    import pandas as pd
    from datetime import datetime, timezone
    deps = MockDeps()
    outputs = []

    # Typical with SL
    df = pd.DataFrame([{"timestamp": 0, "open": 64000, "high": 64100, "low": 63900,
                        "close": 64050, "volume": 100.0} for _ in range(50)])
    deps.exchange.fetch_positions = AsyncMock(return_value=[Position(
        symbol="BTC/USDT:USDT", side="long", contracts=0.01, entry_price=64000.0,
        unrealized_pnl=10.0, leverage=3, liquidation_price=55000.0,
        created_at=datetime(2026, 4, 21, 10, 0, tzinfo=timezone.utc),
    )])
    deps.market_data.get_ticker = AsyncMock(return_value=Ticker(
        symbol="BTC/USDT:USDT", last=64100.0, bid=64099.5, ask=64100.5,
        high=65000.0, low=63000.0, base_volume=1000.0, timestamp=0,
    ))
    deps.exchange.fetch_balance = AsyncMock(return_value=Balance(total_usdt=10010, free_usdt=9796, used_usdt=213))
    deps.market_data.get_ohlcv_dataframe = AsyncMock(return_value=df)
    deps.technical.compute_indicators = mocker.Mock(return_value={"atr_14": 88.0})
    deps.exchange.fetch_open_orders = AsyncMock(return_value=[
        Order(id="o1", symbol="BTC/USDT:USDT", side="sell", order_type="stop",
              amount=0.01, price=62000.0, status="open"),
    ])
    deps.exchange.get_contract_size = AsyncMock(return_value=1.0)
    outputs.append(await get_position(deps))

    # No SL/TP
    deps.exchange.fetch_open_orders = AsyncMock(return_value=[])
    outputs.append(await get_position(deps))

    # Multi-TP scalping scenario (spec §2.4 — multiple TPs sorted rendering path)
    deps.exchange.fetch_open_orders = AsyncMock(return_value=[
        Order(id="tp1", symbol="BTC/USDT:USDT", side="sell", order_type="take_profit",
              amount=0.005, price=66000.0, status="open"),
        Order(id="tp2", symbol="BTC/USDT:USDT", side="sell", order_type="take_profit",
              amount=0.005, price=70000.0, status="open"),
    ])
    outputs.append(await get_position(deps))

    # ATR(1h) unavailable: OHLCV fetch fails → ATR suffix omission path
    deps.market_data.get_ohlcv_dataframe = AsyncMock(side_effect=Exception("ohlcv down"))
    deps.exchange.fetch_open_orders = AsyncMock(return_value=[
        Order(id="sl", symbol="BTC/USDT:USDT", side="sell", order_type="stop",
              amount=0.01, price=62000.0, status="open"),
    ])
    outputs.append(await get_position(deps))

    hits = _scan("\n".join(outputs))
    assert not hits, f"Banned words in get_position outputs: {hits}"
