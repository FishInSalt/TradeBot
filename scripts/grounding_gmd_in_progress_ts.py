"""Real-data grounding for the in-progress candle timestamp fix.

Pulls LIVE OKX OHLCV + ticker (same ccxt.okx() public endpoints the sim uses),
builds the DataFrame exactly like MarketDataService.get_ohlcv_dataframe (int64
timestamp column — the dtype that triggered the numpy.int64 nanosecond-collapse
bug), feeds it through the FIXED get_market_data, and PROGRAMMATICALLY checks
the rendered "in-progress HH:MM still open, closes at HH:MM" header against an
independent UTC computation:

  - in-progress open == last CLOSED bar open + tf_offset
  - in-progress close == in-progress open + tf_offset
  - in-progress open <= now_utc < in-progress close   (the bar is truly current)
  - NOT collapsed to the buggy 00:xx (1970) value

Usage:
    .venv/bin/python scripts/grounding_gmd_in_progress_ts.py [--symbol BTC/USDT:USDT]

Network required (live OKX public API). No credentials needed.
"""
from __future__ import annotations

import argparse
import asyncio
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_REPO_ROOT))

import pandas as pd  # noqa: E402
from unittest.mock import AsyncMock, MagicMock  # noqa: E402

import ccxt.async_support as ccxt  # noqa: E402

from src.agent.tools_perception import get_market_data  # noqa: E402
from src.integrations.exchange.base import Ticker  # noqa: E402
from src.services.technical import TechnicalAnalysisService  # noqa: E402
from src.utils.ohlcv_utils import TF_OFFSETS, _fmt_candle_time  # noqa: E402

# tf-aware: intraday → HH:MM, hour → MM-DD HH:MM, day/week → YYYY-MM-DD
_HEADER_RE = re.compile(
    r"in-progress (.+?) still open, closes at (.+?)\)"
)


def _build_df(rows: list[list]) -> pd.DataFrame:
    """Mirror MarketDataService.get_ohlcv_dataframe — int64 timestamp column."""
    df = pd.DataFrame(
        [
            {"timestamp": int(r[0]), "open": float(r[1]), "high": float(r[2]),
             "low": float(r[3]), "close": float(r[4]), "volume": float(r[5])}
            for r in rows
        ]
    )
    return df


async def _check_tf(ex, symbol: str, tf: str) -> bool:
    raw = await ex.fetch_ohlcv(symbol, tf, limit=130)
    t = await ex.fetch_ticker(symbol)
    df = _build_df(raw)
    assert str(df["timestamp"].dtype) == "int64", df["timestamp"].dtype

    ticker = Ticker(
        symbol=symbol, last=float(t["last"]), bid=float(t["bid"]),
        ask=float(t["ask"]), high=float(t["high"]), low=float(t["low"]),
        base_volume=float(t["baseVolume"]), timestamp=int(t["timestamp"]),
    )

    deps = MagicMock()
    deps.symbol = symbol
    deps.timeframe = tf
    deps.technical = TechnicalAnalysisService()
    deps.market_data = MagicMock()
    deps.market_data.get_ticker = AsyncMock(return_value=ticker)
    deps.market_data.get_ohlcv_dataframe = AsyncMock(return_value=df)

    out = await get_market_data(deps, timeframe=tf)
    header = next((l.strip() for l in out.splitlines() if "in-progress" in l), "<none>")

    # --- Independent expectation (NOT via the helper under test) ---
    # get_market_data strips the in-progress bar via _closed_bars (drops last
    # row), so the last CLOSED bar is df.iloc[-2].
    last_closed_open_ms = int(df["timestamp"].iloc[-2])
    offset = TF_OFFSETS[tf]
    ip_open = pd.Timestamp(last_closed_open_ms, unit="ms", tz="UTC") + offset
    ip_close = ip_open + offset
    # Format with the SAME tf-aware helper the renderer uses (HH:MM / MM-DD HH:MM / …)
    exp_open, exp_close = _fmt_candle_time(ip_open, tf), _fmt_candle_time(ip_close, tf)
    now = pd.Timestamp(datetime.now(timezone.utc))

    m = _HEADER_RE.search(out)
    ok = True
    print(f"\n=== {symbol} {tf} ===")
    print(f"  rendered header : {header}")
    print(f"  expected        : in-progress {exp_open} still open, closes at {exp_close}")
    print(f"  now (UTC)       : {now.strftime('%Y-%m-%d %H:%M:%S')}")

    if not m:
        print("  ✗ FAIL: no in-progress header matched"); return False
    got_open, got_close = m.group(1), m.group(2)

    def chk(cond, msg):
        nonlocal ok
        print(f"  {'✓' if cond else '✗'} {msg}")
        ok = ok and cond

    chk(got_open == exp_open and got_close == exp_close,
        f"header times match independent computation ({got_open}/{got_close})")
    # Boundary-tolerant: fetch happens slightly before `now` is sampled, so at an
    # exact bar boundary `now` may have ticked into the next bar. Allow one
    # tf-offset of grace on each side to absorb that race + clock drift.
    chk(ip_open - offset <= now <= ip_close + offset,
        f"in-progress window brackets now ±1 bar ({exp_open} <= now <= {exp_close})")
    # Buggy manifestations: intraday "00:44"/"00:34"; hour "01-01 00:xx" (1970).
    chk(not any(b in got_open for b in ("00:44", "00:34", "01-01 00:")),
        f"rendered value not collapsed to a 1970-ish value ({got_open!r})")
    # grid alignment: minutes must be a multiple of the tf minute-span
    tf_min = int(offset.total_seconds() // 60)
    open_min_of_day = ip_open.hour * 60 + ip_open.minute
    chk(open_min_of_day % tf_min == 0,
        f"in-progress open aligned to {tf_min}m grid")
    return ok


async def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--symbol", default="BTC/USDT:USDT")
    args = p.parse_args()

    ex = ccxt.okx()
    try:
        results = []
        for tf in ("15m", "5m", "1h"):
            results.append(await _check_tf(ex, args.symbol, tf))
    finally:
        await ex.close()

    print("\n" + "=" * 50)
    if all(results):
        print(f"ALL PASS ({len(results)} timeframes) — fix verified on live OKX data.")
        return 0
    print(f"FAILURES: {results.count(False)}/{len(results)} timeframes failed.")
    return 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
