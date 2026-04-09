from src.services.metrics import PerformanceMetrics


def test_format_metrics():
    from src.cli.display import format_metrics

    metrics = PerformanceMetrics(
        total_return_pct=12.5,
        total_pnl=1250.0,
        win_rate=0.65,
        max_drawdown_pct=4.2,
        profit_factor=1.8,
        total_trades=23,
        winning_trades=15,
        losing_trades=8,
        current_position="long",
    )
    output = format_metrics(metrics)
    assert "12.5" in output
    assert "65" in output
    assert "23" in output
    assert "LONG" in output.upper()
