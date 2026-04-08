from __future__ import annotations
import pandas as pd
import pandas_ta as ta


class TechnicalAnalysisService:
    def compute_indicators(self, df: pd.DataFrame) -> dict[str, float | None]:
        close = df["close"]
        rsi = ta.rsi(close, length=14)
        ma_20 = ta.sma(close, length=20)
        ma_50 = ta.sma(close, length=50)
        macd_df = ta.macd(close)
        bb_df = ta.bbands(close, length=20)

        def _last(series: pd.Series | None) -> float | None:
            if series is None or series.empty or pd.isna(series.iloc[-1]):
                return None
            return float(series.iloc[-1])

        macd_cols = macd_df.columns.tolist() if macd_df is not None else []
        bb_cols = bb_df.columns.tolist() if bb_df is not None else []

        return {
            "rsi_14": _last(rsi),
            "ma_20": _last(ma_20),
            "ma_50": _last(ma_50),
            "macd": _last(macd_df[macd_cols[0]]) if macd_df is not None else None,
            "macd_signal": _last(macd_df[macd_cols[1]]) if macd_df is not None and len(macd_cols) > 1 else None,
            "macd_histogram": _last(macd_df[macd_cols[2]]) if macd_df is not None and len(macd_cols) > 2 else None,
            "bb_upper": _last(bb_df[bb_cols[0]]) if bb_df is not None else None,
            "bb_middle": _last(bb_df[bb_cols[1]]) if bb_df is not None and len(bb_cols) > 1 else None,
            "bb_lower": _last(bb_df[bb_cols[2]]) if bb_df is not None and len(bb_cols) > 2 else None,
        }

    def format_for_llm(self, indicators: dict[str, float | None], current_price: float) -> str:
        lines = [f"Current Price: {current_price:.2f}", ""]
        labels = {
            "rsi_14": "RSI(14)", "ma_20": "MA(20)", "ma_50": "MA(50)",
            "macd": "MACD", "macd_signal": "MACD Signal", "macd_histogram": "MACD Histogram",
            "bb_upper": "Bollinger Upper", "bb_middle": "Bollinger Middle", "bb_lower": "Bollinger Lower",
        }
        for key, label in labels.items():
            val = indicators.get(key)
            lines.append(f"{label}: {val:.2f}" if val is not None else f"{label}: N/A")
        return "\n".join(lines)
