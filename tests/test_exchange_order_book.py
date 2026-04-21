"""Tests for BaseExchange.fetch_order_book / fetch_trades / get_contract_size across OKX and Sim implementations."""
from __future__ import annotations
import pytest
import random
from unittest.mock import MagicMock

from src.integrations.exchange.base import OrderBook, OrderBookLevel, Trade, Ticker
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


def _prime_sim_ticker(ex: SimulatedExchange, last: float = 50000.0) -> None:
    """Directly seed SimulatedExchange._latest_ticker without routing through _process_tick.

    Exists because SimulatedExchange has no public set_ticker method —
    real price updates come through the internal tick loop which we don't want
    to exercise in unit tests.
    """
    ex._latest_ticker = Ticker(
        symbol="BTC/USDT:USDT", last=last, bid=last - 0.5, ask=last + 0.5,
        high=last + 100, low=last - 100, base_volume=1000.0, timestamp=0,
    )


@pytest.mark.asyncio
async def test_sim_fetch_order_book_structure():
    """Sim fetch_order_book returns correctly-structured OrderBook synthesized from ticker."""
    ex = _make_sim()
    _prime_sim_ticker(ex, last=50000.0)
    ob = await ex.fetch_order_book("BTC/USDT:USDT", depth=20)
    assert isinstance(ob, OrderBook)
    assert ob.symbol == "BTC/USDT:USDT"
    assert len(ob.bids) == 20
    assert len(ob.asks) == 20
    # Bids descending (best first)
    assert all(ob.bids[i].price >= ob.bids[i+1].price for i in range(len(ob.bids) - 1))
    # Asks ascending
    assert all(ob.asks[i].price <= ob.asks[i+1].price for i in range(len(ob.asks) - 1))
    # Best bid below best ask
    assert ob.bids[0].price < ob.asks[0].price


@pytest.mark.asyncio
async def test_sim_fetch_order_book_custom_depth():
    """Depth parameter respected."""
    ex = _make_sim()
    _prime_sim_ticker(ex, last=50000.0)
    ob = await ex.fetch_order_book("BTC/USDT:USDT", depth=5)
    assert len(ob.bids) == 5
    assert len(ob.asks) == 5


@pytest.mark.asyncio
async def test_sim_fetch_trades_structure():
    """Sim fetch_trades returns Trade list with valid fields."""
    ex = _make_sim()
    _prime_sim_ticker(ex, last=50000.0)
    trades = await ex.fetch_trades("BTC/USDT:USDT", limit=500)
    assert isinstance(trades, list)
    assert 20 <= len(trades) <= 50
    for t in trades:
        assert isinstance(t, Trade)
        assert t.side in ("buy", "sell")
        assert t.price > 0
        assert 0.001 <= t.amount <= 0.01
        assert t.timestamp > 0


@pytest.mark.asyncio
async def test_sim_fetch_trades_direction_bias_rising():
    """Over N rounds of rising ticker, cumulative buy volume > sell volume (bias 55%+).

    Manually advances _prev_ticker each round because _prime_sim_ticker bypasses
    _process_tick (which is where prev-save happens in production). Without this,
    _prev_ticker stays None forever → price_change_pct = 0 → buy_prob = 0.5 → flat.
    """
    random.seed(42)
    ex = _make_sim()
    _prime_sim_ticker(ex, last=50000.0)  # seed initial _latest_ticker
    total_buy = 0.0
    total_sell = 0.0
    price = 50000.0
    for _ in range(100):
        ex._prev_ticker = ex._latest_ticker  # manually advance (mimics _process_tick behavior)
        price *= 1.005  # +0.5% each round
        _prime_sim_ticker(ex, last=price)
        trades = await ex.fetch_trades("BTC/USDT:USDT", limit=500)
        total_buy += sum(t.amount for t in trades if t.side == "buy")
        total_sell += sum(t.amount for t in trades if t.side == "sell")
    total = total_buy + total_sell
    buy_share = total_buy / total
    assert buy_share >= 0.55, f"Expected buy bias >= 55% under rising ticker, got {buy_share:.2%}"


@pytest.mark.asyncio
async def test_sim_fetch_trades_direction_bias_falling():
    """Over N rounds of falling ticker, cumulative sell volume > buy volume (bias 55%+)."""
    random.seed(42)
    ex = _make_sim()
    _prime_sim_ticker(ex, last=50000.0)  # seed initial
    total_buy = 0.0
    total_sell = 0.0
    price = 50000.0
    for _ in range(100):
        ex._prev_ticker = ex._latest_ticker  # manually advance
        price *= 0.995  # -0.5% each round
        _prime_sim_ticker(ex, last=price)
        trades = await ex.fetch_trades("BTC/USDT:USDT", limit=500)
        total_buy += sum(t.amount for t in trades if t.side == "buy")
        total_sell += sum(t.amount for t in trades if t.side == "sell")
    total = total_buy + total_sell
    sell_share = total_sell / total
    assert sell_share >= 0.55, f"Expected sell bias >= 55% under falling ticker, got {sell_share:.2%}"


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
