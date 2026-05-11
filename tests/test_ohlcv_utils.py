"""Unit tests for src/utils/ohlcv_utils.py helpers.

Covers the §6.4 helper module: _live_price, _closed_bars, _atr_series.
The §6.4.2 invariant (ATR series last-value bit-equality with
compute_indicators) is covered here; the §2.2.1 end-to-end invariant
(MTS / HTF rendered overlap signals equal) is covered in
tests/test_multi_tf_drift_guards.py and operates on rendered output,
not helpers.
"""
from __future__ import annotations
import pytest
import pandas as pd

# Fixture imports — pytest discovers fixtures by name in the test module's
# namespace, so importing the fixture functions registers them for tests
# below that take them as parameters. Tasks 3-5 will import additional
# fixtures into their own test modules.
from tests.fixtures.multi_tf_ohlcv import (
    df_4h_250bars,
    fake_ticker_81870,
)


def test_live_price_returns_ticker_last_as_float(fake_ticker_81870):
    from src.utils.ohlcv_utils import _live_price
    assert _live_price(fake_ticker_81870) == 81870.50
    assert isinstance(_live_price(fake_ticker_81870), float)


def test_closed_bars_strips_last_row(df_4h_250bars):
    from src.utils.ohlcv_utils import _closed_bars
    closed = _closed_bars(df_4h_250bars)
    assert len(closed) == len(df_4h_250bars) - 1
    assert closed["timestamp"].iloc[-1] == df_4h_250bars["timestamp"].iloc[-2]


def test_closed_bars_returns_view_or_copy_not_mutating_input(df_4h_250bars):
    """Helper must not mutate the input frame."""
    from src.utils.ohlcv_utils import _closed_bars
    before_len = len(df_4h_250bars)
    _ = _closed_bars(df_4h_250bars)
    assert len(df_4h_250bars) == before_len


def test_atr_series_last_value_equals_compute_indicators_atr_14(df_4h_250bars):
    """§6.4.2 invariant: _atr_series(df_closed, 14).iloc[-1] must equal
    TechnicalAnalysisService.compute_indicators(df_closed)['atr_14'] bit-for-bit.
    Locks pandas_ta mamode='rma' against future library default drift."""
    from src.utils.ohlcv_utils import _closed_bars, _atr_series
    from src.services.technical import TechnicalAnalysisService
    df_closed = _closed_bars(df_4h_250bars)
    series = _atr_series(df_closed, period=14)
    scalar = TechnicalAnalysisService().compute_indicators(df_closed)["atr_14"]
    assert series.iloc[-1] == pytest.approx(scalar, rel=0, abs=0)  # bit-equal


def test_atr_series_returns_pandas_series(df_4h_250bars):
    from src.utils.ohlcv_utils import _closed_bars, _atr_series
    df_closed = _closed_bars(df_4h_250bars)
    assert isinstance(_atr_series(df_closed, 14), pd.Series)


def test_format_for_llm_bb_label_uses_full_words_and_explicit_periods():
    """F-O2: `BB(20,2): Upper X | Middle Y | Lower Z (position: P%, 0%=Lower / 100%=Upper)`."""
    from src.services.technical import TechnicalAnalysisService
    indicators = {
        "rsi_14": 50.0,
        "ma_20": 81700.0, "ma_50": 81800.0,
        "macd": 0.0, "macd_signal": 0.0, "macd_histogram": 0.0,
        "bb_upper": 81960.0, "bb_middle": 81727.0, "bb_lower": 81494.0,
        "atr_14": 122.5, "volume_ratio": 1.1,
    }
    out = TechnicalAnalysisService().format_for_llm(indicators, current_price=81870.50)
    assert "BB(20,2):" in out, out
    assert "Upper 81960.00" in out
    assert "Middle 81727.00" in out
    assert "Lower 81494.00" in out
    assert "0%=Lower" in out and "100%=Upper" in out
    assert "position:" in out
    # Old format must be gone:
    assert "BB: 81960" not in out
