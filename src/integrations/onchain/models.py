"""Data models for stablecoin supply data."""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class StablecoinSnapshot:
    """Single-stablecoin supply snapshot with 7d change.

    change_7d_pct is None when prev_week == 0 (no baseline to compute %
    against). Rendering layer must condition on None and emit
    'N/A (no prior-week data)' rather than formatting None into a % spec.
    """
    symbol: str                    # "USDT" / "USDC"
    circulating_usd: float
    change_7d_usd: float
    change_7d_pct: float | None


@dataclass(frozen=True)
class StablecoinTotal:
    """Aggregate total across tracked stablecoins.

    total_change_7d_pct is None when total_prev == 0 (all tracked symbols
    missing prior-week data). See StablecoinSnapshot for rendering rule.
    """
    total_circulating_usd: float
    total_change_7d_usd: float
    total_change_7d_pct: float | None
