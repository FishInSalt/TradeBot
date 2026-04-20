# src/services/metrics.py
from __future__ import annotations

import statistics
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncEngine

from src.storage.database import get_session
from src.storage.models import ToolCall, TradeAction


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


@dataclass
class ToolCallStats:
    count: int                            # count >= 1 (zero-call tools not in dict)
    ok_count: int
    error_count: int
    error_rate: float                     # 0..1 ratio; script layer multiplies by 100 for %
    p50_duration_ms: int
    p95_duration_ms: int
    error_breakdown: dict[str, int]       # {"TimeoutError": 3, ...}
    last_called_at: datetime              # MAX(created_at); always has value for tools in dict


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
        losing_pnls = [p for p in pnls if p <= 0]  # breakeven (0.0) counted as loss
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

    async def get_tool_call_summary(
        self,
        session_id: str | None = None,
        since: timedelta | None = None,
        tool_name: str | None = None,
    ) -> dict[str, ToolCallStats]:
        """聚合 tool_calls 按 tool_name。零调用工具不入 dict。

        Args:
            session_id: None = 跨所有 session 聚合；否则限定该 session
            since: None = 全部历史；否则限定 created_at > now - since
            tool_name: None = 所有工具；否则只返回该工具

        Returns:
            {tool_name: ToolCallStats}; ToolCallStats.count >= 1 by contract.
        """
        stmt = select(ToolCall)
        if session_id is not None:
            stmt = stmt.where(ToolCall.session_id == session_id)
        if since is not None:
            cutoff = datetime.now(timezone.utc) - since
            stmt = stmt.where(ToolCall.created_at > cutoff)
        if tool_name is not None:
            stmt = stmt.where(ToolCall.tool_name == tool_name)

        async with get_session(self._engine) as db:
            rows = (await db.execute(stmt)).scalars().all()

        # Group in-memory by tool_name
        by_tool: dict[str, list[ToolCall]] = {}
        for row in rows:
            by_tool.setdefault(row.tool_name, []).append(row)

        result: dict[str, ToolCallStats] = {}
        for name, tool_rows in by_tool.items():
            count = len(tool_rows)
            ok_count = sum(1 for r in tool_rows if r.status == "ok")
            error_count = count - ok_count
            durations = [r.duration_ms for r in tool_rows]
            # Python 3.13: quantiles handles N=1 (returns repeated single value).
            # `method='inclusive'` keeps p50/p95 bounded by sample max (see spec §4.2).
            q = statistics.quantiles(durations, n=100, method="inclusive")
            p50 = int(q[49])      # index 49 = 50th percentile; int() truncates per spec §4.2
            p95 = int(q[94])      # index 94 = 95th percentile
            error_breakdown: dict[str, int] = {}
            for r in tool_rows:
                if r.error_type is not None:
                    error_breakdown[r.error_type] = error_breakdown.get(r.error_type, 0) + 1
            last_called = max(r.created_at for r in tool_rows)

            result[name] = ToolCallStats(
                count=count,
                ok_count=ok_count,
                error_count=error_count,
                error_rate=error_count / count,
                p50_duration_ms=p50,
                p95_duration_ms=p95,
                error_breakdown=error_breakdown,
                last_called_at=last_called,
            )

        return result
