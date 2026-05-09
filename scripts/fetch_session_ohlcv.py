"""F7 — OKX REST OHLCV post-hoc helper for sim sessions.

See docs/superpowers/specs/2026-05-09-iter-w2r2-obs-followup-a-design.md
for full spec including resource contract, retry semantics, and AC list.
"""
from __future__ import annotations


# spec §2.2 + §3.4：硬编码 dict 不走 ccxt.parse_timeframe；
# AC-F7-4 drift guard 锁定这些数值。
TF_MS: dict[str, int] = {
    "1m": 60_000,
    "5m": 300_000,
    "15m": 900_000,
    "1h": 3_600_000,
    "4h": 14_400_000,
    "1d": 86_400_000,
}

TIMEFRAMES: tuple[str, ...] = ("1m", "5m", "15m", "1h", "4h", "1d")
