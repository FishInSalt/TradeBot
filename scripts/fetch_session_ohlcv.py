"""F7 — OKX REST OHLCV post-hoc helper for sim sessions.

See docs/superpowers/specs/2026-05-09-iter-w2r2-obs-followup-a-design.md
for full spec including resource contract, retry semantics, and AC list.
"""
from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession
from sqlalchemy.orm import sessionmaker

from src.storage.models import Session as SessionModel


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


def _ensure_utc(dt: datetime) -> datetime:
    """aiosqlite 读出的 DateTime(timezone=True) 是 naive；显式补 UTC tzinfo
    与 scripts/_sim_metrics.py:33-34 / :727-728 既有 pattern 一致。"""
    return dt.replace(tzinfo=timezone.utc) if dt.tzinfo is None else dt


async def _resolve_session(engine: AsyncEngine, session_id: str) -> tuple[str, int, int]:
    """Look up sim session; return (symbol, start_ms, end_ms) for [created_at, last_active_at).

    Raises ValueError if session_id not found, or if the resulting window has
    zero duration. last_active_at falls back to updated_at (schema NOT NULL,
    always populated by ORM default+onupdate); spec §4.

    Uses AsyncSession (parallel to scripts/analyze_sim.py:55-71) — engine.connect()
    + scalar_one_or_none returns first column not ORM object.
    """
    async_session = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with async_session() as db:
        result = await db.execute(select(SessionModel).where(SessionModel.id == session_id))
        row = result.scalars().first()
    if row is None:
        raise ValueError(f"session not found: {session_id}")

    end_dt = row.last_active_at if row.last_active_at is not None else row.updated_at
    start_ms = int(_ensure_utc(row.created_at).timestamp() * 1000)
    end_ms = int(_ensure_utc(end_dt).timestamp() * 1000)
    if end_ms <= start_ms:
        raise ValueError(f"session has zero duration: {session_id}")
    return row.symbol, start_ms, end_ms
