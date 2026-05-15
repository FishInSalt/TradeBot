"""Iter tool-opt-position-atr-closed-bars drift guards (G-calc-rigor-audit §G-1).

Two guards:
  1. Call-site static: every `compute_indicators(...)` invocation inside
     `src/agent/tools_perception.py` must pass a closed-bars-only frame
     (R2-Next-D §6.4 algorithm-lock invariant). Catches future regressions
     where a new caller forgets `_closed_bars(df)`.
  2. Numeric behavior: a fixture with an extreme in-progress 1h bar must
     leave the rendered ATR-multiple suffix matching the closed-only ATR(14),
     not the raw-df ATR(14).
"""
from __future__ import annotations

import re
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pandas as pd
import pytest


def test_compute_indicators_callsite_uses_closed_bars():
    """Static guard: tools_perception.py compute_indicators calls must pass
    a closed-only frame.

    Approach: regex-scan call sites and require the single positional arg
    to contain "closed" (matches `df_closed`, `_closed_bars(df)`, etc.).
    Naming convention is part of the contract here — `_closed_bars(...)`
    inline also satisfies the substring check.
    """
    src_path = Path(__file__).resolve().parent.parent / "src" / "agent" / "tools_perception.py"
    src = src_path.read_text()
    # Match `.compute_indicators(<arg>)` with a single token / paren-call arg.
    calls = re.findall(r"\.compute_indicators\(([^)]+)\)", src)
    assert calls, "no compute_indicators call sites found — guard fixture broken"
    bad = [arg.strip() for arg in calls if "closed" not in arg.lower()]
    assert not bad, (
        f"compute_indicators called with non-closed-only arg(s): {bad}. "
        f"Every caller must pass _closed_bars(df) per R2-Next-D §6.4."
    )


def _make_1h_df_extreme_last_bar() -> pd.DataFrame:
    """50-bar stationary 1h OHLCV with an extreme in-progress final bar.

    Bars 0..48 (closed): high=100, low=99, close=99.5 — ATR(14) converges to 1.0.
    Bar 49 (in-progress): high=500, low=10, close=400 — when included raw,
    inflates atr_14 by ~3× via Wilder smoothing.
    """
    n = 50
    rows = [{"open": 99.5, "high": 100.0, "low": 99.0, "close": 99.5, "volume": 10.0}
            for _ in range(n - 1)]
    rows.append({"open": 99.5, "high": 500.0, "low": 10.0, "close": 400.0, "volume": 10.0})
    df = pd.DataFrame(rows)
    df.index = pd.date_range("2026-01-01", periods=n, freq="1h", tz="UTC")
    return df


@pytest.mark.asyncio
async def test_position_atr_strips_in_progress_bar():
    """Numeric drift guard: with an extreme in-progress 1h bar in the
    OHLCV frame, the ATR-multiple suffix in get_position output must
    equal the closed-only ATR(14), not the raw-df ATR(14).
    """
    from src.agent.tools_perception import get_position
    from src.integrations.exchange.base import Balance, Position, Ticker
    from src.services.technical import TechnicalAnalysisService
    from src.utils.ohlcv_utils import _closed_bars

    df = _make_1h_df_extreme_last_bar()
    svc = TechnicalAnalysisService()
    atr_closed = svc.compute_indicators(_closed_bars(df))["atr_14"]
    atr_raw = svc.compute_indicators(df)["atr_14"]
    assert atr_closed is not None and atr_raw is not None
    assert atr_raw != atr_closed, (
        f"fixture failed to discriminate closed vs raw: "
        f"atr_closed={atr_closed} atr_raw={atr_raw}"
    )

    deps = MagicMock()
    deps.symbol = "BTC/USDT:USDT"
    deps.initial_balance = 10_000.0
    deps.fee_rate = 0.0005
    deps.technical = svc
    deps.exchange.fetch_positions = AsyncMock(return_value=[
        Position(symbol="BTC/USDT:USDT", side="long", contracts=1.0,
                 entry_price=100.0, unrealized_pnl=0.0, leverage=10,
                 liquidation_price=90.0, created_at=None),
    ])
    deps.exchange.fetch_balance = AsyncMock(return_value=Balance(
        total_usdt=10_000.0, free_usdt=8_000.0, used_usdt=2_000.0,
    ))
    deps.exchange.fetch_open_orders = AsyncMock(return_value=[])
    deps.exchange.get_contract_size = AsyncMock(return_value=0.01)
    deps.exchange.get_mark_price = AsyncMock(return_value=100.0)
    deps.exchange.algo_trigger_reference = "last"
    deps.market_data.get_ticker = AsyncMock(return_value=Ticker(
        symbol="BTC/USDT:USDT", last=100.0, bid=99.5, ask=100.5,
        high=500.0, low=10.0, base_volume=10.0, timestamp=1_715_040_000_000,
    ))
    deps.market_data.get_ohlcv_dataframe = AsyncMock(return_value=df)

    out = await get_position(deps)

    # Rendering math: mark=100, liq=90 → liq_dist_pct = 10.0%; atr_mult = liq_dist_pct / atr_pct.
    liq_dist_pct = 10.0
    atr_pct_closed = atr_closed / 100.0 * 100
    atr_pct_raw = atr_raw / 100.0 * 100
    atr_mult_closed = liq_dist_pct / atr_pct_closed
    atr_mult_raw = liq_dist_pct / atr_pct_raw

    expected_suffix = f"= {atr_mult_closed:.1f}× ATR(1h)"
    forbidden_suffix = f"= {atr_mult_raw:.1f}× ATR(1h)"
    assert expected_suffix in out, (
        f"expected closed-only ATR mult '{expected_suffix}' not in output:\n{out}"
    )
    if f"{atr_mult_closed:.1f}" != f"{atr_mult_raw:.1f}":
        assert forbidden_suffix not in out, (
            f"raw-df ATR mult '{forbidden_suffix}' leaked into output:\n{out}"
        )
