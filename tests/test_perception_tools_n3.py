"""Tests for the 4 N3 perception tools."""
from dataclasses import dataclass
from unittest.mock import AsyncMock, MagicMock

import pandas as pd
import pytest

from src.integrations.crypto_etf.models import ETFFlowEntry
from src.integrations.macro.models import (
    EquityQuote, FREDObservation, MacroSnapshot,
)
from src.integrations.onchain.models import StablecoinSnapshot, StablecoinTotal


@dataclass
class MockDeps:
    symbol: str = "BTC/USDT:USDT"
    timeframe: str = "15m"
    market_data: object = None
    exchange: object = None
    technical: object = None
    memory: object = None
    session_id: str = "test"
    db_engine: object = None
    approval_gate: object = None
    approval_enabled: bool = False
    wake_min_minutes: int = 1
    wake_max_minutes: int = 60
    set_next_wake_fn: object = None
    initial_balance: float = 10000.0
    metrics: object = None
    news: object = None
    macro: object = None
    crypto_etf: object = None
    onchain: object = None


def _make_deps(**overrides) -> MockDeps:
    return MockDeps(**overrides)


# ===== get_higher_timeframe_view =====

def _make_ohlcv_df(n_rows: int, last_close: float = 75_234.50) -> pd.DataFrame:
    """Build a synthetic OHLCV dataframe of n_rows.

    Prices ascend linearly so MAs are deterministic; highs add +500 and
    lows subtract -500 for a stable range.

    NOTE: this shape is intentionally extreme — 100-period high always falls
    in the last row, so range position is ~92%. Tests below assert on string
    presence only, not numeric correctness of range position. If a future
    test asserts on the range-position number, replace this helper with a
    fixture that produces a less degenerate shape.
    """
    base = last_close - (n_rows - 1) * 50
    rows = []
    for i in range(n_rows):
        close = base + i * 50
        rows.append({
            "timestamp": 1_776_000_000 + i * 86_400_000,
            "open": close - 10, "high": close + 500, "low": close - 500,
            "close": close, "volume": 1000.0,
        })
    return pd.DataFrame(rows)


async def test_htf_view_format_1d():
    from src.agent.tools_perception import get_higher_timeframe_view

    market_data = AsyncMock()
    market_data.get_ohlcv_dataframe.return_value = _make_ohlcv_df(250)
    deps = _make_deps(market_data=market_data)

    result = await get_higher_timeframe_view(deps, timeframe="1d")

    assert "Higher Timeframe View (1d" in result
    assert "BTC/USDT:USDT" in result
    assert "MA50:" in result
    assert "MA100:" in result
    assert "MA200:" in result
    assert "100-period High" in result
    assert "100-period Low" in result
    assert "Current price within range" in result
    assert "20-period High" in result
    assert "20-period Low" in result
    assert "20-period range width" in result
    # Period-unit label: 1d → "days"
    assert "days ago" in result


async def test_htf_view_period_label_for_4h():
    from src.agent.tools_perception import get_higher_timeframe_view

    market_data = AsyncMock()
    market_data.get_ohlcv_dataframe.return_value = _make_ohlcv_df(250)
    deps = _make_deps(market_data=market_data)

    result = await get_higher_timeframe_view(deps, timeframe="4h")
    assert "4h-bars ago" in result


async def test_htf_view_period_label_for_1w():
    from src.agent.tools_perception import get_higher_timeframe_view

    market_data = AsyncMock()
    market_data.get_ohlcv_dataframe.return_value = _make_ohlcv_df(250)
    deps = _make_deps(market_data=market_data)

    result = await get_higher_timeframe_view(deps, timeframe="1w")
    assert "weeks ago" in result


async def test_htf_view_period_label_for_1m():
    from src.agent.tools_perception import get_higher_timeframe_view

    market_data = AsyncMock()
    market_data.get_ohlcv_dataframe.return_value = _make_ohlcv_df(250)
    deps = _make_deps(market_data=market_data)

    result = await get_higher_timeframe_view(deps, timeframe="1M")
    assert "months ago" in result


async def test_htf_view_passes_symbol_and_limit_to_market_data():
    from src.agent.tools_perception import get_higher_timeframe_view

    market_data = AsyncMock()
    market_data.get_ohlcv_dataframe.return_value = _make_ohlcv_df(250)
    deps = _make_deps(market_data=market_data)

    await get_higher_timeframe_view(deps, timeframe="1d")
    market_data.get_ohlcv_dataframe.assert_awaited_once_with(
        "BTC/USDT:USDT", "1d", limit=250,
    )


async def test_htf_view_has_no_subjective_labels():
    """Spec §3.1: no 'uptrend / strong / upper third' labels — fact-only."""
    from src.agent.tools_perception import get_higher_timeframe_view

    market_data = AsyncMock()
    market_data.get_ohlcv_dataframe.return_value = _make_ohlcv_df(250)
    deps = _make_deps(market_data=market_data)
    result = await get_higher_timeframe_view(deps, timeframe="1d")

    lower = result.lower()
    for label in ("uptrend", "downtrend", "strong", "weak",
                  "bullish", "bearish", "upper third", "lower third",
                  "signals", "precedes", "follows"):
        assert label not in lower, f"found subjective label '{label}'"


async def test_htf_view_upstream_failure_degrades():
    from src.agent.tools_perception import get_higher_timeframe_view

    market_data = AsyncMock()
    market_data.get_ohlcv_dataframe.side_effect = RuntimeError("OKX down")
    deps = _make_deps(market_data=market_data)
    result = await get_higher_timeframe_view(deps, timeframe="1d")

    assert "temporarily unavailable" in result.lower()


async def test_htf_view_insufficient_data_for_ma200():
    """If fewer than 200 candles are returned, MA200 degrades but others work."""
    from src.agent.tools_perception import get_higher_timeframe_view

    market_data = AsyncMock()
    market_data.get_ohlcv_dataframe.return_value = _make_ohlcv_df(150)
    deps = _make_deps(market_data=market_data)
    result = await get_higher_timeframe_view(deps, timeframe="1d")

    assert "MA50:" in result
    assert "MA100:" in result
    # MA200 should appear but flagged as insufficient.
    assert "MA200" in result
    assert "insufficient data" in result.lower()
