"""Iter 6 diagnostic — verify OKX demo ticker data source.

Compares:
  1. fetch_ticker (ccxt unified, public endpoint)
  2. fetch_funding_rate.mark_price (private public endpoint)
  3. _client.public_get_market_ticker (raw OKX endpoint)
  4. Two consecutive fetch_ticker calls (lag/drift check)
  5. fetch_positions → mark_price (if position exists)

Helps determine whether 51280 race is:
  (a) ticker data source mismatch (demo vs mainnet)
  (b) demo ticker has refresh lag vs trade engine
  (c) trigger validation uses last price; demo mark/last drift offers buffer (see project memory okx-demo-mark-vs-last-drift)
"""
from __future__ import annotations

import asyncio
import json
import os
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from dotenv import load_dotenv

from src.integrations.exchange.okx import OKXExchange


SYMBOL = "BTC/USDT:USDT"


async def main():
    load_dotenv(".env")
    if os.environ.get("OKX_SANDBOX", "").lower() != "true":
        print("ABORT: OKX_SANDBOX must be 'true'")
        return 1

    ex = OKXExchange(
        api_key=os.environ["OKX_DEMO_API_KEY"],
        secret=os.environ["OKX_DEMO_SECRET"],
        password=os.environ["OKX_DEMO_PASSWORD"],
        symbol=SYMBOL,
        sandbox=True,
    )

    try:
        await ex.start()
        print(f"\n=== Headers verification ===")
        print(f"x-simulated-trading header: {ex._client.headers.get('x-simulated-trading', 'NOT SET')}")

        print(f"\n=== Test 1: fetch_ticker twice (drift check) ===")
        t0 = time.monotonic()
        ticker1 = await ex.fetch_ticker(SYMBOL)
        t1 = time.monotonic()
        ticker2 = await ex.fetch_ticker(SYMBOL)
        t2 = time.monotonic()
        print(f"  ticker1: last={ticker1.last:.2f} bid={ticker1.bid:.2f} ask={ticker1.ask:.2f} (took {(t1-t0)*1000:.0f}ms)")
        print(f"  ticker2: last={ticker2.last:.2f} bid={ticker2.bid:.2f} ask={ticker2.ask:.2f} (took {(t2-t1)*1000:.0f}ms)")
        drift = abs(ticker2.last - ticker1.last) / ticker1.last * 100
        print(f"  drift between calls: {drift:.4f}% (over {(t2-t0)*1000:.0f}ms total)")

        print(f"\n=== Test 2: raw OKX public ticker endpoint ===")
        raw = await ex._client.public_get_market_ticker({"instId": "BTC-USDT-SWAP"})
        if raw.get("data"):
            d = raw["data"][0]
            print(f"  raw last: {d.get('last')}")
            print(f"  raw bidPx: {d.get('bidPx')}")
            print(f"  raw askPx: {d.get('askPx')}")
            print(f"  raw ts: {d.get('ts')}")

        print(f"\n=== Test 3: mark price (via funding rate endpoint) ===")
        try:
            fr_raw = await ex._client.public_get_public_mark_price({"instType": "SWAP", "instId": "BTC-USDT-SWAP"})
            if fr_raw.get("data"):
                mp = fr_raw["data"][0]
                print(f"  mark_price: {mp.get('markPx')}")
                print(f"  ts: {mp.get('ts')}")
                if ticker1.last:
                    diff_pct = (ticker1.last - float(mp.get('markPx', 0))) / float(mp.get('markPx', 0)) * 100
                    print(f"  last vs mark drift (last - mark / mark): {diff_pct:+.4f}%")
        except Exception as e:
            print(f"  mark price fetch failed: {e}")

        print(f"\n=== Test 4: position mark price (if any position exists) ===")
        positions = await ex.fetch_positions(SYMBOL)
        if positions:
            p = positions[0]
            print(f"  position: {p.side} {p.contracts} @ entry={p.entry_price:.2f}")
            print(f"  liquidation_price: {p.liquidation_price}")
            print(f"  unrealized_pnl: {p.unrealized_pnl}")
        else:
            print(f"  no position (skipped)")

        print(f"\n=== Test 5: explicit comparison — would SL @ -0.6% be rejected? ===")
        sl_at_06 = ticker1.last * 0.994
        print(f"  ticker.last={ticker1.last:.2f}")
        print(f"  computed SL trigger (-0.6%): {sl_at_06:.2f}")
        print(f"  diff: -{ticker1.last - sl_at_06:.2f}")
        print(f"  → if OKX rejects (51280), it means OKX-side last < {sl_at_06:.2f} at submit time")

    finally:
        await ex.close()


if __name__ == "__main__":
    sys.exit(asyncio.run(main()) or 0)
