"""iter-tool-opt-getpos-mark-suppress — drift==0 suffix suppression.

Audit `.working/tool-audits/2026-05-28-get_position.md` 议题 1: sim 阶段 mark
== ticker.last (SimulatedExchange.get_mark_price returns _latest_ticker.last,
simulated.py:130-142), so the Mark line in get_position renders
`Mark: <X> (Last: <X>, drift +0.00%)` 100% of the time (129/129 in latest sim
run `session_f0f7b24f`). Fix: when drift rounds to ±0.00% at the .2f display
precision, omit the `(Last:..., drift ±X.XX%)` suffix entirely — keep only
`Mark: <X>`. Saves ~1 line of tokens per open-position render under sim, and
remains a faithful fact-only rendering under live (only suppresses when there
is literally no drift to report).

Reuses `mock_deps_for_position` fixture from
`tests/test_iter_tool_opt_mark_vs_last.py` indirectly via inline rebuild for
isolation; the fixture pattern is identical.
"""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pandas as pd
import pytest

from src.integrations.exchange.base import Balance, Position, Ticker


def _make_deps_mark_eq_last(last_price: float, mark_price: float):
    """Build minimal deps with full IO mocked. `last_price` and `mark_price`
    are the only knobs callers vary across tests."""
    deps = MagicMock()
    deps.symbol = "BTC/USDT:USDT"
    deps.initial_balance = 10_000.0
    deps.fee_rate = 0.0005

    deps.exchange.fetch_positions = AsyncMock(return_value=[
        Position(symbol="BTC/USDT:USDT", side="long", contracts=0.5,
                 entry_price=80_000.0, unrealized_pnl=0.0, leverage=10,
                 liquidation_price=51_000.0, created_at=None),
    ])
    deps.exchange.fetch_balance = AsyncMock(return_value=Balance(
        total_usdt=10_500.0, free_usdt=8_000.0, used_usdt=2_500.0,
    ))
    deps.exchange.fetch_open_orders = AsyncMock(return_value=[])
    deps.exchange.get_contract_size = AsyncMock(return_value=0.01)
    deps.exchange.get_mark_price = AsyncMock(return_value=mark_price)
    deps.exchange.algo_trigger_reference = "last"
    deps.market_data.get_ticker = AsyncMock(return_value=Ticker(
        symbol="BTC/USDT:USDT", last=last_price, bid=last_price - 1, ask=last_price + 1,
        high=last_price + 2_000, low=last_price - 2_000, base_volume=12_000.0,
        timestamp=1_715_040_000_000,
    ))
    deps.market_data.get_ohlcv_dataframe = AsyncMock(return_value=pd.DataFrame())
    return deps


@pytest.mark.asyncio
async def test_mark_line_omits_drift_suffix_when_mark_equals_last_exactly():
    """Sim signature: mark == last exactly → drift = 0.00% exact → omit the
    whole `(Last:..., drift ...)` suffix.
    """
    from src.agent.tools_perception import get_position

    deps = _make_deps_mark_eq_last(last_price=80_000.0, mark_price=80_000.0)
    out = await get_position(deps)

    # (a) Mark line still present with its value
    assert "Mark: 80000.00" in out
    # (b) Last/drift suffix omitted (no parenthesized tail on the Mark line)
    assert "drift" not in out
    assert "Last: 80000.00" not in out
    # (c) Risk Exposure section still renders the rest normally
    assert "Notional value:" in out
    assert "Margin used:" in out
    # (d) Liquidation distance still rendered (anchored to mark, independent of drift)
    assert "Liquidation: 51000.00 (36.25% away)" in out


@pytest.mark.asyncio
async def test_mark_line_omits_drift_suffix_when_drift_rounds_to_zero():
    """|drift| < 0.005% rounds to +0.00% at .2f → suppress.

    Fixture: mark=80000, last=80001 → drift = 0.00125% → renders +0.00% at
    .2f. Guards against the rounding-edge regression where suppression is
    keyed off `drift_pct == 0.0` (exact equality) and misses near-zero drift
    that still displays as +0.00%.
    """
    from src.agent.tools_perception import get_position

    deps = _make_deps_mark_eq_last(last_price=80_001.0, mark_price=80_000.0)
    out = await get_position(deps)

    assert "Mark: 80000.00" in out
    assert "drift" not in out


@pytest.mark.asyncio
async def test_mark_line_keeps_drift_suffix_when_drift_visibly_nonzero():
    """Regression lock: drift >= 0.005% (renders nonzero at .2f) must still
    show the `(Last:..., drift ±X.XX%)` suffix — the suppression is narrow.

    Fixture: mark=80000, last=80048 → drift = +0.06% (visibly nonzero).
    """
    from src.agent.tools_perception import get_position

    deps = _make_deps_mark_eq_last(last_price=80_048.0, mark_price=80_000.0)
    out = await get_position(deps)

    assert "Mark: 80000.00 (Last: 80048.00, drift +0.06%)" in out
