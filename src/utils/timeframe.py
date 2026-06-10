"""Free-form timeframe canonicalization to the project's lowercase ccxt form.

The project convention (see src/integrations/exchange/base.py) exposes
timeframes lowercase across all abstractions, matching ccxt's case-sensitive
`parse_timeframe` which only accepts lowercase h/m/d/w/s units (and uppercase M
for month, Y for year). An uppercase hour/day/week — e.g. "1H", "4H", "1D" — is
a natural notation (TradingView convention) but ccxt rejects it with
"timeframe unit H is not supported".

`normalize_timeframe` folds the unambiguous unit letters (H/D/W/Y → h/d/w/y) to
the canonical lowercase form while preserving the minute (lowercase m) vs month
(uppercase M) distinction, and raises ValueError for anything not in the
project's supported set. It is the single normalization point shared by config
loading, session creation, and the agent-facing market-data tools.
"""
from __future__ import annotations

import re

# Canonical supported timeframes. Kept in lockstep with
# src.utils.ohlcv_utils.TF_OFFSETS (drift-guarded by test). Lowercase units
# except month "1M" (uppercase M = month, distinct from minute lowercase m).
SUPPORTED_TIMEFRAMES: frozenset[str] = frozenset({
    "1m", "3m", "5m", "15m", "30m",
    "1h", "2h", "4h", "6h", "8h", "12h",
    "1d", "3d", "1w", "1M",
})

# Unambiguous unit letters that may be case-folded to canonical lowercase. Only
# the units that actually appear (uppercase) in SUPPORTED_TIMEFRAMES are listed
# (hour/day/week). 'm'/'M' is DELIBERATELY excluded: lowercase m = minute,
# uppercase M = month — folding would silently change the timeframe's meaning.
_UNAMBIGUOUS_UNIT_FOLD = {"H": "h", "D": "d", "W": "w"}

_TF_RE = re.compile(r"^(\d+)([a-zA-Z])$")


def normalize_timeframe(tf: str) -> str:
    """Return the canonical lowercase ccxt timeframe for ``tf``.

    Folds unambiguous uppercase units (H/D/W → h/d/w) but preserves the
    minute/month distinction (lowercase m vs uppercase M). Raises ValueError
    for any value not in SUPPORTED_TIMEFRAMES after folding (including non-str
    input — never lets a raw TypeError escape).
    """
    if not isinstance(tf, str):
        raise ValueError(f"Unsupported timeframe {tf!r}: expected a string.")
    s = tf.strip()
    if s in SUPPORTED_TIMEFRAMES:
        return s

    match = _TF_RE.match(s)
    if match is not None:
        amount, unit = match.group(1), match.group(2)
        folded_unit = _UNAMBIGUOUS_UNIT_FOLD.get(unit, unit)
        candidate = f"{amount}{folded_unit}"
        if candidate in SUPPORTED_TIMEFRAMES:
            return candidate

    raise ValueError(
        f"Unsupported timeframe {tf!r}. Supported: "
        f"{', '.join(sorted(SUPPORTED_TIMEFRAMES))} "
        f"(lowercase units; uppercase M = month, lowercase m = minute)."
    )
