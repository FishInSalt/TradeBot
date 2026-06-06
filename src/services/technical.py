# src/services/technical.py
from __future__ import annotations
import pandas as pd
import pandas_ta as ta  # type: ignore[import-untyped]


class TechnicalAnalysisService:
    def compute_indicators(self, df: pd.DataFrame) -> dict[str, float | None]:
        close = df["close"]
        high = df["high"]
        low = df["low"]

        rsi = ta.rsi(close, length=14)  # type: ignore[attr-defined]
        ma_20 = ta.sma(close, length=20)  # type: ignore[attr-defined]
        ma_50 = ta.sma(close, length=50)  # type: ignore[attr-defined]
        macd_df = ta.macd(close)  # type: ignore[attr-defined]
        # ddof=0 (population stdev) aligns with TradingView / TA-Lib. pandas_ta's
        # pure-pandas path defaults to ddof=1 (sample stdev) which inflates band
        # width by √(N/(N-1)) ≈ 1.026 at N=20; the TA-Lib path ignores `ddof`
        # entirely (uses population stdev internally), so explicit ddof=0 also
        # locks behavior across dev (no TA-Lib) vs prod (with TA-Lib).
        # G-calc-rigor-audit §G-5.
        bb_df = ta.bbands(close, length=20, ddof=0)  # type: ignore[attr-defined]
        atr = ta.atr(high, low, close, length=14)  # type: ignore[attr-defined]
        # Volume ratio intentionally not surfaced here — HTF inlines its own
        # "Last bar vol (X× SMA(20) avg)" rendering with a different numerator
        # (most-recent closed bar) than the historical baseline (second-to-last
        # closed bar). G-calc-rigor-audit §G-4.

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
        }

    def format_for_llm(
        self,
        indicators: dict[str, float | None],
        current_price: float,
        timeframe: str = "5m",  # part of API contract; callers pass it, reserved for future use
    ) -> str:
        def _fmt(val: float | None, fmt: str = ".2f") -> str:
            return f"{val:{fmt}}" if val is not None else "N/A"

        lines: list[str] = []

        # RSI
        rsi = indicators.get("rsi_14")
        if rsi is not None:
            lines.append(f"RSI(14): {rsi:.2f}")
        else:
            lines.append("RSI(14): N/A")

        # MA
        for period in (20, 50):
            ma = indicators.get(f"ma_{period}")
            if ma is not None:
                dist_pct = (current_price - ma) / ma * 100
                lines.append(f"MA({period}): {ma:.2f}  (Last {current_price:.2f} → {dist_pct:+.1f}% vs MA)")
            else:
                lines.append(f"MA({period}): N/A")

        # MACD
        macd = indicators.get("macd")
        signal = indicators.get("macd_signal")
        hist = indicators.get("macd_histogram")
        if all(v is not None for v in (macd, signal, hist)):
            lines.append(
                f"MACD: {macd:.2f} | Signal: {signal:.2f} | Histogram: {hist:.2f}"
            )
        else:
            lines.append(f"MACD: {_fmt(macd)} | Signal: {_fmt(signal)} | Histogram: {_fmt(hist)}")

        # Bollinger Bands (F-O2 per spec §6.3): full-word labels, explicit
        # (20, 2) periods, explicit 0%=Lower / 100%=Upper anchor.
        # Asymmetric anchor by design (spec §2.3 #2): inside the band, position
        # is rendered as % of band width (the band is the reference frame);
        # outside the band, position is rendered as % distance from the
        # broken band edge (the edge is the reference frame). The frame
        # changes with the regime, not the formula.
        bb_u = indicators.get("bb_upper")
        bb_m = indicators.get("bb_middle")
        bb_l = indicators.get("bb_lower")
        if all(v is not None for v in (bb_u, bb_m, bb_l)):
            if bb_u == bb_l:
                pos = "position: N/A"
            elif current_price < bb_l:
                pct_below = (bb_l - current_price) / bb_l * 100
                pos = f"Last {current_price:.2f} → {pct_below:.1f}% below Lower"
            elif current_price > bb_u:
                pct_above = (current_price - bb_u) / bb_u * 100
                pos = f"Last {current_price:.2f} → {pct_above:.1f}% above Upper"
            else:
                pct = (current_price - bb_l) / (bb_u - bb_l) * 100
                pos = f"Last {current_price:.2f} → {pct:.0f}% of band, 0%=Lower / 100%=Upper"
            lines.append(
                f"BB(20,2): Upper {bb_u:.2f} | Middle {bb_m:.2f} | Lower {bb_l:.2f}  ({pos})"
            )
        else:
            lines.append(
                f"BB(20,2): Upper {_fmt(bb_u)} | Middle {_fmt(bb_m)} | Lower {_fmt(bb_l)}"
            )

        # ATR (议题5: 归位进 Technical Indicators；% 以 live Last 为分母，显式标 of Last)
        atr = indicators.get("atr_14")
        if atr is not None and current_price > 0:
            lines.append(f"ATR(14): {atr:.2f} ({atr / current_price * 100:.2f}% of Last {current_price:.2f})")
        else:
            lines.append("ATR(14): N/A")

        return "\n".join(lines)
