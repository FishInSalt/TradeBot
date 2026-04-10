from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncEngine

from src.storage.database import get_session
from src.storage.models import TradeAction


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

    async def compute(
        self,
        engine: AsyncEngine,
        session_id: str,
        current_position: str = "none",
    ) -> PerformanceMetrics:
        async with get_session(engine) as session:
            result = await session.execute(
                select(TradeAction)
                .where(TradeAction.session_id == session_id)
                .where(TradeAction.action == "order_filled")
                .where(TradeAction.pnl.isnot(None))
                .order_by(TradeAction.created_at)
            )
            fills = result.scalars().all()

        pnls: list[float] = [f.pnl for f in fills]
        if not pnls:
            return PerformanceMetrics(current_position=current_position)

        total_pnl = sum(pnls)
        winning_pnls = [p for p in pnls if p > 0]
        losing_pnls = [p for p in pnls if p <= 0]
        gross_profit = sum(winning_pnls) if winning_pnls else 0.0
        gross_loss = abs(sum(losing_pnls)) if losing_pnls else 0.0

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
            current_position=current_position,
        )
