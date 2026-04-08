import pytest
from datetime import datetime, timezone
from src.storage.models import TradeRecord


def _trade(pnl: float) -> TradeRecord:
    return TradeRecord(
        id=0, symbol="BTC/USDT:USDT", side="long", entry_price=65000.0,
        exit_price=65000.0 + pnl * 100, quantity=0.01, leverage=3,
        status="closed", pnl=pnl,
        created_at=datetime(2026, 4, 1, tzinfo=timezone.utc),
        closed_at=datetime(2026, 4, 1, 4, tzinfo=timezone.utc),
    )


def test_compute_metrics():
    from src.services.metrics import MetricsService
    service = MetricsService(initial_balance=10000.0)
    trades = [_trade(30.0), _trade(-15.0), _trade(180.0)]
    metrics = service.compute_from_trades(trades)
    assert metrics.total_trades == 3
    assert metrics.win_rate == pytest.approx(2 / 3, abs=0.01)
    assert metrics.total_pnl == pytest.approx(195.0)
    assert metrics.profit_factor > 1.0


def test_compute_metrics_empty():
    from src.services.metrics import MetricsService
    service = MetricsService(initial_balance=10000.0)
    metrics = service.compute_from_trades([])
    assert metrics.total_trades == 0
    assert metrics.win_rate == 0.0
