# src/services/metrics.py
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
    avg_win: float = 0.0
    avg_loss: float = 0.0
    best_trade: float = 0.0
    worst_trade: float = 0.0
    recent_summary: str = ""
    total_fees: float = 0.0


class MetricsService:
    def __init__(
        self,
        engine: AsyncEngine,
        session_id: str,
        initial_balance: float = 10000.0,
    ):
        self._engine = engine
        self._session_id = session_id
        self._initial_balance = initial_balance

    async def compute(
        self,
        current_position: str = "none",
    ) -> PerformanceMetrics:
        # Query all fills (including opens with pnl=None) for fee totaling
        async with get_session(self._engine) as session:
            result = await session.execute(
                select(TradeAction)
                .where(TradeAction.session_id == self._session_id)
                .where(TradeAction.action == "order_filled")
                .order_by(TradeAction.created_at)
            )
            all_fills = result.scalars().all()

        # Total fees from ALL fills (open + close)
        total_fees = sum(f.fee for f in all_fills if f.fee is not None)

        # PnL trades: only fills with pnl (close fills)
        pnl_fills = [f for f in all_fills if f.pnl is not None]
        pnls: list[float] = [f.pnl for f in pnl_fills]

        if not pnls:
            return PerformanceMetrics(
                current_position=current_position,
                total_fees=total_fees,
            )

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

        # Recent summary: last N trades
        n = min(5, len(pnls))
        recent_pnls = pnls[-n:]
        recent_wins = sum(1 for p in recent_pnls if p > 0)
        recent_losses = n - recent_wins
        trade_word = "trade" if n == 1 else "trades"
        recent_summary = f"{recent_wins}W {recent_losses}L (last {n} {trade_word})"

        return PerformanceMetrics(
            total_return_pct=(total_pnl / self._initial_balance) * 100 if self._initial_balance > 0 else 0.0,
            total_pnl=total_pnl,
            win_rate=len(winning_pnls) / len(pnls),
            max_drawdown_pct=(max_dd / self._initial_balance) * 100 if self._initial_balance > 0 else 0.0,
            profit_factor=gross_profit / gross_loss if gross_loss > 0 else float("inf"),
            total_trades=len(pnls),
            winning_trades=len(winning_pnls),
            losing_trades=len(losing_pnls),
            current_position=current_position,
            avg_win=gross_profit / len(winning_pnls) if winning_pnls else 0.0,
            avg_loss=-gross_loss / len(losing_pnls) if losing_pnls else 0.0,
            best_trade=max(pnls),
            worst_trade=min(pnls),
            recent_summary=recent_summary,
            total_fees=total_fees,
        )
