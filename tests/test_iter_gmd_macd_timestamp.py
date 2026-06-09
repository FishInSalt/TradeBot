"""iter-gmd-macd-timestamp: GMD 两点打磨的渲染 / 描述 drift-guard 测试。

两议题（2026-06-09 get_market_data tool-audit 聚焦复审）:
- 议题1 [P2]: Technical Indicators 表头 `values as of last closed <T>` 的 <T> 是
  最近收盘 candle 的**开盘**时间，与字面 "closed" 冲突（1h bar 开 09:00 实收 10:00）。
  修法 = 补 `candle: open` 字样消歧，对齐兄弟工具 HTF `(last closed candle: open <ts>)`，
  保持全工具开盘时间口径统一。
- 议题2 [P3]: MACD 是六指标里唯一不带参数的（RSI(14)/MA(20)/MA(50)/BB(20,2)/ATR(14)
  全带）。修法 = 领头标签补 `(12,26,9)`，对齐 `BB(20,2)` 约定（Signal 的 9 已在元组内、
  Histogram 无独立参数，二者维持裸标签是正确的）。

测试断言 LLM 实见的串（渲染输出 + tool_def.description），per memory
project_tool_docstring_llm_channel。
"""
import re

import pytest
from unittest.mock import AsyncMock, MagicMock

from tests.fixtures.multi_tf_ohlcv import (
    df_5m_130bars, df_1h_250bars, fake_ticker_81870,
)


def _build_gmd_deps(ticker, ohlcv_by_tf, symbol="BTC/USDT:USDT", tf="5m"):
    """Local copy of the helper from test_iter_tool_opt_gmd_polish (intentional
    copy, not import — avoids coupling this iter's tests to a sibling test
    file's internal helper)."""
    from src.services.technical import TechnicalAnalysisService
    deps = MagicMock()
    deps.symbol = symbol
    deps.timeframe = tf
    deps.technical = TechnicalAnalysisService()
    deps.market_data = MagicMock()
    deps.market_data.get_ticker = AsyncMock(return_value=ticker)

    async def _ohlcv(sym, t, limit):
        if t not in ohlcv_by_tf:
            raise RuntimeError(f"no fixture for {t}")
        return ohlcv_by_tf[t]

    deps.market_data.get_ohlcv_dataframe = AsyncMock(side_effect=_ohlcv)
    return deps


# === 议题1: Technical Indicators 表头补 `candle: open` 消歧 ===

class TestIssue1HeaderOpenDisambiguation:
    @pytest.mark.asyncio
    async def test_header_5m_has_open_word_and_no_bare_time_after_closed(
        self, fake_ticker_81870, df_5m_130bars,
    ):
        """5m 分支: 表头含 `values as of last closed candle: open `，且时间不再直接
        紧跟 "closed"（负向 guard：旧格式 `last closed HH:MM` 已绝迹）。"""
        from src.agent.tools_perception import get_market_data
        deps = _build_gmd_deps(fake_ticker_81870, {"5m": df_5m_130bars}, tf="5m")
        out = await get_market_data(deps)
        ti_line = next(
            l for l in out.split("\n") if l.startswith("=== Technical Indicators")
        )
        assert "values as of last closed candle: open " in ti_line, \
            f"表头缺 `candle: open` 消歧措辞: {ti_line!r}"
        # 负向 guard: 时间不再直接跟在 "closed" 后（旧格式 `last closed 09:00`）
        assert not re.search(r"last closed \d", ti_line), \
            f"`closed` 后仍直接跟数字时间，未消歧: {ti_line!r}"

    @pytest.mark.asyncio
    async def test_header_1h_uses_last_closed_bar_open_time(
        self, fake_ticker_81870, df_1h_250bars,
    ):
        """1h 分支（`%m-%d %H:%M` 格式）: 表头 open 时点 == 最近收盘 bar 的开盘时间
        （= 下方 OHLCV 表末行同一根 bar，无 `一根 bar 两个时间` 对账面）。"""
        from src.agent.tools_perception import get_market_data
        from src.utils.ohlcv_utils import (
            _closed_bars, _fmt_candle_time, _to_pd_timestamp_utc,
        )
        df = df_1h_250bars
        last_closed_ts = _to_pd_timestamp_utc(_closed_bars(df)["timestamp"].iloc[-1])
        expected = _fmt_candle_time(last_closed_ts, "1h")  # e.g. "11-14 22:00"
        deps = _build_gmd_deps(fake_ticker_81870, {"1h": df}, tf="1h")
        out = await get_market_data(deps)
        assert f"values as of last closed candle: open {expected}" in out, \
            f"1h 表头未用最近收盘 bar 开盘时间 {expected!r}; out 头部: {out[:400]!r}"


# === 议题2: MACD 标签补 (12,26,9) ===

class TestIssue2MacdParams:
    def _indicators(self, macd=12.5, signal=8.3, hist=4.2):
        return {
            "rsi_14": 50.0, "ma_20": 100.0, "ma_50": 100.0,
            "macd": macd, "macd_signal": signal, "macd_histogram": hist,
            "bb_upper": 110.0, "bb_middle": 100.0, "bb_lower": 90.0,
            "atr_14": 5.0,
        }

    def test_macd_label_has_periods_when_present(self):
        """MACD 行带 `(12,26,9)`，对齐同段 BB(20,2)；裸 `MACD:` 不再出现。"""
        from src.services.technical import TechnicalAnalysisService
        text = TechnicalAnalysisService().format_for_llm(
            self._indicators(), current_price=100.0, timeframe="5m",
        )
        assert "MACD(12,26,9):" in text, f"MACD 标签缺参数 (12,26,9): {text!r}"
        # 负向 guard: 裸 `MACD:` 标签消失（`MACD(12,26,9):` 不含子串 `MACD:`）
        assert "MACD:" not in text, f"裸 `MACD:` 标签仍存在: {text!r}"

    def test_macd_label_has_periods_when_na(self):
        """N/A 兜底分支也带 `(12,26,9)` 标签（参数是结构事实，与值是否可得无关）。"""
        from src.services.technical import TechnicalAnalysisService
        ind = self._indicators(macd=None, signal=None, hist=None)
        text = TechnicalAnalysisService().format_for_llm(
            ind, current_price=100.0, timeframe="5m",
        )
        macd_line = next(l for l in text.split("\n") if l.startswith("MACD"))
        assert macd_line.startswith("MACD(12,26,9):"), \
            f"N/A 分支 MACD 标签缺参数: {macd_line!r}"
        assert "N/A" in macd_line


# === 描述 Example output drift-guard（两议题同步进 tool_def.description）===

class TestDescriptionExampleDriftGuard:
    def _desc(self):
        from src.agent.trader import create_trader_agent
        from src.config import PersonaConfig
        agent = create_trader_agent(model="test", persona_config=PersonaConfig())
        return agent._function_toolset.tools["get_market_data"].tool_def.description

    def test_example_header_has_open_word(self):
        """议题1: Example output 表头同步带 `candle: open`，防 Example 与渲染漂移。"""
        desc = self._desc()
        assert "values as of last closed candle: open " in desc, \
            f"描述 Example 表头缺 `candle: open`: {desc!r}"
        assert not re.search(r"last closed \d", desc), \
            f"描述里 `closed` 后仍直接跟时间: {desc!r}"

    def test_example_macd_has_periods(self):
        """议题2: Example output MACD 行同步带 `(12,26,9)`。"""
        desc = self._desc()
        assert "MACD(12,26,9):" in desc, \
            f"描述 Example MACD 行缺参数 (12,26,9): {desc!r}"
