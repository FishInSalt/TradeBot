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
        closed = [t for t in trades if t.status == "closed" and t.pnl is not None]
        if not closed:
            return PerformanceMetrics()
        total_pnl = sum(t.pnl for t in closed)
        winners = [t for t in closed if t.pnl > 0]
        losers = [t for t in closed if t.pnl <= 0]
        gross_profit = sum(t.pnl for t in winners) if winners else 0.0
        gross_loss = abs(sum(t.pnl for t in losers)) if losers else 0.0
        cumulative = 0.0
        peak = 0.0
        max_dd = 0.0
        for t in closed:
            cumulative += t.pnl
            if cumulative > peak:
                peak = cumulative
            dd = peak - cumulative
            if dd > max_dd:
                max_dd = dd
        return PerformanceMetrics(
            total_return_pct=(total_pnl / self._initial_balance) * 100,
            total_pnl=total_pnl,
            win_rate=len(winners) / len(closed),
            max_drawdown_pct=(max_dd / self._initial_balance) * 100,
            profit_factor=gross_profit / gross_loss if gross_loss > 0 else float("inf"),
            total_trades=len(closed),
            winning_trades=len(winners),
            losing_trades=len(losers),
        )
