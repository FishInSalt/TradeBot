"""Tests for get_position Fee & Breakeven section + gross labels (Task 18).

Spec: docs/superpowers/plans/ iter-tool-opt-fee-visibility Task 18.

Fixture pattern: MagicMock deps with AsyncMock IO — mirrors
test_iter_tool_opt_mark_vs_last.py mock_deps_for_position pattern.
fee_rate set explicitly on deps (TradingDeps.fee_rate field added Task 2).
"""
from __future__ import annotations

import re
from unittest.mock import AsyncMock, MagicMock

import pandas as pd
import pytest

from src.integrations.exchange.base import Balance, Position, Ticker


def _make_deps(
    *,
    side: str = "long",
    entry_price: float = 80_000.0,
    contracts: float = 0.5,
    current_price: float = 80_200.0,
    fee_rate: float = 0.001,
    initial_balance: float = 10_000.0,
    unrealized_pnl: float = 100.0,
) -> MagicMock:
    """Minimal deps mock for get_position tests.

    Sets fee_rate, position, ticker, balance, orders, contract_size, mark_price.
    OHLCV is empty DataFrame → no ATR suffix (cleaner assertions).
    """
    deps = MagicMock()
    deps.symbol = "BTC/USDT:USDT"
    deps.initial_balance = initial_balance
    deps.fee_rate = fee_rate

    deps.exchange.fetch_positions = AsyncMock(return_value=[
        Position(
            symbol="BTC/USDT:USDT",
            side=side,
            contracts=contracts,
            entry_price=entry_price,
            unrealized_pnl=unrealized_pnl,
            leverage=10,
            liquidation_price=50_000.0 if side == "long" else 120_000.0,
            created_at=None,
        ),
    ])
    deps.exchange.fetch_balance = AsyncMock(return_value=Balance(
        total_usdt=10_500.0, free_usdt=8_000.0, used_usdt=2_500.0,
    ))
    deps.exchange.fetch_open_orders = AsyncMock(return_value=[])
    deps.exchange.get_contract_size = AsyncMock(return_value=1.0)
    deps.exchange.get_mark_price = AsyncMock(return_value=current_price)
    deps.exchange.algo_trigger_reference = "last"
    deps.market_data.get_ticker = AsyncMock(return_value=Ticker(
        symbol="BTC/USDT:USDT",
        last=current_price,
        bid=current_price - 5.0,
        ask=current_price + 5.0,
        high=current_price * 1.02,
        low=current_price * 0.98,
        base_volume=12_000.0,
        timestamp=1_715_040_000_000,
    ))
    # Empty OHLCV → no ATR suffix (cleaner assertions)
    deps.market_data.get_ohlcv_dataframe = AsyncMock(return_value=pd.DataFrame())
    return deps


@pytest.mark.asyncio
async def test_renders_fee_breakeven_section_long():
    """Long position renders Fee & Breakeven section with formula and signed distance."""
    from src.agent.tools_perception import get_position

    # entry=80000, contracts=0.5, fee_rate=0.001, current=80200
    # entry_fee = 80000 × 0.5 × 0.001 = 40.00
    # breakeven = 80000 × (1 + 2 × 0.001) = 80000 × 1.002 = 80160.00
    # distance = 80200 - 80160 = +40 pts
    deps = _make_deps(side="long", entry_price=80_000.0, contracts=0.5,
                      current_price=80_200.0, fee_rate=0.001)
    out = await get_position(deps, "BTC/USDT:USDT")

    assert "=== Fee & Breakeven ===" in out
    assert "Entry fee paid: ~-40.00 USDT (= entry × contracts × contract_size × rate)" in out
    assert "Breakeven: 80,160.00" in out
    assert "[current 80,200.00, +40 pts]" in out
    assert "= 80,000.00 × (1 + 2 × fee_rate) [long round-trip taker]" in out


@pytest.mark.asyncio
async def test_renders_fee_breakeven_section_short():
    """Short position uses (1 − 2r) formula with Unicode minus."""
    from src.agent.tools_perception import get_position

    # entry=80000, contracts=0.5, fee_rate=0.001, current=79800
    # breakeven = 80000 × (1 - 0.002) = 79840.00
    # distance = 79840 - 79800 = +40 pts
    deps = _make_deps(side="short", entry_price=80_000.0, contracts=0.5,
                      current_price=79_800.0, fee_rate=0.001,
                      unrealized_pnl=100.0)
    out = await get_position(deps, "BTC/USDT:USDT")

    assert "Breakeven: 79,840.00" in out
    # Unicode minus sign U+2212, not hyphen
    assert "= 80,000.00 × (1 − 2 × fee_rate) [short round-trip taker]" in out


@pytest.mark.asyncio
async def test_fee_breakeven_section_does_not_render_fee_rate_number():
    """Drift guard: rate digits only in system prompt (single-source principle).

    Fee & Breakeven section MUST NOT print fee_rate as a number — only entry_fee
    + breakeven + formula caption with `fee_rate` symbol.
    """
    from src.agent.tools_perception import get_position

    deps = _make_deps(side="long", entry_price=80_000.0, contracts=0.5,
                      current_price=80_200.0, fee_rate=0.001)
    out = await get_position(deps, "BTC/USDT:USDT")

    fb_start = out.index("=== Fee & Breakeven ===")
    # Find the next "===" that starts a new section after the header itself
    search_from = fb_start + len("=== Fee & Breakeven ===")
    fb_end = out.index("===", search_from)
    fb_segment = out[fb_start:fb_end]

    assert "0.001" not in fb_segment
    assert "0.05%" not in fb_segment
    assert "0.10%" not in fb_segment
    assert "0.20%" not in fb_segment


@pytest.mark.asyncio
async def test_entry_fee_matches_recompute_formula():
    """Entry fee = entry_price × contracts × fee_rate (math identity).

    entry=81878.6, contracts=0.366, fee_rate=0.001
    expected = 81878.6 × 0.366 × 0.001 = 29.96756... → formatted as ~-29.97
    """
    from src.agent.tools_perception import get_position

    deps = _make_deps(side="long", entry_price=81_878.6, contracts=0.366,
                      current_price=82_000.0, fee_rate=0.001)
    out = await get_position(deps, "BTC/USDT:USDT")

    assert "Entry fee paid: ~-29.97 USDT" in out


@pytest.mark.asyncio
async def test_position_section_includes_gross_label():
    """Position section: Unrealized line carries '(gross)' label."""
    from src.agent.tools_perception import get_position

    deps = _make_deps()
    out = await get_position(deps, "BTC/USDT:USDT")

    assert re.search(r"Unrealized: [+\-]\d+\.\d+ USDT \(gross\)", out), (
        f"'Unrealized: ... USDT (gross)' not found in output:\n{out}"
    )


@pytest.mark.asyncio
async def test_pnl_section_includes_gross_label():
    """PnL section: PnL line carries 'gross' label."""
    from src.agent.tools_perception import get_position

    deps = _make_deps()
    out = await get_position(deps, "BTC/USDT:USDT")

    assert re.search(r"PnL: [+\-]\d+\.\d+ USDT gross", out), (
        f"'PnL: ... USDT gross' not found in output:\n{out}"
    )


@pytest.mark.asyncio
async def test_fee_breakeven_section_uses_contract_size_factor():
    """Entry fee formula = entry × contracts × contract_size × rate.

    Drift guard for OKX live where contract_size != 1.0 (e.g., BTC swap 0.01,
    ETH swap 0.1). Without the contract_size factor, USDT-denominated fees
    would be off by 10-100× on OKX.

    entry=80000, contracts=10 contracts (= 0.1 BTC at contract_size=0.01),
    fee_rate=0.001 → entry_fee_USDT = 80000 × 10 × 0.01 × 0.001 = 8.00
    """
    from src.agent.tools_perception import get_position

    deps = _make_deps(side="long", entry_price=80_000.0, contracts=10.0,
                      current_price=80_200.0, fee_rate=0.001)
    deps.exchange.get_contract_size = AsyncMock(return_value=0.01)
    out = await get_position(deps, "BTC/USDT:USDT")

    assert "=== Fee & Breakeven ===" in out
    assert "Entry fee paid: ~-8.00 USDT" in out, (
        f"contract_size factor missing — got:\n{out}"
    )


@pytest.mark.asyncio
async def test_fee_breakeven_long_distance_negative_when_below_breakeven():
    """Long position below breakeven: distance pts should be negative."""
    from src.agent.tools_perception import get_position

    # breakeven = 80000 × 1.002 = 80160, current = 80100 → distance = 80100 - 80160 = -60
    deps = _make_deps(side="long", entry_price=80_000.0, contracts=0.5,
                      current_price=80_100.0, fee_rate=0.001)
    out = await get_position(deps, "BTC/USDT:USDT")

    assert "[current 80,100.00, -60 pts]" in out
