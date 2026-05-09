"""F7 OKX REST OHLCV helper — unit tests (mock-only, no live REST)."""
from __future__ import annotations

import pytest


# ===== TF_MS drift guard (AC-F7-4 配套) =====

EXPECTED_TF_MS = {
    "1m": 60_000,
    "5m": 300_000,
    "15m": 900_000,
    "1h": 3_600_000,
    "4h": 14_400_000,
    "1d": 86_400_000,
}

EXPECTED_TIMEFRAMES = frozenset({"1m", "5m", "15m", "1h", "4h", "1d"})


def test_tf_ms_dict_matches_expected():
    """spec §2.2 决议：tf_ms 硬编码 dict（不走 ccxt.parse_timeframe），本测试是 drift guard。"""
    from scripts.fetch_session_ohlcv import TF_MS
    assert TF_MS == EXPECTED_TF_MS, f"TF_MS drift: {TF_MS} vs {EXPECTED_TF_MS}"


def test_timeframes_whitelist_matches_tf_ms_keys():
    """spec §3.4：TIMEFRAMES 白名单与 TF_MS 同位维护。"""
    from scripts.fetch_session_ohlcv import TIMEFRAMES, TF_MS
    assert frozenset(TIMEFRAMES) == frozenset(TF_MS.keys()) == EXPECTED_TIMEFRAMES
