"""Iter tool-opt-volume-ratio-cleanup drift guard (G-calc-rigor-audit §G-4).

`compute_indicators.volume_ratio` was a dead field: no production consumer
(format_for_llm did not render it; GMD/HTF inline their own "Last bar vol"
ratio with a different numerator — most-recent closed bar vs the historical
baseline's second-to-last closed bar). Removed in this iter.

Negative drift guard: prevent silent re-introduction of dead fields in
`compute_indicators` return shape. If a future caller actually needs a
shared volume ratio, the consumer must land first.
"""
from __future__ import annotations

import pandas as pd


def test_compute_indicators_has_no_volume_ratio_field():
    """`volume_ratio` was removed; future re-introduction without a consumer
    would re-create the cross-tool numerator inconsistency that motivated
    the removal (G-calc-rigor-audit §G-4)."""
    from src.services.technical import TechnicalAnalysisService

    n = 30
    df = pd.DataFrame({
        "open":   [100.0] * n,
        "high":   [101.0] * n,
        "low":    [99.0] * n,
        "close":  [100.0] * n,
        "volume": [10.0] * n,
    })
    indicators = TechnicalAnalysisService().compute_indicators(df)
    assert "volume_ratio" not in indicators, (
        f"`volume_ratio` re-introduced into compute_indicators dict — "
        f"see G-calc-rigor-audit §G-4. Keys present: {sorted(indicators.keys())}"
    )
