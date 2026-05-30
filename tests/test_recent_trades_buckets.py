"""Tests for the get_recent_trades count-bucket refactor + fetch_trades 张->base
unit normalization. Spec docs/superpowers/specs/2026-05-30-order-flow-tools-redesign-design.md
§3.4 (count buckets), §4.2 (Option B adapter), §5 ④⑤.
"""
import pytest
from unittest.mock import AsyncMock, MagicMock


@pytest.mark.asyncio
async def test_sim_fetch_trades_normalizes_contracts_to_base():
    """§4.2/④: raw ccxt amount is OKX contracts (张); multiply by real market
    contractSize so Trade.amount is base-currency. Mock-fidelity (⑤): include
    info.sz + contractSize != 1 (BTC swap 0.01)."""
    from src.integrations.exchange.simulated import SimulatedExchange
    ex = SimulatedExchange.__new__(SimulatedExchange)
    ex._ccxt = MagicMock()
    ex._ccxt.market.return_value = {"id": "BTC-USDT-SWAP", "contractSize": 0.01}
    ex._ccxt.fetch_trades = AsyncMock(return_value=[
        {"timestamp": 1, "side": "buy", "price": 70000.0, "amount": 5.0,
         "id": "a", "info": {"sz": "5"}},   # 5 张 * 0.01 = 0.05 base
        {"timestamp": 2, "side": "sell", "price": 70010.0, "amount": 2.0,
         "id": "b", "info": {"sz": "2"}},
    ])
    ex._validate_symbol = lambda s: None
    trades = await ex.fetch_trades("BTC/USDT:USDT", limit=500)
    assert trades[0].amount == pytest.approx(0.05)
    assert trades[1].amount == pytest.approx(0.02)


@pytest.mark.asyncio
async def test_sim_fetch_trades_contractsize_missing_defaults_to_one():
    from src.integrations.exchange.simulated import SimulatedExchange
    ex = SimulatedExchange.__new__(SimulatedExchange)
    ex._ccxt = MagicMock()
    ex._ccxt.market.return_value = {"id": "X"}  # no contractSize key
    ex._ccxt.fetch_trades = AsyncMock(return_value=[
        {"timestamp": 1, "side": "buy", "price": 100.0, "amount": 3.0, "id": "a"}])
    ex._validate_symbol = lambda s: None
    trades = await ex.fetch_trades("X/USDT:USDT", limit=500)
    assert trades[0].amount == pytest.approx(3.0)  # cs defaults to 1.0


@pytest.mark.asyncio
async def test_okx_fetch_trades_normalizes_contracts_to_base():
    from src.integrations.exchange.okx import OKXExchange
    ex = OKXExchange.__new__(OKXExchange)
    ex._client = MagicMock()
    ex._client.markets = {"ETH/USDT:USDT": {"contractSize": 0.1}}
    ex._client.market.return_value = {"contractSize": 0.1}
    ex._client.fetch_trades = AsyncMock(return_value=[
        {"timestamp": 1, "side": "buy", "price": 3000.0, "amount": 4.0, "id": "a",
         "info": {"sz": "4"}}])  # 4 张 * 0.1 = 0.4 base
    trades = await ex.fetch_trades("ETH/USDT:USDT", limit=500)
    assert trades[0].amount == pytest.approx(0.4)
