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


async def _add_fill(engine, pnl, trigger_reason="market"):
    async with get_session(engine) as session:
        session.add(TradeAction(
            session_id="test-session", action="order_filled",
            order_id=f"o-{pnl}", symbol="BTC/USDT:USDT", side="long",
            trigger_reason=trigger_reason, pnl=pnl,
            reasoning=f"(exchange: {trigger_reason} filled)",
        ))
        await session.commit()


async def test_compute_metrics(metrics_db):
    from src.services.metrics import MetricsService
    await _add_fill(metrics_db, 30.0)
    await _add_fill(metrics_db, -15.0)
    await _add_fill(metrics_db, 180.0)

    service = MetricsService(initial_balance=10000.0)
    metrics = await service.compute(metrics_db, "test-session")
    assert metrics.total_trades == 3
    assert metrics.win_rate == pytest.approx(2 / 3, abs=0.01)
    assert metrics.total_pnl == pytest.approx(195.0)
    assert metrics.profit_factor > 1.0


async def test_compute_metrics_empty(metrics_db):
    from src.services.metrics import MetricsService
    service = MetricsService(initial_balance=10000.0)
    metrics = await service.compute(metrics_db, "test-session")
    assert metrics.total_trades == 0
    assert metrics.win_rate == 0.0


async def test_compute_metrics_with_position(metrics_db):
    from src.services.metrics import MetricsService
    service = MetricsService(initial_balance=10000.0)
    metrics = await service.compute(metrics_db, "test-session", current_position="long 0.001")
    assert metrics.current_position == "long 0.001"
