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
