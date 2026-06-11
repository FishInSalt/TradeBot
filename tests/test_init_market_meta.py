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


def _make_okx() -> "OKXExchange":
    from unittest.mock import patch
    from src.integrations.exchange.okx import OKXExchange
    with patch("src.integrations.exchange.okx.ccxt"):  # 既有 idiom（test_okx_algo_normalization.py:8），构造期不碰真实 ccxt
        return OKXExchange(api_key="k", secret="s", password="p",
                           symbol="BTC/USDT:USDT", sandbox=True)


async def test_okx_init_market_meta_returns_contract_size():
    ex = _make_okx()
    ex._preload_markets = AsyncMock()
    ex._client = MagicMock()
    ex._client.markets = {"BTC/USDT:USDT": {"contractSize": 0.01}}
    assert await ex.init_market_meta() == 0.01


async def test_okx_init_market_meta_raises_on_missing_market():
    """覆盖 okx.py 惰性 get_contract_size 的 1.0 兜底盲区（spec §3.6 硬约束 1 细则）。"""
    ex = _make_okx()
    ex._preload_markets = AsyncMock()
    ex._client = MagicMock()
    ex._client.markets = {}
    with pytest.raises(RuntimeError, match="contractSize"):
        await ex.init_market_meta()


async def test_okx_init_market_meta_raises_on_missing_contract_size_key():
    """market 存在但无 contractSize 键 → 仍 raise（锁 `if market else None` 另一臂）。"""
    ex = _make_okx()
    ex._preload_markets = AsyncMock()
    ex._client = MagicMock()
    ex._client.markets = {"BTC/USDT:USDT": {}}
    with pytest.raises(RuntimeError, match="contractSize"):
        await ex.init_market_meta()


# ============ 回调时序回归（spec §3.6 硬约束 3）============
# 注：BaseExchange._dispatch_fill_event 的 clear/skip/failure-isolation/no-callback
# SRP 单测在 tests/test_alert_lifecycle.py（test_dispatch_fill_event_*）。此处不重复
# 那些行为，仅守卫硬约束 3 特有的不变量：回调注册先于撮合循环启动时 fill 必达，
# 以及 run() 内 on_fill/on_alert 注册的结构序早于 await exchange.start()。

async def test_fill_dispatch_reaches_callback_registered_before_start():
    """回调注册先于循环启动的相对序下，fill 事件必达回调（spec §3.6 硬约束 3）。

    不真启动撮合循环（需网络）——直接驱动派发方法验证'注册后派发必达'；
    窗口不存在性由 test_init_market_meta_does_not_start_matching_loops 守卫。
    """
    from tests._fixtures import make_fill_event

    ex = _make_exchange()
    received = []

    async def _cb(fill):
        received.append(fill)

    ex.on_fill(_cb)
    fill = make_fill_event(order_id="f1")
    await ex._dispatch_fill_event(fill)
    assert received and received[0].order_id == "f1"


def test_app_registers_callbacks_before_exchange_start():
    """结构守卫：run() 内 on_fill/on_alert 注册必须先于 await exchange.start()（spec §3.6 硬约束 3）。"""
    import pathlib

    src = (pathlib.Path(__file__).resolve().parents[1] / "src" / "cli" / "app.py").read_text()
    assert "async def run(" in src, "app.py 不再含 'async def run(' 锚点——结构守卫需更新（spec §3.6 硬约束 3）"
    run_body = src[src.index("async def run("):]
    for anchor in ("exchange.on_fill(", "exchange.on_alert(", "await exchange.start()"):
        assert anchor in run_body, f"run() 不再含 '{anchor}' 锚点——结构守卫需更新（spec §3.6 硬约束 3）"
    start_idx = run_body.index("await exchange.start()")
    assert run_body.index("exchange.on_fill(") < start_idx
    assert run_body.index("exchange.on_alert(") < start_idx
