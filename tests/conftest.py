import os
import shutil
from unittest.mock import AsyncMock, MagicMock

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncEngine, async_sessionmaker, create_async_engine

from src.config import Settings, ExchangeConfig, TradingConfig, TraderConfig, PersonaConfig


@pytest.fixture
def settings() -> Settings:
    return Settings(
        exchange=ExchangeConfig(name="okx", api_key="test", secret="test", password="test"),
        trading=TradingConfig(initial_balance_usdt=10000.0),
    )


@pytest.fixture
def trader_config() -> TraderConfig:
    return TraderConfig(persona=PersonaConfig())


@pytest.fixture
async def engine() -> AsyncEngine:
    """In-memory SQLite engine + schema (R2-4 共享 fixture，原在 test_tool_call_recorder.py)."""
    from src.storage.database import init_db
    return await init_db("sqlite+aiosqlite:///:memory:")


@pytest.fixture
async def session_with_row(engine: AsyncEngine) -> str:
    """Insert parent session row so child rows' FK holds (R2-4 共享 fixture)."""
    from src.storage.database import get_session
    from src.storage.models import Session as SessionModel
    async with get_session(engine) as db:
        db.add(SessionModel(id="sess-test", name="unit-test"))
        await db.commit()
    return "sess-test"


# === Phase 1 (T9): make_usage factory for unified RunUsage mocking ===

@pytest.fixture
def make_usage():
    """Factory for pydantic-ai RunUsage mock with Phase 1 standard attrs.

    Default values reflect a typical DeepSeek cycle (input ~1000, cache_read 70%).
    Override per test as needed for happy/forensic/edge-case scenarios.
    """
    def _make(
        input_tokens: int = 1000,
        output_tokens: int = 200,
        cache_read_tokens: int = 700,
        cache_write_tokens: int = 0,
        details: dict | None = None,
    ):
        if details is None:
            details = {
                "prompt_cache_hit_tokens": cache_read_tokens,
                "prompt_cache_miss_tokens": input_tokens - cache_read_tokens,
                "reasoning_tokens": 0,
            }
        usage = MagicMock()
        usage.total_tokens = input_tokens + output_tokens
        usage.input_tokens = input_tokens
        usage.output_tokens = output_tokens
        usage.cache_read_tokens = cache_read_tokens
        usage.cache_write_tokens = cache_write_tokens
        usage.details = details
        return usage
    return _make


# === Phase 1 (T9): pytest CLI options for AC-9 sim DB drift-guard ===
def pytest_addoption(parser):
    parser.addoption(
        "--sim-db", action="store", default=None,
        help="Path to archived sim DB for drift-guard tests (skip if not provided)"
    )
    parser.addoption(
        "--session-id", action="store", default=None,
        help="Session ID to filter within --sim-db (used by AC-9 baseline test)"
    )


# === Phase 1 (T9): Phase 1 测试 fixtures (used by T8/T12/T14/T16/T18/T19/T21) ===

@pytest_asyncio.fixture
async def db_engine(tmp_path):
    """Async engine on a fresh tmp DB at Phase 1 head + 3 views applied.

    init_db Path 3 (空库 → Base.metadata.create_all + _apply_views + stamp head)
    现在内置应用 view SQL（PR #42 fix）；fixture 直接调用即可，view 自动可用。
    """
    from src.storage.database import init_db

    db_path = tmp_path / "phase1_test.db"
    db_url = f"sqlite+aiosqlite:///{db_path}"
    engine = await init_db(db_url)
    yield engine
    await engine.dispose()


@pytest_asyncio.fixture
async def db_session(db_engine):
    """Async session bound to db_engine; auto-rollback on test exit."""
    async_session_factory = async_sessionmaker(db_engine, expire_on_commit=False)
    async with async_session_factory() as session:
        yield session


@pytest.fixture
def deps_factory(db_engine):
    """Factory for TradingDeps with SimulatedExchange (used by run_agent_cycle tests).

    Returns a callable that creates a fresh TradingDeps per call;
    使 T12 三路径测试可独立配 deps。
    """
    from src.agent.trader import TradingDeps
    from src.integrations.market_data import MarketDataService
    from src.services.technical import TechnicalAnalysisService
    from src.cli.approval import ApprovalGate
    from src.integrations.exchange.simulated import SimulatedExchange
    from src.config import ExchangeConfig

    def _make(symbol="BTC/USDT:USDT", session_id=None, initial_balance=10000.0):
        if session_id is None:
            import uuid
            session_id = str(uuid.uuid4())
        config = ExchangeConfig(name="simulated", fee_rate=0.0005)
        exchange = SimulatedExchange(
            config=config, db_engine=db_engine,
            session_id=session_id, symbol=symbol,
        )
        # Pre-populate state mirroring tests/_fixtures.py:make_sim_exchange — avoids
        # async start() while making _latest_price / _latest_ticker available so tools
        # (e.g. add_price_level_alert) reading these attrs don't AttributeError.
        from tests._fixtures import inject_mock_ccxt, make_ticker
        exchange._free_usdt = initial_balance
        exchange._used_usdt = 0.0
        exchange._frozen_usdt = 0.0
        exchange._positions = {}
        exchange._pending_orders = []
        exchange._leverage = {}
        exchange._latest_ticker = make_ticker(symbol=symbol)
        exchange._latest_mark_price = exchange._latest_ticker.last   # third path: default mark = last seed
        exchange._latest_price = exchange._latest_ticker.last
        exchange._running = True
        inject_mock_ccxt(exchange)
        deps = TradingDeps(
            session_id=session_id, symbol=symbol,
            timeframe="15m", exchange=exchange,
            market_data=MarketDataService(exchange),
            technical=MagicMock(spec=TechnicalAnalysisService),
            memory=MagicMock(format_for_prompt=AsyncMock(return_value="No relevant memories.")),
            approval_gate=ApprovalGate(enabled=False, timeout_seconds=30, console=MagicMock()),
            approval_enabled=False, db_engine=db_engine,
        )
        return deps
    return _make


@pytest_asyncio.fixture
async def deps_with_sim_exchange(deps_factory):
    """TradingDeps with SimulatedExchange — used by T8 alert_id tests."""
    return deps_factory()


@pytest_asyncio.fixture
async def db_engine_with_real_db(tmp_path):
    """Copy of data/tradebot.db + alembic upgrade head 到副本 (AC-8 historical compat).

    **不依赖主 DB schema 状态** — copy 后 init_db 走 Path 1（已有 schema → 走 alembic
    upgrade head 增量到 Phase 1 head）确保 fresh checkout / 主 DB 处于 R2-7 head
    时也可跑（不受 T13/T15/T17 副作用影响）。

    与 db_engine 区别：本 fixture 用 alembic CLI（DB 已 W1-like schema 存在），
    db_engine 用 init_db Path 3（fresh empty DB）。

    **Skip if data/tradebot.db missing** (PR #42 review I-3): data/ 是 gitignored，
    clean CI / fresh contributor checkout 没有此文件；shutil.copy 会 hard-fail。
    历史兼容测试本质需要真实 sim DB，无文件就 skip。
    """
    import subprocess
    from pathlib import Path
    src = Path("data/tradebot.db")
    if not src.exists():
        pytest.skip(f"{src} not present (gitignored); historical compat tests need real sim DB")
    dst = tmp_path / "compat_test.db"
    shutil.copy(src, dst)
    db_url = f"sqlite+aiosqlite:///{dst}"
    # PR #42 review v4 I-4: 强 downgrade -1 → upgrade head 真测 migration 上行,
    # 不只是验证"已 head 状态"。data/tradebot.db 早在 T13/T15/T17 已 upgrade 到
    # Phase 1 head, 直接 upgrade 是 no-op；显式回到 R2-7 再上让 migration 在
    # production-shape 数据上跑一遍 (335 rows, 3 sessions) 真验证 AC-8.
    sub_env = {**os.environ, "TRADEBOT_DB_URL": db_url}
    subprocess.run(["alembic", "downgrade", "-1"], env=sub_env, check=True, capture_output=True)
    subprocess.run(["alembic", "upgrade", "head"], env=sub_env, check=True, capture_output=True)
    engine = create_async_engine(db_url)
    yield engine
    await engine.dispose()
