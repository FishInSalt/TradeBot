"""Data models for stablecoin supply data."""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class StablecoinSnapshot:
    """Single-stablecoin supply snapshot with 7d change."""
    symbol: str                    # "USDT" / "USDC"
    circulating_usd: float
    change_7d_usd: float
    change_7d_pct: float


@dataclass(frozen=True)
class StablecoinTotal:
    """Aggregate total across tracked stablecoins."""
    total_circulating_usd: float
    total_change_7d_usd: float
    total_change_7d_pct: float
