# src/services/metrics.py
from __future__ import annotations

import logging
import statistics
from collections import deque
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncEngine

from src.storage.database import get_session
from src.storage.models import ToolCall, TradeAction

logger = logging.getLogger(__name__)


@dataclass
class PerformanceMetrics:
    # Gross metrics (existing — per-lot-pair semantics shift per spec §0)
    total_return_pct: float = 0.0
    total_pnl: float = 0.0
    win_rate: float = 0.0
    max_drawdown_pct: float = 0.0
    profit_factor: float | None = None  # zero-denom → None per spec §2
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
    # Net metrics (iter-tool-opt-net-pnl-metrics — per spec §C3)
    net_pnl: float = 0.0
    net_profit_factor: float | None = None
    net_win_rate: float = 0.0
    avg_win_net: float = 0.0
    avg_loss_net: float = 0.0
    best_trade_net: float = 0.0
    worst_trade_net: float = 0.0
    net_winning_trades: int = 0
    net_losing_trades: int = 0
    # Caveats (per spec §6.2)
    legacy_open_skipped: int = 0
    legacy_close_skipped: int = 0
    missing_close_entry_price_count: int = 0
    invariant_violations: int = 0


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


@dataclass
class _Lot:
    """In-memory FIFO lot (spec §5.2; mirrors scripts/_sim_metrics._Lot subset)."""
    side: str
    entry_px: float
    original_amount: float
    remaining_amount: float
    open_fee: float


@dataclass
class _Roundtrip:
    """Lot pair result (spec §5.2; mirrors scripts/_sim_metrics.Roundtrip subset)."""
    side: str
    entry_px: float
    exit_px: float
    amount: float
    pnl_gross: float
    fee_open_share: float
    fee_close_share: float
    pnl_net: float
    is_liquidation: bool


_EPS = 1e-9


async def _collect_roundtrips_from_trade_actions(
    engine: AsyncEngine,
    session_id: str,
) -> tuple[list[_Roundtrip], dict[str, int]]:
    """FIFO lot pairing from trade_actions (spec §5.2).

    Reads trade_actions for the session, reconstructs FIFO lot queue from
    open fills (pnl IS NULL), pairs against close fills (pnl IS NOT NULL).
    Uses lot.open_fee + close.fee directly (no fee_rate dependency).

    Returns (roundtrips, caveats). Caveats keys: legacy_open_skipped,
    legacy_close_skipped, missing_close_entry_price_count, invariant_violations.
    """
    async with get_session(engine) as session:
        result = await session.execute(
            select(TradeAction)
            .where(TradeAction.session_id == session_id)
            .where(TradeAction.action == "order_filled")
            .order_by(TradeAction.created_at, TradeAction.id)
        )
        fills = list(result.scalars().all())

    lots: dict[str, deque[_Lot]] = {"long": deque(), "short": deque()}
    roundtrips: list[_Roundtrip] = []
    caveats = {
        "legacy_open_skipped": 0,
        "legacy_close_skipped": 0,
        "missing_close_entry_price_count": 0,
        "invariant_violations": 0,
    }

    for fill in fills:
        # OPEN vs CLOSE discriminator (spec §5.2): pnl IS NULL → open
        if fill.pnl is None:
            if fill.amount is None:
                caveats["legacy_open_skipped"] += 1
                logger.warning("metrics FIFO: legacy open fill id=%s amount IS NULL, skipping", fill.id)
                continue
            if fill.amount <= 0 or fill.price <= 0:
                logger.error("metrics FIFO: open fill id=%s corrupt amount=%s or price=%s",
                             fill.id, fill.amount, fill.price)
                caveats["invariant_violations"] += 1
                continue
            lots[fill.side].append(_Lot(
                side=fill.side, entry_px=fill.price,
                original_amount=fill.amount, remaining_amount=fill.amount,
                open_fee=fill.fee or 0.0,
            ))
            continue

        # CLOSE fill
        if fill.amount is None:
            caveats["legacy_close_skipped"] += 1
            logger.warning("metrics FIFO: legacy close fill id=%s amount IS NULL, skipping", fill.id)
            continue
        if fill.amount <= 0:
            logger.error("metrics FIFO: close fill id=%s amount %s <= 0 (corrupt data), skipping", fill.id, fill.amount)
            caveats["invariant_violations"] += 1
            continue
        if fill.entry_price is None:
            caveats["missing_close_entry_price_count"] += 1
            # NOT skip — FIFO uses lot.entry_px from open fill (spec §6.2 b)

        is_liquidation = fill.trigger_reason == "liquidation"
        liq_pnl_per_unit: float | None = None
        if is_liquidation:
            if fill.pnl is None or fill.amount <= 0:
                caveats["invariant_violations"] += 1
                logger.error("metrics FIFO: liquidation id=%s missing pnl or zero amount", fill.id)
                liq_pnl_per_unit = 0.0
            else:
                liq_pnl_per_unit = fill.pnl / fill.amount

        close_remaining = fill.amount
        close_fee_total = fill.fee or 0.0
        while close_remaining > _EPS:
            if not lots[fill.side]:
                caveats["invariant_violations"] += 1
                logger.error(
                    "metrics FIFO: close fill id=%s no preceding open lot for side=%s",
                    fill.id, fill.side,
                )
                break
            lot = lots[fill.side][0]
            consumed = min(lot.remaining_amount, close_remaining)
            fee_open_share = lot.open_fee * (consumed / lot.original_amount)
            fee_close_share = close_fee_total * (consumed / fill.amount)
            sign = 1.0 if fill.side == "long" else -1.0
            if is_liquidation:
                pnl_gross = (liq_pnl_per_unit or 0.0) * consumed
            else:
                pnl_gross = (fill.price - lot.entry_px) * consumed * sign
            pnl_net = pnl_gross - fee_open_share - fee_close_share
            roundtrips.append(_Roundtrip(
                side=lot.side, entry_px=lot.entry_px, exit_px=fill.price,
                amount=consumed,
                pnl_gross=pnl_gross,
                fee_open_share=fee_open_share, fee_close_share=fee_close_share,
                pnl_net=pnl_net,
                is_liquidation=is_liquidation,
            ))
            lot.remaining_amount -= consumed
            close_remaining -= consumed
            if lot.remaining_amount <= _EPS:
                lots[fill.side].popleft()

    return roundtrips, caveats


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

        # Max drawdown — equity-peak-based (matches scripts/_sim_metrics.max_drawdown_pct
        # algorithm; G-calc-rigor-audit §G-3). equity_t = initial_balance + cumulative PnL;
        # peak_t = running max; dd_t = (peak_t - equity_t) / peak_t.
        equity = self._initial_balance
        peak_equity = self._initial_balance
        max_dd_ratio = 0.0
        for p in pnls:
            equity += p
            peak_equity = max(peak_equity, equity)
            if peak_equity > 0:
                max_dd_ratio = max(max_dd_ratio, (peak_equity - equity) / peak_equity)

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
            max_drawdown_pct=max_dd_ratio * 100.0,
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
