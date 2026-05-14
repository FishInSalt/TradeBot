"""Iter tool-opt-bb-ddof drift guard (G-calc-rigor-audit §G-5).

Numerical guard: BB(20, 2) must use ddof=0 (population stdev) to align with
TradingView and TA-Lib. ddof=1 (sample stdev) — the pandas_ta pure-pandas
default — inflates band width by sqrt(N/(N-1)) ≈ 1.026 at N=20.

Cross-environment consistency: the pandas_ta TA-Lib path (Imports['talib']
True) discards `ddof` and uses population stdev internally. Explicit
ddof=0 in our call therefore makes dev (no TA-Lib) match prod (with
TA-Lib), preventing the split-env regression flagged in §G-5.
"""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest


def test_bb_uses_ddof_zero_population_stdev():
    """Deterministic 30-bar series (closes = 100..129); the last 20 closes
    are 110..129 → mean 119.5, population stdev = sqrt(33.25) ≈ 5.7663.

    Expected:
      bb_middle = 119.5
      bb_upper  = 119.5 + 2 * sqrt(33.25)
      bb_lower  = 119.5 - 2 * sqrt(33.25)

    A regression to ddof=1 (sample stdev) would give stdev = sqrt(35.0) ≈
    5.9161 — easily distinguishable from the assertion below.
    """
    from src.services.technical import TechnicalAnalysisService

    n = 30
    closes = np.array([100.0 + i for i in range(n)])
    df = pd.DataFrame({
        "open": closes, "high": closes + 1.0, "low": closes - 1.0,
        "close": closes, "volume": [10.0] * n,
    })
    indicators = TechnicalAnalysisService().compute_indicators(df)

    window = closes[-20:]
    expected_middle = float(np.mean(window))
    expected_stdev = float(np.std(window, ddof=0))
    expected_upper = expected_middle + 2.0 * expected_stdev
    expected_lower = expected_middle - 2.0 * expected_stdev

    assert indicators["bb_middle"] == pytest.approx(expected_middle, rel=1e-9)
    assert indicators["bb_upper"] == pytest.approx(expected_upper, rel=1e-9)
    assert indicators["bb_lower"] == pytest.approx(expected_lower, rel=1e-9)

    # Cross-check that the discriminator works: ddof=1 expectation must differ
    # measurably from the ddof=0 expectation so a regression would visibly fail.
    sample_stdev = float(np.std(window, ddof=1))
    sample_upper = expected_middle + 2.0 * sample_stdev
    assert sample_upper != pytest.approx(expected_upper, rel=1e-6), (
        "fixture failed to discriminate ddof=0 vs ddof=1"
    )
