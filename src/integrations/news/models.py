from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime
from typing import Literal

# Collapse any run of whitespace (including \n, \r, \t) into a single space,
# and drop the other C0/C1 control characters. Applied to free-form upstream
# strings (news titles, OKX announcement titles, FGI classification, etc.)
# so they don't accidentally break the section structure rendered to the LLM
# or — in the worst case — inject fake headers the Agent might trust.
_WHITESPACE_RUN = re.compile(r"\s+")
_CONTROL_CHARS = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")
_MAX_FREEFORM_LEN = 300


def _sanitize_freeform(text: str) -> str:
    # Idempotent over the empty string: "" → "" without the earlier
    # truthiness short-circuit (which also accepted None and lied about the
    # signature). Callers stay str in, str out.
    cleaned = _CONTROL_CHARS.sub("", text)
    cleaned = _WHITESPACE_RUN.sub(" ", cleaned).strip()
    if len(cleaned) > _MAX_FREEFORM_LEN:
        cleaned = cleaned[: _MAX_FREEFORM_LEN - 1].rstrip() + "…"
    return cleaned


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

    `title` and `content` are sanitized on construction: whitespace runs
    collapse to single spaces, control characters are stripped, and the
    result is capped at 300 chars. This protects the LLM-facing formatter
    from upstream strings that contain newlines or pathological lengths.
    """

    timestamp: datetime
    source: str  # "coindesk" / "alternative_me" / "okx_announcement" / "okx_status" / "forexfactory"
    category: str  # "news" / "fgi" / "announcement" / "maintenance" / "macro_event"
    importance: Literal["low", "medium", "high"]
    title: str
    content: str = ""
    url: str = ""
    symbols: list[str] = field(default_factory=list)

    def __post_init__(self) -> None:
        self.title = _sanitize_freeform(self.title)
        self.content = _sanitize_freeform(self.content)


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
