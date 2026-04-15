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
    # Peak at 100, then drops by 80 → 0.8% of 10000
    assert metrics.max_drawdown_pct == pytest.approx(0.8)
