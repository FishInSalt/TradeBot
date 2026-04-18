"""Data models for crypto spot ETF flow tracking."""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class ETFFlowEntry:
    """Single-day ETF flow entry.

    `net_inflow_usd` is computed from cum_net_inflow deltas (spec §5.3),
    NOT directly from SoSoValue's `total_net_inflow` field — the latter
    can differ across multi-row same-date responses.
    """
    date: str                # ISO "YYYY-MM-DD"
    net_inflow_usd: float    # signed; negative on net outflow days
    cumulative_usd: float    # cum_net_inflow at end of `date`
    aum_usd: float           # total_net_assets at end of `date`
