"""init_market_meta: DB-cache 优先 / fetch 路径 fail-loud / 幂等（spec §3.6 硬约束 1）。"""
from unittest.mock import AsyncMock, MagicMock

import pytest
from sqlalchemy.ext.asyncio import create_async_engine

from src.config import ExchangeConfig
from src.integrations.exchange.simulated import SimulatedExchange
from src.storage.models import Base, Session as SessionModel
from src.storage.database import get_session


def _make_exchange(db_engine=None) -> SimulatedExchange:
    config = ExchangeConfig(name="simulated", fee_rate=0.001)  # 注意：ExchangeConfig 无 precision 字段（config.py:14），勿传死参
    return SimulatedExchange(
        config=config, db_engine=db_engine,
        session_id="imm-test", symbol="BTC/USDT:USDT",
    )


class _FakeCcxt:
    def __init__(self, contract_size):
        self._cs = contract_size
        self.closed = False

    async def load_markets(self):
        return {}

    def market(self, symbol):
        return {"contractSize": self._cs}

    async def close(self):
        self.closed = True


async def test_idempotent_second_call_returns_cached_without_network():
    ex = _make_exchange()
    ex._ccxt = _FakeCcxt(0.01)
    first = await ex.init_market_meta()
    ex._ccxt = object()  # 毒丸：二次调用若再走网络路径 → load_markets AttributeError（不会真触网）
    second = await ex.init_market_meta()
    assert first == second == 0.01


async def test_fetch_path_raises_on_missing_contract_size():
    """contractSize 缺失必须 raise，不允许 or 1.0 静默兜底（spec §3.6 硬约束 1）。"""
    ex = _make_exchange()
    ex._ccxt = _FakeCcxt(None)
    with pytest.raises(RuntimeError, match="contractSize"):
        await ex.init_market_meta()
    assert ex._market_meta_ready is False


async def test_db_cache_hit_skips_network_entirely():
    """sessions.contract_size 命中 → 不创建 ccxt client、不走网络。"""
    engine = create_async_engine("sqlite+aiosqlite://")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    async with get_session(engine) as session:
        session.add(SessionModel(
            id="imm-test", name="imm", symbol="BTC/USDT:USDT",
            initial_balance=10_000.0, status="active",
            exchange_type="simulated", timeframe="15m",
            scheduler_interval_min=15, approval_enabled=False,
            token_budget=1_000_000, contract_size=0.01,
        ))
        await session.commit()
    ex = _make_exchange(db_engine=engine)
    cs = await ex.init_market_meta()
    assert cs == 0.01
    assert getattr(ex, "_ccxt", None) is None  # 没碰网络客户端（_ccxt 属性 pre-start 不存在，勿直接访问）
    await engine.dispose()


async def test_init_market_meta_does_not_start_matching_loops():
    """spec §3.6 硬约束 3 前提：init_market_meta 不拉起撮合循环（回调窗口不存在）。"""
    ex = _make_exchange()
    ex._ccxt = _FakeCcxt(0.01)
    await ex.init_market_meta()
    assert ex._running is False
    assert getattr(ex, "_matching_task", None) is None


async def test_close_tolerates_pre_start_state():
    """__init__ 后立即 close 不抛（spec §3.6 硬约束 2 的清理路径前提）。"""
    ex = _make_exchange()
    await ex.close()  # _ccxt is None → 不应 await None.close()
