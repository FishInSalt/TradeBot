"""F7 — OKX REST OHLCV post-hoc helper for sim sessions.

See docs/superpowers/specs/2026-05-09-iter-w2r2-obs-followup-a-design.md
for full spec including resource contract, retry semantics, and AC list.

拉取核心已上提至 src/services/ohlcv_history.py（spec 2026-06-16 §A）；下方按旧私名
re-export 以保既有测试 import 不动。本脚本仅留 DataFrame / CSV / CLI。
"""
from __future__ import annotations

import argparse
import asyncio
import re
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.orm import sessionmaker

from src.storage.models import Session as SessionModel

# 旧私名 re-export：使 `from scripts.fetch_session_ohlcv import _resolve_session,
# _paginate_ohlcv, TF_MS, TIMEFRAMES` 的存量测试 import 完全不动。
# 其中 fetch_ohlcv_window 为本脚本主入口自用；_ensure_utc / _fetch_with_retry 无现有
# 测试直接 import，按防漂移原则一并 re-export（其行为由 test_ohlcv_history.py 直测）。
from src.services.ohlcv_history import (  # noqa: F401  (re-export)
    TF_MS,
    TIMEFRAMES,
    _ensure_utc,
    _fetch_with_retry,
    _paginate_ohlcv,
    fetch_ohlcv_window,
    resolve_session_window as _resolve_session,
)


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
    try:
        symbol, start_ms, end_ms = await _resolve_session(engine, session_id)
        # 排序 / 去重 / 半开过滤已在 fetch_ohlcv_window 内完成；client.close 也在其 finally。
        rows = await fetch_ohlcv_window(symbol, timeframe, start_ms, end_ms)
        df = _to_dataframe(rows)
        if output_path is not None:
            _write_csv(df, output_path)
        return df
    finally:
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
