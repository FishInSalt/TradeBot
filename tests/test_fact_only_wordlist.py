"""Fact-only regression: ensure new/enhanced tools don't emit banned subjective words (spec §3.5)."""
from __future__ import annotations
import re
import pandas as pd
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
    timeframe: str = "5m"  # Iter 3: get_price_pivots reads deps.timeframe
    # Iter 4 fact-only coverage extension (real TradingDeps field names):
    memory: AsyncMock = field(default_factory=AsyncMock)
    db_engine: object | None = None
    metrics: object | None = None
    news: object | None = None
    macro: object | None = None
    crypto_etf: object | None = None
    onchain: object | None = None
    set_next_wake_fn: object | None = None
    wake_min_minutes: int = 1
    wake_max_minutes: int = 60
    session_id: str = "test-session"
    cycle_id: str | None = "test-cycle"  # defense-in-depth: ToolCallRecorder reads this in real flow
    # Execution-tool defaults (Task 12) — skip approval gate by default
    approval_enabled: bool = False
    approval_gate: object | None = None
    fee_rate: float = 0.0005


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
    deps.market_data.get_ticker = AsyncMock(return_value=Ticker(
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
    deps.exchange.get_mark_price = AsyncMock(return_value=64100.0)
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


PIVOTS_BANNED_WORDS = (
    # Strength
    "strong", "weak", "strongly", "weakly",
    # Importance
    "important", "unimportant", "key", "major", "minor",
    "critical", "crucial", "significant", "insignificant",
    # Sentiment (inherited from global, listed here so this test does not
    # depend on the global wordlist — see plan §5.4 wordlist scope decision)
    "bullish", "bearish",
    # Iter 3 §1.2 non-goals — guard against future regressions producing them
    "broken", "breached",
)
PIVOTS_BANNED_RE = re.compile(
    r"\b(" + "|".join(PIVOTS_BANNED_WORDS) + r")\b", re.IGNORECASE,
)


def _pivots_df(highs, lows):
    n = len(highs)
    return pd.DataFrame({
        "open": highs, "high": highs, "low": lows, "close": highs,
        "volume": [1.0] * n,
    })


def _pivots_ticker():
    return Ticker(
        symbol="BTC/USDT:USDT",
        last=66523.40,
        bid=66523.0,
        ask=66524.0,
        high=66623.40,
        low=66423.40,
        base_volume=0.0,
        timestamp=0,
    )


def _pivots_ohlcv_side_effect(by_tf):
    async def _impl(symbol, timeframe, limit=None):
        result = by_tf.get(timeframe)
        if isinstance(result, Exception):
            raise result
        return result
    return _impl


def _build_normal_deps() -> MockDeps:
    """100 bar main TF with explicit pivots + 3 priors ok."""
    deps = MockDeps()
    deps.market_data.get_ticker = AsyncMock(return_value=_pivots_ticker())
    highs = [66000.0 + i * 0.1 for i in range(100)]
    lows = [65900.0 + i * 0.1 for i in range(100)]
    highs[15] = 67500.0  # swing high
    lows[20] = 64500.0   # swing low
    main_df = _pivots_df(highs, lows)
    daily = _pivots_df([67234.0, 67100.0], [65500.0, 65400.0])
    weekly = _pivots_df([68500.0, 68400.0], [64200.0, 64100.0])
    monthly = _pivots_df([71200.0, 71100.0], [60800.0, 60700.0])
    deps.market_data.get_ohlcv_dataframe = AsyncMock(side_effect=_pivots_ohlcv_side_effect({
        "5m": main_df, "1d": daily, "1w": weekly, "1M": monthly,
    }))
    return deps


def _build_monotonic_uptrend_deps() -> MockDeps:
    """100 bar strictly increasing → no swing pivots; 3 priors ok."""
    deps = MockDeps()
    deps.market_data.get_ticker = AsyncMock(return_value=_pivots_ticker())
    main_df = _pivots_df([66000.0 + i for i in range(100)], [65900.0 + i for i in range(100)])
    daily = _pivots_df([67234.0, 67100.0], [65500.0, 65400.0])
    weekly = _pivots_df([68500.0, 68400.0], [64200.0, 64100.0])
    monthly = _pivots_df([71200.0, 71100.0], [60800.0, 60700.0])
    deps.market_data.get_ohlcv_dataframe = AsyncMock(side_effect=_pivots_ohlcv_side_effect({
        "5m": main_df, "1d": daily, "1w": weekly, "1M": monthly,
    }))
    return deps


def _build_50bar_with_insufficient_prior_deps() -> MockDeps:
    deps = MockDeps()
    deps.market_data.get_ticker = AsyncMock(return_value=_pivots_ticker())
    highs = [66000.0 + i * 0.1 for i in range(50)]
    lows = [65900.0 + i * 0.1 for i in range(50)]
    highs[15] = 67500.0
    lows[20] = 64500.0
    main_df = _pivots_df(highs, lows)
    short_df = _pivots_df([100.0], [99.0])  # len 1 → insufficient
    deps.market_data.get_ohlcv_dataframe = AsyncMock(side_effect=_pivots_ohlcv_side_effect({
        "5m": main_df, "1d": short_df, "1w": short_df, "1M": short_df,
    }))
    return deps


def _build_main_tf_error_with_prior_ok_deps() -> MockDeps:
    deps = MockDeps()
    deps.market_data.get_ticker = AsyncMock(return_value=_pivots_ticker())
    daily = _pivots_df([67234.0, 67100.0], [65500.0, 65400.0])
    weekly = _pivots_df([68500.0, 68400.0], [64200.0, 64100.0])
    monthly = _pivots_df([71200.0, 71100.0], [60800.0, 60700.0])
    deps.market_data.get_ohlcv_dataframe = AsyncMock(side_effect=_pivots_ohlcv_side_effect({
        "5m": Exception("main tf down"),
        "1d": daily, "1w": weekly, "1M": monthly,
    }))
    return deps


def _build_main_tf_empty_with_prior_error_deps() -> MockDeps:
    deps = MockDeps()
    deps.market_data.get_ticker = AsyncMock(return_value=_pivots_ticker())
    err = RuntimeError("api down")
    deps.market_data.get_ohlcv_dataframe = AsyncMock(side_effect=_pivots_ohlcv_side_effect({
        "5m": _pivots_df([], []),
        "1d": err, "1w": err, "1M": err,
    }))
    return deps


@pytest.mark.asyncio
async def test_get_price_pivots_fact_only_5_scenarios():
    """Normal / swing-empty / short-window / main-TF-error / all-prior-error
    — none of the 5 scenarios may emit any PIVOTS_BANNED_WORDS."""
    from src.agent.tools_perception import get_price_pivots

    scenarios = [
        ("normal_full", _build_normal_deps()),
        ("swing_empty", _build_monotonic_uptrend_deps()),
        ("short_window", _build_50bar_with_insufficient_prior_deps()),
        ("main_tf_error", _build_main_tf_error_with_prior_ok_deps()),
        ("all_prior_error", _build_main_tf_empty_with_prior_error_deps()),
    ]
    for name, deps in scenarios:
        output = await get_price_pivots(deps)
        matches = PIVOTS_BANNED_RE.findall(output)
        assert not matches, f"Banned words in scenario '{name}': {matches}\n--- output ---\n{output}"


@pytest.mark.asyncio
async def test_get_market_data_fact_only(mocker):
    """get_market_data typical-path output must not emit banned subjective words."""
    from src.agent.tools_perception import get_market_data
    deps = MockDeps()
    df = pd.DataFrame([{"timestamp": 0, "open": 64000, "high": 64100, "low": 63900,
                        "close": 64050, "volume": 100.0} for _ in range(250)])
    deps.market_data.get_ohlcv_dataframe = AsyncMock(return_value=df)
    deps.market_data.get_ticker = AsyncMock(return_value=Ticker(
        symbol="BTC/USDT:USDT", last=64050.0, bid=64049.5, ask=64050.5,
        high=65000.0, low=63000.0, base_volume=1000.0, timestamp=0,
    ))
    deps.technical.compute_indicators = mocker.Mock(return_value={
        "rsi_14": 55.0, "macd": 0.5, "macd_signal": 0.3, "macd_hist": 0.2,
        "bb_upper": 65000.0, "bb_middle": 64000.0, "bb_lower": 63000.0, "atr_14": 85.0,
    })
    deps.technical.format_for_llm = mocker.Mock(return_value="RSI(14): 55.0 | MACD: 0.50")
    output = await get_market_data(deps)
    hits = _scan(output)
    assert hits == [], f"get_market_data emitted banned words: {hits}"


@pytest.mark.asyncio
async def test_get_account_balance_fact_only():
    """Happy path: rendered balance lines."""
    from src.agent.tools_perception import get_account_balance
    deps = MockDeps()
    deps.exchange.fetch_balance = AsyncMock(return_value=Balance(
        total_usdt=10500.0, free_usdt=8500.0, used_usdt=2000.0,
    ))
    output = await get_account_balance(deps)
    hits = _scan(output)
    assert hits == [], f"get_account_balance emitted banned words: {hits}"


@pytest.mark.asyncio
async def test_get_open_orders_fact_only():
    """Empty + non-empty rendering paths."""
    from src.agent.tools_perception import get_open_orders
    deps = MockDeps()
    outputs = []

    # Scenario 1: no pending orders → "No pending orders." (early return)
    deps.exchange.fetch_open_orders = AsyncMock(return_value=[])
    outputs.append(await get_open_orders(deps))

    # Scenario 2: limit + OCO pair (covers _render_single_order + OCO branch)
    deps.exchange.fetch_open_orders = AsyncMock(return_value=[
        Order(id="lim1", symbol="BTC/USDT:USDT", side="buy", order_type="limit",
              amount=0.01, price=63000.0, status="open"),
        Order(id="oco1", symbol="BTC/USDT:USDT", side="sell", order_type="stop",
              amount=0.01, price=62000.0, status="open", is_algo=True),
        Order(id="oco1", symbol="BTC/USDT:USDT", side="sell", order_type="take_profit",
              amount=0.01, price=66000.0, status="open", is_algo=True),
    ])
    deps.market_data.get_ticker = AsyncMock(return_value=Ticker(
        symbol="BTC/USDT:USDT", last=64000.0, bid=63999.5, ask=64000.5,
        high=65000.0, low=63000.0, base_volume=1000.0, timestamp=0,
    ))
    outputs.append(await get_open_orders(deps))

    hits = _scan("\n".join(outputs))
    assert hits == [], f"get_open_orders emitted banned words: {hits}"


@pytest.mark.asyncio
async def test_get_trade_journal_fact_only():
    """Early return path when no db_engine — covers degraded-state output."""
    from src.agent.tools_perception import get_trade_journal
    deps = MockDeps()  # db_engine=None by default
    output = await get_trade_journal(deps)
    hits = _scan(output)
    assert hits == [], f"get_trade_journal emitted banned words: {hits}"


@pytest.mark.asyncio
async def test_get_memories_fact_only():
    """deps.memory.format_for_prompt() returns rendered string."""
    from src.agent.tools_perception import get_memories
    deps = MockDeps()
    deps.memory.format_for_prompt = AsyncMock(return_value="No memories yet.")
    output = await get_memories(deps)
    hits = _scan(output)
    assert hits == [], f"get_memories emitted banned words: {hits}"


@pytest.mark.asyncio
async def test_get_active_alerts_fact_only(mocker):
    """Volatility config + price level alerts rendering."""
    from src.agent.tools_perception import get_active_alerts
    mocker.patch("src.integrations.exchange.base.time.time", return_value=1700000000.0)
    mocker.patch("src.agent.tools_perception.time.time", return_value=1700000000.0)
    deps = MockDeps()
    outputs = []

    # Scenario 1: volatility alert not set + no price levels
    deps.exchange.get_alert_params = mocker.Mock(return_value=None)
    deps.exchange.get_price_level_alerts = mocker.Mock(return_value=[])
    outputs.append(await get_active_alerts(deps))

    # Scenario 2: alerts ON + 2 price levels
    deps.exchange.get_alert_params = mocker.Mock(return_value=(1.5, 30))
    deps.exchange.get_price_level_alerts = mocker.Mock(return_value=[
        {"id": "abcd1234", "direction": "above", "price": 65000.0, "reasoning": "test",
         "created_at": 1700000000.0},
        {"id": "ef567890", "direction": "below", "price": 62000.0, "reasoning": "test",
         "created_at": 1700000000.0},
    ])
    outputs.append(await get_active_alerts(deps))

    hits = _scan("\n".join(outputs))
    assert hits == [], f"get_active_alerts emitted banned words: {hits}"


@pytest.mark.asyncio
async def test_get_performance_fact_only():
    """metrics=None early-return path covers minimal rendering."""
    from src.agent.tools_perception import get_performance
    deps = MockDeps()  # metrics=None by default
    deps.exchange.fetch_balance = AsyncMock(return_value=Balance(
        total_usdt=10100.0, free_usdt=9000.0, used_usdt=1100.0,
    ))
    output = await get_performance(deps)
    hits = _scan(output)
    assert hits == [], f"get_performance emitted banned words: {hits}"


@pytest.mark.asyncio
async def test_get_market_news_fact_only():
    """News service unavailable early-return path."""
    from src.agent.tools_perception import get_market_news
    deps = MockDeps()  # news=None by default
    output = await get_market_news(deps)
    hits = _scan(output)
    assert hits == [], f"get_market_news emitted banned words: {hits}"


@pytest.mark.asyncio
async def test_get_exchange_announcements_fact_only():
    """Empty announcements list (typical) + None (degraded) outputs."""
    from src.agent.tools_perception import get_exchange_announcements
    deps = MockDeps()
    deps.news = AsyncMock()
    outputs = []
    deps.news.get_announcements = AsyncMock(return_value=[])
    outputs.append(await get_exchange_announcements(deps))
    deps.news.get_announcements = AsyncMock(return_value=None)
    outputs.append(await get_exchange_announcements(deps))
    hits = _scan("\n".join(outputs))
    assert hits == [], f"get_exchange_announcements emitted banned words: {hits}"


@pytest.mark.asyncio
async def test_get_macro_calendar_fact_only():
    """Empty (footer shows) + None (footer hidden) per spec §3.4."""
    from src.agent.tools_perception import get_macro_calendar
    deps = MockDeps()
    deps.news = AsyncMock()
    outputs = []
    deps.news.get_macro_events = AsyncMock(return_value=[])
    outputs.append(await get_macro_calendar(deps))
    deps.news.get_macro_events = AsyncMock(return_value=None)
    outputs.append(await get_macro_calendar(deps))
    hits = _scan("\n".join(outputs))
    assert hits == [], f"get_macro_calendar emitted banned words: {hits}"


@pytest.mark.asyncio
async def test_get_derivatives_data_fact_only():
    """All 3 sub-fetches fail → 'temporarily unavailable' rendering path."""
    from src.agent.tools_perception import get_derivatives_data
    deps = MockDeps()
    deps.market_data.get_funding_rate = AsyncMock(side_effect=Exception("down"))
    deps.market_data.get_open_interest_history = AsyncMock(side_effect=Exception("down"))
    deps.market_data.get_long_short_ratio = AsyncMock(side_effect=Exception("down"))
    output = await get_derivatives_data(deps)
    hits = _scan(output)
    assert hits == [], f"get_derivatives_data emitted banned words: {hits}"


@pytest.mark.asyncio
async def test_get_higher_timeframe_view_fact_only():
    """Typical 250-bar OHLCV → MA + range rendering."""
    from src.agent.tools_perception import get_higher_timeframe_view
    from types import SimpleNamespace
    deps = MockDeps()
    df = pd.DataFrame([{"timestamp": i * 14_400_000, "open": 64000 + i,
                        "high": 64100 + i, "low": 63900 + i,
                        "close": 64050 + i, "volume": 100.0}
                       for i in range(250)])
    deps.market_data.get_ohlcv_dataframe = AsyncMock(return_value=df)
    deps.market_data.get_ticker = AsyncMock(return_value=SimpleNamespace(
        last=64200.0, bid=64199.5, ask=64200.5,
    ))
    output = await get_higher_timeframe_view(deps, ["4h"])
    hits = _scan(output)
    assert hits == [], f"get_higher_timeframe_view emitted banned words: {hits}"


@pytest.mark.asyncio
async def test_get_macro_context_fact_only():
    """macro=None early-return path."""
    from src.agent.tools_perception import get_macro_context
    deps = MockDeps()  # macro=None by default
    output = await get_macro_context(deps)
    hits = _scan(output)
    assert hits == [], f"get_macro_context emitted banned words: {hits}"


@pytest.mark.asyncio
async def test_get_etf_flows_fact_only():
    """crypto_etf=None early-return path."""
    from src.agent.tools_perception import get_etf_flows
    deps = MockDeps()  # crypto_etf=None by default
    output = await get_etf_flows(deps)
    hits = _scan(output)
    assert hits == [], f"get_etf_flows emitted banned words: {hits}"


@pytest.mark.asyncio
async def test_get_stablecoin_supply_fact_only():
    """onchain=None early-return path."""
    from src.agent.tools_perception import get_stablecoin_supply
    deps = MockDeps()  # onchain=None by default
    output = await get_stablecoin_supply(deps)
    hits = _scan(output)
    assert hits == [], f"get_stablecoin_supply emitted banned words: {hits}"


@pytest.mark.asyncio
async def test_get_price_pivots_global_wordlist_fact_only():
    """Global wordlist coverage — separate from existing PIVOTS_BANNED_WORDS per-tool
    test (test_fact_only_wordlist.py:336+) which guards strong/weak/important/key/major/minor.
    This test ensures price_pivots also passes the global sentiment wordlist.
    """
    from src.agent.tools_perception import get_price_pivots
    from src.integrations.exchange.base import Ticker
    deps = MockDeps()
    # Inline 100-row DataFrame with timestamp column (price_pivots reads timestamp).
    # Shape independent from `_pivots_df` helper — that helper's no-timestamp shape
    # is incompatible with this tool, so we build a per-test fixture instead.
    df = pd.DataFrame([{"timestamp": i, "open": 64000, "high": 64100 + (i % 7) * 10,
                        "low": 63900 - (i % 5) * 10, "close": 64050,
                        "volume": 100.0} for i in range(100)])
    deps.market_data.get_ohlcv_dataframe = AsyncMock(return_value=df)
    # get_price_pivots also requires get_ticker for current_price baseline.
    deps.market_data.get_ticker = AsyncMock(return_value=Ticker(
        symbol="BTC/USDT:USDT", last=64050.0, bid=64049.5, ask=64050.5,
        high=65000.0, low=63000.0, base_volume=1000.0, timestamp=0,
    ))
    output = await get_price_pivots(deps)
    hits = _scan(output)
    assert hits == [], f"get_price_pivots emitted banned words: {hits}"


@pytest.mark.asyncio
@pytest.mark.parametrize("invoker", [
    "_invoke_open_position",
    "_invoke_close_position",
    "_invoke_set_stop_loss",
    "_invoke_set_take_profit",
    "_invoke_adjust_leverage",
    "_invoke_set_price_volatility_alert",
    "_invoke_cancel_order",
    "_invoke_add_price_level_alert",
    "_invoke_set_next_wake",
    "_invoke_set_next_wake_at",
    "_invoke_place_limit_order",
])
async def test_execution_tool_fact_only(invoker, mocker):
    """Execution tools — outputs are fixed templates; verify global wordlist clean.
    Each helper exercises a representative path (early-return where minimal,
    happy-path where the early-return is trivially clean).
    MockDeps default `approval_enabled=False` skips the approval gate."""
    deps = MockDeps()
    output = await globals()[invoker](deps, mocker)
    hits = _scan(output)
    assert hits == [], f"{invoker} emitted banned words: {hits}"


# === Execution tool invokers — each sets up minimal mocks for one representative path ===

async def _invoke_open_position(deps, mocker):
    """Happy path: full mock chain through create_order."""
    from src.agent.tools_execution import open_position
    deps.exchange.fetch_balance = AsyncMock(return_value=Balance(
        total_usdt=10000.0, free_usdt=8000.0, used_usdt=2000.0,
    ))
    deps.market_data.get_ticker = AsyncMock(return_value=Ticker(
        symbol="BTC/USDT:USDT", last=64000.0, bid=63999.5, ask=64000.5,
        high=65000.0, low=63000.0, base_volume=1000.0, timestamp=0,
    ))
    deps.exchange.amount_to_precision = mocker.Mock(return_value=0.01)
    deps.exchange.has_pending_market_order = mocker.Mock(return_value=False)
    deps.exchange.set_leverage = AsyncMock(return_value=None)
    deps.exchange.create_order = AsyncMock(return_value=Order(
        id="ord1", symbol="BTC/USDT:USDT", side="buy", order_type="market",
        amount=0.01, price=None, status="open",
    ))
    return await open_position(deps, "long", 10.0, 5, reasoning="test")


async def _invoke_close_position(deps, mocker):
    """Early return: no positions."""
    from src.agent.tools_execution import close_position
    deps.exchange.fetch_positions = AsyncMock(return_value=[])
    return await close_position(deps, reasoning="test")


async def _invoke_set_stop_loss(deps, mocker):
    """Early return: no position."""
    from src.agent.tools_execution import set_stop_loss
    deps.exchange.fetch_positions = AsyncMock(return_value=[])
    return await set_stop_loss(deps, 62000.0, reasoning="test")


async def _invoke_set_take_profit(deps, mocker):
    """Early return: no position."""
    from src.agent.tools_execution import set_take_profit
    deps.exchange.fetch_positions = AsyncMock(return_value=[])
    return await set_take_profit(deps, 66000.0, reasoning="test")


async def _invoke_adjust_leverage(deps, mocker):
    """Happy path: no position -> set_leverage no-op + record_action skipped (db_engine=None).

    Empty fetch_positions required since iter-tool-opt-adjust-leverage-guard
    added an explicit reject when holding (was phantom guard).
    """
    from src.agent.tools_execution import adjust_leverage
    deps.exchange.fetch_positions = AsyncMock(return_value=[])
    deps.exchange.set_leverage = AsyncMock(return_value=None)
    return await adjust_leverage(deps, 5, reasoning="test")


async def _invoke_set_price_volatility_alert(deps, mocker):
    """Create path: lazy-set the singleton."""
    from src.agent.tools_execution import set_price_volatility_alert
    deps.exchange.get_alert_params = mocker.Mock(return_value=None)
    deps.exchange.set_volatility_alert = mocker.Mock()
    return await set_price_volatility_alert(deps, 1.5, 30, reasoning="test")


async def _invoke_cancel_order(deps, mocker):
    """Early return: order not found."""
    from src.agent.tools_execution import cancel_order
    deps.exchange.fetch_open_orders = AsyncMock(return_value=[])
    return await cancel_order(deps, "nonexistent-id", reasoning="test")


async def _invoke_add_price_level_alert(deps, mocker):
    """Early return: invalid direction (must be 'above' or 'below')."""
    from src.agent.tools_execution import add_price_level_alert
    return await add_price_level_alert(deps, 64000.0, "sideways", reasoning="test")


async def _invoke_set_next_wake(deps, mocker):
    """Early return: set_next_wake_fn=None."""
    from src.agent.tools_execution import set_next_wake
    return await set_next_wake(deps, 30, reasoning="test")


async def _invoke_set_next_wake_at(deps, mocker):
    """Early return: set_next_wake_fn=None (MockDeps default)."""
    from src.agent.tools_execution import set_next_wake_at
    return await set_next_wake_at(deps, "10:37", reasoning="test")


async def _invoke_place_limit_order(deps, mocker):
    """Early return: invalid side (must be 'long' or 'short')."""
    from src.agent.tools_execution import place_limit_order
    return await place_limit_order(deps, "neutral", 64000.0, 10.0, 5, reasoning="test")


async def _invoke_place_limit_order_happy(deps, mocker):
    """F-P13: happy path through create_order — covers new multi-line `Note:` return."""
    from src.agent.tools_execution import place_limit_order
    deps.exchange.fetch_positions = AsyncMock(return_value=[])
    deps.exchange.set_leverage = AsyncMock(return_value=None)
    deps.exchange.fetch_balance = AsyncMock(return_value=Balance(
        total_usdt=10000.0, free_usdt=8000.0, used_usdt=2000.0,
    ))
    deps.exchange.amount_to_precision = mocker.Mock(return_value=0.05)
    deps.exchange.create_order = AsyncMock(return_value=Order(
        id="abc12345-6789-0123-4567-89abcdef0123",  # UUID-shaped
        symbol="BTC/USDT:USDT", side="buy", order_type="limit",
        amount=0.05, price=80000.0, status="open",
    ))
    return await place_limit_order(
        deps, "long", 80000.0, 10.0, 5, reasoning="test entry",
    )


@pytest.mark.asyncio
async def test_save_memory_fact_only():
    """save_memory output (typical save + neutral content) must not emit banned subjective words."""
    from src.agent.tools_memory import save_memory
    deps = MockDeps()
    deps.memory.save_long_term = AsyncMock(return_value=None)
    output = await save_memory(deps, "lesson", "Reduced position size after observing slippage", 0.5)
    hits = _scan(output)
    assert hits == [], f"save_memory emitted banned words: {hits}"


@pytest.mark.asyncio
async def test_place_limit_order_return_format_unchanged(mocker):
    """T-FP13.1 (AC-2): 'ID:' + UUID format strong assertion.

    order.id = str(uuid.uuid4()) — assert head 8 hex + dash explicit
    UUID shape so a simple 8-hex regex match doesn't pass weakly.
    """
    deps = MockDeps()
    result = await _invoke_place_limit_order_happy(deps, mocker)
    assert "ID: " in result
    assert re.search(r"ID: [a-f0-9]{8}-", result), \
        f"expected UUID format ID: xxxxxxxx-..., got: {result}"


@pytest.mark.asyncio
async def test_place_limit_order_return_includes_async_note(mocker):
    """T-FP13.2 (AC-1): return string contains 'only submits' AND 'has been filled'.

    sim #8 cycle 4de0585a 实证误读对齐：agent prose 用词 'limit not filled'，
    提示用 'has been filled' 命中相同 mental concept。
    """
    deps = MockDeps()
    result = await _invoke_place_limit_order_happy(deps, mocker)
    assert "only submits" in result
    assert "has been filled" in result


@pytest.mark.asyncio
async def test_place_limit_order_return_no_decision_label(mocker):
    """T-FP13.3 (AC-3): fact-only regression — _scan(output) helper applies
    full FACT_ONLY_BANNED_WORDS_RE + FACT_ONLY_BANNED_PHRASES_RE regex sets.
    """
    deps = MockDeps()
    result = await _invoke_place_limit_order_happy(deps, mocker)
    hits = _scan(result)
    assert hits == [], f"banned regex hits: {hits}"


async def _invoke_place_limit_order_with_position(deps, mocker, position_leverage: int, requested_leverage: int):
    """iter-tool-opt-limit-order-leverage-override helper: place limit order
    with an existing position at `position_leverage`, requesting `requested_leverage`.
    """
    from src.agent.tools_execution import place_limit_order
    deps.exchange.fetch_positions = AsyncMock(return_value=[Position(
        symbol="BTC/USDT:USDT", side="long", contracts=0.1, entry_price=80000.0,
        unrealized_pnl=0.0, leverage=position_leverage, liquidation_price=None,
    )])
    deps.exchange.set_leverage = AsyncMock(return_value=None)
    deps.exchange.fetch_balance = AsyncMock(return_value=Balance(
        total_usdt=10000.0, free_usdt=8000.0, used_usdt=2000.0,
    ))
    deps.exchange.amount_to_precision = mocker.Mock(return_value=0.05)
    deps.exchange.create_order = AsyncMock(return_value=Order(
        id="abc12345-6789-0123-4567-89abcdef0123",
        symbol="BTC/USDT:USDT", side="buy", order_type="limit",
        amount=0.05, price=80000.0, status="open",
    ))
    return await place_limit_order(
        deps, "long", 80000.0, 10.0, requested_leverage, reasoning="test entry",
    )


@pytest.mark.asyncio
async def test_place_limit_order_explicit_override_when_position_diff_leverage(mocker):
    """iter-tool-opt-limit-order-leverage-override: principle 1 fact-provider —
    silent override of requested leverage by position's leverage must be surfaced.
    """
    deps = MockDeps()
    result = await _invoke_place_limit_order_with_position(
        deps, mocker, position_leverage=5, requested_leverage=10,
    )
    assert "5x (matched existing position; requested 10x ignored)" in result, \
        f"expected explicit override suffix, got: {result}"


@pytest.mark.asyncio
async def test_place_limit_order_no_override_msg_when_leverage_matches(mocker):
    """No suffix when requested leverage matches existing position's leverage."""
    deps = MockDeps()
    result = await _invoke_place_limit_order_with_position(
        deps, mocker, position_leverage=5, requested_leverage=5,
    )
    assert "5x" in result
    assert "matched existing position" not in result, \
        f"spurious override suffix when leverage matches, got: {result}"


@pytest.mark.asyncio
async def test_place_limit_order_no_override_msg_when_empty_position(mocker):
    """No suffix when no existing position — requested leverage is the actual leverage."""
    deps = MockDeps()
    result = await _invoke_place_limit_order_happy(deps, mocker)
    # _invoke_place_limit_order_happy uses fetch_positions=[] and requested_leverage=5
    assert "5x" in result
    assert "matched existing position" not in result, \
        f"spurious override suffix when no position, got: {result}"
