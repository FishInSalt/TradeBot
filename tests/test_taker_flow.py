"""Tests for get_taker_flow: rubik taker-volume fetch + minute-level flow rendering.

Covers spec docs/superpowers/specs/2026-05-30-order-flow-tools-redesign-design.md
§2 (rubik source), §3.1-3.3 (taker_flow design), §3.5 (errors), §4.1 (architecture),
§5 ①②③⑤⑥ (tests).
"""
import time
import pytest
from unittest.mock import AsyncMock, MagicMock


def test_taker_flow_bar_dataclass_fields():
    from src.integrations.exchange.base import TakerFlowBar
    b = TakerFlowBar(ts=1778644800000, sell_usd=5_800_000.0, buy_usd=4_200_000.0)
    assert b.ts == 1778644800000
    assert b.sell_usd == pytest.approx(5_800_000.0)
    assert b.buy_usd == pytest.approx(4_200_000.0)


def test_taker_volume_period_map_is_complete():
    """§3.1/§3.3/③: distinct from _OKX_OI_PERIOD; covers tool periods {5m,1h,4h,1d}
    PLUS the 1w anchor up-tier. Reusing _OKX_OI_PERIOD would KeyError on 4h/1w."""
    from src.integrations.exchange.base import _TAKER_VOLUME_PERIOD, _OKX_OI_PERIOD
    assert _TAKER_VOLUME_PERIOD == {"5m": "5m", "1h": "1H", "4h": "4H", "1d": "1D", "1w": "1W"}
    assert _TAKER_VOLUME_PERIOD is not _OKX_OI_PERIOD
    for p in ("5m", "1h", "4h", "1d", "1w"):
        assert p in _TAKER_VOLUME_PERIOD


def _sim_with_rubik(data_rows):
    """SimulatedExchange with mocked _ccxt rubik response. `.market` is SYNC
    (ccxt market() is synchronous) -> MagicMock; the rubik endpoint is async."""
    from src.integrations.exchange.simulated import SimulatedExchange
    ex = SimulatedExchange.__new__(SimulatedExchange)
    ex._ccxt = MagicMock()
    ex._ccxt.market.return_value = {"id": "BTC-USDT-SWAP"}
    ex._ccxt.public_get_rubik_stat_taker_volume_contract = AsyncMock(
        return_value={"code": "0", "data": data_rows, "msg": ""}
    )
    ex._validate_symbol = lambda s: None  # bypass symbol guard for unit isolation
    return ex


@pytest.mark.asyncio
async def test_sim_fetch_taker_flow_parses_and_ascends():
    # Raw OKX rubik is newest-first: [ts, sellVol, buyVol] (col1=sell, col2=buy).
    # Newest row (in-progress current bucket) must survive AND end up LAST after
    # the ascending sort (no drop/shift at fetch layer).
    rows = [
        ["1778644800000", "5800000", "4200000"],  # newest = in-progress
        ["1778644500000", "9000000", "8000000"],
        ["1778644200000", "1000000", "9000000"],  # oldest
    ]
    ex = _sim_with_rubik(rows)
    bars = await ex.fetch_taker_flow("BTC/USDT:USDT", "5m", 3)
    assert len(bars) == 3
    assert bars[0].ts == 1778644200000          # oldest first
    assert bars[-1].ts == 1778644800000         # in-progress newest kept, last
    # Column order [ts, sell, buy] (regression guard against direction flip):
    assert bars[-1].sell_usd == pytest.approx(5800000.0)
    assert bars[-1].buy_usd == pytest.approx(4200000.0)


@pytest.mark.asyncio
async def test_sim_fetch_taker_flow_passes_unit_period_instid_limit():
    ex = _sim_with_rubik([["1778644800000", "1", "2"]])
    await ex.fetch_taker_flow("BTC/USDT:USDT", "4h", 21)
    ex._ccxt.public_get_rubik_stat_taker_volume_contract.assert_awaited_once_with(
        {"instId": "BTC-USDT-SWAP", "period": "4H", "unit": "2", "limit": "21"}
    )


@pytest.mark.asyncio
async def test_sim_fetch_taker_flow_empty():
    ex = _sim_with_rubik([])
    assert await ex.fetch_taker_flow("BTC/USDT:USDT", "5m", 6) == []


@pytest.mark.asyncio
async def test_sim_fetch_taker_flow_rate_limit_raises():
    import ccxt.async_support as ccxt
    from src.utils.cache import RateLimitHit
    ex = _sim_with_rubik([])
    ex._ccxt.public_get_rubik_stat_taker_volume_contract = AsyncMock(
        side_effect=ccxt.RateLimitExceeded("429")
    )
    with pytest.raises(RateLimitHit):
        await ex.fetch_taker_flow("BTC/USDT:USDT", "5m", 6)


def _okx_with_rubik(data_rows):
    from src.integrations.exchange.okx import OKXExchange
    ex = OKXExchange.__new__(OKXExchange)
    ex._client = MagicMock()
    ex._client.market.return_value = {"id": "BTC-USDT-SWAP"}
    ex._client.public_get_rubik_stat_taker_volume_contract = AsyncMock(
        return_value={"code": "0", "data": data_rows, "msg": ""}
    )
    return ex


@pytest.mark.asyncio
async def test_okx_fetch_taker_flow_parses_and_ascends():
    rows = [
        ["1778644800000", "5800000", "4200000"],  # newest = in-progress
        ["1778644500000", "9000000", "8000000"],  # oldest
    ]
    ex = _okx_with_rubik(rows)
    bars = await ex.fetch_taker_flow("BTC/USDT:USDT", "1h", 2)
    assert [b.ts for b in bars] == [1778644500000, 1778644800000]
    assert bars[-1].sell_usd == pytest.approx(5800000.0)
    assert bars[-1].buy_usd == pytest.approx(4200000.0)
    ex._client.public_get_rubik_stat_taker_volume_contract.assert_awaited_once_with(
        {"instId": "BTC-USDT-SWAP", "period": "1H", "unit": "2", "limit": "2"}
    )


@pytest.mark.asyncio
async def test_okx_fetch_taker_flow_empty():
    ex = _okx_with_rubik([])
    assert await ex.fetch_taker_flow("BTC/USDT:USDT", "5m", 6) == []


def test_base_exchange_has_fetch_taker_flow_abstractmethod():
    import inspect
    from src.integrations.exchange.base import BaseExchange
    assert "fetch_taker_flow" in BaseExchange.__abstractmethods__
    sig = inspect.signature(BaseExchange.fetch_taker_flow)
    assert sig.parameters["period"].default == "5m"
    assert sig.parameters["limit"].default == 6


@pytest.mark.asyncio
async def test_market_data_get_taker_flow_passthrough_uncached():
    from src.integrations.market_data import MarketDataService
    from src.integrations.exchange.base import TakerFlowBar
    exchange = AsyncMock()
    exchange.fetch_taker_flow.return_value = [TakerFlowBar(ts=1, sell_usd=2.0, buy_usd=3.0)]
    svc = MarketDataService(exchange)
    out1 = await svc.get_taker_flow("BTC/USDT:USDT", "5m", 21)
    out2 = await svc.get_taker_flow("BTC/USDT:USDT", "5m", 21)
    assert out1[0].buy_usd == pytest.approx(3.0)
    # NOT cached: two calls -> two underlying fetches (unlike get_open_interest_history)
    assert exchange.fetch_taker_flow.await_count == 2
    exchange.fetch_taker_flow.assert_awaited_with("BTC/USDT:USDT", "5m", 21)


def _bars(n, period_ms, *, base_open, sell=1_000_000.0, buy=1_000_000.0):
    """n ascending TakerFlowBar; bar i opens at base_open + i*period_ms.
    Caller sets base_open so the last bar is in-progress relative to now_ms."""
    from src.integrations.exchange.base import TakerFlowBar
    return [TakerFlowBar(ts=base_open + i * period_ms, sell_usd=sell, buy_usd=buy)
            for i in range(n)]


def test_render_taker_flow_now_line_and_in_progress():
    from src.agent.tools_perception import _render_taker_flow
    period_ms = 300_000
    now = 1_000_000_000_000
    # last bar opens 2min before now -> in-progress, 2.0/5min formed
    bars = _bars(21, period_ms, base_open=now - 120_000 - 20 * period_ms)
    # make the newest bar buy-heavy so buy% is checkable
    bars[-1].buy_usd, bars[-1].sell_usd = 700_000.0, 300_000.0
    out = _render_taker_flow(bars, "5m", 6, now_ms=now, symbol="BTC-USDT-SWAP", fetch_ts="04:34")
    assert "=== Taker Flow (BTC-USDT-SWAP · 5m bars · @04:34 UTC) ===" in out
    assert "current 5m, 2.0/5min formed" in out
    assert "70% taker buy" in out                 # newest bar buy%
    assert "row 1 = current in-progress" in out
    assert "still forming (2.0/5min)" in out      # per-bar footnote


def test_render_taker_flow_window_cvd_and_net_sell_count():
    from src.agent.tools_perception import _render_taker_flow
    from src.integrations.exchange.base import TakerFlowBar
    period_ms = 300_000
    now = 1_000_000_000_000
    bars = _bars(21, period_ms, base_open=now - 60_000 - 20 * period_ms)
    # displayed window = last 3 bars; make 1 of them net-sell
    bars[-3].buy_usd, bars[-3].sell_usd = 1_000_000.0, 1_000_000.0   # net 0
    bars[-2].buy_usd, bars[-2].sell_usd = 2_000_000.0, 1_000_000.0   # +1M
    bars[-1].buy_usd, bars[-1].sell_usd = 500_000.0, 1_500_000.0     # -1M (net-sell)
    out = _render_taker_flow(bars, "5m", 3, now_ms=now, symbol="X", fetch_ts="00:00")
    assert "Window (3 bars = 15min):" in out
    assert "1/3 bars net-sell" in out
    # CVD over window (oldest->newest cumulative): 0, +1M, then 0 => window CVD ~ 0.0
    assert "CVD +0.0$M" in out or "CVD -0.0$M" in out


def test_render_taker_flow_rvol_fixed_20_baseline_and_limit_1_no_degeneracy():
    from src.agent.tools_perception import _render_taker_flow
    period_ms = 300_000
    now = 1_000_000_000_000
    # 20 closed bars each total=2M (sell+buy=1M+1M); in-progress newest total=4M
    bars = _bars(21, period_ms, base_open=now - 60_000 - 20 * period_ms)
    bars[-1].buy_usd, bars[-1].sell_usd = 2_000_000.0, 2_000_000.0   # total 4M
    out = _render_taker_flow(bars, "5m", 1, now_ms=now, symbol="X", fetch_ts="00:00")
    # newest total 4M / 20-bar avg 2M = 2.0x ; limit=1 still computes (no "—")
    assert "2.0× (vs 20-bar avg)" in out
    assert "RVol(×20-bar)" in out


def test_render_taker_flow_rvol_degrades_below_20_closed():
    from src.agent.tools_perception import _render_taker_flow
    period_ms = 300_000
    now = 1_000_000_000_000
    bars = _bars(6, period_ms, base_open=now - 60_000 - 5 * period_ms)  # only 5 closed
    out = _render_taker_flow(bars, "5m", 6, now_ms=now, symbol="X", fetch_ts="00:00")
    assert "vol —" in out or "—" in out  # RVol falls back when <20 closed bars


def test_render_taker_flow_close_column_joins_by_ts_and_dashes_missing():
    from src.agent.tools_perception import _render_taker_flow
    period_ms = 300_000
    now = 1_000_000_000_000
    bars = _bars(21, period_ms, base_open=now - 60_000 - 20 * period_ms)
    # provide close for the last 2 displayed bars, omit one -> "—"
    closes = {bars[-1].ts: 73531.0, bars[-2].ts: 73553.0}  # bars[-3] missing
    out = _render_taker_flow(bars, "5m", 3, now_ms=now, symbol="X", fetch_ts="00:00", closes=closes)
    assert "Close" in out
    assert "73531" in out and "73553" in out
    # the unmatched displayed bar shows — in the Close column
    assert out.count("—") >= 1


def test_render_taker_flow_close_all_missing_safety_net_collapses_column():
    from src.agent.tools_perception import _render_taker_flow
    period_ms = 300_000
    now = 1_000_000_000_000
    bars = _bars(21, period_ms, base_open=now - 60_000 - 20 * period_ms)
    out = _render_taker_flow(bars, "5m", 3, now_ms=now, symbol="X", fetch_ts="00:00", closes={})
    # every displayed bar unmatched -> omit column + single explicit note (not per-row —)
    assert "no OHLCV bar matched" in out


def test_render_taker_flow_close_note_omits_column_for_1d():
    from src.agent.tools_perception import _render_taker_flow
    period_ms = 86_400_000
    now = 1_000_000_000_000
    bars = _bars(21, period_ms, base_open=now - 3_600_000 - 20 * period_ms)
    note = "Close: n/a — 1d rubik/OHLCV day-boundary mismatch (16:00 vs 00:00 UTC)"
    out = _render_taker_flow(bars, "1d", 3, now_ms=now, symbol="X", fetch_ts="00:00", close_note=note)
    assert note in out
    assert "Close" not in out.split("Per-bar")[1].splitlines()[1]  # header has no Close col


def test_render_taker_flow_anchor_line_when_provided_and_absent_when_none():
    from src.agent.tools_perception import _render_taker_flow
    from src.integrations.exchange.base import TakerFlowBar
    period_ms = 300_000
    now = 1_000_000_000_000
    bars = _bars(21, period_ms, base_open=now - 60_000 - 20 * period_ms)
    anchor_bar = TakerFlowBar(ts=now - 34 * 60_000, sell_usd=4_700_000.0, buy_usd=5_300_000.0)
    out = _render_taker_flow(bars, "5m", 6, now_ms=now, symbol="X", fetch_ts="00:00",
                             anchor=("1h", anchor_bar))
    assert "1h-scale anchor (current 1h, 34min formed):" in out
    assert "53% buy" in out  # 5.3M / (5.3M+4.7M) = 53.0% exactly (off the .5 round-half-even boundary)
    out2 = _render_taker_flow(bars, "5m", 6, now_ms=now, symbol="X", fetch_ts="00:00")
    assert "anchor" not in out2.lower()


import time as _time
from unittest.mock import AsyncMock, MagicMock
import pandas as pd


def _deps_with_taker(bars_by_period, *, ohlcv=None, ohlcv_exc=None, main_exc=None):
    """TradingDeps double: market_data.get_taker_flow keyed by period;
    get_ohlcv_dataframe returns `ohlcv` df (or raises ohlcv_exc)."""
    deps = MagicMock()
    deps.symbol = "BTC/USDT:USDT"
    async def _gtf(symbol, period, limit):
        if main_exc is not None and period in bars_by_period and limit > 1:
            raise main_exc
        return bars_by_period.get(period, [])
    deps.market_data.get_taker_flow = AsyncMock(side_effect=_gtf)
    if ohlcv_exc is not None:
        deps.market_data.get_ohlcv_dataframe = AsyncMock(side_effect=ohlcv_exc)
    else:
        deps.market_data.get_ohlcv_dataframe = AsyncMock(
            return_value=ohlcv if ohlcv is not None else pd.DataFrame(
                columns=["timestamp", "open", "high", "low", "close", "volume"]))
    return deps


def _live_bars(n, period_ms):
    from src.integrations.exchange.base import TakerFlowBar
    now = int(_time.time() * 1000)
    base = now - 60_000 - (n - 1) * period_ms  # last bar in-progress
    return [TakerFlowBar(ts=base + i * period_ms, sell_usd=1e6, buy_usd=1e6) for i in range(n)]


@pytest.mark.asyncio
async def test_get_taker_flow_rejects_bad_period():
    from src.agent.tools_perception import get_taker_flow
    out = await get_taker_flow(_deps_with_taker({}), period="15m")
    assert "period must be one of: 5m, 1h, 4h, 1d" in out


@pytest.mark.asyncio
async def test_get_taker_flow_rejects_out_of_range_limit():
    from src.agent.tools_perception import get_taker_flow
    deps = _deps_with_taker({})
    assert "limit must be in [1, 36]" in await get_taker_flow(deps, "5m", 0)
    assert "limit must be in [1, 36]" in await get_taker_flow(deps, "5m", 37)


@pytest.mark.asyncio
async def test_get_taker_flow_main_failure_unavailable():
    from src.agent.tools_perception import get_taker_flow
    deps = _deps_with_taker({"5m": _live_bars(21, 300_000)}, main_exc=RuntimeError("boom"))
    out = await get_taker_flow(deps, "5m", 6)
    assert "Taker flow temporarily unavailable" in out


@pytest.mark.asyncio
async def test_get_taker_flow_empty():
    from src.agent.tools_perception import get_taker_flow
    out = await get_taker_flow(_deps_with_taker({"5m": []}), "5m", 6)
    assert "No taker-volume data available." in out


@pytest.mark.asyncio
async def test_get_taker_flow_ohlcv_failure_degrades_close_but_renders_flow():
    from src.agent.tools_perception import get_taker_flow
    deps = _deps_with_taker({"5m": _live_bars(21, 300_000), "1h": _live_bars(2, 3_600_000)},
                            ohlcv_exc=RuntimeError("ohlcv down"))
    out = await get_taker_flow(deps, "5m", 6)
    assert "Close: n/a — OHLCV temporarily unavailable" in out
    assert "Per-bar" in out  # flow rows still render


@pytest.mark.asyncio
async def test_get_taker_flow_1d_omits_close_column():
    from src.agent.tools_perception import get_taker_flow
    deps = _deps_with_taker({"1d": _live_bars(21, 86_400_000), "1w": _live_bars(2, 604_800_000)})
    out = await get_taker_flow(deps, "1d", 6)
    assert "day-boundary mismatch" in out


@pytest.mark.asyncio
async def test_get_taker_flow_anchor_failure_drops_anchor_line():
    from src.agent.tools_perception import get_taker_flow
    async def _gtf(symbol, period, limit):
        if period == "1h":
            raise RuntimeError("anchor down")
        return _live_bars(21, 300_000)
    deps = _deps_with_taker({"5m": _live_bars(21, 300_000)})
    deps.market_data.get_taker_flow = AsyncMock(side_effect=_gtf)
    out = await get_taker_flow(deps, "5m", 6)
    assert "Per-bar" in out          # main series renders
    assert "anchor" not in out.lower()  # anchor line dropped silently


@pytest.mark.asyncio
async def test_get_taker_flow_happy_path_includes_close_and_anchor():
    from src.agent.tools_perception import get_taker_flow
    main = _live_bars(21, 300_000)
    anchor = _live_bars(2, 3_600_000)
    ohlcv = pd.DataFrame([{"timestamp": b.ts, "open": 1, "high": 1, "low": 1,
                           "close": 73000 + i, "volume": 1} for i, b in enumerate(main)])
    deps = _deps_with_taker({"5m": main, "1h": anchor}, ohlcv=ohlcv)
    out = await get_taker_flow(deps, "5m", 6)
    assert "Close" in out
    assert "1h-scale anchor" in out
