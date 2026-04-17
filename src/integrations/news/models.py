from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Literal


@dataclass
class InformationEvent:
    """Unified data model for all market intelligence events.

    The `content` field is source-specific free-form metadata used by the
    formatter for that section. Conventions:
      - coindesk       → original media name (e.g. "CoinTelegraph")
      - alternative_me → classification string (e.g. "Extreme Fear")
      - forexfactory   → "Previous: X | Forecast: Y" for macro events
      - okx_announcement / okx_status → unused (empty string)

    Each tool section formats events from a single source, so the per-source
    convention is safe in practice. If a new tool ever renders mixed sources,
    add a dedicated field rather than overloading `content` further.
    """

    timestamp: datetime
    source: str  # "coindesk" / "alternative_me" / "okx_announcement" / "okx_status" / "forexfactory"
    category: str  # "news" / "fgi" / "announcement" / "maintenance" / "macro_event"
    importance: Literal["low", "medium", "high"]
    title: str
    content: str = ""
    url: str = ""
    symbols: list[str] = field(default_factory=list)


def extract_base_currency(symbol: str) -> str:
    """Extract base currency for matching against CoinDesk CATEGORY_DATA.

    Strips OKX multiplier prefixes (1000PEPE → PEPE, kSHIB → SHIB) so those
    symbols aren't silently excluded from symbol-specific news.

    BTC/USDT:USDT      → BTC
    ETH/USDT:USDT      → ETH
    1000PEPE/USDT:USDT → PEPE
    kSHIB/USDT:USDT    → SHIB
    """
    base = symbol.split("/")[0]
    for prefix in ("1000", "k"):
        if base.startswith(prefix):
            remainder = base[len(prefix):]
            if remainder and remainder.isalpha():
                return remainder
    return base
