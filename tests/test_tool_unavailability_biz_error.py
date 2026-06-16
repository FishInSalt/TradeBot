"""半 B — 自收敛工具不可用可观测化（spec §2 + §3）。

seam：工具调 note_biz_error('source_unavailable') 写 ContextVar；这里在工具返回后读
该 ContextVar（= recorder 的读取点），验证打点是否发生。ContextVar→DB 翻译另有覆盖。
"""
from __future__ import annotations

from dataclasses import dataclass
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from src.services.tool_call_recorder import _biz_error_type


@dataclass
class MockDeps:
    symbol: str = "BTC/USDT:USDT"
    timeframe: str = "15m"
    market_data: object = None
    exchange: object = None
    technical: object = None
    memory: object = None
    session_id: str = "test"
    news: object = None
    macro: object = None
    crypto_etf: object = None
    onchain: object = None


async def _biz_after(coro):
    """跑工具 coro，返回 (result, 它设的 biz_error type)；镜像 recorder 读取点。"""
    token = _biz_error_type.set(None)
    try:
        result = await coro
        return result, _biz_error_type.get()
    finally:
        _biz_error_type.reset(token)


# ============ POINT 工具：异常 catch → biz_error ============

@pytest.mark.asyncio
async def test_taker_flow_outage_points():
    from src.agent.tools_perception import get_taker_flow
    md = SimpleNamespace(get_taker_flow=AsyncMock(side_effect=RuntimeError("down")))
    result, biz = await _biz_after(get_taker_flow(MockDeps(market_data=md), "1h", 20))
    assert "unavailable" in result.lower()
    assert biz == "source_unavailable"


@pytest.mark.asyncio
async def test_derivatives_all_sources_failed_points():
    from src.agent.tools_perception import get_derivatives_data
    md = SimpleNamespace(
        get_funding_rate=AsyncMock(side_effect=RuntimeError("d")),
        get_open_interest_history=AsyncMock(side_effect=RuntimeError("d")),
        get_long_short_ratio=AsyncMock(side_effect=RuntimeError("d")),
    )
    result, biz = await _biz_after(get_derivatives_data(MockDeps(market_data=md)))
    assert "all 3 data sources failed" in result.lower()
    assert biz == "source_unavailable"


@pytest.mark.asyncio
async def test_htf_view_ticker_outage_points():
    from src.agent.tools_perception import get_higher_timeframe_view
    md = SimpleNamespace(get_ticker=AsyncMock(side_effect=RuntimeError("t")))
    result, biz = await _biz_after(get_higher_timeframe_view(MockDeps(market_data=md), timeframes=["1d"]))
    assert "temporarily unavailable" in result.lower()
    assert biz == "source_unavailable"


@pytest.mark.asyncio
async def test_macro_context_snapshot_exception_points():
    from src.agent.tools_perception import get_macro_context
    macro = SimpleNamespace(get_snapshot=AsyncMock(side_effect=RuntimeError("m")))
    result, biz = await _biz_after(get_macro_context(MockDeps(macro=macro)))
    assert "temporarily unavailable" in result.lower()
    assert biz == "source_unavailable"


@pytest.mark.asyncio
async def test_macro_context_all_sources_none_points():
    """snapshot 成功但全字段 None → any_available False → 总失败点。"""
    from src.agent.tools_perception import get_macro_context
    snap = SimpleNamespace(
        btc_dominance=None, eth_dominance=None, total_mcap_usd=None, mcap_change_24h_pct=None,
        usd_index_broad_tw=None, vix=None, treasury_10y=None, spread_10y_2y=None, inflation_10y=None,
        spy=None, qqq=None,
    )
    macro = SimpleNamespace(get_snapshot=AsyncMock(return_value=snap))
    result, biz = await _biz_after(get_macro_context(MockDeps(macro=macro)))
    assert "all sources temporarily unavailable" in result.lower()
    assert biz == "source_unavailable"


@pytest.mark.asyncio
async def test_order_book_outage_points():
    from src.agent.tools_perception import get_order_book
    md = SimpleNamespace(get_order_book=AsyncMock(side_effect=RuntimeError("ob")))
    result, biz = await _biz_after(get_order_book(MockDeps(market_data=md)))
    assert "temporarily unavailable" in result.lower()
    assert biz == "source_unavailable"


@pytest.mark.asyncio
async def test_recent_trades_outage_points():
    from src.agent.tools_perception import get_recent_trades
    md = SimpleNamespace(get_recent_trades=AsyncMock(side_effect=RuntimeError("rt")))
    result, biz = await _biz_after(get_recent_trades(MockDeps(market_data=md)))
    assert "temporarily unavailable" in result.lower()
    assert biz == "source_unavailable"


@pytest.mark.asyncio
async def test_mts_ticker_outage_points():
    from src.agent.tools_perception import get_multi_timeframe_snapshot
    md = SimpleNamespace(get_ticker=AsyncMock(side_effect=RuntimeError("t")))
    result, biz = await _biz_after(get_multi_timeframe_snapshot(MockDeps(market_data=md)))
    assert "temporarily unavailable" in result.lower()
    assert biz == "source_unavailable"


@pytest.mark.asyncio
async def test_mts_all_timeframes_failed_points():
    """ticker 成功但所有 TF 的 OHLCV 全失败 → 总失败点。"""
    from src.agent.tools_perception import get_multi_timeframe_snapshot
    md = SimpleNamespace(
        get_ticker=AsyncMock(return_value=SimpleNamespace(last=75000.0, bid=74999.0, ask=75001.0)),
        get_ohlcv_dataframe=AsyncMock(side_effect=RuntimeError("ohlcv")),
    )
    result, biz = await _biz_after(get_multi_timeframe_snapshot(MockDeps(market_data=md), tfs=["5m", "1h"]))
    assert "all timeframes failed" in result.lower()
    assert biz == "source_unavailable"


@pytest.mark.asyncio
async def test_price_pivots_ticker_outage_points():
    from src.agent.tools_perception import get_price_pivots
    md = SimpleNamespace(get_ticker=AsyncMock(side_effect=RuntimeError("t")))
    result, biz = await _biz_after(get_price_pivots(MockDeps(market_data=md)))
    assert "temporarily unavailable" in result.lower()
    assert biz == "source_unavailable"


@pytest.mark.asyncio
async def test_exchange_announcements_outage_points():
    from src.agent.tools_perception import get_exchange_announcements
    news = SimpleNamespace(get_announcements=AsyncMock(side_effect=RuntimeError("a")))
    result, biz = await _biz_after(get_exchange_announcements(MockDeps(news=news)))
    assert "temporarily unavailable" in result.lower()
    assert biz == "source_unavailable"


# ============ 收敛为 None-sentinel 的总失败点 → biz_error ============
# 这些工具把上游故障（异常被工具内部 catch→None，或上游直接返 None）汇到一个 None-check，
# 在该 check 处 POINT。B3 的 instrument 落点 = None-check 块（区别于上一段「except 块内」打点）。
# 注入异常是最真实的 outage 场景，故多数 mock 用 side_effect；stablecoin 另有一条 return_value=None
# 直证「上游直接返 None」也走同一 check。

@pytest.mark.asyncio
async def test_macro_calendar_none_sentinel_points():
    """上游抛异常 → 工具 except 内转 macro_events=None → None-check 处 POINT（spec §2）。"""
    from src.agent.tools_perception import get_macro_calendar
    news = SimpleNamespace(get_macro_events=AsyncMock(side_effect=RuntimeError("m")))
    result, biz = await _biz_after(get_macro_calendar(MockDeps(news=news)))
    assert "temporarily unavailable" in result.lower()
    assert biz == "source_unavailable"


@pytest.mark.asyncio
async def test_etf_flows_both_none_sentinel_points():
    """BTC+ETH 两侧都抛 → btc=eth=None → 总失败点。"""
    from src.agent.tools_perception import get_etf_flows
    etf = SimpleNamespace(get_etf_flows=AsyncMock(side_effect=RuntimeError("e")))
    result, biz = await _biz_after(get_etf_flows(MockDeps(crypto_etf=etf)))
    assert "temporarily unavailable" in result.lower()
    assert biz == "source_unavailable"


@pytest.mark.asyncio
async def test_stablecoin_exception_points():
    from src.agent.tools_perception import get_stablecoin_supply
    onchain = SimpleNamespace(get_stablecoin_snapshot=AsyncMock(side_effect=RuntimeError("s")))
    result, biz = await _biz_after(get_stablecoin_supply(MockDeps(onchain=onchain)))
    assert "temporarily unavailable" in result.lower()
    assert biz == "source_unavailable"


@pytest.mark.asyncio
async def test_stablecoin_none_sentinel_points():
    """snapshot 返回 None（上游 outage sentinel）→ 总失败点。"""
    from src.agent.tools_perception import get_stablecoin_supply
    onchain = SimpleNamespace(get_stablecoin_snapshot=AsyncMock(return_value=None))
    result, biz = await _biz_after(get_stablecoin_supply(MockDeps(onchain=onchain)))
    assert "temporarily unavailable" in result.lower()
    assert biz == "source_unavailable"


# ============ 反例：保持 ok（ContextVar 不被 set）============

@pytest.mark.asyncio
async def test_stablecoin_schema_drift_stays_ok():
    """result['coins'] 空 = schema-drift（源可达、数据不可映射）→ ok，不打点。"""
    from src.agent.tools_perception import get_stablecoin_supply
    onchain = SimpleNamespace(get_stablecoin_snapshot=AsyncMock(return_value={"coins": [], "total": None}))
    result, biz = await _biz_after(get_stablecoin_supply(MockDeps(onchain=onchain)))
    assert "no tracked symbols" in result.lower()
    assert biz is None


@pytest.mark.asyncio
async def test_htf_per_tf_partial_degrade_stays_ok():
    """ticker 成功、某 TF 失败 = 部分降级（仍返回可用数据）→ ok。"""
    from src.agent.tools_perception import get_higher_timeframe_view
    md = SimpleNamespace(
        get_ticker=AsyncMock(return_value=SimpleNamespace(last=75000.0, bid=74999.0, ask=75001.0)),
        get_ohlcv_dataframe=AsyncMock(side_effect=RuntimeError("one tf")),
    )
    result, biz = await _biz_after(get_higher_timeframe_view(MockDeps(market_data=md), timeframes=["1d"]))
    assert "[1d] error: temporarily unavailable" in result.lower()  # per-TF 降级行
    assert "last:" in result.lower(), "部分降级的实质：ticker 成功 → 仍渲染 Last 可用数据"
    assert biz is None, "部分降级不打点（agent 仍拿到 Last 等可用数据）"


@pytest.mark.asyncio
async def test_market_news_not_configured_stays_ok():
    """deps.news is None = 配置缺失（非瞬态故障）→ ok，且 market_news 全程未被 instrument。"""
    from src.agent.tools_perception import get_market_news
    result, biz = await _biz_after(get_market_news(MockDeps(news=None)))
    assert "not configured" in result.lower()
    assert biz is None


# ============ §3 崩溃穿透：get_market_data / get_open_orders 不降级、不打点 ============

@pytest.mark.asyncio
async def test_get_market_data_ticker_outage_propagates():
    """primary 市场数据不可用必须 abort cycle（由 crash-backoff 恢复）：异常穿透，不记 biz_error。"""
    from src.agent.tools_perception import get_market_data
    md = SimpleNamespace(get_ticker=AsyncMock(side_effect=RuntimeError("ticker down")))
    token = _biz_error_type.set(None)
    try:
        with pytest.raises(RuntimeError, match="ticker down"):
            await get_market_data(MockDeps(market_data=md))
        assert _biz_error_type.get() is None, "get_market_data 不得降级 / 打 biz_error"
    finally:
        _biz_error_type.reset(token)


@pytest.mark.asyncio
async def test_get_open_orders_ticker_outage_propagates():
    """get_open_orders ticker 裸调（real-net）：超时穿透崩溃，行为本 iter 不改。

    get_open_orders 在 fetch_open_orders 返回非空列表后，直接调 get_ticker（line 567），
    无任何 order 属性访问在先——[object()] 足以让执行走到 ticker 处。
    """
    from src.agent.tools_perception import get_open_orders
    exchange = SimpleNamespace(fetch_open_orders=AsyncMock(return_value=[object()]))  # 非空 → 走到 ticker
    md = SimpleNamespace(get_ticker=AsyncMock(side_effect=RuntimeError("ticker down")))
    token = _biz_error_type.set(None)
    try:
        with pytest.raises(RuntimeError, match="ticker down"):
            await get_open_orders(MockDeps(exchange=exchange, market_data=md))
        assert _biz_error_type.get() is None, "get_open_orders 不得降级 / 打 biz_error"
    finally:
        _biz_error_type.reset(token)
