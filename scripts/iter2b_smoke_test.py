"""Iter 2b end-to-end smoke test — manual run, not CI.

Validates the OKX demo account round-trip:
  1. Balance check (§5.4 USDT gate)
  2. Place conditional SL → fetch_open_orders shows it → cancel
  3. Place OCO → fetch_open_orders shows merged line → cancel

Usage:
  cp .env.example .env  # fill OKX_DEMO_* then
  OKX_SANDBOX=true python scripts/iter2b_smoke_test.py

Exit codes:
  0 — full smoke passed
  1 — env guard failed (OKX_SANDBOX != 'true')
  2 — balance gate failed (USDT <= 0)
  3 — SKIP: no open position (open small demo position in web UI, then re-run)
"""
from __future__ import annotations

import asyncio
import os
import sys
from pathlib import Path

# Ensure repo root on PYTHONPATH
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from dotenv import load_dotenv

from src.integrations.exchange.okx import OKXExchange


async def main() -> int:
    load_dotenv(".env")
    if os.environ.get("OKX_SANDBOX", "").lower() != "true":
        print("ABORT: OKX_SANDBOX must be 'true' for smoke test")
        return 1

    ex = OKXExchange(
        api_key=os.environ["OKX_DEMO_API_KEY"],
        secret=os.environ["OKX_DEMO_SECRET"],
        password=os.environ["OKX_DEMO_PASSWORD"],
        symbol="BTC/USDT:USDT",
        sandbox=True,
    )

    try:
        # 1. start + balance gate
        await ex.start()
        bal = await ex.fetch_balance()
        print(f"[OK] balance USDT total={bal.total_usdt:.2f} free={bal.free_usdt:.2f}")
        if bal.total_usdt <= 0:
            print("ABORT: USDT=0 (see spec §5.4 auto_transfers_ccy risk)")
            return 2

        # 1.1 Dump idle balance fixture for spec §7.1 USDT/USDC auto-conversion observation-period comparison
        import json
        raw_bal = await ex._client.fetch_balance()
        fixture_path = Path(__file__).resolve().parent.parent / "tests" / "fixtures" / "okx_fetch_balance_idle.json"
        fixture_path.write_text(json.dumps(
            {"total": raw_bal.get("total", {}), "free": raw_bal.get("free", {}),
             "used": raw_bal.get("used", {})},
            indent=2,
        ))
        print(f"[OK] idle balance fixture dumped → {fixture_path.name}")

        # 2. conditional SL round-trip
        positions = await ex.fetch_positions("BTC/USDT:USDT")
        if not positions:
            print("[SKIP] no open position — smoke only verifies SL/TP when a position exists.")
            print("       Open a small demo position via OKX web, then re-run.")
            return 3  # skip distinct from 0 (success) to avoid misread in shell

        p = positions[0]
        side = "sell" if p.side == "long" else "buy"
        trigger_px = p.entry_price * (0.97 if p.side == "long" else 1.03)
        sl = await ex.create_order("BTC/USDT:USDT", side, "stop", p.contracts, price=trigger_px)
        print(f"[OK] SL created: id={sl.id} is_algo={sl.is_algo}")
        assert sl.is_algo, "SL must be is_algo=True on OKX live"
        opens = await ex.fetch_open_orders("BTC/USDT:USDT")
        assert any(o.id == sl.id for o in opens), "SL not in fetch_open_orders"
        print(f"[OK] SL visible in fetch_open_orders ({len(opens)} total)")
        await ex.cancel_order(sl.id, "BTC/USDT:USDT", is_algo=True)
        print(f"[OK] SL cancelled")

        # 3. OCO round-trip (use direct raw private API since create_order does not support OCO in this task;
        #    this step validates fetch/render/cancel round-trip)
        tp_px = p.entry_price * (1.1 if p.side == "long" else 0.9)
        sl_px = p.entry_price * (0.9 if p.side == "long" else 1.1)
        oco_resp = await ex._client.private_post_trade_order_algo({
            "instId": "BTC-USDT-SWAP",
            "tdMode": "isolated",
            "side": side,
            "ordType": "oco",
            "sz": str(p.contracts),
            "slTriggerPx": str(sl_px),
            "slOrdPx": "-1",
            "tpTriggerPx": str(tp_px),
            "tpOrdPx": "-1",
        })
        algo_id = oco_resp["data"][0]["algoId"]
        print(f"[OK] OCO placed: algoId={algo_id}")
        opens2 = await ex.fetch_open_orders("BTC/USDT:USDT")
        oco_legs = [o for o in opens2 if o.id == algo_id]
        assert len(oco_legs) == 2, f"OCO should render as 2 legs, got {len(oco_legs)}"
        types = {o.order_type for o in oco_legs}
        assert types == {"stop", "take_profit"}, f"unexpected types: {types}"
        print(f"[OK] OCO renders as 2 Orders sharing id, types={types}")
        await ex.cancel_order(algo_id, "BTC/USDT:USDT", is_algo=True)
        print(f"[OK] OCO cancelled (atomic: both legs gone)")

        print("\n[SUCCESS] Iter 2b smoke test passed")
        return 0
    finally:
        await ex.close()


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
