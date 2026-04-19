# tests/test_technical.py
import pytest
import pandas as pd
import numpy as np


@pytest.fixture
def sample_ohlcv() -> pd.DataFrame:
    np.random.seed(42)
    n = 100  # need enough rows for MA(50), RSI(14), ATR(14)
    close = 65000 + np.cumsum(np.random.randn(n) * 100)
    return pd.DataFrame({
        "timestamp": range(n),
        "open": close - np.random.rand(n) * 50,
        "high": close + np.random.rand(n) * 100,
        "low": close - np.random.rand(n) * 100,
        "close": close,
        "volume": np.random.rand(n) * 1000 + 500,
    })


@pytest.fixture
def short_ohlcv() -> pd.DataFrame:
    """Only 10 rows — not enough for MA(50) or ATR(14)."""
    n = 10
    close = [65000 + i * 10 for i in range(n)]
    return pd.DataFrame({
        "timestamp": range(n),
        "open": [c - 5 for c in close],
        "high": [c + 50 for c in close],
        "low": [c - 50 for c in close],
        "close": close,
        "volume": [1000.0] * n,
    })


def test_compute_indicators_keys(sample_ohlcv):
    from src.services.technical import TechnicalAnalysisService
    service = TechnicalAnalysisService()
    indicators = service.compute_indicators(sample_ohlcv)
    assert isinstance(indicators, dict)
    # Original keys
    for key in ("rsi_14", "ma_20", "ma_50", "macd", "macd_signal", "macd_histogram",
                "bb_upper", "bb_middle", "bb_lower"):
        assert key in indicators
    # New keys
    assert "atr_14" in indicators
    assert "volume_ratio" in indicators


def test_compute_indicators_bb_order(sample_ohlcv):
    """BB columns must be lower < middle < upper."""
    from src.services.technical import TechnicalAnalysisService
    service = TechnicalAnalysisService()
    indicators = service.compute_indicators(sample_ohlcv)
    if indicators["bb_lower"] is not None:
        assert indicators["bb_lower"] < indicators["bb_middle"] < indicators["bb_upper"]


def test_compute_indicators_macd_histogram_sign(sample_ohlcv):
    """MACD histogram = MACD - signal (verify fields aren't swapped)."""
    from src.services.technical import TechnicalAnalysisService
    service = TechnicalAnalysisService()
    indicators = service.compute_indicators(sample_ohlcv)
    if all(indicators[k] is not None for k in ("macd", "macd_signal", "macd_histogram")):
        expected_hist = indicators["macd"] - indicators["macd_signal"]
        assert indicators["macd_histogram"] == pytest.approx(expected_hist, abs=0.01)


def test_compute_indicators_atr_positive(sample_ohlcv):
    from src.services.technical import TechnicalAnalysisService
    service = TechnicalAnalysisService()
    indicators = service.compute_indicators(sample_ohlcv)
    assert indicators["atr_14"] is not None
    assert indicators["atr_14"] > 0


def test_compute_indicators_volume_ratio(sample_ohlcv):
    from src.services.technical import TechnicalAnalysisService
    service = TechnicalAnalysisService()
    indicators = service.compute_indicators(sample_ohlcv)
    assert indicators["volume_ratio"] is not None
    assert indicators["volume_ratio"] > 0


def test_compute_indicators_short_data(short_ohlcv):
    """Short data returns None for indicators that need more history."""
    from src.services.technical import TechnicalAnalysisService
    service = TechnicalAnalysisService()
    indicators = service.compute_indicators(short_ohlcv)
    assert indicators["ma_50"] is None
    assert indicators["atr_14"] is None


def test_format_for_llm_is_fact_only(sample_ohlcv):
    from src.services.technical import TechnicalAnalysisService
    service = TechnicalAnalysisService()
    indicators = service.compute_indicators(sample_ohlcv)
    text = service.format_for_llm(indicators, current_price=65000.0, timeframe="5m")
    assert "RSI" in text
    assert "MA(20)" in text
    # Fact-only: no qualitative / directional labels
    for label in ("neutral", "bullish", "bearish", "overbought", "oversold",
                  "upper half", "lower half", "price above", "price below"):
        assert label not in text.lower()
    # Positive anchors: guard against "deleted label but forgot to add the
    # fact-only replacement" regression — negative-only assertions would pass
    # silently if MA/BB rendered without the new phrasing.
    assert "price vs MA:" in text
    assert any(
        phrase in text
        for phrase in ("of band width", "above upper band", "below lower band")
    )
    # format_for_llm should NOT include ATR or Volume (those are in Market Context)
    assert "ATR" not in text
    assert "Volume" not in text


def test_format_for_llm_none_values(short_ohlcv):
    from src.services.technical import TechnicalAnalysisService
    service = TechnicalAnalysisService()
    indicators = service.compute_indicators(short_ohlcv)
    text = service.format_for_llm(indicators, current_price=65000.0, timeframe="5m")
    assert "N/A" in text


def test_format_for_llm_bb_position_at_lower_band():
    """When price == bb_lower, position should be 0% of band width."""
    from src.services.technical import TechnicalAnalysisService
    service = TechnicalAnalysisService()
    indicators = {
        "rsi_14": 50.0, "ma_20": 100.0, "ma_50": 100.0,
        "macd": 0.0, "macd_signal": 0.0, "macd_histogram": 0.0,
        "bb_upper": 110.0, "bb_middle": 100.0, "bb_lower": 90.0,
        "atr_14": None, "volume_ratio": None,
    }
    text = service.format_for_llm(indicators, current_price=90.0, timeframe="5m")
    # BB line must mention 0% position
    bb_line = next(line for line in text.split("\n") if line.startswith("BB:"))
    assert "0%" in bb_line
    assert "of band width" in bb_line
    assert "above" not in bb_line and "below" not in bb_line


def test_format_for_llm_bb_position_at_upper_band():
    """When price == bb_upper, position should be 100% of band width."""
    from src.services.technical import TechnicalAnalysisService
    service = TechnicalAnalysisService()
    indicators = {
        "rsi_14": 50.0, "ma_20": 100.0, "ma_50": 100.0,
        "macd": 0.0, "macd_signal": 0.0, "macd_histogram": 0.0,
        "bb_upper": 110.0, "bb_middle": 100.0, "bb_lower": 90.0,
        "atr_14": None, "volume_ratio": None,
    }
    text = service.format_for_llm(indicators, current_price=110.0, timeframe="5m")
    bb_line = next(line for line in text.split("\n") if line.startswith("BB:"))
    assert "100%" in bb_line
    assert "of band width" in bb_line


def test_format_for_llm_bb_position_edge_case_equal_bands():
    """When bb_upper == bb_lower (extremely narrow band), position segment must be N/A.

    Acceptance criteria (spec §6.1):
      - position segment inside BB line parentheses contains 'N/A'
      - position segment must NOT contain '%' or numeric digits (prevents future
        regression writing 'N/A%' or '0%' as a compromise)
    """
    from src.services.technical import TechnicalAnalysisService
    import re
    service = TechnicalAnalysisService()
    indicators = {
        "rsi_14": 50.0, "ma_20": 100.0, "ma_50": 100.0,
        "macd": 0.0, "macd_signal": 0.0, "macd_histogram": 0.0,
        "bb_upper": 100.0, "bb_middle": 100.0, "bb_lower": 100.0,
        "atr_14": None, "volume_ratio": None,
    }
    text = service.format_for_llm(indicators, current_price=100.0, timeframe="5m")
    bb_line = next(line for line in text.split("\n") if line.startswith("BB:"))
    # Extract content inside the parentheses (position segment only)
    m = re.search(r"\(([^)]*)\)", bb_line)
    assert m, f"BB line missing parentheses: {bb_line}"
    pos_segment = m.group(1)
    assert "N/A" in pos_segment
    # Guard against future 'N/A%' or '0%' compromise
    assert "%" not in pos_segment
    assert not any(ch.isdigit() for ch in pos_segment)
