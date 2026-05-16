# tests/test_metrics.py
import pytest
from src.storage.database import init_db, get_session
from src.storage.models import Session, TradeAction


@pytest.fixture
async def metrics_db(tmp_path):
    engine = await init_db(f"sqlite+aiosqlite:///{tmp_path}/metrics_test.db")
    async with get_session(engine) as session:
        session.add(Session(id="test-session", name="metrics-test", initial_balance=10000.0))
        await session.commit()
    yield engine
    await engine.dispose()


async def _add_paired_trade(engine, gross_pnl, fee_open=0.25, fee_close=0.25,
                              entry_price=50000.0, amount=0.1):
    """Add a paired open + close fill that produces a roundtrip with the given gross pnl.

    FIFO requires open + close pair; this helper preserves the original test contract
    (one call = one trade) by inserting both fills under unique order_ids.
    """
    async with get_session(engine) as session:
        oid_base = f"o-{gross_pnl:.4f}"
        # Open fill
        session.add(TradeAction(
            session_id="test-session", action="order_filled",
            order_id=f"{oid_base}-open", symbol="BTC/USDT:USDT", side="long",
            trigger_reason="market", price=entry_price, pnl=None, fee=fee_open,
            amount=amount, entry_price=None,
            reasoning="(exchange: market open filled)",
        ))
        await session.commit()
    # Close fill: derive exit price from gross_pnl (long: exit = entry + pnl/amount)
    exit_price = entry_price + gross_pnl / amount
    async with get_session(engine) as session:
        session.add(TradeAction(
            session_id="test-session", action="order_filled",
            order_id=f"{oid_base}-close", symbol="BTC/USDT:USDT", side="long",
            trigger_reason="market", price=exit_price, pnl=gross_pnl, fee=fee_close,
            amount=amount, entry_price=entry_price,
            reasoning="(exchange: market close filled)",
        ))
        await session.commit()


async def _add_open_fill(engine, fee=0.5, entry_price=50000.0, amount=0.1):
    """Open fill without paired close — for testing total_fees aggregation."""
    async with get_session(engine) as session:
        session.add(TradeAction(
            session_id="test-session", action="order_filled",
            order_id=f"o-open-{fee}", symbol="BTC/USDT:USDT", side="long",
            trigger_reason="market", price=entry_price, pnl=None, fee=fee,
            amount=amount, entry_price=None,
            reasoning="(exchange: market open filled)",
        ))
        await session.commit()


async def test_compute_metrics(metrics_db):
    from src.services.metrics import MetricsService
    await _add_paired_trade(metrics_db, 30.0, fee_open=0.25, fee_close=0.25)
    await _add_paired_trade(metrics_db, -15.0, fee_open=0.15, fee_close=0.15)
    await _add_paired_trade(metrics_db, 180.0, fee_open=0.4, fee_close=0.4)

    service = MetricsService(engine=metrics_db, session_id="test-session", initial_balance=10000.0)
    metrics = await service.compute()
    assert metrics.total_trades == 3
    assert metrics.win_rate == pytest.approx(2 / 3, abs=0.01)
    assert metrics.total_pnl == pytest.approx(195.0)
    assert metrics.profit_factor is not None
    assert metrics.profit_factor > 1.0
    assert metrics.avg_win == pytest.approx(105.0)
    assert metrics.avg_loss == pytest.approx(-15.0)
    assert metrics.best_trade == pytest.approx(180.0)
    assert metrics.worst_trade == pytest.approx(-15.0)
    # Each paired trade has fee_open + fee_close = total per-trade fee
    assert metrics.total_fees == pytest.approx(0.5 + 0.3 + 0.8)
    # Net = gross - fees per trade
    assert metrics.net_pnl == pytest.approx(195.0 - 1.6)


async def test_compute_metrics_empty(metrics_db):
    from src.services.metrics import MetricsService
    service = MetricsService(engine=metrics_db, session_id="test-session", initial_balance=10000.0)
    metrics = await service.compute()
    assert metrics.total_trades == 0
    assert metrics.win_rate == 0.0
    assert metrics.total_fees == 0.0
    assert metrics.avg_win == 0.0
    assert metrics.avg_loss == 0.0
    assert metrics.recent_summary == ""


async def test_compute_metrics_with_position(metrics_db):
    from src.services.metrics import MetricsService
    service = MetricsService(engine=metrics_db, session_id="test-session", initial_balance=10000.0)
    metrics = await service.compute(current_position="long 0.001")
    assert metrics.current_position == "long 0.001"


async def test_compute_metrics_recent_summary(metrics_db):
    from src.services.metrics import MetricsService
    await _add_paired_trade(metrics_db, 30.0)
    await _add_paired_trade(metrics_db, -10.0)
    await _add_paired_trade(metrics_db, 50.0)
    await _add_paired_trade(metrics_db, 20.0)

    service = MetricsService(engine=metrics_db, session_id="test-session", initial_balance=10000.0)
    metrics = await service.compute()
    assert "3W 1L" in metrics.recent_summary
    assert "last 4" in metrics.recent_summary


async def test_compute_metrics_total_fees_includes_opens(metrics_db):
    """total_fees includes open fills (pnl=None) that have fee."""
    from src.services.metrics import MetricsService
    await _add_open_fill(metrics_db, fee=0.5)
    await _add_paired_trade(metrics_db, 30.0, fee_open=0.25, fee_close=0.25)
    service = MetricsService(engine=metrics_db, session_id="test-session", initial_balance=10000.0)
    metrics = await service.compute()
    # Solo open fill leaves a lot in queue but doesn't pair → total_trades only counts paired roundtrip
    assert metrics.total_trades == 1
    # total_fees aggregates all order_filled rows regardless of FIFO pairing
    assert metrics.total_fees == pytest.approx(0.5 + 0.25 + 0.25)


async def test_compute_metrics_max_drawdown(metrics_db):
    from src.services.metrics import MetricsService
    await _add_paired_trade(metrics_db, 100.0, fee_open=0.0, fee_close=0.0)
    await _add_paired_trade(metrics_db, -50.0, fee_open=0.0, fee_close=0.0)
    await _add_paired_trade(metrics_db, -30.0, fee_open=0.0, fee_close=0.0)
    await _add_paired_trade(metrics_db, 200.0, fee_open=0.0, fee_close=0.0)
    service = MetricsService(engine=metrics_db, session_id="test-session", initial_balance=10000.0)
    metrics = await service.compute()
    # net = gross (fees=0); equity series [10000, 10100, 10050, 10020, 10220]
    # peak after step 1 = 10100, trough at step 3 = 10020 → dd = 80/10100
    assert metrics.max_drawdown_pct == pytest.approx(80 / 10100 * 100)


# --- Tool-call summary tests ---

async def test_tool_call_summary_empty():
    """No tool_calls rows → empty dict."""
    from datetime import timedelta
    from src.storage.database import init_db, get_session
    from src.storage.models import Session as SessionModel
    from src.services.metrics import MetricsService

    engine = await init_db("sqlite+aiosqlite:///:memory:")
    async with get_session(engine) as db:
        db.add(SessionModel(id="s1", name="n"))
        await db.commit()

    ms = MetricsService(engine, session_id="s1")
    summary = await ms.get_tool_call_summary()
    assert summary == {}


async def test_tool_call_summary_aggregation():
    """Multi-tool multi-call: counts, error rate, last_called_at all correct."""
    from datetime import datetime, timezone, timedelta
    from src.storage.database import init_db, get_session
    from src.storage.models import Session as SessionModel, ToolCall
    from src.services.metrics import MetricsService

    engine = await init_db("sqlite+aiosqlite:///:memory:")
    async with get_session(engine) as db:
        db.add(SessionModel(id="s1", name="n"))
        await db.commit()

    t0 = datetime(2026, 4, 20, 12, 0, 0, tzinfo=timezone.utc)
    rows = [
        # 3 successful get_market_data calls
        ToolCall(session_id="s1", cycle_id="c1", tool_name="get_market_data",
                 status="ok", duration_ms=100, created_at=t0),
        ToolCall(session_id="s1", cycle_id="c1", tool_name="get_market_data",
                 status="ok", duration_ms=200, created_at=t0 + timedelta(seconds=1)),
        ToolCall(session_id="s1", cycle_id="c2", tool_name="get_market_data",
                 status="ok", duration_ms=300, created_at=t0 + timedelta(seconds=2)),
        # 1 failed get_position
        ToolCall(session_id="s1", cycle_id="c1", tool_name="get_position",
                 status="error", duration_ms=50, error_type="TimeoutError",
                 created_at=t0 + timedelta(seconds=3)),
    ]
    async with get_session(engine) as db:
        db.add_all(rows)
        await db.commit()

    ms = MetricsService(engine, session_id="s1")
    summary = await ms.get_tool_call_summary(session_id="s1")

    assert set(summary.keys()) == {"get_market_data", "get_position"}

    mkt = summary["get_market_data"]
    assert mkt.count == 3
    assert mkt.ok_count == 3
    assert mkt.error_count == 0
    assert mkt.error_rate == 0.0
    assert mkt.last_called_at == (t0 + timedelta(seconds=2)).replace(tzinfo=None)
    assert mkt.error_breakdown == {}

    pos = summary["get_position"]
    assert pos.count == 1
    assert pos.error_count == 1
    assert pos.error_rate == 1.0
    assert pos.error_breakdown == {"TimeoutError": 1}
    assert pos.last_called_at == (t0 + timedelta(seconds=3)).replace(tzinfo=None)


async def test_tool_call_summary_filter_session():
    """session_id=None aggregates across sessions; specifying one filters correctly."""
    from src.storage.database import init_db, get_session
    from src.storage.models import Session as SessionModel, ToolCall
    from src.services.metrics import MetricsService

    engine = await init_db("sqlite+aiosqlite:///:memory:")
    async with get_session(engine) as db:
        db.add_all([
            SessionModel(id="s1", name="n1"),
            SessionModel(id="s2", name="n2"),
        ])
        await db.commit()
        db.add_all([
            ToolCall(session_id="s1", cycle_id="c1", tool_name="get_market_data",
                     status="ok", duration_ms=10),
            ToolCall(session_id="s2", cycle_id="c2", tool_name="get_market_data",
                     status="ok", duration_ms=20),
        ])
        await db.commit()

    ms = MetricsService(engine, session_id="s1")  # instance session_id unused here

    all_sessions = await ms.get_tool_call_summary()  # None → cross-session
    assert all_sessions["get_market_data"].count == 2

    only_s1 = await ms.get_tool_call_summary(session_id="s1")
    assert only_s1["get_market_data"].count == 1


async def test_tool_call_summary_filter_since():
    """since=timedelta limits to rows with created_at > now - since."""
    from datetime import datetime, timezone, timedelta
    from src.storage.database import init_db, get_session
    from src.storage.models import Session as SessionModel, ToolCall
    from src.services.metrics import MetricsService

    engine = await init_db("sqlite+aiosqlite:///:memory:")
    async with get_session(engine) as db:
        db.add(SessionModel(id="s1", name="n"))
        await db.commit()

    now = datetime.now(timezone.utc)
    async with get_session(engine) as db:
        db.add_all([
            ToolCall(session_id="s1", cycle_id="c1", tool_name="get_market_data",
                     status="ok", duration_ms=10,
                     created_at=now - timedelta(days=2)),
            ToolCall(session_id="s1", cycle_id="c2", tool_name="get_market_data",
                     status="ok", duration_ms=10,
                     created_at=now - timedelta(minutes=5)),
        ])
        await db.commit()

    ms = MetricsService(engine, session_id="s1")

    last_hour = await ms.get_tool_call_summary(session_id="s1", since=timedelta(hours=1))
    assert last_hour["get_market_data"].count == 1

    last_week = await ms.get_tool_call_summary(session_id="s1", since=timedelta(days=7))
    assert last_week["get_market_data"].count == 2


async def test_tool_call_summary_error_breakdown():
    """error_breakdown counts by error_type."""
    from src.storage.database import init_db, get_session
    from src.storage.models import Session as SessionModel, ToolCall
    from src.services.metrics import MetricsService

    engine = await init_db("sqlite+aiosqlite:///:memory:")
    async with get_session(engine) as db:
        db.add(SessionModel(id="s1", name="n"))
        await db.commit()
        db.add_all([
            ToolCall(session_id="s1", cycle_id="c", tool_name="get_x",
                     status="error", duration_ms=5, error_type="TimeoutError"),
            ToolCall(session_id="s1", cycle_id="c", tool_name="get_x",
                     status="error", duration_ms=5, error_type="TimeoutError"),
            ToolCall(session_id="s1", cycle_id="c", tool_name="get_x",
                     status="error", duration_ms=5, error_type="HTTPStatusError"),
            ToolCall(session_id="s1", cycle_id="c", tool_name="get_x",
                     status="ok", duration_ms=5),
        ])
        await db.commit()

    ms = MetricsService(engine, session_id="s1")
    summary = await ms.get_tool_call_summary(session_id="s1")

    stats = summary["get_x"]
    assert stats.count == 4
    assert stats.error_count == 3
    assert stats.error_rate == 0.75
    assert stats.error_breakdown == {"TimeoutError": 2, "HTTPStatusError": 1}


async def test_tool_call_summary_percentiles_inclusive():
    """p50/p95 use method='inclusive', bounded by sample max; N=1 and N>=2."""
    from src.storage.database import init_db, get_session
    from src.storage.models import Session as SessionModel, ToolCall
    from src.services.metrics import MetricsService

    engine = await init_db("sqlite+aiosqlite:///:memory:")
    async with get_session(engine) as db:
        db.add(SessionModel(id="s1", name="n"))
        await db.commit()
        # tool_a: N=1 (single call duration=500)
        db.add(ToolCall(session_id="s1", cycle_id="c", tool_name="tool_a",
                        status="ok", duration_ms=500))
        # tool_b: N=10, durations 10..100 ms
        for d in range(10, 101, 10):  # 10,20,...,100
            db.add(ToolCall(session_id="s1", cycle_id="c", tool_name="tool_b",
                            status="ok", duration_ms=d))
        await db.commit()

    ms = MetricsService(engine, session_id="s1")
    summary = await ms.get_tool_call_summary(session_id="s1")

    # N=1: p50=p95=single value
    assert summary["tool_a"].p50_duration_ms == 500
    assert summary["tool_a"].p95_duration_ms == 500

    # N=10 inclusive: p95 bounded by sample max (100)
    assert summary["tool_b"].p95_duration_ms <= 100
    assert summary["tool_b"].p50_duration_ms <= 100
    # Sanity: p50 somewhere middle, p95 near high end
    assert 40 <= summary["tool_b"].p50_duration_ms <= 60
    assert 80 <= summary["tool_b"].p95_duration_ms <= 100


def test_performance_metrics_has_net_fields():
    """spec §C3: PerformanceMetrics +7 net 字段 + 2 计数字段 + 4 caveat."""
    from src.services.metrics import PerformanceMetrics
    pm = PerformanceMetrics()
    # 7 net metric fields
    assert pm.net_pnl == 0.0
    assert pm.net_profit_factor is None
    assert pm.net_win_rate == 0.0
    assert pm.avg_win_net == 0.0
    assert pm.avg_loss_net == 0.0
    assert pm.best_trade_net == 0.0
    assert pm.worst_trade_net == 0.0
    # 2 count fields
    assert pm.net_winning_trades == 0
    assert pm.net_losing_trades == 0
    # 4 caveat counters
    assert pm.legacy_open_skipped == 0
    assert pm.legacy_close_skipped == 0
    assert pm.missing_close_entry_price_count == 0
    assert pm.invariant_violations == 0


def test_performance_metrics_profit_factor_default_none():
    """spec §2 zero-denom decision: PF default None (was 0.0/inf)."""
    from src.services.metrics import PerformanceMetrics
    pm = PerformanceMetrics()
    assert pm.profit_factor is None


# --- Task 7: MetricsService.compute() FIFO integration tests ---

from sqlalchemy import text


async def _setup_compute_session(engine, sid: str, initial_balance: float = 10000.0,
                                  fee_rate: float | None = 0.0005, fills: list[dict] | None = None):
    """Test helper: insert sessions + trade_actions for compute() tests (raw SQL with all NOT NULL cols)."""
    fr_clause = "NULL" if fee_rate is None else str(fee_rate)
    async with engine.begin() as conn:
        await conn.execute(text(
            f"INSERT INTO sessions "
            f"(id, name, symbol, initial_balance, status, created_at, updated_at, "
            f" exchange_type, timeframe, scheduler_interval_min, approval_enabled, "
            f" token_budget, fee_rate) "
            f"VALUES (:sid, :sid, 'BTC/USDT:USDT', :bal, 'active', "
            f"        '2026-01-01T00:00:00', '2026-01-01T00:00:00', "
            f"        'simulated', '15m', 15, 1, 500000, {fr_clause})"
        ), {"sid": sid, "bal": initial_balance})
        for f in (fills or []):
            defaults = {"session_id": sid, "action": "order_filled",
                        "symbol": "BTC/USDT:USDT", "trigger_reason": "market",
                        "created_at": "2026-01-01T00:00:00"}
            defaults.update(f)
            cols = ", ".join(defaults.keys())
            placeholders = ", ".join(f":{k}" for k in defaults.keys())
            await conn.execute(text(f"INSERT INTO trade_actions ({cols}) VALUES ({placeholders})"), defaults)


@pytest.mark.asyncio
async def test_compute_uses_fifo_with_gross_and_net(engine):
    """spec §5.2: compute() returns gross + net metrics."""
    from src.services.metrics import MetricsService
    sid = "compute-fifo-1"
    await _setup_compute_session(engine, sid, fills=[
        {"side": "long", "price": 50000.0, "amount": 0.1, "fee": 2.5, "pnl": None},
        {"side": "long", "price": 51000.0, "amount": 0.1, "fee": 2.55,
         "pnl": 100.0, "entry_price": 50000.0},
    ])
    svc = MetricsService(engine, sid, initial_balance=10000.0)
    m = await svc.compute()

    assert m.total_pnl == pytest.approx(100.0)
    assert m.total_trades == 1
    assert m.winning_trades == 1
    assert m.win_rate == pytest.approx(1.0)
    assert m.net_pnl == pytest.approx(94.95)  # 100 - 2.5 - 2.55
    assert m.net_winning_trades == 1
    assert m.net_win_rate == pytest.approx(1.0)
    assert m.total_fees == pytest.approx(5.05)


@pytest.mark.asyncio
async def test_compute_net_mdd_uses_net_equity(engine):
    """spec §A1: max_drawdown_pct uses net equity series."""
    from src.services.metrics import MetricsService
    sid = "mdd-net"
    # net pnl = -100 - 2.5 - 2.45 = -104.95
    # equity trough: 1000 + (-104.95) = 895.05
    # dd = (1000 - 895.05) / 1000 = 0.10495 → 10.495%
    await _setup_compute_session(engine, sid, initial_balance=1000.0, fills=[
        {"side": "long", "price": 50000.0, "amount": 0.1, "fee": 2.5, "pnl": None},
        {"side": "long", "price": 49000.0, "amount": 0.1, "fee": 2.45,
         "pnl": -100.0, "entry_price": 50000.0},
    ])
    svc = MetricsService(engine, sid, initial_balance=1000.0)
    m = await svc.compute()
    assert m.max_drawdown_pct == pytest.approx(10.495, abs=0.01)


@pytest.mark.asyncio
async def test_compute_fee_rate_null_fallback_warns(engine, caplog):
    """spec §6.1: sessions.fee_rate IS NULL → log.warning (algorithm unaffected since FIFO uses lot.open_fee directly)."""
    from src.services.metrics import MetricsService
    sid = "fee-null"
    await _setup_compute_session(engine, sid, fee_rate=None, fills=[
        {"side": "long", "price": 50000.0, "amount": 0.1, "fee": 2.5, "pnl": None},
        {"side": "long", "price": 51000.0, "amount": 0.1, "fee": 2.55,
         "pnl": 100.0, "entry_price": 50000.0},
    ])
    svc = MetricsService(engine, sid, initial_balance=10000.0)
    with caplog.at_level("WARNING"):
        m = await svc.compute()
    assert m.total_trades == 1
    # Stricter: only match our service's logger (avoids ORM / sqlalchemy noise)
    metrics_records = [r for r in caplog.records if r.name == "src.services.metrics"]
    assert any("fee_rate" in r.message.lower() for r in metrics_records), (
        f"Expected fee_rate warning from src.services.metrics; got: {[r.message for r in metrics_records]}"
    )


@pytest.mark.asyncio
async def test_compute_legacy_session_all_stats_unavailable(engine):
    """spec §6.2(c): all close fills legacy → all stats N/A, total_trades=0."""
    from src.services.metrics import MetricsService
    sid = "legacy-all"
    await _setup_compute_session(engine, sid, fills=[
        {"side": "long", "price": 50000.0, "amount": None, "fee": 2.5, "pnl": None},
        {"side": "long", "price": 51000.0, "amount": None, "fee": 2.55,
         "pnl": 100.0, "entry_price": None},
    ])
    svc = MetricsService(engine, sid, initial_balance=10000.0)
    m = await svc.compute()
    assert m.total_trades == 0
    assert m.legacy_open_skipped == 1
    assert m.legacy_close_skipped == 1


@pytest.mark.asyncio
async def test_compute_profit_factor_none_on_zero_losses(engine):
    """spec §2 zero-denom: PF None when no losses."""
    from src.services.metrics import MetricsService
    sid = "pf-no-loss"
    await _setup_compute_session(engine, sid, fills=[
        {"side": "long", "price": 50000.0, "amount": 0.1, "fee": 2.5, "pnl": None},
        {"side": "long", "price": 51000.0, "amount": 0.1, "fee": 2.55,
         "pnl": 100.0, "entry_price": 50000.0},
    ])
    svc = MetricsService(engine, sid, initial_balance=10000.0)
    m = await svc.compute()
    assert m.profit_factor is None
    assert m.net_profit_factor is None


@pytest.mark.asyncio
async def test_compute_break_even_trade_excluded_from_losses(engine):
    """spec §3 single-source convention: break-even trade (pnl == 0) must NOT
    count as loss; aligns src with scripts/_sim_metrics (uses < 0 not <= 0).

    PR #57 review I-1 regression guard.

    Fixture:
      - Trade A: open @50000 → close @50000, gross_pnl=0 (price-flat), fees=5.0,
        net_pnl=-5.0 → gross break-even, net loss.
      - Trade B: open @50000 → close @51010 (gross=+101) with fees=5.05 → net=+95.95
        → gross win, net win.
    Expected: gross 1W/0L (NOT 1W/1L); avg_loss=0.0 (no gross losses).
              net 1W/1L (trade A net=-5 is a real loss).
    """
    from src.services.metrics import MetricsService
    sid = "break-even"
    await _setup_compute_session(engine, sid, fills=[
        # Trade A: gross break-even, net loss (open @50000 → close @50000)
        {"side": "long", "price": 50000.0, "amount": 0.1, "fee": 2.5, "pnl": None},
        {"side": "long", "price": 50000.0, "amount": 0.1, "fee": 2.5,
         "pnl": 0.0, "entry_price": 50000.0},
        # Trade B: clear gross + net win (open @50000 → close @51010)
        {"side": "long", "price": 50000.0, "amount": 0.1, "fee": 2.5, "pnl": None},
        {"side": "long", "price": 51010.0, "amount": 0.1, "fee": 2.55,
         "pnl": 101.0, "entry_price": 50000.0},
    ])
    svc = MetricsService(engine, sid, initial_balance=10000.0)
    m = await svc.compute()
    assert m.total_trades == 2
    # Gross side: trade A (pnl_gross=0) excluded; trade B (pnl_gross=+101) is win
    assert m.winning_trades == 1
    assert m.losing_trades == 0, (
        f"break-even gross trade must NOT count as loss; got {m.losing_trades}"
    )
    assert m.break_even_trades == 1
    # Partition completeness (PR #57 review M-6): W + L + B = total
    assert m.winning_trades + m.losing_trades + m.break_even_trades == m.total_trades
    assert m.avg_loss == 0.0, (
        f"avg_loss must be 0.0 (no gross losses); got {m.avg_loss}"
    )
    # Net side: trade A (pnl_net=-5) is real loss; trade B (pnl_net=+95.95) is win
    assert m.net_winning_trades == 1
    assert m.net_losing_trades == 1
    assert m.net_break_even_trades == 0
    assert m.net_winning_trades + m.net_losing_trades + m.net_break_even_trades == m.total_trades
    assert m.avg_loss_net == pytest.approx(-5.0)
