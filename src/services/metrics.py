from __future__ import annotations

from dataclasses import dataclass

from src.storage.models import TradeRecord


@dataclass
class PerformanceMetrics:
    total_return_pct: float = 0.0
    total_pnl: float = 0.0
    win_rate: float = 0.0
    max_drawdown_pct: float = 0.0
    profit_factor: float = 0.0
    total_trades: int = 0
    winning_trades: int = 0
    losing_trades: int = 0
    current_position: str = "none"


class MetricsService:
    def __init__(self, initial_balance: float = 10000.0):
        self._initial_balance = initial_balance

    def compute_from_trades(self, trades: list[TradeRecord]) -> PerformanceMetrics:
        # Extract closed trades with known PnL as float list
        pnls: list[float] = [
            t.pnl for t in trades if t.status == "closed" and t.pnl is not None
        ]
        if not pnls:
            return PerformanceMetrics()

        total_pnl = sum(pnls)
        winning_pnls = [p for p in pnls if p > 0]
        losing_pnls = [p for p in pnls if p <= 0]
        gross_profit = sum(winning_pnls) if winning_pnls else 0.0
        gross_loss = abs(sum(losing_pnls)) if losing_pnls else 0.0

        # Max drawdown
        cumulative = 0.0
        peak = 0.0
        max_dd = 0.0
        for p in pnls:
            cumulative += p
            if cumulative > peak:
                peak = cumulative
            dd = peak - cumulative
            if dd > max_dd:
                max_dd = dd

        return PerformanceMetrics(
            total_return_pct=(total_pnl / self._initial_balance) * 100,
            total_pnl=total_pnl,
            win_rate=len(winning_pnls) / len(pnls),
            max_drawdown_pct=(max_dd / self._initial_balance) * 100,
            profit_factor=gross_profit / gross_loss if gross_loss > 0 else float("inf"),
            total_trades=len(pnls),
            winning_trades=len(winning_pnls),
            losing_trades=len(losing_pnls),
        )
