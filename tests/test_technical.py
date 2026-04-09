import pytest
import pandas as pd
import numpy as np


@pytest.fixture
def sample_ohlcv() -> pd.DataFrame:
    np.random.seed(42)
    n = 50
    close = 65000 + np.cumsum(np.random.randn(n) * 100)
    return pd.DataFrame({
        "timestamp": range(n),
        "open": close - np.random.rand(n) * 50,
        "high": close + np.random.rand(n) * 100,
        "low": close - np.random.rand(n) * 100,
        "close": close,
        "volume": np.random.rand(n) * 1000 + 500,
    })


def test_compute_indicators(sample_ohlcv):
    from src.services.technical import TechnicalAnalysisService
    service = TechnicalAnalysisService()
    indicators = service.compute_indicators(sample_ohlcv)
    assert isinstance(indicators, dict)
    assert "rsi_14" in indicators
    assert "ma_20" in indicators
    assert "macd" in indicators
    assert "bb_upper" in indicators


def test_format_for_llm(sample_ohlcv):
    from src.services.technical import TechnicalAnalysisService
    service = TechnicalAnalysisService()
    indicators = service.compute_indicators(sample_ohlcv)
    text = service.format_for_llm(indicators, current_price=65000.0)
    assert "RSI" in text
    assert "MA" in text
    assert "65000" in text
