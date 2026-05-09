"""F7 — OKX REST OHLCV post-hoc helper for sim sessions.

See docs/superpowers/specs/2026-05-09-iter-w2r2-obs-followup-a-design.md
for full spec including resource contract, retry semantics, and AC list.
"""
from __future__ import annotations

import argparse
import asyncio
import re
from datetime import datetime, timezone
from pathlib import Path

import ccxt
import ccxt.async_support
import pandas as pd
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, create_async_engine
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


_RETRY_SLEEP_SCHEDULE: tuple[float, ...] = (1.0, 2.0)  # 2 sleeps; raise 后不再 sleep
_THROTTLE_SLEEP_S: float = 0.5
_PAGE_LIMIT: int = 100


async def _paginate_ohlcv(
    client, symbol: str, timeframe: str, start_ms: int, end_ms: int
) -> list[list]:
    """Paginate OKX REST fetch_ohlcv from start_ms forward until end_ms.

    spec §2.1: cursor advances by last-candle-ts + tf_ms (not blind 100×tf_ms).
    Termination: cursor >= end_ms OR empty page OR last_ts <= prev_last_ts.
    Retry: 3 attempts total, sleeps [1.0, 2.0], only NetworkError /
    ExchangeNotAvailable / TimeoutError; raise others immediately.
    """
    tf_ms = TF_MS[timeframe]
    cursor_ms = start_ms
    last_seen_ts: int | None = None
    rows: list[list] = []

    while cursor_ms < end_ms:
        page = await _fetch_with_retry(client, symbol, timeframe, cursor_ms)
        if not page:
            break
        page_last_ts = page[-1][0]
        if last_seen_ts is not None and page_last_ts <= last_seen_ts:
            break  # 末根不前进 → 防卡死
        rows.extend(page)
        last_seen_ts = page_last_ts
        cursor_ms = page_last_ts + tf_ms
        await asyncio.sleep(_THROTTLE_SLEEP_S)

    return rows


async def _fetch_with_retry(client, symbol: str, timeframe: str, since_ms: int) -> list[list]:
    """3 attempts total; sleep [1.0, 2.0] between failures; no tail sleep."""
    last_err: Exception | None = None
    for attempt in range(3):
        try:
            return await client.fetch_ohlcv(symbol, timeframe, since=since_ms, limit=_PAGE_LIMIT)
        except (ccxt.NetworkError, ccxt.ExchangeNotAvailable, asyncio.TimeoutError) as e:
            last_err = e
            if attempt < 2:  # 前 2 次失败 sleep；第 3 次失败直接 raise
                await asyncio.sleep(_RETRY_SLEEP_SCHEDULE[attempt])
    assert last_err is not None
    raise last_err


_DTYPE_SCHEMA = {
    "timestamp_ms": "int64",
    "datetime_iso": "object",
    "open": "float64",
    "high": "float64",
    "low": "float64",
    "close": "float64",
    "volume": "float64",
}


def _to_dataframe(rows: list[list]) -> pd.DataFrame:
    """Convert ccxt raw OHLCV rows to DataFrame; force §3.2 dtypes even when empty."""
    if not rows:
        # Build empty DataFrame with explicit dtypes (default empty df is 'object')
        return pd.DataFrame({col: pd.Series(dtype=dtype) for col, dtype in _DTYPE_SCHEMA.items()})

    records = [
        {
            "timestamp_ms": int(r[0]),
            "datetime_iso": datetime.fromtimestamp(r[0] / 1000, tz=timezone.utc).isoformat(),
            "open": float(r[1]),
            "high": float(r[2]),
            "low": float(r[3]),
            "close": float(r[4]),
            "volume": float(r[5]),
        }
        for r in rows
    ]
    df = pd.DataFrame(records)
    return df.astype(_DTYPE_SCHEMA)


async def fetch_session_ohlcv(
    session_id: str,
    timeframe: str = "1m",
    db_path: str = "data/tradebot.db",
    output_path: Path | None = None,
) -> pd.DataFrame:
    """Fetch OKX REST OHLCV for a sim session's [created_at, last_active_at) window.

    Returns: DataFrame；window 内 OKX 无数据时返回空（不抛）。完整 schema 与
             空返回契约见 spec §3.2 / §4 表。

    Raises: ValueError if session_id not found or window has zero duration.
            Re-raises ccxt errors after retry exhaustion (transient) or
            immediately (permanent — BadSymbol etc).
    """
    assert timeframe in TF_MS, f"unsupported timeframe: {timeframe}"

    engine = create_async_engine(f"sqlite+aiosqlite:///{db_path}")
    client = ccxt.async_support.okx()
    try:
        symbol, start_ms, end_ms = await _resolve_session(engine, session_id)
        rows = await _paginate_ohlcv(client, symbol, timeframe, start_ms, end_ms)
        # Sort + dedup (spec §2.1 step 6)
        rows.sort(key=lambda r: r[0])
        seen: set[int] = set()
        deduped: list[list] = []
        for r in rows:
            ts = r[0]
            if ts in seen:
                continue
            seen.add(ts)
            deduped.append(r)
        # Half-open filter [start_ms, end_ms)
        filtered = [r for r in deduped if start_ms <= r[0] < end_ms]
        df = _to_dataframe(filtered)
        if output_path is not None:
            _write_csv(df, output_path)
        return df
    finally:
        await client.close()
        await engine.dispose()


def _write_csv(df: pd.DataFrame, path: Path) -> None:
    """Write DataFrame to CSV; mkdir parents if missing; overwrite existing."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    df.to_csv(path, index=False)


def _sanitize_label(name: str) -> str:
    """spec §3.3: re.sub(r'[^\\w-]+', '_', name).strip('_')[:40]"""
    return re.sub(r"[^\w-]+", "_", name).strip("_")[:40]


def _sanitize_symbol(symbol: str) -> str:
    """spec §3.3: 'BTC/USDT:USDT' → 'BTC_USDT_USDT'."""
    return symbol.replace("/", "_").replace(":", "_")


def _build_default_output_path(
    session_id: str, name: str, symbol: str, timeframe: str
) -> Path:
    """spec §3.3 default: .working/ohlcv/<label>_<symbol_safe>_<tf>.csv"""
    label = _sanitize_label(name) or session_id[:8]
    symbol_safe = _sanitize_symbol(symbol)
    return Path(".working/ohlcv") / f"{label}_{symbol_safe}_{timeframe}.csv"


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Fetch OKX REST OHLCV for a sim session's window."
    )
    parser.add_argument("--session", required=True, help="session id (UUID)")
    parser.add_argument("--timeframe", default="1m", choices=list(TIMEFRAMES))
    parser.add_argument("--db", default="data/tradebot.db", dest="db_path",
                        help="SQLite DB path (default: data/tradebot.db)")
    parser.add_argument("--output", default=None, dest="output_path",
                        help="output CSV path (default: .working/ohlcv/<label>_<symbol>_<tf>.csv)")
    args = parser.parse_args()

    async def _run():
        # Resolve default output: need symbol + name from DB.
        # Use AsyncSession to get ORM object (engine.connect+scalar_one returns first column not entity).
        output_path = args.output_path
        if output_path is None:
            engine = create_async_engine(f"sqlite+aiosqlite:///{args.db_path}")
            try:
                async_session = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
                async with async_session() as db:
                    result = await db.execute(
                        select(SessionModel).where(SessionModel.id == args.session)
                    )
                    row = result.scalars().first()
                if row is None:
                    raise ValueError(f"session not found: {args.session}")
                output_path = _build_default_output_path(
                    args.session, row.name, row.symbol, args.timeframe
                )
            finally:
                await engine.dispose()
        await fetch_session_ohlcv(
            args.session, timeframe=args.timeframe,
            db_path=args.db_path, output_path=Path(output_path),
        )

    asyncio.run(_run())


if __name__ == "__main__":
    main()
