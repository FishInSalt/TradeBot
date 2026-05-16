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


async def _add_fill(engine, pnl, trigger_reason="market", fee=0.5):
    async with get_session(engine) as session:
        session.add(TradeAction(
            session_id="test-session", action="order_filled",
            order_id=f"o-{pnl}", symbol="BTC/USDT:USDT", side="long",
            trigger_reason=trigger_reason, pnl=pnl, fee=fee,
            reasoning=f"(exchange: {trigger_reason} filled)",
        ))
        await session.commit()


async def _add_open_fill(engine, fee=0.5):
    """Add an open-position fill (pnl=None, has fee)."""
    async with get_session(engine) as session:
        session.add(TradeAction(
            session_id="test-session", action="order_filled",
            order_id="o-open", symbol="BTC/USDT:USDT", side="long",
            trigger_reason="market", pnl=None, fee=fee,
            reasoning="(exchange: market filled)",
        ))
        await session.commit()


async def test_compute_metrics(metrics_db):
    from src.services.metrics import MetricsService
    await _add_fill(metrics_db, 30.0, fee=0.5)
    await _add_fill(metrics_db, -15.0, fee=0.3)
    await _add_fill(metrics_db, 180.0, fee=0.8)

    service = MetricsService(engine=metrics_db, session_id="test-session", initial_balance=10000.0)
    metrics = await service.compute()
    assert metrics.total_trades == 3
    assert metrics.win_rate == pytest.approx(2 / 3, abs=0.01)
    assert metrics.total_pnl == pytest.approx(195.0)
    assert metrics.profit_factor > 1.0
    assert metrics.avg_win == pytest.approx(105.0)  # (30+180)/2
    assert metrics.avg_loss == pytest.approx(-15.0)
    assert metrics.best_trade == pytest.approx(180.0)
    assert metrics.worst_trade == pytest.approx(-15.0)
    assert metrics.total_fees == pytest.approx(1.6)  # 0.5+0.3+0.8


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
    await _add_fill(metrics_db, 30.0)
    await _add_fill(metrics_db, -10.0)
    await _add_fill(metrics_db, 50.0)
    await _add_fill(metrics_db, 20.0)

    service = MetricsService(engine=metrics_db, session_id="test-session", initial_balance=10000.0)
    metrics = await service.compute()
    assert "3W 1L" in metrics.recent_summary
    assert "last 4" in metrics.recent_summary


async def test_compute_metrics_total_fees_includes_opens(metrics_db):
    """total_fees includes open fills (pnl=None) that have fee."""
    from src.services.metrics import MetricsService
    await _add_open_fill(metrics_db, fee=0.5)
    await _add_fill(metrics_db, 30.0, fee=0.5)

    service = MetricsService(engine=metrics_db, session_id="test-session", initial_balance=10000.0)
    metrics = await service.compute()
    assert metrics.total_trades == 1  # only close fills count as trades
    assert metrics.total_fees == pytest.approx(1.0)  # both open + close fees


async def test_compute_metrics_max_drawdown(metrics_db):
    from src.services.metrics import MetricsService
    await _add_fill(metrics_db, 100.0, fee=0.0)
    await _add_fill(metrics_db, -50.0, fee=0.0)
    await _add_fill(metrics_db, -30.0, fee=0.0)
    await _add_fill(metrics_db, 200.0, fee=0.0)

    service = MetricsService(engine=metrics_db, session_id="test-session", initial_balance=10000.0)
    metrics = await service.compute()
    # Equity-peak-based (G-3): equity series [10100, 10050, 10020, 10220], peak=10100
    # at step 1; trough=10020 at step 3 → max_dd_ratio = 80/10100; pct ≈ 0.7921.
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
