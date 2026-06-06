"""Tests for iter-tool-opt-gmd-polish — shared helpers (Task 1) +
issue-specific assertions (Tasks 2-6).

Helpers tested here:
- _to_pd_timestamp_utc (Task 1)
- _fmt_candle_time (Task 1)
- TF_OFFSETS dict (Task 1)
"""
from __future__ import annotations
from datetime import datetime, timezone

import pandas as pd
import pytest


# === Task 1: helpers ===

class TestToPdTimestampUtc:
    def test_int_ms_epoch(self):
        from src.utils.ohlcv_utils import _to_pd_timestamp_utc
        ts = _to_pd_timestamp_utc(1_700_000_000_000)
        assert ts.tz is not None
        assert ts.tz.utcoffset(None).total_seconds() == 0  # UTC
        assert ts.year == 2023 and ts.month == 11

    def test_float_ms_epoch(self):
        from src.utils.ohlcv_utils import _to_pd_timestamp_utc
        ts = _to_pd_timestamp_utc(1_700_000_000_000.0)
        assert ts.tz is not None

    def test_naive_datetime_gets_localized_utc(self):
        from src.utils.ohlcv_utils import _to_pd_timestamp_utc
        naive = datetime(2026, 5, 28, 12, 0, 0)
        ts = _to_pd_timestamp_utc(naive)
        assert ts.tz is not None
        assert ts.tz.utcoffset(None).total_seconds() == 0

    def test_aware_datetime_passthrough(self):
        from src.utils.ohlcv_utils import _to_pd_timestamp_utc
        aware = datetime(2026, 5, 28, 12, 0, 0, tzinfo=timezone.utc)
        ts = _to_pd_timestamp_utc(aware)
        assert ts.tz is not None
        assert ts.tz.utcoffset(None).total_seconds() == 0
        assert ts.hour == 12

    def test_aware_non_utc_gets_converted(self):
        """Non-UTC aware datetime must be CONVERTED (not just preserved)
        so the resulting wall-clock time reflects UTC, not the source tz."""
        from datetime import timedelta
        from src.utils.ohlcv_utils import _to_pd_timestamp_utc
        # UTC+8 noon = UTC 04:00
        aware_utc8 = datetime(2026, 5, 28, 12, 0, 0, tzinfo=timezone(timedelta(hours=8)))
        ts = _to_pd_timestamp_utc(aware_utc8)
        assert ts.tz.utcoffset(None).total_seconds() == 0
        assert ts.hour == 4


class TestTfOffsets:
    @pytest.mark.parametrize("tf,expected_seconds", [
        ("1m", 60), ("3m", 180), ("5m", 300), ("15m", 900), ("30m", 1800),
        ("1h", 3600), ("2h", 7200), ("4h", 14400), ("6h", 21600),
        ("8h", 28800), ("12h", 43200),
        ("1d", 86400), ("3d", 259200), ("1w", 604800),
    ])
    def test_timedelta_tfs(self, tf, expected_seconds):
        from src.utils.ohlcv_utils import TF_OFFSETS
        assert TF_OFFSETS[tf].total_seconds() == expected_seconds

    def test_1M_is_dateoffset(self):
        from src.utils.ohlcv_utils import TF_OFFSETS
        from pandas.tseries.offsets import DateOffset
        assert isinstance(TF_OFFSETS["1M"], DateOffset)

    def test_1M_advances_calendar_aware(self):
        """1M must respect calendar month length (28-31 days), not be a fixed
        30-day delta."""
        from src.utils.ohlcv_utils import TF_OFFSETS
        jan = pd.Timestamp("2026-01-31", tz="UTC")
        feb = jan + TF_OFFSETS["1M"]
        # Feb has 28 days in 2026 → Jan 31 + 1M = Feb 28 (pandas DateOffset behavior)
        assert feb.month == 2

    def test_unknown_tf_absent(self):
        from src.utils.ohlcv_utils import TF_OFFSETS
        assert "7m" not in TF_OFFSETS
        assert "2d" not in TF_OFFSETS


class TestFmtCandleTime:
    @pytest.mark.parametrize("tf,expected", [
        ("1m", "12:34"), ("3m", "12:34"), ("5m", "12:34"),
        ("15m", "12:34"), ("30m", "12:34"),
    ])
    def test_intraday_minute(self, tf, expected):
        from src.utils.ohlcv_utils import _fmt_candle_time
        dt = pd.Timestamp("2026-05-28 12:34:00", tz="UTC")
        assert _fmt_candle_time(dt, tf) == expected

    @pytest.mark.parametrize("tf", ["1h", "2h", "4h", "6h", "8h", "12h"])
    def test_hour_tfs(self, tf):
        from src.utils.ohlcv_utils import _fmt_candle_time
        dt = pd.Timestamp("2026-05-28 12:00:00", tz="UTC")
        assert _fmt_candle_time(dt, tf) == "05-28 12:00"

    @pytest.mark.parametrize("tf", ["1d", "3d", "1w"])
    def test_day_week_tfs(self, tf):
        from src.utils.ohlcv_utils import _fmt_candle_time
        dt = pd.Timestamp("2026-05-28", tz="UTC")
        assert _fmt_candle_time(dt, tf) == "2026-05-28"

    def test_1M_month_format(self):
        from src.utils.ohlcv_utils import _fmt_candle_time
        dt = pd.Timestamp("2026-05-01", tz="UTC")
        assert _fmt_candle_time(dt, "1M") == "2026-05"

    def test_unknown_tf_degraded_fallback(self):
        """Unknown tf returns ISO date — degraded fallback, no raise."""
        from src.utils.ohlcv_utils import _fmt_candle_time
        dt = pd.Timestamp("2026-05-28 12:34:00", tz="UTC")
        result = _fmt_candle_time(dt, "7m")  # synthetic unknown
        assert result == "2026-05-28"  # falls back to %Y-%m-%d


# === Task 2: RVol column ===

from unittest.mock import AsyncMock, MagicMock

from tests.fixtures.multi_tf_ohlcv import (
    df_5m_130bars, df_5m_anomaly,
    fake_ticker_81870,
)


def _build_gmd_deps(ticker, ohlcv_by_tf, symbol="BTC/USDT:USDT", tf="5m"):
    """Local copy of _build_deps from test_iter_w2r2_next_d_goldens.

    Intentional copy (not import) to avoid coupling this iter's tests to a
    sibling test file's internal helper. If `_build_deps` proves stable across
    iter boundaries, a future refactor can promote it to
    `tests/fixtures/multi_tf_ohlcv.py` co-located with the fixtures it consumes.
    """
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


class TestRVolColumn:
    @pytest.mark.asyncio
    async def test_rvol_column_header_present(
        self, fake_ticker_81870, df_5m_130bars,
    ):
        """Issue 1: OHLCV table header has RVol(×SMA20) column."""
        from src.agent.tools_perception import get_market_data
        deps = _build_gmd_deps(fake_ticker_81870, {"5m": df_5m_130bars})
        out = await get_market_data(deps)
        assert "RVol(×SMA20)" in out, f"RVol column header missing: {out[:600]}"

    @pytest.mark.asyncio
    async def test_rvol_values_have_x_suffix(
        self, fake_ticker_81870, df_5m_130bars,
    ):
        """Issue 1: each RVol value renders with × suffix (e.g. `1.00×`)."""
        import re
        from src.agent.tools_perception import get_market_data
        deps = _build_gmd_deps(fake_ticker_81870, {"5m": df_5m_130bars})
        out = await get_market_data(deps)
        # Extract OHLCV section: split at next section header. Use `\n\n=== `
        # so it cuts to the next top-level section (In-progress Candle / Period
        # summary), isolating the closed-candle table cleanly.
        section = out.split("=== Recent Closed Candles")[1].split("\n\n=== ")[0]
        # At least one row should have a `N.NN×` value
        assert re.search(r"\d+\.\d{2}×", section), \
            f"No RVol value with × suffix found in OHLCV section: {section[:600]}"

    @pytest.mark.asyncio
    async def test_rvol_numeric_matches_vol_over_sma20(
        self, fake_ticker_81870, df_5m_130bars,
    ):
        """Issue 1: RVol value == bar.volume / SMA(20) of last 20 closed bars
        ending at that bar.

        df_5m_130bars has constant volume=100, so every bar's vol / SMA(20) = 1.0.
        """
        from src.agent.tools_perception import get_market_data
        deps = _build_gmd_deps(fake_ticker_81870, {"5m": df_5m_130bars})
        out = await get_market_data(deps)
        section = out.split("=== Recent Closed Candles")[1].split("\n\n=== ")[0]
        # Every visible RVol value (vol / 100 = 1.0) should render as `1.00×`
        # — assert at least one such value appears (full match across all
        # rows is brittle to column-alignment whitespace; presence is enough).
        assert "1.00×" in section, \
            f"Expected RVol 1.00× (vol/SMA=1.0 for constant-vol fixture); section={section[:600]}"

    @pytest.mark.asyncio
    async def test_rvol_marker_consistency_high_volume(
        self, fake_ticker_81870, df_5m_anomaly,
    ):
        """Issue 1: when bar volume = 6× the baseline (input ratio), RVol on
        rendered table shows ≈ 4.8× AND vol↑ marker present. Tests common
        case (not FP-boundary).

        df_5m_anomaly: bar 127 volume = 600 vs baseline 100. The rendered
        RVol uses `rolling(20).mean()` AT bar 127, which **includes** bar
        127's anomalous volume in the SMA window: SMA = (19×100 + 600)/20 =
        125 → RVol = 600/125 = 4.8× (matches df_4h_recent_vol_spike fixture
        docstring math). The 6× input ratio gets attenuated by the SMA
        self-inclusion to ~4.8× — this is by design (RVol shows the bar's
        volume relative to its own 20-bar context, not a forward-looking
        baseline).
        """
        from src.agent.tools_perception import get_market_data
        deps = _build_gmd_deps(fake_ticker_81870, {"5m": df_5m_anomaly})
        out = await get_market_data(deps)
        # Common case: very-high RVol bar
        # Find a row with a RVol ratio > 4 — should be the anomaly bar
        import re
        # Match `<digit(s)>.<digit><digit>×` and look for high values
        rvol_matches = re.findall(r"(\d+)\.(\d{2})×", out)
        high_rvols = [float(f"{a}.{b}") for a, b in rvol_matches if int(a) >= 4]
        assert high_rvols, \
            f"Expected at least one RVol ≥ 4.00× from anomaly fixture; out: {out[:600]}"
        assert "vol↑" in out, \
            f"vol↑ marker should accompany high RVol; out: {out[:600]}"

    @pytest.mark.asyncio
    async def test_rvol_marker_consistency_low_volume(
        self, fake_ticker_81870, df_5m_130bars,
    ):
        """Issue 1: when RVol << 2, no vol↑ marker.

        df_5m_130bars: constant volume → RVol ≈ 1.00×, no vol↑.
        """
        from src.agent.tools_perception import get_market_data
        deps = _build_gmd_deps(fake_ticker_81870, {"5m": df_5m_130bars})
        out = await get_market_data(deps)
        # In a constant-volume fixture, no bar should trigger vol↑
        section = out.split("=== Recent Closed Candles")[1].split("\n\n=== ")[0]
        assert "vol↑" not in section, \
            f"vol↑ should not fire on constant-volume fixture; section: {section[:600]}"


# === Task 3 (renamed Task 4): in-progress candle independent section ===

class TestInProgressSection:
    @pytest.mark.asyncio
    async def test_in_progress_section_header_and_columns_5m(
        self, fake_ticker_81870, df_5m_130bars,
    ):
        """议题1: 独立 In-progress Candle section（header + so-far 列头 + caveat）。"""
        from src.agent.tools_perception import get_market_data
        deps = _build_gmd_deps(fake_ticker_81870, {"5m": df_5m_130bars})
        out = await get_market_data(deps)
        assert "=== In-progress Candle (5m):" in out
        assert "High(so far)" in out and "Low(so far)" in out and "Vol(so far)" in out
        assert "(partial bar — excluded from all indicators; no RVol/markers until close)" in out

    @pytest.mark.asyncio
    async def test_in_progress_row_values_from_iloc_minus1(
        self, fake_ticker_81870, df_5m_130bars,
    ):
        """议题1 护栏2: section 行 O/H/L/Last/Vol 全取 df.iloc[-1]（含被丢弃那根）。"""
        from src.agent.tools_perception import get_market_data
        df = df_5m_130bars
        ip = df.iloc[-1]
        deps = _build_gmd_deps(fake_ticker_81870, {"5m": df})
        out = await get_market_data(deps)
        ip_block = out.split("=== In-progress Candle")[1].split("\n\n=== ")[0]  # 限定到段内
        assert f"{ip['open']:.2f}" in ip_block
        assert f"{ip['high']:.2f}" in ip_block
        assert f"{ip['low']:.2f}" in ip_block
        assert f"{ip['close']:.2f}" in ip_block   # Last 列 = df.iloc[-1].close

    @pytest.mark.asyncio
    async def test_in_progress_no_rvol_no_markers(
        self, fake_ticker_81870, df_5m_anomaly,
    ):
        """议题1 护栏1: in-progress 行不含 RVol(×) / vol↑ / range↑。"""
        from src.agent.tools_perception import get_market_data
        deps = _build_gmd_deps(fake_ticker_81870, {"5m": df_5m_anomaly})
        out = await get_market_data(deps)
        # 限定到 in-progress 段内（"\n\n=== " 切到下一段头）——否则仍未删的 Period
        # summary 的 (2.00×)（df_5m_anomaly: bar127=600 落在 last_5）会污染 not-in 断言。
        ip_block = out.split("=== In-progress Candle")[1].split("\n\n=== ")[0]
        assert "×" not in ip_block
        assert "vol↑" not in ip_block and "range↑" not in ip_block

    @pytest.mark.asyncio
    async def test_in_progress_open_close_timestamps_5m(
        self, fake_ticker_81870, df_5m_130bars,
    ):
        """议题1: header 的 open/close 时点 = df.iloc[-1] timestamp 与 +1 tf offset。"""
        import pandas as pd
        from src.agent.tools_perception import get_market_data
        df = df_5m_130bars
        ip_open_ms = int(df["timestamp"].iloc[-1])  # 独立换算，不经被测函数
        ip_open = pd.Timestamp(ip_open_ms, unit="ms", tz="UTC")
        ip_close = ip_open + pd.Timedelta(minutes=5)
        deps = _build_gmd_deps(fake_ticker_81870, {"5m": df})
        out = await get_market_data(deps)
        assert f"{ip_open.strftime('%H:%M')} open" in out
        assert f"closes {ip_close.strftime('%H:%M')}" in out

    @pytest.mark.asyncio
    async def test_in_progress_monthly_uses_dateoffset(
        self, fake_ticker_81870,
    ):
        """议题1: 1M tf 用 DateOffset（calendar-aware），elapsed 走 days 单位。"""
        import pandas as pd
        from tests.fixtures.multi_tf_ohlcv import _build
        from src.utils.ohlcv_utils import _to_pd_timestamp_utc, TF_OFFSETS
        from src.agent.tools_perception import get_market_data
        closes = [50000.0 + i * 100 for i in range(81)]
        df_1M = _build(
            start_ms=int(pd.Timestamp("2020-01-01", tz="UTC").value / 1e6),
            tf="1M", closes=closes,
        )
        ip_open = _to_pd_timestamp_utc(df_1M["timestamp"].iloc[-1])
        ip_close = ip_open + TF_OFFSETS["1M"]
        deps = _build_gmd_deps(fake_ticker_81870, {"1M": df_1M}, tf="1M")
        out = await get_market_data(deps, timeframe="1M")
        assert f"{ip_open.strftime('%Y-%m')} open" in out
        assert f"closes {ip_close.strftime('%Y-%m')}" in out
        assert "days elapsed ===" in out   # 1M total > 48h → days 单位

    @pytest.mark.asyncio
    async def test_in_progress_hourly_unit_for_1d(
        self, fake_ticker_81870,
    ):
        """议题1: 1d tf 的 elapsed 走中间 'h' 单位档（90min < 24h <= 48h）。

        钉住三档分支里此前完全无覆盖的 'h' 档（涵盖 2h/4h/1d 等核心交易周期）。
        """
        import pandas as pd
        from tests.fixtures.multi_tf_ohlcv import _build
        from src.utils.ohlcv_utils import _to_pd_timestamp_utc, TF_OFFSETS, _fmt_candle_time
        from src.agent.tools_perception import get_market_data
        closes = [50000.0 + i * 100 for i in range(60)]
        df_1d = _build(
            start_ms=int(pd.Timestamp("2023-01-01", tz="UTC").value / 1e6),
            tf="1d", closes=closes,
        )
        # 独立换算期望时点（不经被测函数）
        ip_open = _to_pd_timestamp_utc(df_1d["timestamp"].iloc[-1])
        ip_close = ip_open + TF_OFFSETS["1d"]
        deps = _build_gmd_deps(fake_ticker_81870, {"1d": df_1d}, tf="1d")
        out = await get_market_data(deps, timeframe="1d")
        assert "=== In-progress Candle (1d):" in out
        # 限定到 in-progress header 行，钉 'h' 档且排除误判 min/days 档
        ip_line = next(
            l for l in out.splitlines() if l.startswith("=== In-progress Candle")
        )
        assert ip_line.endswith("h elapsed ===")          # 1d total = 24h → 'h' 单位
        assert "min elapsed" not in ip_line and "days elapsed" not in ip_line
        # open/close 时点独立验证（1d → _fmt_candle_time 用 %Y-%m-%d）
        assert f"{_fmt_candle_time(ip_open, '1d')} open" in out
        assert f"closes {_fmt_candle_time(ip_close, '1d')}" in out

    @pytest.mark.asyncio
    async def test_in_progress_unknown_tf_degraded_open_only(
        self, fake_ticker_81870, df_5m_130bars,
    ):
        """议题1 降级: 未知 tf → header 只显 open（无 closes/elapsed），不 raise。"""
        from src.agent.tools_perception import get_market_data
        deps = _build_gmd_deps(fake_ticker_81870, {"7m": df_5m_130bars}, tf="7m")
        out = await get_market_data(deps, timeframe="7m")
        assert "=== In-progress Candle (7m):" in out
        assert "open ===" in out          # 降级头以 ` open ===` 收尾
        assert "elapsed" not in out.split("=== In-progress Candle")[1].split("\n")[0]

    @pytest.mark.asyncio
    async def test_recent_table_renamed_no_suffix(
        self, fake_ticker_81870, df_5m_130bars,
    ):
        """议题1 配套: 表头 Recent Closed Candles，旧 in-progress 后缀消失。"""
        from src.agent.tools_perception import get_market_data
        deps = _build_gmd_deps(fake_ticker_81870, {"5m": df_5m_130bars})
        out = await get_market_data(deps)
        assert "=== Recent Closed Candles (5m, last" in out
        assert "still open, closes at" not in out   # 旧后缀已收敛到独立 section


# === Task 4: delete N-candle High-Low row ===

class TestDeletedNCandleHL:
    @pytest.mark.asyncio
    async def test_no_n_candle_high_low_row(
        self, fake_ticker_81870, df_5m_130bars,
    ):
        """Market Context no longer contains `<N>-candle High-Low` row.
        Audit: 1.1% adoption; 24h H/L (ticker section, 54.4% adoption) is the
        surviving anchor."""
        import re
        from src.agent.tools_perception import get_market_data
        deps = _build_gmd_deps(fake_ticker_81870, {"5m": df_5m_130bars})
        out = await get_market_data(deps)
        # Old format: `30-candle High-Low: 76430 — 77594`
        assert not re.search(r"\d+-candle High-Low:", out), \
            f"N-candle High-Low row should be deleted; out={out[:1200]}"
        # 24h H/L (from ticker) should still be present
        assert "24h High:" in out and "24h Low:" in out, \
            f"24h H/L should still be in ticker section as surviving anchor"
        # Market Context section deleted (议题4+5); ATR moved into Technical Indicators
        assert "=== Market Context ===" not in out
        assert "ATR(14):" in out  # still present, now in Technical Indicators section


# === Task 5: delete Period summary 整段 (议题6) ===

class TestPeriodSummaryDeleted:
    @pytest.mark.asyncio
    async def test_period_summary_section_removed(
        self, fake_ticker_81870, df_5m_130bars,
    ):
        """议题6: Period summary 整段删除（决策价值 4.7%，被 taker_flow/RVol 覆盖）。"""
        from src.agent.tools_perception import get_market_data
        deps = _build_gmd_deps(fake_ticker_81870, {"5m": df_5m_130bars})
        out = await get_market_data(deps)
        assert "=== Period summary" not in out
        assert "Net Δclose" not in out
        assert "Avg vol:" not in out


# === Task 6: docstring updates across 3 channels ===

class TestDocstringRewrite:
    def test_ch_desc_description_contains_new_content(self):
        """CH-DESC (tools_descriptions.py:GET_MARKET_DATA_DESCRIPTION
        override → tool_def.description) reflects new OHLCV table format
        (RVol column + in-progress section). Block-style Example call/output
        preserved (bypasses griffe per @tool(description=...) override;
        verified by test_dual_mode_tool_wrapper)."""
        from src.agent.trader import create_trader_agent
        from src.config import PersonaConfig

        agent = create_trader_agent(model="test", persona_config=PersonaConfig())
        tool = agent._function_toolset.tools["get_market_data"]
        desc = tool.tool_def.description

        # Block-style sections still present (CH-DESC bypasses griffe)
        assert "=== Ticker" in desc, "Ticker section header missing from CH-DESC"
        assert "=== Recent Closed Candles" in desc, "Recent Closed Candles header missing"
        assert "=== Period summary" not in desc, "Period summary should be removed"
        assert "=== In-progress Candle" in desc, "In-progress Candle section missing"

        # New content from this iter:
        assert "RVol(×SMA20)" in desc, \
            f"RVol column header (literal `RVol(×SMA20)`) missing in CH-DESC: {desc!r}"
        assert "in-progress" in desc, \
            f"in-progress documentation missing in CH-DESC: {desc!r}"

        # Markers semantics preserved:
        assert "vol↑" in desc, "vol↑ marker semantics missing"
        assert "range↑" in desc, "range↑ marker semantics missing"

        # Deletions reflected:
        assert "Avg range" not in desc, \
            f"Avg range should be removed: {desc!r}"
        assert "Market Context" not in desc, \
            f"Market Context should be removed: {desc!r}"

    def test_candle_count_clamp_text_in_params_schema(self):
        """Clamp explicit text reaches LLM via CH-ARGS channel (trader.py:124-140
        inner ctx-receiver docstring's Args block → parameters_json_schema),
        NOT via CH-DESC. Per spec §2.5 / test_dual_mode_tool_wrapper: griffe
        parses the decorated function's own Args block — which for
        get_market_data lives at trader.py:124-140 inner ctx-receiver, NOT at
        tools_perception.py:51 impl."""
        from src.agent.trader import create_trader_agent
        from src.config import PersonaConfig

        agent = create_trader_agent(model="test", persona_config=PersonaConfig())
        tool = agent._function_toolset.tools["get_market_data"]
        schema = tool.tool_def.parameters_json_schema
        candle_count_desc = schema["properties"]["candle_count"]["description"]

        # Clamp explicit:
        assert "Clamped to [10, 80]" in candle_count_desc, \
            f"candle_count clamp explicit text missing in params schema: {candle_count_desc!r}"
        # Reasoning behind floor / cap (strict — match the implementation literal):
        assert "minimum useful window" in candle_count_desc, \
            f"floor=10 reasoning ('minimum useful window') missing: {candle_count_desc!r}"
        assert "exchange API" in candle_count_desc, \
            f"cap=80 reasoning ('exchange API') missing: {candle_count_desc!r}"
