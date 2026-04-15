# src/services/technical.py
from __future__ import annotations
import pandas as pd
import pandas_ta as ta  # type: ignore[import-untyped]


class TechnicalAnalysisService:
    def compute_indicators(self, df: pd.DataFrame) -> dict[str, float | None]:
        close = df["close"]
        high = df["high"]
        low = df["low"]
        volume = df["volume"]

        rsi = ta.rsi(close, length=14)  # type: ignore[attr-defined]
        ma_20 = ta.sma(close, length=20)  # type: ignore[attr-defined]
        ma_50 = ta.sma(close, length=50)  # type: ignore[attr-defined]
        macd_df = ta.macd(close)  # type: ignore[attr-defined]
        bb_df = ta.bbands(close, length=20)  # type: ignore[attr-defined]
        atr = ta.atr(high, low, close, length=14)  # type: ignore[attr-defined]
        vol_sma = ta.sma(volume, length=20)  # type: ignore[attr-defined]

        def _last(series: pd.Series | None) -> float | None:
            if series is None or series.empty or pd.isna(series.iloc[-1]):
                return None
            return float(series.iloc[-1])

        def _col(frame: pd.DataFrame | None, like: str) -> pd.Series | None:
            if frame is None:
                return None
            cols = frame.filter(like=like)
            if cols.empty:
                return None
            return cols.iloc[:, 0]

        # Volume ratio: use iloc[-2] (last completed candle) for both numerator and denominator
        volume_ratio: float | None = None
        if vol_sma is not None and len(volume) >= 2 and len(vol_sma) >= 2:
            sma_val = vol_sma.iloc[-2] if not pd.isna(vol_sma.iloc[-2]) else None
            vol_val = volume.iloc[-2]
            if sma_val is not None and sma_val > 0:
                volume_ratio = float(vol_val / sma_val)

        return {
            "rsi_14": _last(rsi),
            "ma_20": _last(ma_20),
            "ma_50": _last(ma_50),
            "macd": _last(_col(macd_df, "MACD_")),
            "macd_signal": _last(_col(macd_df, "MACDs_")),
            "macd_histogram": _last(_col(macd_df, "MACDh_")),
            "bb_upper": _last(_col(bb_df, "BBU_")),
            "bb_middle": _last(_col(bb_df, "BBM_")),
            "bb_lower": _last(_col(bb_df, "BBL_")),
            "atr_14": _last(atr),
            "volume_ratio": volume_ratio,
        }

    def format_for_llm(
        self,
        indicators: dict[str, float | None],
        current_price: float,
        timeframe: str = "5m",
    ) -> str:
        def _fmt(val: float | None, fmt: str = ".2f") -> str:
            return f"{val:{fmt}}" if val is not None else "N/A"

        lines: list[str] = []

        # RSI
        rsi = indicators.get("rsi_14")
        if rsi is not None:
            if rsi < 30:
                label = "oversold"
            elif rsi < 45:
                label = "bearish"
            elif rsi <= 55:
                label = "neutral"
            elif rsi <= 70:
                label = "bullish"
            else:
                label = "overbought"
            lines.append(f"RSI(14): {rsi:.2f} ({label})")
        else:
            lines.append("RSI(14): N/A")

        # MA
        for period in (20, 50):
            ma = indicators.get(f"ma_{period}")
            if ma is not None:
                rel = "price above — bullish" if current_price > ma else "price below — bearish"
                lines.append(f"MA({period}): {ma:.2f} ({rel})")
            else:
                lines.append(f"MA({period}): N/A")

        # MACD
        macd = indicators.get("macd")
        signal = indicators.get("macd_signal")
        hist = indicators.get("macd_histogram")
        if all(v is not None for v in (macd, signal, hist)):
            if hist > 0:
                label = "bullish"
            elif hist < 0:
                label = "bearish"
            else:
                label = "neutral"
            lines.append(f"MACD: {macd:.2f} | Signal: {signal:.2f} | Histogram: {hist:.2f} ({label})")
        else:
            lines.append(f"MACD: {_fmt(macd)} | Signal: {_fmt(signal)} | Histogram: {_fmt(hist)}")

        # Bollinger Bands
        bb_u = indicators.get("bb_upper")
        bb_m = indicators.get("bb_middle")
        bb_l = indicators.get("bb_lower")
        if all(v is not None for v in (bb_u, bb_m, bb_l)):
            if current_price > bb_m:
                pos = "price in upper half"
            else:
                pos = "price in lower half"
            lines.append(f"BB: {bb_u:.0f} / {bb_m:.0f} / {bb_l:.0f} ({pos})")
        else:
            lines.append(f"BB: {_fmt(bb_u)} / {_fmt(bb_m)} / {_fmt(bb_l)}")

        return "\n".join(lines)
