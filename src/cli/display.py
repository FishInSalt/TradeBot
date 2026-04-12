from __future__ import annotations

from rich.panel import Panel

from src.services.metrics import PerformanceMetrics


def format_metrics(metrics: PerformanceMetrics) -> str:
    pos = metrics.current_position.upper() if metrics.current_position != "none" else "FLAT"
    return (
        f"Return: {metrics.total_return_pct:+.2f}% ({metrics.total_pnl:+.2f} USDT)\n"
        f"Win Rate: {metrics.win_rate * 100:.1f}% ({metrics.winning_trades}W / {metrics.losing_trades}L)\n"
        f"Max Drawdown: -{metrics.max_drawdown_pct:.2f}%\n"
        f"Profit Factor: {metrics.profit_factor:.2f}\n"
        f"Total Trades: {metrics.total_trades}\n"
        f"Position: {pos}"
    )


def display_metrics(metrics: PerformanceMetrics, console) -> None:
    color = "green" if metrics.total_pnl >= 0 else "red"
    console.print(Panel(format_metrics(metrics), title="[bold]Performance[/]", border_style=color))
