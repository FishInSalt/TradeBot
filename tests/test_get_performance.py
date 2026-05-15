# tests/test_get_performance.py
"""Task 19: get_performance gross-based labels + wrapper docstring rewrite."""
import pytest
from dataclasses import dataclass
from unittest.mock import AsyncMock, MagicMock

from src.integrations.exchange.base import Ticker, Balance, Order


# ---------------------------------------------------------------------------
# Shared fixture helpers (mirrors test_tool_enhancement.py MockDeps pattern)
# ---------------------------------------------------------------------------

@dataclass
class _MockDeps:
    symbol: str
    timeframe: str
    market_data: AsyncMock
    exchange: AsyncMock
    technical: MagicMock
    memory: AsyncMock
    session_id: str = "test-session"
    db_engine: object = None
    approval_gate: object = None
    approval_enabled: bool = False
    wake_min_minutes: int = 1
    wake_max_minutes: int = 60
    set_next_wake_fn: object = None
    initial_balance: float = 10000.0
    fee_rate: float = 0.0005
    metrics: object = None


def _make_deps() -> _MockDeps:
    d = _MockDeps(
        symbol="BTC/USDT:USDT",
        timeframe="5m",
        market_data=AsyncMock(),
        exchange=AsyncMock(),
        technical=MagicMock(),
        memory=AsyncMock(),
    )
    d.exchange.fetch_balance.return_value = Balance(10000.0, 10000.0, 0.0)
    d.exchange.fetch_positions.return_value = []
    d.exchange.algo_trigger_reference = "last"
    return d


async def _make_deps_with_metrics(tmp_path):
    """Build deps with MetricsService backed by a DB with two completed trades."""
    from src.storage.database import init_db, get_session
    from src.storage.models import Session, TradeAction
    from src.services.metrics import MetricsService

    engine = await init_db(f"sqlite+aiosqlite:///{tmp_path}/perf19.db")
    async with get_session(engine) as session:
        session.add(Session(id="s19", name="test-perf-19", initial_balance=10000.0))
        session.add(TradeAction(
            session_id="s19", action="order_filled", order_id="o1",
            symbol="BTC/USDT:USDT", side="long", pnl=45.0, fee=0.5,
        ))
        session.add(TradeAction(
            session_id="s19", action="order_filled", order_id="o2",
            symbol="BTC/USDT:USDT", side="long", pnl=-22.0, fee=0.3,
        ))
        await session.commit()

    deps = _make_deps()
    deps.db_engine = engine
    deps.session_id = "s19"
    deps.initial_balance = 10000.0
    deps.metrics = MetricsService(engine=engine, session_id="s19", initial_balance=10000.0)
    deps.exchange.fetch_balance.return_value = Balance(10023.0, 9023.0, 1000.0)
    return deps, engine


# ---------------------------------------------------------------------------
# Task 19 AC: gross-based label assertions
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_trade_stats_includes_gross_based_label(tmp_path):
    """Trade Stats labels each metric as (gross-based) until net iter lands."""
    from src.agent.tools_perception import get_performance

    deps, engine = await _make_deps_with_metrics(tmp_path)
    try:
        out = await get_performance(deps)
    finally:
        await engine.dispose()

    # Win rate line contains gross-based label
    assert "Win" in out
    assert "(gross-based)" in out

    # Key stat lines all carry the label
    assert "Profit Factor:" in out
    assert "Max Drawdown:" in out
    assert "Best Trade:" in out
    assert "Worst Trade:" in out


@pytest.mark.asyncio
async def test_win_rate_line_has_gross_based_label(tmp_path):
    """Win rate specifically annotated with 'gross-based'."""
    from src.agent.tools_perception import get_performance

    deps, engine = await _make_deps_with_metrics(tmp_path)
    try:
        out = await get_performance(deps)
    finally:
        await engine.dispose()

    # The win-rate part of the Total Trades line should read "(...%, gross-based)"
    import re
    assert re.search(r"\d+\.?\d*%,\s*gross-based\)", out), (
        f"Expected 'NN%, gross-based)' on win-rate line; got:\n{out}"
    )


@pytest.mark.asyncio
async def test_profit_factor_has_gross_based_label(tmp_path):
    """Profit Factor line ends with '(gross-based)'."""
    from src.agent.tools_perception import get_performance

    deps, engine = await _make_deps_with_metrics(tmp_path)
    try:
        out = await get_performance(deps)
    finally:
        await engine.dispose()

    for line in out.splitlines():
        if line.startswith("Profit Factor:"):
            assert "(gross-based)" in line, (
                f"Expected '(gross-based)' on Profit Factor line; got: {line!r}"
            )
            break
    else:
        pytest.fail(f"'Profit Factor:' line not found in output:\n{out}")


@pytest.mark.asyncio
async def test_max_drawdown_has_gross_based_label(tmp_path):
    """Max Drawdown line carries '(gross-based equity)' label."""
    from src.agent.tools_perception import get_performance

    deps, engine = await _make_deps_with_metrics(tmp_path)
    try:
        out = await get_performance(deps)
    finally:
        await engine.dispose()

    for line in out.splitlines():
        if line.startswith("Max Drawdown:"):
            assert "(gross-based equity)" in line, (
                f"Expected '(gross-based equity)' on Max Drawdown line; got: {line!r}"
            )
            break
    else:
        pytest.fail(f"'Max Drawdown:' line not found in output:\n{out}")


@pytest.mark.asyncio
async def test_best_worst_trade_has_gross_based_label(tmp_path):
    """Best/Worst Trade line carries '(gross-based)'."""
    from src.agent.tools_perception import get_performance

    deps, engine = await _make_deps_with_metrics(tmp_path)
    try:
        out = await get_performance(deps)
    finally:
        await engine.dispose()

    for line in out.splitlines():
        if line.startswith("Best Trade:"):
            assert "(gross-based)" in line, (
                f"Expected '(gross-based)' on Best/Worst Trade line; got: {line!r}"
            )
            break
    else:
        pytest.fail(f"'Best Trade:' line not found in output:\n{out}")


# ---------------------------------------------------------------------------
# Task 19 AC: wrapper docstring
# ---------------------------------------------------------------------------

def test_get_performance_wrapper_docstring_lists_fee_fields_and_gross_caveat():
    """Wrapper docstring lists Total Fees field and gross-based caveat."""
    from src.agent.trader import create_trader_agent
    from src.config import PersonaConfig

    agent = create_trader_agent(model="test", persona_config=PersonaConfig())
    tool = agent._function_toolset.tools["get_performance"]
    desc = tool.tool_def.description or ""

    assert "Total Fees" in desc, (
        f"'Total Fees' missing from get_performance wrapper docstring:\n{desc!r}"
    )
    assert "gross-based" in desc, (
        f"'gross-based' caveat missing from get_performance wrapper docstring:\n{desc!r}"
    )


# ---------------------------------------------------------------------------
# Regression: existing get_performance tests still pass (no Trading Perf regression)
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_get_performance_trading_perf_section_unchanged(tmp_path):
    """Trading Performance section still renders correctly after Task 19."""
    from src.agent.tools_perception import get_performance
    import re

    deps, engine = await _make_deps_with_metrics(tmp_path)
    try:
        out = await get_performance(deps)
    finally:
        await engine.dispose()

    assert re.search(
        r"=== Trading Performance \(@ \d{2}:\d{2}:\d{2} UTC\) ===",
        out,
    ), f"Trading Performance header missing or malformed:\n{out[:200]}"
    assert "Initial Balance:" in out
    assert "Current Balance:" in out
    assert "Total Return:" in out
    assert "Realized PnL:" in out
    assert "Total Fees:" in out


@pytest.mark.asyncio
async def test_get_performance_no_metrics_service_unchanged():
    """Regression: get_performance(deps.metrics=None) path unaffected by Task 19."""
    from src.agent.tools_perception import get_performance

    deps = _make_deps()
    deps.metrics = None

    out = await get_performance(deps)
    assert "No metrics service available" in out


@pytest.mark.asyncio
async def test_get_performance_no_trades_unchanged(tmp_path):
    """Regression: zero-trade path still emits 'No completed trades yet.'"""
    from src.agent.tools_perception import get_performance
    from src.storage.database import init_db, get_session
    from src.storage.models import Session
    from src.services.metrics import MetricsService

    engine = await init_db(f"sqlite+aiosqlite:///{tmp_path}/perf19_empty.db")
    async with get_session(engine) as session:
        session.add(Session(id="s19e", name="empty", initial_balance=10000.0))
        await session.commit()

    deps = _make_deps()
    deps.db_engine = engine
    deps.session_id = "s19e"
    deps.initial_balance = 10000.0
    deps.metrics = MetricsService(engine=engine, session_id="s19e", initial_balance=10000.0)

    try:
        out = await get_performance(deps)
    finally:
        await engine.dispose()

    assert "No completed trades yet" in out
