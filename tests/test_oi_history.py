"""Tests for OI history fetch + anchors + delta rendering.

Covers spec sections:
  §2.1 OpenInterestHistoryPoint + _OKX_OI_PERIOD
  §2.2/2.3 OKX + Simulated fetch_open_interest_history
  §2.4 MarketDataService.get_open_interest_history
  §2.5 render helpers + get_derivatives_data wire
  §5.2 19 unit tests + §5.3 simulated integration + §5.4 drift guard
"""
import pytest


def test_oi_history_point_dataclass_fields():
    from src.integrations.exchange.base import OpenInterestHistoryPoint
    p = OpenInterestHistoryPoint(timestamp=1778644800000, open_interest=33174.25, open_interest_value=2693065783.51)
    assert p.timestamp == 1778644800000
    assert p.open_interest == pytest.approx(33174.25)
    assert p.open_interest_value == pytest.approx(2693065783.51)


def test_okx_oi_period_mapping():
    from src.integrations.exchange.base import _OKX_OI_PERIOD
    assert _OKX_OI_PERIOD == {"5m": "5m", "1h": "1H", "1d": "1D"}
