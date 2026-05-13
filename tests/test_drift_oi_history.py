"""Drift guard for OI history anchor render format (T-DG-OI-1).

Spec §5.4 — assert that a snapshot of get_derivatives_data output containing
a happy-path OI line includes the exact anchor substrings '(1h ago ' and
'24h ago '. Prevents accidental regression of the anchor inline format
during future R2-8c-style sectioning refactors.
"""
import pytest
from unittest.mock import AsyncMock, MagicMock


@pytest.mark.asyncio
async def test_oi_anchor_format_drift_guard():
    from src.agent.tools_perception import get_derivatives_data
    from src.integrations.exchange.base import (
        FundingRate, LongShortRatio, OpenInterestHistoryPoint,
    )

    deps = MagicMock()
    deps.symbol = "BTC/USDT:USDT"
    deps.market_data = MagicMock()
    deps.market_data.get_funding_rate = AsyncMock(return_value=FundingRate(
        symbol="BTC/USDT:USDT", rate=0.000014,
        next_funding_time=1778660000000, timestamp=1778645000000,
    ))
    deps.market_data.get_long_short_ratio = AsyncMock(return_value=LongShortRatio(
        symbol="BTC/USDT:USDT", long_short_ratio=0.66,
        long_ratio=0.399, short_ratio=0.601, timestamp=1778645000000,
    ))
    # 26-record OI history with distinct anchors.
    vals = [2_900_000_000.0] * 26
    vals[-25] = 2_910_000_000.0
    vals[-2] = 2_930_000_000.0
    vals[-1] = 2_920_000_000.0
    points = [
        OpenInterestHistoryPoint(timestamp=1778640000000 + i * 3600000,
                                 open_interest=v / 80000.0, open_interest_value=v)
        for i, v in enumerate(vals)
    ]
    deps.market_data.get_open_interest_history = AsyncMock(return_value=points)

    out = await get_derivatives_data(deps, "BTC/USDT:USDT")

    # Drift guard: exact anchor substrings — refactors must not silently
    # drop the inline anchor format (e.g., move to sub-line, change "ago"
    # to "back", reorder windows, etc.).
    assert "(1h ago " in out, "1h-anchor inline format dropped — refactor regression?"
    assert "24h ago " in out, "24h-anchor inline format dropped — refactor regression?"
    # Both must appear in the SAME line as the current OI value (inline form).
    oi_line = [ln for ln in out.splitlines() if ln.startswith("Open Interest:")][0]
    assert "1h ago" in oi_line and "24h ago" in oi_line
