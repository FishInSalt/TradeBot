import pytest
from unittest.mock import MagicMock, AsyncMock
from sqlalchemy import select
from src.integrations.exchange.simulated import SimulatedExchange
from src.storage.database import init_db, get_session
from src.storage.models import Session as SessionModel


def _bare(symbol="BTC/USDT:USDT"):
    cfg = MagicMock(); cfg.fee_rate = 0.0005
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
async def test_init_market_meta_caches_real_cs():
    # 成功 → 从 market() 缓存真实 cs（db_engine=None → 跳过 DB-cache、不 persist、走 ccxt path）
    ex = _bare()
    ex._ccxt = MagicMock()
    ex._ccxt.load_markets = AsyncMock()
    ex._ccxt.market = MagicMock(return_value={"contractSize": 0.01})
    await ex.init_market_meta()
    assert ex._contract_size == 0.01
    ex._ccxt.market.assert_called_once_with("BTC/USDT:USDT")


@pytest.mark.asyncio
async def test_persist_contract_size_writes_to_db(tmp_path):
    # Verify _persist_contract_size() actually writes the correct value to the
    # sessions row — ensures the WHERE clause and column name are correct.
    engine = await init_db(f"sqlite+aiosqlite:///{tmp_path}/cs_persist.db")

    # Insert a session row with contract_size initially NULL → DB-cache misses,
    # init_market_meta falls through to the ccxt path (which then persists).
    async with get_session(engine) as s:
        s.add(SessionModel(
            id="sess-cs", name="test", symbol="BTC/USDT:USDT",
            initial_balance=1000.0,
        ))
        await s.commit()

    # Build exchange pointing at the real engine.
    cfg = MagicMock()
    cfg.fee_rate = 0.0005
    ex = SimulatedExchange(config=cfg, db_engine=engine, session_id="sess-cs",
                           symbol="BTC/USDT:USDT")

    # Mock _ccxt so init_market_meta() succeeds without real network calls.
    ex._ccxt = MagicMock()
    ex._ccxt.load_markets = AsyncMock()
    ex._ccxt.market = MagicMock(return_value={"contractSize": 0.01})

    # db_engine set + sessions.contract_size NULL → ccxt path runs and persists.
    await ex.init_market_meta()

    # Re-query the DB and confirm the value was persisted.
    async with get_session(engine) as s:
        stored = (await s.execute(
            select(SessionModel.contract_size).where(SessionModel.id == "sess-cs")
        )).scalar_one()
    assert stored == 0.01
