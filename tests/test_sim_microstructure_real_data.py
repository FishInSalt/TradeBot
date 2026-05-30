"""工具层耦合验证：换真实数据后 get_order_book / get_recent_trades 才会遇到的形态。"""
from __future__ import annotations
import time
import pytest
from unittest.mock import AsyncMock, MagicMock

from src.integrations.exchange.base import OrderBook, OrderBookLevel, Trade
from src.agent.tools_perception import get_order_book, get_recent_trades


def _deps_with_order_book(ob: OrderBook):
    deps = MagicMock()
    deps.symbol = "BTC/USDT:USDT"
    deps.market_data = MagicMock()
    deps.market_data.get_order_book = AsyncMock(return_value=ob)
    return deps


def _deps_with_trades(trades):
    deps = MagicMock()
    deps.symbol = "BTC/USDT:USDT"
    deps.market_data = MagicMock()
    deps.market_data.get_recent_trades = AsyncMock(return_value=trades)
    return deps


@pytest.mark.asyncio
async def test_get_order_book_renders_concentrated_levels():
    """某档 amount > 3× 同侧 median → 渲染 Concentrated Levels 段。"""
    bids = [OrderBookLevel(100.0 - i, 1.0) for i in range(15)]
    bids[5] = OrderBookLevel(95.0, 10.0)  # 10 > 3× median(1.0)=3.0 → wall
    asks = [OrderBookLevel(101.0 + i, 1.0) for i in range(15)]
    deps = _deps_with_order_book(OrderBook("BTC/USDT:USDT", bids, asks, 0))
    out = await get_order_book(deps, depth=15)
    assert "Concentrated Levels" in out
    assert "95.00" in out  # 该 bid wall 的价格出现
    assert "below mid" not in out  # 新格式去距离列


@pytest.mark.asyncio
async def test_get_order_book_non_balanced_bid_share():
    """total_bid >> total_ask → 渲染 'bid : ask = N:1' 非均衡分支。"""
    bids = [OrderBookLevel(100.0 - i * 0.1, 10.0) for i in range(15)]  # total 150
    asks = [OrderBookLevel(101.0 + i * 0.1, 1.0) for i in range(15)]   # total 15
    deps = _deps_with_order_book(OrderBook("BTC/USDT:USDT", bids, asks, 0))
    out = await get_order_book(deps, depth=15)
    assert "bid : ask =" in out
    assert "~50%" not in out  # 不是 balanced 分支


@pytest.mark.asyncio
async def test_get_order_book_degrades_on_failure():
    """market_data 抛异常 → 工具返 temporarily unavailable。"""
    deps = MagicMock()
    deps.symbol = "BTC/USDT:USDT"
    deps.market_data = MagicMock()
    deps.market_data.get_order_book = AsyncMock(side_effect=Exception("boom"))
    out = await get_order_book(deps, depth=15)
    assert "Temporarily unavailable" in out


@pytest.mark.asyncio
async def test_get_recent_trades_partial_coverage():
    """500 笔成交全落窗口末 <120s（fetch_ratio=1.0 且 oldest_age_ratio<0.95）→ partial coverage 注记。"""
    now_ms = int(time.time() * 1000)
    trades = [Trade(timestamp=now_ms - (i % 120) * 1000,
                    side="buy" if i % 2 else "sell",
                    price=70000.0, amount=0.01, trade_id=str(i))
              for i in range(500)]  # RECENT_TRADES_MAX_FETCH = 500 → fetch_ratio=1.0
    deps = _deps_with_trades(trades)
    out = await get_recent_trades(deps, window_seconds=300)
    assert "partial coverage" in out


@pytest.mark.asyncio
async def test_get_recent_trades_degrades_on_failure():
    """market_data 抛异常 → 工具返 temporarily unavailable。"""
    deps = MagicMock()
    deps.symbol = "BTC/USDT:USDT"
    deps.market_data = MagicMock()
    deps.market_data.get_recent_trades = AsyncMock(side_effect=Exception("boom"))
    out = await get_recent_trades(deps, window_seconds=300)
    assert "Temporarily unavailable" in out


@pytest.mark.asyncio
async def test_get_order_book_renders_usd_notional():
    """规模量 = amount × price → USD notional（best 行不再标 base 币）。"""
    bids = [OrderBookLevel(100.0 - i * 0.1, 50.0) for i in range(15)]
    asks = [OrderBookLevel(101.0 + i * 0.1, 50.0) for i in range(15)]
    deps = _deps_with_order_book(OrderBook("BTC/USDT:USDT", bids, asks, 0))
    out = await get_order_book(deps, depth=15)
    assert "$5.0K" in out          # best bid notional 50 × 100 = 5000
    assert "BTC  |" not in out     # 旧 "× N BTC  |  Best ask" 格式消失（notional 取代 base 币标签）


@pytest.mark.asyncio
async def test_get_order_book_distance_in_pts_and_bp():
    """spread / depth span 用 pts + bp，不用 %。"""
    bids = [OrderBookLevel(100.0 - i * 0.1, 10.0) for i in range(15)]
    asks = [OrderBookLevel(101.0 + i * 0.1, 10.0) for i in range(15)]
    deps = _deps_with_order_book(OrderBook("BTC/USDT:USDT", bids, asks, 0))
    out = await get_order_book(deps, depth=15)
    assert "pts" in out
    assert "bp" in out
    assert "% deep" not in out
    assert "0.00%" not in out


@pytest.mark.asyncio
async def test_get_order_book_bid_share_factonly_near_50():
    """接近 50% 时显实际值 + 比值，无 'balanced' 评价词。"""
    bids = [OrderBookLevel(100.0 - i * 0.1, 10.0) for i in range(15)]   # total 150
    asks = [OrderBookLevel(101.0 + i * 0.1, 10.5) for i in range(15)]   # total 157.5
    deps = _deps_with_order_book(OrderBook("BTC/USDT:USDT", bids, asks, 0))
    out = await get_order_book(deps, depth=15)
    assert "balanced" not in out
    assert "~50%" not in out
    assert "bid : ask =" in out
    assert "by size" in out  # P2: share 口径标注（区别于 depth 段 $ notional 失衡）


@pytest.mark.asyncio
async def test_get_order_book_concentrated_excludes_best_no_distance():
    """Concentrated 段排除 best 档（已在 Best 行）+ 无距离列。"""
    bids = [OrderBookLevel(100.0 - i, 1.0) for i in range(15)]
    bids[0] = OrderBookLevel(100.0, 100.0)   # best 是最大单
    bids[5] = OrderBookLevel(95.0, 10.0)     # 纵深 wall（>3× median 1.0）
    asks = [OrderBookLevel(101.0 + i, 1.0) for i in range(15)]
    deps = _deps_with_order_book(OrderBook("BTC/USDT:USDT", bids, asks, 0))
    out = await get_order_book(deps, depth=15)
    conc = out.split("Concentrated Levels")[1]
    assert "95.00" in conc          # 纵深 wall 在
    assert "100.00" not in conc     # best 被排除（best 行另算）
    assert "below mid" not in conc  # 无距离列
