"""Iter tool-opt-pivots-closed-bars drift guards (G-calc-rigor-audit §G-2).

Two guards:
  1. Call-site static: every `_compute_swing_pivots(...)` invocation inside
     `src/agent/tools_perception.py` must pass a closed-bars-only frame.
     Catches future regressions where a new caller forgets `_closed_bars(df)`.
  2. Numeric/bars_ago anchor: with a 31-bar raw fixture (30 closed + 1
     in-progress), a swing high at row 15 must render bars_ago=14 (anchored
     at the most-recent closed bar), not 15 (anchored at the in-progress bar).
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from unittest.mock import AsyncMock

import pandas as pd
import pytest

from src.agent.tools_perception import get_price_pivots
from src.integrations.exchange.base import Ticker


def test_compute_swing_pivots_callsite_uses_closed_bars():
    """Static guard: every `_compute_swing_pivots(<arg>, …)` call inside
    `src/agent/tools_perception.py` must pass a closed-bars-only frame.
    Mirrors `test_compute_indicators_callsite_uses_closed_bars` (G-1).
    """
    src_path = Path(__file__).resolve().parent.parent / "src" / "agent" / "tools_perception.py"
    src = src_path.read_text()
    # Match `_compute_swing_pivots(<arg>, …)` — first positional arg only.
    # Negative lookbehind skips the `def _compute_swing_pivots(` signature line.
    calls = re.findall(r"(?<!def )_compute_swing_pivots\(\s*([^,)\s]+)", src)
    assert calls, "no _compute_swing_pivots call sites found — guard fixture broken"
    bad = [arg for arg in calls if "closed" not in arg.lower()]
    assert not bad, (
        f"_compute_swing_pivots called with non-closed-only arg(s): {bad}. "
        f"Every caller must pass _closed_bars(df) per G-calc-rigor-audit §G-2."
    )


@dataclass
class _PivotsDeps:
    symbol: str = "BTC/USDT:USDT"
    timeframe: str = "5m"
    market_data: AsyncMock = field(default_factory=AsyncMock)


def _ticker_at(price: float = 66523.40) -> Ticker:
    return Ticker(
        symbol="BTC/USDT:USDT", last=price, bid=price - 0.5, ask=price + 0.5,
        high=price + 100.0, low=price - 100.0, base_volume=0.0, timestamp=0,
    )


def _df(highs: list[float], lows: list[float]) -> pd.DataFrame:
    n = len(highs)
    return pd.DataFrame({
        "open": highs, "high": highs, "low": lows, "close": highs,
        "volume": [1.0] * n,
    })


def _ohlcv_side_effect(by_tf: dict):
    async def _impl(symbol, timeframe, limit=None):
        result = by_tf.get(timeframe)
        if isinstance(result, Exception):
            raise result
        return result
    return _impl


@pytest.mark.asyncio
async def test_pivots_bars_ago_anchored_at_last_closed_bar():
    """Numeric drift guard: bars_ago must anchor at the most-recent closed
    bar after _closed_bars stripping.

    Fixture: 31 raw bars (= 30 closed + 1 in-progress). Bar 15 is a strict
    swing high (high=200, neighbors=100). Expected rendering:
      - Pre-fix (raw last_idx=30):   bars_ago = 30 - 15 = 15 → '15 bars ago'
      - Post-fix (closed last_idx=29): bars_ago = 29 - 15 = 14 → '14 bars ago'
    """
    deps = _PivotsDeps()
    deps.market_data.get_ticker = AsyncMock(return_value=_ticker_at())
    n = 31
    highs = [100.0] * n
    lows = [99.0] * n
    highs[15] = 200.0  # strict swing high (n=5 neighbors on each side all = 100)
    main_df = _df(highs, lows)
    short_df = _df([100.0], [99.0])  # prior periods: insufficient
    deps.market_data.get_ohlcv_dataframe = AsyncMock(side_effect=_ohlcv_side_effect({
        "5m": main_df, "1d": short_df, "1w": short_df, "1M": short_df,
    }))

    out = await get_price_pivots(deps)

    assert "Swing High: 200.00" in out, out
    assert "14 bars ago" in out, (
        f"expected closed-anchored bars_ago=14, output:\n{out}"
    )
    assert "15 bars ago" not in out, (
        f"raw-anchored bars_ago=15 leaked into output:\n{out}"
    )
