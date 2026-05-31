import pytest
from unittest.mock import MagicMock, AsyncMock
from src.integrations.exchange.simulated import SimulatedExchange


def _bare(symbol="BTC/USDT:USDT"):
    cfg = MagicMock(); cfg.fee_rate = 0.0005; cfg.precision = {}
    return SimulatedExchange(config=cfg, db_engine=None, session_id="s", symbol=symbol)


@pytest.mark.asyncio
async def test_get_contract_size_defaults_1_before_start():
    ex = _bare()
    assert await ex.get_contract_size("BTC/USDT:USDT") == 1.0   # __init__ 默认


@pytest.mark.asyncio
async def test_get_contract_size_returns_cached_real_cs():
    ex = _bare()
    ex._contract_size = 0.01           # start() 缓存后的状态
    assert await ex.get_contract_size("BTC/USDT:USDT") == 0.01


@pytest.mark.asyncio
async def test_get_contract_size_validates_symbol():
    ex = _bare()
    with pytest.raises(ValueError, match="Symbol mismatch"):
        await ex.get_contract_size("ETH/USDT:USDT")


@pytest.mark.asyncio
async def test_load_markets_failfast_after_3(monkeypatch):
    # load 连续失败 → fail-fast RuntimeError（不静默回退 1.0）
    import src.integrations.exchange.simulated as simmod
    ex = _bare()
    ex._ccxt = MagicMock()
    ex._ccxt.load_markets = AsyncMock(side_effect=Exception("net down"))
    monkeypatch.setattr(simmod.asyncio, "sleep", AsyncMock())   # 跳过退避等待
    with pytest.raises(RuntimeError, match="Failed to load_markets after 3"):
        await ex._load_markets_with_retry()
    assert ex._ccxt.load_markets.await_count == 3


@pytest.mark.asyncio
async def test_init_contract_size_caches_real_cs():
    # 成功 → 从 market() 缓存真实 cs（db_engine=None → 不 persist）
    ex = _bare()
    ex._ccxt = MagicMock()
    ex._ccxt.load_markets = AsyncMock()
    ex._ccxt.market = MagicMock(return_value={"contractSize": 0.01})
    await ex._init_contract_size()
    assert ex._contract_size == 0.01
    ex._ccxt.market.assert_called_once_with("BTC/USDT:USDT")
