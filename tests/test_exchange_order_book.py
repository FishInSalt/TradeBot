"""Tests for BaseExchange.fetch_order_book / fetch_trades / get_contract_size across OKX and Sim implementations."""
from __future__ import annotations
import pytest
from unittest.mock import AsyncMock, MagicMock

import ccxt as ccxt_sync
import ccxt.async_support as ccxt

from src.integrations.exchange.base import OrderBook
from src.integrations.exchange.simulated import SimulatedExchange


def _make_sim(symbol: str = "BTC/USDT:USDT") -> SimulatedExchange:
    """Construct a SimulatedExchange with no DB / mock config for unit tests.

    Mirrors the helper in tests/test_simulated_exchange.py: the real constructor
    takes (config, db_engine, session_id, symbol), not (symbol=, initial_balance=).
    """
    config = MagicMock()
    config.fee_rate = 0.0005
    config.precision = {"BTC/USDT:USDT": 3, "ETH/USDT:USDT": 2}
    return SimulatedExchange(
        config=config, db_engine=None, session_id="test-order-book", symbol=symbol,
    )


def _sim_with_ccxt(symbol: str = "BTC/USDT:USDT") -> SimulatedExchange:
    """_make_sim + 挂一个 MagicMock _ccxt（换源后两方法依赖 self._ccxt）。"""
    ex = _make_sim(symbol)
    ex._ccxt = MagicMock()
    return ex


@pytest.mark.asyncio
async def test_sim_fetch_order_book_maps_ccxt():
    """映射 CCXT-parsed bids/asks → OrderBookLevel，保留 symbol + timestamp。"""
    ex = _sim_with_ccxt()
    ex._ccxt.fetch_order_book = AsyncMock(return_value={
        "bids": [[100.0, 1.5, 1], [99.0, 2.0, 1]],
        "asks": [[101.0, 1.0, 1], [102.0, 3.0, 1]],
        "timestamp": 1700000000000,
    })
    ob = await ex.fetch_order_book("BTC/USDT:USDT", depth=20)
    assert isinstance(ob, OrderBook)
    assert ob.symbol == "BTC/USDT:USDT"
    assert ob.timestamp == 1700000000000
    assert [(l.price, l.amount) for l in ob.bids] == [(100.0, 1.5), (99.0, 2.0)]
    assert [(l.price, l.amount) for l in ob.asks] == [(101.0, 1.0), (102.0, 3.0)]


@pytest.mark.asyncio
async def test_sim_fetch_order_book_explicit_sort():
    """乱序输入 → 输出 bids 价降序 / asks 价升序（不依赖 CCXT 内部 sort）。"""
    ex = _sim_with_ccxt()
    ex._ccxt.fetch_order_book = AsyncMock(return_value={
        "bids": [[98.0, 1.0], [100.0, 1.0], [99.0, 1.0]],
        "asks": [[103.0, 1.0], [101.0, 1.0], [102.0, 1.0]],
        "timestamp": 0,
    })
    ob = await ex.fetch_order_book("BTC/USDT:USDT", depth=20)
    assert [l.price for l in ob.bids] == [100.0, 99.0, 98.0]
    assert [l.price for l in ob.asks] == [101.0, 102.0, 103.0]


@pytest.mark.asyncio
async def test_sim_fetch_order_book_skips_none_fields():
    """price/amount 为 None 的档位被跳过，不崩。"""
    ex = _sim_with_ccxt()
    ex._ccxt.fetch_order_book = AsyncMock(return_value={
        "bids": [[100.0, 1.0], [None, 1.0], [99.0, None]],
        "asks": [[101.0, 1.0]],
        "timestamp": 0,
    })
    ob = await ex.fetch_order_book("BTC/USDT:USDT", depth=20)
    assert [l.price for l in ob.bids] == [100.0]
    assert [l.price for l in ob.asks] == [101.0]


@pytest.mark.asyncio
async def test_sim_fetch_order_book_handles_2_and_3_element():
    """2 元素 [p,a] 与 3 元素 [p,a,count] 都能映射（*_ 解包）。"""
    ex = _sim_with_ccxt()
    ex._ccxt.fetch_order_book = AsyncMock(return_value={
        "bids": [[100.0, 1.0], [99.0, 2.0, 5]],
        "asks": [[101.0, 1.0, 5], [102.0, 2.0]],
        "timestamp": 0,
    })
    ob = await ex.fetch_order_book("BTC/USDT:USDT", depth=20)
    assert len(ob.bids) == 2 and len(ob.asks) == 2


@pytest.mark.asyncio
async def test_sim_fetch_order_book_depth_forwarded():
    """depth 透传给 _ccxt.fetch_order_book(limit=depth)。"""
    ex = _sim_with_ccxt()
    ex._ccxt.fetch_order_book = AsyncMock(return_value={"bids": [[100.0, 1.0]], "asks": [[101.0, 1.0]], "timestamp": 0})
    await ex.fetch_order_book("BTC/USDT:USDT", depth=5)
    ex._ccxt.fetch_order_book.assert_awaited_once_with("BTC/USDT:USDT", limit=5)


@pytest.mark.asyncio
async def test_sim_fetch_order_book_not_started():
    """未 start（无 _ccxt）→ RuntimeError。"""
    ex = _make_sim()  # 不挂 _ccxt
    with pytest.raises(RuntimeError, match="not started"):
        await ex.fetch_order_book("BTC/USDT:USDT", depth=20)


@pytest.mark.asyncio
async def test_sim_fetch_order_book_ratelimit():
    """_ccxt 抛 RateLimitExceeded → 转 RateLimitHit。"""
    from src.utils.cache import RateLimitHit
    ex = _sim_with_ccxt()
    ex._ccxt.fetch_order_book = AsyncMock(side_effect=ccxt.RateLimitExceeded("429"))
    with pytest.raises(RateLimitHit):
        await ex.fetch_order_book("BTC/USDT:USDT", depth=20)


@pytest.mark.asyncio
async def test_sim_fetch_trades_maps_ccxt():
    """映射 CCXT unified trade dict → Trade。"""
    ex = _sim_with_ccxt()
    ex._ccxt.fetch_trades = AsyncMock(return_value=[
        {"timestamp": 1700000000000, "side": "buy", "price": 70000.0, "amount": 0.01, "id": "t1"},
        {"timestamp": 1700000001000, "side": "sell", "price": 70010.0, "amount": 0.02, "id": "t2"},
    ])
    trades = await ex.fetch_trades("BTC/USDT:USDT", limit=500)
    assert [(t.side, t.price, t.amount, t.trade_id) for t in trades] == [
        ("buy", 70000.0, 0.01, "t1"), ("sell", 70010.0, 0.02, "t2"),
    ]


@pytest.mark.asyncio
async def test_sim_fetch_trades_sorted_ascending():
    """乱序输入 → 按 timestamp 升序。"""
    ex = _sim_with_ccxt()
    ex._ccxt.fetch_trades = AsyncMock(return_value=[
        {"timestamp": 3000, "side": "buy", "price": 1.0, "amount": 0.01, "id": "c"},
        {"timestamp": 1000, "side": "buy", "price": 1.0, "amount": 0.01, "id": "a"},
        {"timestamp": 2000, "side": "buy", "price": 1.0, "amount": 0.01, "id": "b"},
    ])
    trades = await ex.fetch_trades("BTC/USDT:USDT", limit=500)
    assert [t.timestamp for t in trades] == [1000, 2000, 3000]


@pytest.mark.asyncio
async def test_sim_fetch_trades_skips_none_fields():
    """ts/side/price/amount 任一为 None 的成交被跳过。"""
    ex = _sim_with_ccxt()
    ex._ccxt.fetch_trades = AsyncMock(return_value=[
        {"timestamp": 1000, "side": "buy", "price": 1.0, "amount": 0.01, "id": "ok"},
        {"timestamp": None, "side": "buy", "price": 1.0, "amount": 0.01, "id": "bad_ts"},
        {"timestamp": 2000, "side": None, "price": 1.0, "amount": 0.01, "id": "bad_side"},
    ])
    trades = await ex.fetch_trades("BTC/USDT:USDT", limit=500)
    assert [t.trade_id for t in trades] == ["ok"]


@pytest.mark.asyncio
async def test_sim_fetch_trades_none_id_ok():
    """id 为 None → trade_id=None，不跳过。"""
    ex = _sim_with_ccxt()
    ex._ccxt.fetch_trades = AsyncMock(return_value=[
        {"timestamp": 1000, "side": "buy", "price": 1.0, "amount": 0.01, "id": None},
    ])
    trades = await ex.fetch_trades("BTC/USDT:USDT", limit=500)
    assert len(trades) == 1 and trades[0].trade_id is None


@pytest.mark.asyncio
async def test_sim_fetch_trades_limit_forwarded_and_not_started():
    """limit 透传；无 _ccxt → RuntimeError。"""
    ex = _sim_with_ccxt()
    ex._ccxt.fetch_trades = AsyncMock(return_value=[])
    await ex.fetch_trades("BTC/USDT:USDT", limit=500)
    ex._ccxt.fetch_trades.assert_awaited_once_with("BTC/USDT:USDT", limit=500)
    ex2 = _make_sim()  # 无 _ccxt
    with pytest.raises(RuntimeError, match="not started"):
        await ex2.fetch_trades("BTC/USDT:USDT", limit=500)


@pytest.mark.asyncio
async def test_sim_fetch_trades_ratelimit():
    """_ccxt 抛 RateLimitExceeded → 转 RateLimitHit。"""
    from src.utils.cache import RateLimitHit
    ex = _sim_with_ccxt()
    ex._ccxt.fetch_trades = AsyncMock(side_effect=ccxt.RateLimitExceeded("429"))
    with pytest.raises(RateLimitHit):
        await ex.fetch_trades("BTC/USDT:USDT", limit=500)


@pytest.mark.asyncio
async def test_sim_get_contract_size_always_one():
    """Sim always returns 1.0 (no contract multiplier model)."""
    ex = _make_sim()
    assert await ex.get_contract_size("BTC/USDT:USDT") == 1.0
    assert await ex.get_contract_size("ETH/USDT:USDT") == 1.0


@pytest.mark.asyncio
async def test_okx_fetch_order_book_parses_ccxt_response(mocker):
    """OKX fetch_order_book parses CCXT raw dict into OrderBook dataclass."""
    from src.integrations.exchange.okx import OKXExchange
    ex = OKXExchange(api_key="k", secret="s", password="p", symbol="BTC/USDT:USDT")
    mock_fetch = mocker.patch.object(
        ex._client, "fetch_order_book",
        return_value={
            "bids": [[50000.0, 1.0], [49999.5, 0.5]],
            "asks": [[50001.0, 0.8], [50001.5, 1.2]],
            "timestamp": 1700000000000,
        }
    )
    ob = await ex.fetch_order_book("BTC/USDT:USDT", depth=2)
    assert ob.symbol == "BTC/USDT:USDT"
    assert ob.timestamp == 1700000000000
    assert len(ob.bids) == 2
    assert ob.bids[0].price == 50000.0
    assert ob.bids[0].amount == 1.0
    assert ob.asks[0].price == 50001.0
    mock_fetch.assert_called_once_with("BTC/USDT:USDT", limit=2)


@pytest.mark.asyncio
async def test_okx_fetch_order_book_timestamp_none_fallback(mocker):
    """If CCXT returns timestamp=None, OKX layer fills with current time."""
    from src.integrations.exchange.okx import OKXExchange
    ex = OKXExchange(api_key="k", secret="s", password="p", symbol="BTC/USDT:USDT")
    mocker.patch.object(ex._client, "fetch_order_book", return_value={
        "bids": [[50000.0, 1.0]], "asks": [[50001.0, 1.0]], "timestamp": None,
    })
    ob = await ex.fetch_order_book("BTC/USDT:USDT", depth=1)
    import time
    now_ms = int(time.time() * 1000)
    assert ob.timestamp is not None
    assert abs(ob.timestamp - now_ms) < 10_000  # within 10s


@pytest.mark.asyncio
async def test_okx_fetch_order_book_retry_params(mocker):
    """@_retry(max_retries=2, base_delay=0.5) — exactly 2 total attempts, then raises."""
    import ccxt
    from src.integrations.exchange.okx import OKXExchange
    ex = OKXExchange(api_key="k", secret="s", password="p", symbol="BTC/USDT:USDT")
    mock_fetch = mocker.patch.object(
        ex._client, "fetch_order_book",
        side_effect=ccxt.NetworkError("temporary network failure"),
    )
    mocker.patch("asyncio.sleep", return_value=None)
    with pytest.raises(ccxt.NetworkError):
        await ex.fetch_order_book("BTC/USDT:USDT", depth=20)
    assert mock_fetch.call_count == 2, (
        f"Expected 2 total attempts for max_retries=2 "
        f"(per okx.py:62 `for attempt in range(max_retries)` → max_retries IS total attempt count, not +1), "
        f"got {mock_fetch.call_count}. "
        "If count=3, @_retry is still using default max_retries=3 — verify fetch_order_book decoration."
    )


@pytest.mark.asyncio
async def test_okx_fetch_trades_parses_and_sorts(mocker):
    """OKX fetch_trades parses CCXT response and explicitly sorts ascending by timestamp."""
    from src.integrations.exchange.okx import OKXExchange
    ex = OKXExchange(api_key="k", secret="s", password="p", symbol="BTC/USDT:USDT")
    # Deliberately unordered to test explicit sort
    mocker.patch.object(ex._client, "fetch_trades", return_value=[
        {"timestamp": 1700000030000, "side": "buy", "price": 50001.0, "amount": 0.01, "id": "t3"},
        {"timestamp": 1700000010000, "side": "sell", "price": 50000.0, "amount": 0.02, "id": "t1"},
        {"timestamp": 1700000020000, "side": "buy", "price": 50000.5, "amount": 0.015, "id": None},
    ])
    trades = await ex.fetch_trades("BTC/USDT:USDT", limit=500)
    assert len(trades) == 3
    # Sorted ascending by timestamp
    assert trades[0].timestamp == 1700000010000
    assert trades[1].timestamp == 1700000020000
    assert trades[2].timestamp == 1700000030000
    # trade_id None handling
    assert trades[0].trade_id == "t1"
    assert trades[1].trade_id is None


@pytest.mark.asyncio
async def test_okx_get_contract_size_loaded(mocker):
    """Markets preloaded: returns contractSize directly from memory."""
    from src.integrations.exchange.okx import OKXExchange
    ex = OKXExchange(api_key="k", secret="s", password="p", symbol="BTC/USDT:USDT")
    ex._client.markets = {"BTC/USDT:USDT": {"contractSize": 0.01}}
    load_mock = mocker.patch.object(ex._client, "load_markets")
    size = await ex.get_contract_size("BTC/USDT:USDT")
    assert size == 0.01
    load_mock.assert_not_called()  # no lazy load needed


@pytest.mark.asyncio
async def test_okx_get_contract_size_lazy_load(mocker):
    """Markets not loaded: triggers lazy load_markets."""
    from unittest.mock import AsyncMock
    from src.integrations.exchange.okx import OKXExchange
    ex = OKXExchange(api_key="k", secret="s", password="p", symbol="BTC/USDT:USDT")
    ex._client.markets = {}  # empty → falsy → lazy load triggered
    def _side_effect(*_, **__):
        ex._client.markets = {"BTC/USDT:USDT": {"contractSize": 0.01}}
    load_mock = mocker.patch.object(
        ex._client, "load_markets", new_callable=AsyncMock, side_effect=_side_effect,
    )
    size = await ex.get_contract_size("BTC/USDT:USDT")
    assert size == 0.01
    load_mock.assert_called_once()


@pytest.mark.asyncio
async def test_okx_get_contract_size_unknown_market_fallback(mocker):
    """Market not in markets dict → returns 1.0 fallback + warning."""
    from src.integrations.exchange.okx import OKXExchange
    ex = OKXExchange(api_key="k", secret="s", password="p", symbol="BTC/USDT:USDT")
    ex._client.markets = {"ETH/USDT:USDT": {"contractSize": 0.01}}
    size = await ex.get_contract_size("BTC/USDT:USDT")
    assert size == 1.0


@pytest.mark.asyncio
async def test_okx_fetch_order_book_handles_three_element_bidask_entries(mocker):
    """CCXT parse_bid_ask appends `countOrId` (e.g. OKX numOrders) to each entry,
    so real responses are `[price, amount, num_orders]` not `[price, amount]`.

    Regression for production crash: previous `for p, a in ...` unpack raised
    ValueError on every real OKX call. Fixed via `for p, a, *_ in ...`.
    """
    from src.integrations.exchange.okx import OKXExchange
    ex = OKXExchange(api_key="k", secret="s", password="p", symbol="BTC/USDT:USDT")
    mocker.patch.object(ex._client, "fetch_order_book", return_value={
        # Real OKX-shaped CCXT entries: 3 elements per row
        "bids": [[50000.0, 1.0, 5], [49999.5, 0.5, 2]],
        "asks": [[50001.0, 0.8, 3], [50001.5, 1.2, 7]],
        "timestamp": 1700000000000,
    })
    ob = await ex.fetch_order_book("BTC/USDT:USDT", depth=2)
    assert len(ob.bids) == 2
    assert ob.bids[0].price == 50000.0
    assert ob.bids[0].amount == 1.0
    assert len(ob.asks) == 2
    assert ob.asks[0].price == 50001.0
    assert ob.asks[0].amount == 0.8


# --- ① mock 保真：用真实 CCXT parse 文档 raw 形态，验证 sim impl 假设的 parsed 形态成立 ---
# per memory project_iter2_mock_fidelity_lesson —— 不用手写 parsed mock 自证自，
# 而是喂 OKX 文档 raw 响应给真实 parse，确认 (3 元素 / float / 排序 / 字段名) 与 impl 一致。

def test_ccxt_okx_parse_order_book_contract():
    """真实 ccxt.okx().parse_order_book(文档 raw) → sim fetch_order_book 假设的 parsed 形态。"""
    client = ccxt_sync.okx()
    # OKX raw 盘口形态 ["px","sz","deprecated","numOrders"]（4 元素），乱序喂入
    raw = {
        "asks": [["100.60", "1.0", "0", "2"], ["100.50", "2.0", "0", "1"]],
        "bids": [["100.30", "1.0", "0", "1"], ["100.40", "3.0", "0", "1"]],
        "ts": "1621438475342",
    }
    parsed = client.parse_order_book(raw, "BTC/USDT:USDT", 1621438475342)
    # impl 假设 1：每个 entry 是 [price, amount, count] 3 元素，price/amount 为 float
    for entry in parsed["bids"] + parsed["asks"]:
        assert len(entry) == 3
        assert isinstance(entry[0], float) and isinstance(entry[1], float)
    # impl 假设 2：bids 价降序 / asks 价升序（sim 显式 sort 即便如此也自保证，但确认 CCXT 同向）
    assert [e[0] for e in parsed["bids"]] == [100.40, 100.30]
    assert [e[0] for e in parsed["asks"]] == [100.50, 100.60]
    # impl 假设 3：data["timestamp"] 键存在
    assert parsed["timestamp"] == 1621438475342


def test_ccxt_okx_parse_trade_contract():
    """真实 ccxt.okx().parse_trade(文档 raw) → sim fetch_trades 消费的 unified 字段成立。"""
    client = ccxt_sync.okx()
    # OKX public fetchTrades raw 形态
    raw = {"instId": "BTC-USDT-SWAP", "side": "buy", "sz": "0.5",
           "px": "70000.1", "tradeId": "123", "ts": "1621446178316"}
    parsed = client.parse_trade(raw)
    # impl 消费 r["timestamp"|"side"|"price"|"amount"|"id"]
    assert parsed["timestamp"] == 1621446178316
    assert parsed["side"] == "buy"
    assert float(parsed["price"]) == pytest.approx(70000.1)
    assert float(parsed["amount"]) == pytest.approx(0.5)
    assert parsed["id"] == "123"
