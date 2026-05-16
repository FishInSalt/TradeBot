# tests/test_get_performance.py
"""iter-tool-opt-net-pnl-metrics: get_performance gross/net dual-view output + wrapper docstring."""
import pytest
import pytest_asyncio
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
    """Build deps with MetricsService backed by a DB with two completed paired trades.

    iter-tool-opt-net-pnl-metrics: TradeAction 必须有 amount + entry_price (close fill)
    for FIFO to produce roundtrips.
    """
    from src.storage.database import init_db, get_session
    from src.storage.models import Session, TradeAction
    from src.services.metrics import MetricsService

    engine = await init_db(f"sqlite+aiosqlite:///{tmp_path}/perf19.db")
    async with get_session(engine) as session:
        session.add(Session(id="s19", name="test-perf-19", initial_balance=10000.0, fee_rate=0.0005))
        # Trade 1: open @50000 -> close @50450, gross=+45 (long 0.1)
        session.add(TradeAction(
            session_id="s19", action="order_filled", order_id="o1-open",
            symbol="BTC/USDT:USDT", side="long",
            price=50000.0, amount=0.1, fee=0.25, pnl=None, entry_price=None,
        ))
        session.add(TradeAction(
            session_id="s19", action="order_filled", order_id="o1-close",
            symbol="BTC/USDT:USDT", side="long",
            price=50450.0, amount=0.1, fee=0.25, pnl=45.0, entry_price=50000.0,
        ))
        # Trade 2: open @50000 -> close @49780, gross=-22 (long 0.1)
        session.add(TradeAction(
            session_id="s19", action="order_filled", order_id="o2-open",
            symbol="BTC/USDT:USDT", side="long",
            price=50000.0, amount=0.1, fee=0.15, pnl=None, entry_price=None,
        ))
        session.add(TradeAction(
            session_id="s19", action="order_filled", order_id="o2-close",
            symbol="BTC/USDT:USDT", side="long",
            price=49780.0, amount=0.1, fee=0.15, pnl=-22.0, entry_price=50000.0,
        ))
        await session.commit()

    deps = _make_deps()
    deps.db_engine = engine
    deps.session_id = "s19"
    deps.initial_balance = 10000.0
    deps.fee_rate = 0.0005
    deps.metrics = MetricsService(engine=engine, session_id="s19", initial_balance=10000.0)
    deps.exchange.fetch_balance.return_value = Balance(10023.0, 9023.0, 1000.0)
    return deps, engine


# ---------------------------------------------------------------------------
# iter-tool-opt-net-pnl-metrics AC: dual gross/net label assertions
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_trade_stats_includes_gross_based_label(tmp_path):
    """Trade Stats labels each metric with both gross and net dual views."""
    from src.agent.tools_perception import get_performance

    deps, engine = await _make_deps_with_metrics(tmp_path)
    try:
        out = await get_performance(deps)
    finally:
        await engine.dispose()

    # Output carries dual gross/net view
    assert "Win" in out
    assert "gross" in out and "net" in out

    # Key stat lines all present
    assert "Profit Factor:" in out
    assert "Max Drawdown:" in out
    assert "Best Trade:" in out
    assert "Worst Trade:" in out


@pytest.mark.asyncio
async def test_win_rate_line_has_gross_based_label(tmp_path):
    """Win rate line emits dual 'NN% gross ... NN% net' schema."""
    from src.agent.tools_perception import get_performance

    deps, engine = await _make_deps_with_metrics(tmp_path)
    try:
        out = await get_performance(deps)
    finally:
        await engine.dispose()

    import re
    assert re.search(r"\d+%\s+gross.*\d+%\s+net", out), (
        f"Expected 'NN% gross ... NN% net' on win-rate line; got:\n{out}"
    )


@pytest.mark.asyncio
async def test_profit_factor_has_gross_based_label(tmp_path):
    """Profit Factor line contains both 'gross' and 'net' tokens."""
    from src.agent.tools_perception import get_performance

    deps, engine = await _make_deps_with_metrics(tmp_path)
    try:
        out = await get_performance(deps)
    finally:
        await engine.dispose()

    for line in out.splitlines():
        if line.startswith("Profit Factor:"):
            assert "gross" in line and "net" in line, (
                f"Expected gross+net on Profit Factor line; got: {line!r}"
            )
            break
    else:
        pytest.fail(f"'Profit Factor:' line not found in output:\n{out}")


@pytest.mark.asyncio
async def test_max_drawdown_has_gross_based_label(tmp_path):
    """Max Drawdown line carries '(net equity)' label (spec §A1)."""
    from src.agent.tools_perception import get_performance

    deps, engine = await _make_deps_with_metrics(tmp_path)
    try:
        out = await get_performance(deps)
    finally:
        await engine.dispose()

    for line in out.splitlines():
        if line.startswith("Max Drawdown:"):
            assert "(net equity)" in line, (
                f"Expected '(net equity)' on Max Drawdown line; got: {line!r}"
            )
            break
    else:
        pytest.fail(f"'Max Drawdown:' line not found in output:\n{out}")


@pytest.mark.asyncio
async def test_best_worst_trade_has_gross_based_label(tmp_path):
    """Best/Worst Trade lines emit dual gross/net view."""
    from src.agent.tools_perception import get_performance

    deps, engine = await _make_deps_with_metrics(tmp_path)
    try:
        out = await get_performance(deps)
    finally:
        await engine.dispose()

    for line in out.splitlines():
        if line.startswith("Best Trade:"):
            assert "gross" in line and "net" in line, (
                f"Expected gross+net on Best Trade line; got: {line!r}"
            )
            break
    else:
        pytest.fail(f"'Best Trade:' line not found in output:\n{out}")


# ---------------------------------------------------------------------------
# AC: wrapper docstring
# ---------------------------------------------------------------------------

def test_get_performance_wrapper_docstring_lists_fee_fields_and_gross_caveat():
    """Wrapper docstring lists Total Fees field + dual view tokens + MDD net equity."""
    from src.agent.trader import create_trader_agent
    from src.config import PersonaConfig

    agent = create_trader_agent(model="test", persona_config=PersonaConfig())
    tool = agent._function_toolset.tools["get_performance"]
    desc = tool.tool_def.description or ""

    assert "Total Fees" in desc, (
        f"'Total Fees' missing from get_performance wrapper docstring:\n{desc!r}"
    )
    assert "gross" in desc and "net" in desc, (
        f"gross/net dual view missing from get_performance wrapper docstring:\n{desc!r}"
    )
    assert "(net equity)" in desc, (
        f"'(net equity)' MDD label missing from wrapper docstring:\n{desc!r}"
    )


# ---------------------------------------------------------------------------
# Regression: existing get_performance tests still pass
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_get_performance_trading_perf_section_unchanged(tmp_path):
    """Trading Performance section header + key fields still render."""
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
    """Regression: get_performance(deps.metrics=None) emits 'No metrics service available.'"""
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


# ---------------------------------------------------------------------------
# iter-tool-opt-net-pnl-metrics: new positive dual-view assertions (spec §8.1)
# ---------------------------------------------------------------------------

@pytest_asyncio.fixture
async def deps_with_one_winning_trade(db_engine, deps_factory):
    """deps_factory + metrics service + 1 winning paired trade (raw SQL insert)."""
    from sqlalchemy import text
    from src.services.metrics import MetricsService

    sid = "perf-test-1"
    async with db_engine.begin() as conn:
        # Defensive idempotent — deps_factory doesn't insert into sessions, but
        # tmp_path reuse across tests may leave residue.
        await conn.execute(text("DELETE FROM sessions WHERE id = :sid"), {"sid": sid})
        await conn.execute(text(
            "INSERT INTO sessions "
            "(id, name, symbol, initial_balance, status, created_at, updated_at, "
            " exchange_type, timeframe, scheduler_interval_min, approval_enabled, "
            " token_budget, fee_rate) "
            "VALUES (:sid, :sid, 'BTC/USDT:USDT', 10000.0, 'active', "
            "        '2026-01-01T00:00:00', '2026-01-01T00:00:00', "
            "        'simulated', '15m', 15, 1, 500000, 0.0005)"
        ), {"sid": sid})
        # Paired open + close (one winning roundtrip). Distinct created_at
        # so FIFO ORDER BY created_at puts open before close.
        for fill in [
            {"session_id": sid, "action": "order_filled", "symbol": "BTC/USDT:USDT",
             "side": "long", "trigger_reason": "market", "price": 50000.0,
             "amount": 0.1, "fee": 2.5, "pnl": None, "entry_price": None,
             "order_id": "o-open", "created_at": "2026-01-01T00:00:00"},
            {"session_id": sid, "action": "order_filled", "symbol": "BTC/USDT:USDT",
             "side": "long", "trigger_reason": "market", "price": 51000.0,
             "amount": 0.1, "fee": 2.55, "pnl": 100.0, "entry_price": 50000.0,
             "order_id": "o-close", "created_at": "2026-01-01T00:00:01"},
        ]:
            cols = ", ".join(fill.keys())
            placeholders = ", ".join(f":{k}" for k in fill.keys())
            await conn.execute(text(f"INSERT INTO trade_actions ({cols}) VALUES ({placeholders})"), fill)

    deps = deps_factory(session_id=sid, initial_balance=10000.0)
    deps.metrics = MetricsService(db_engine, sid, initial_balance=10000.0)
    deps.fee_rate = 0.0005
    return deps


@pytest.mark.asyncio
async def test_get_performance_dual_view_lines(deps_with_one_winning_trade):
    """spec §8.1: 输出 gross/net 双视角."""
    from src.agent.tools_perception import get_performance

    out = await get_performance(deps_with_one_winning_trade)
    # Win Rate line
    win_line = next(line for line in out.splitlines() if line.startswith("Win Rate"))
    assert "gross" in win_line and "net" in win_line, (
        f"Win rate line missing dual view: {win_line!r}"
    )
    # Profit Factor line
    pf_line = next(line for line in out.splitlines() if line.startswith("Profit Factor"))
    assert "gross" in pf_line and "net" in pf_line, (
        f"PF line missing dual view: {pf_line!r}"
    )
    # Max Drawdown line
    mdd_line = next(line for line in out.splitlines() if "Max Drawdown" in line)
    assert "net equity" in mdd_line, (
        f"MDD line missing 'net equity': {mdd_line!r}"
    )
    # Realized PnL line — lock in gross/net numeric pairing (fixture: gross=+100, fees=5.05, net=+94.95)
    # Guards against gross/net field swaps that would slip past substring-only checks.
    pnl_line = next(line for line in out.splitlines() if line.startswith("Realized PnL"))
    assert "+100.00 USDT gross" in pnl_line, (
        f"Realized PnL line missing gross value: {pnl_line!r}"
    )
    assert "+94.95 USDT net" in pnl_line, (
        f"Realized PnL line missing net value: {pnl_line!r}"
    )


@pytest_asyncio.fixture
async def deps_with_legacy_only(db_engine, deps_factory):
    """deps + metrics service + only pre-iter legacy fills (amount IS NULL on both open + close).

    Mirrors pre-net-metrics-iter session shape: scripts/_sim_metrics from sim_orders
    still has forensic data, but trade_actions has no amount/entry_price → FIFO skips
    everything → total_trades=0 → output should be "Stats unavailable: all close fills
    are pre-net-metrics-iter legacy data ...".
    """
    from sqlalchemy import text
    from src.services.metrics import MetricsService

    sid = "perf-test-legacy"
    async with db_engine.begin() as conn:
        await conn.execute(text("DELETE FROM sessions WHERE id = :sid"), {"sid": sid})
        await conn.execute(text(
            "INSERT INTO sessions "
            "(id, name, symbol, initial_balance, status, created_at, updated_at, "
            " exchange_type, timeframe, scheduler_interval_min, approval_enabled, "
            " token_budget, fee_rate) "
            "VALUES (:sid, :sid, 'BTC/USDT:USDT', 10000.0, 'active', "
            "        '2026-01-01T00:00:00', '2026-01-01T00:00:00', "
            "        'simulated', '15m', 15, 1, 500000, 0.0005)"
        ), {"sid": sid})
        for fill in [
            {"session_id": sid, "action": "order_filled", "symbol": "BTC/USDT:USDT",
             "side": "long", "trigger_reason": "market", "price": 50000.0,
             "amount": None, "fee": 2.5, "pnl": None, "entry_price": None,
             "order_id": "o-legacy-open", "created_at": "2026-01-01T00:00:00"},
            {"session_id": sid, "action": "order_filled", "symbol": "BTC/USDT:USDT",
             "side": "long", "trigger_reason": "market", "price": 51000.0,
             "amount": None, "fee": 2.55, "pnl": 100.0, "entry_price": None,
             "order_id": "o-legacy-close", "created_at": "2026-01-01T00:00:01"},
        ]:
            cols = ", ".join(fill.keys())
            placeholders = ", ".join(f":{k}" for k in fill.keys())
            await conn.execute(text(f"INSERT INTO trade_actions ({cols}) VALUES ({placeholders})"), fill)

    deps = deps_factory(session_id=sid, initial_balance=10000.0)
    deps.metrics = MetricsService(db_engine, sid, initial_balance=10000.0)
    deps.fee_rate = 0.0005
    return deps


@pytest.mark.asyncio
async def test_get_performance_legacy_session_stats_unavailable_text(deps_with_legacy_only):
    """spec §6.2(c): all-legacy session UX — agent sees explicit
    'Stats unavailable: all close fills are pre-net-metrics-iter legacy data ...'
    pointing to scripts/_sim_metrics.py for forensic recovery.

    PR #57 mini-fix I-3: prior coverage only asserted internal caveat counters
    (test_compute_legacy_session_all_stats_unavailable), not the rendered output.
    """
    from src.agent.tools_perception import get_performance

    out = await get_performance(deps_with_legacy_only)
    assert "Stats unavailable" in out, f"Missing degradation label in:\n{out}"
    assert "pre-net-metrics-iter legacy data" in out, f"Missing legacy caveat in:\n{out}"
    assert "scripts/_sim_metrics" in out, f"Missing forensic pointer in:\n{out}"
