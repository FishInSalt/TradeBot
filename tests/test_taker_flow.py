"""Tests for get_taker_flow: rubik taker-volume fetch + minute-level flow rendering.

Covers spec docs/superpowers/specs/2026-05-30-order-flow-tools-redesign-design.md
§2 (rubik source), §3.1-3.3 (taker_flow design), §3.5 (errors), §4.1 (architecture),
§5 ①②③⑤⑥ (tests).
"""
import time
import pytest
from unittest.mock import AsyncMock, MagicMock


def test_taker_flow_bar_dataclass_fields():
    from src.integrations.exchange.base import TakerFlowBar
    b = TakerFlowBar(ts=1778644800000, sell_usd=5_800_000.0, buy_usd=4_200_000.0)
    assert b.ts == 1778644800000
    assert b.sell_usd == pytest.approx(5_800_000.0)
    assert b.buy_usd == pytest.approx(4_200_000.0)


def test_taker_volume_period_map_is_complete():
    """§3.1/§3.3/③: distinct from _OKX_OI_PERIOD; covers tool periods {5m,1h,4h,1d}
    PLUS the 1w anchor up-tier. Reusing _OKX_OI_PERIOD would KeyError on 4h/1w."""
    from src.integrations.exchange.base import _TAKER_VOLUME_PERIOD, _OKX_OI_PERIOD
    assert _TAKER_VOLUME_PERIOD == {"5m": "5m", "1h": "1H", "4h": "4H", "1d": "1D", "1w": "1W"}
    assert _TAKER_VOLUME_PERIOD is not _OKX_OI_PERIOD
    for p in ("5m", "1h", "4h", "1d", "1w"):
        assert p in _TAKER_VOLUME_PERIOD
