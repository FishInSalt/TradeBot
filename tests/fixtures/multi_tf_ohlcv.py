"""Reproducible OHLCV fixtures for multi-TF tool tests.

Each builder returns a pandas DataFrame with columns
[timestamp, open, high, low, close, volume] where timestamp is
millisecond UTC. Prices scaled to BTC/USDT:USDT ≈ 81000 to match
the empirical anchor (verify_ohlcv_semantics_v2.py 2026-05-10).

The last row of every fixture represents an in-progress candle: tests
that call `_closed_bars(df)` MUST end up with one fewer row than the
raw fixture. Use this to verify closed-only stripping unambiguously.
"""
from __future__ import annotations
import pandas as pd
import pytest

_TF_MS = {"1m": 60_000, "5m": 300_000, "1h": 3_600_000,
          "4h": 14_400_000, "1d": 86_400_000,
          "1w": 7 * 86_400_000, "1M": 30 * 86_400_000}


def _build(start_ms: int, tf: str, closes: list[float], base_vol: float = 100.0) -> pd.DataFrame:
    """Build a DataFrame where each candle has open=prev_close, close=closes[i],
    high=close+10, low=close-10, volume=base_vol. The final bar is treated as
    in-progress (timestamp = start_ms + (N-1)*tf_ms)."""
    step = _TF_MS[tf]
    rows = []
    prev = closes[0]
    for i, c in enumerate(closes):
        rows.append({
            "timestamp": start_ms + i * step,
            "open": prev,
            "high": max(prev, c) + 10.0,
            "low": min(prev, c) - 10.0,
            "close": c,
            "volume": base_vol,
        })
        prev = c
    return pd.DataFrame(rows)


@pytest.fixture
def df_4h_250bars() -> pd.DataFrame:
    """4h OHLCV with 250 closed bars + 1 in-progress (251 rows)."""
    closes = [75000.0 + i * (7000.0 / 250) for i in range(251)]
    return _build(start_ms=1_700_000_000_000, tf="4h", closes=closes)


@pytest.fixture
def df_1d_250bars() -> pd.DataFrame:
    """1d OHLCV with 250 closed bars + 1 in-progress (251 rows)."""
    closes = [55000.0 + i * (26000.0 / 250) for i in range(251)]
    return _build(start_ms=1_700_000_000_000, tf="1d", closes=closes)


@pytest.fixture
def df_5m_130bars() -> pd.DataFrame:
    """5m OHLCV with 129 closed bars + 1 in-progress (130 rows).

    130 rows is the minimum to satisfy GMD's default `candle_count=30`
    display window: `available_closed (129) >= candle_count + 50 (80)`
    must hold for `display_count = 30` to apply; otherwise GMD falls back
    to `max(10, available_closed - 50)` and the header reads "last N"
    with N < 30, breaking the golden test.
    """
    closes = [81000.0 + (i % 10) * 5.0 for i in range(130)]
    return _build(start_ms=1_700_000_000_000, tf="5m", closes=closes)


@pytest.fixture
def df_1h_250bars() -> pd.DataFrame:
    """1h OHLCV with 249 closed bars + 1 in-progress (250 rows)."""
    closes = [78000.0 + (i % 50) * 100.0 for i in range(250)]
    return _build(start_ms=1_700_000_000_000, tf="1h", closes=closes)


@pytest.fixture
def df_5m_anomaly() -> pd.DataFrame:
    """5m OHLCV with one bar volume = 5× SMA(20), one bar range = 4× ATR(14).
    Used to drive GMD vol↑ / range↑ marker tests in Task 4.
    Sized to 130 rows for the same GMD display-window reason as
    df_5m_130bars."""
    closes = [81000.0 + (i % 10) * 5.0 for i in range(130)]
    df = _build(start_ms=1_700_000_000_000, tf="5m", closes=closes)
    df.loc[127, "volume"] = 600.0  # 6× the 100.0 SMA baseline
    df.loc[128, "high"] = df.loc[128, "close"] + 200.0  # widens range
    df.loc[128, "low"] = df.loc[128, "close"] - 200.0
    return df


@pytest.fixture
def df_4h_recent_vol_spike() -> pd.DataFrame:
    """4h OHLCV identical to df_4h_250bars except the LAST closed bar
    (df.loc[249]) has volume = 600 (6× the 100.0 baseline).

    Used by drift-guard #7 (test_gmd_htf_last_bar_vol_ratio_match) to
    make the SMA(20)-window choice observable in the rendered ratio:

      - iloc[-20:] window (correct, spec §5.5):
          mean = (19·100 + 600) / 20 = 125 → ratio = 600 / 125 = 4.8
      - iloc[-21:-1] window (regression target):
          mean = 100                       → ratio = 600 / 100 = 6.0

    Without a recent spike the fixture has uniform volume = 100 and every
    window choice yields ratio = 1.0, making the guard a tautology.
    """
    closes = [75000.0 + i * (7000.0 / 250) for i in range(251)]
    df = _build(start_ms=1_700_000_000_000, tf="4h", closes=closes)
    df.loc[249, "volume"] = 600.0  # df.iloc[-2] = last closed bar = vol_now
    return df


@pytest.fixture
def fake_ticker_81870():
    """Mock ticker.last = 81870.50, bid 81870.40, ask 81870.60."""
    from types import SimpleNamespace
    return SimpleNamespace(
        last=81870.50, bid=81870.40, ask=81870.60,
        high=82500.00, low=81000.00, base_volume=1234.56,
    )
