"""Iter 2b Write-Path Pre-work — probe CCXT unified create_order routing.

Purpose
-------
Iter 2b read path (fetch_open_orders normalization) was verified via
scripts/iter2b_sample_okx_algo_orders.py on raw OKX algo responses. But
agent's WRITE path (`tools_execution.py:141-143 → exchange.create_order(
order_type="stop", price=X)`) goes through CCXT UNIFIED create_order,
not raw OKX `private_post_trade_order_algo`.

This script probes what CCXT actually does with `type="stop"` on OKX swap:
does it auto-route to the algo endpoint (producing `info.ordType=conditional`
+ `info.algoId`), or does it hit the plain order endpoint? The answer
decides whether `OKXExchange.create_order` needs an explicit algo routing
layer in Iter 2b scope.

Probes (5 attempts on demo, far-from-market prices to avoid trigger):
  A. create_order(type="stop", price=sl_px)  ← current system call exactly
  B. create_order(type="stop", price=sl_px, params={"stopLossPrice": sl_px})
  C. create_order(type="market", params={"stopLossPrice": sl_px})  ← attach pattern
  D. create_order(type="market", params={"stopLoss": {"triggerPrice": sl_px, "price": sl_px}})
  E. create_order(type="take_profit", price=tp_px, params={"takeProfitPrice": tp_px})  ← symmetric to B

For each: dump CCXT response (id / type / info.ordType / info.algoId /
info.slTriggerPx), classify (algo vs plain vs error), auto-cancel created
orders, and summarize which attempts work.

Safety
------
- Refuses unless OKX_SANDBOX=true.
- Reads OKX_DEMO_* credentials only.
- Refuses if demo has existing open positions (cleanup attribution safety).
- Far-from-market trigger prices (current × 0.7) — no trigger risk.
- try/finally auto-cleanup: cancels every created order id, attempts both
  algo (stop+trigger params) and plain cancel paths for each.
- No position open attempts (all probes are standalone algo or sell-on-imagined-long).
- If some probe accidentally opens a position (e.g. OKX decides "stop 50000
  = market sell now" because condition immediately met), try/finally does
  a market close as defense.

Execution
---------
    uv run python scripts/iter2b_write_path_probe.py

Paste the [SUMMARY] section output back for spec decision.
"""
from __future__ import annotations

import asyncio
import json
import os
import sys
from pathlib import Path
from typing import Any

import ccxt.async_support as ccxt
from dotenv import load_dotenv

SYMBOL_UNIFIED = "BTC/USDT:USDT"
SYMBOL_OKX = "BTC-USDT-SWAP"
AMOUNT_CONTRACTS = 1        # 1 contract = 0.01 BTC on OKX swap
LEVERAGE = 3
TD_MODE = "isolated"


def _fail(msg: str) -> None:
    print(f"[REFUSE] {msg}", file=sys.stderr)
    sys.exit(1)


def _check_env() -> None:
    load_dotenv(".env")
    sandbox = os.environ.get("OKX_SANDBOX", "").lower()
    if sandbox != "true":
        _fail(f"OKX_SANDBOX must be 'true' (got {sandbox!r}).")
    for key in ("OKX_DEMO_API_KEY", "OKX_DEMO_SECRET", "OKX_DEMO_PASSWORD"):
        if not os.environ.get(key):
            _fail(f"{key} not set in .env.")


def _build_client() -> ccxt.okx:
    client = ccxt.okx({
        "apiKey": os.environ["OKX_DEMO_API_KEY"],
        "secret": os.environ["OKX_DEMO_SECRET"],
        "password": os.environ["OKX_DEMO_PASSWORD"],
        "options": {"defaultType": "swap", "fetchMarkets": ["swap"]},
        "timeout": 30000,
    })
    client.set_sandbox_mode(True)
    return client


async def _refuse_if_positions(client: ccxt.okx) -> None:
    positions = await client.fetch_positions([SYMBOL_UNIFIED])
    open_pos = [p for p in positions if float(p.get("contracts") or 0) > 0]
    if open_pos:
        _fail(f"Demo account has {len(open_pos)} open position(s); close manually and re-run.")


def _classify(order: dict | None, err: Exception | None) -> str:
    """Classify the result: algo / plain / error."""
    if err is not None:
        return f"ERROR: {type(err).__name__}: {err}"
    if order is None:
        return "NONE (no response)"
    info = order.get("info") or {}
    ord_type_raw = info.get("ordType") or ""
    algo_id = info.get("algoId") or ""
    top_type = order.get("type") or ""
    if algo_id or ord_type_raw in ("conditional", "oco", "trigger"):
        return f"ALGO (type={top_type!r}, info.ordType={ord_type_raw!r}, algoId={algo_id!r})"
    return f"PLAIN (type={top_type!r}, info.ordType={ord_type_raw!r}, ordId={info.get('ordId')!r})"


def _dump_order(label: str, order: dict | None, err: Exception | None) -> None:
    print(f"\n{'=' * 60}\n[{label}]\n{'=' * 60}")
    if err is not None:
        print(f"EXCEPTION: {type(err).__name__}: {err}")
        return
    if order is None:
        print("NONE")
        return
    # Top-level unified fields only (info dumped separately)
    top = {k: v for k, v in order.items()
           if k != "info" and v not in (None, "", [], {}, False)}
    print("── unified top-level (non-empty) ──")
    print(json.dumps(top, indent=2, default=str))
    info = order.get("info") or {}
    nonempty_info = {k: v for k, v in info.items()
                     if v not in (None, "", "0", [], {})}
    if nonempty_info:
        print("── info (OKX raw, non-empty) ──")
        print(json.dumps(nonempty_info, indent=2, default=str))


async def _try_probe(
    client: ccxt.okx, label: str,
    type_arg: str, side: str, amount: float, price: float | None, params: dict,
) -> tuple[dict | None, Exception | None, str | None]:
    """Execute one create_order probe. Returns (order_dict, exception, created_id)."""
    print(f"\n→ [{label}] create_order({SYMBOL_UNIFIED!r}, {type_arg!r}, {side!r}, "
          f"{amount}, price={price}, params={params})")
    try:
        order = await client.create_order(
            SYMBOL_UNIFIED, type_arg, side, amount, price, params,
        )
        created_id = order.get("id")
        return order, None, created_id
    except Exception as e:
        return None, e, None


async def _cleanup(client: ccxt.okx, created_ids: list[str]) -> None:
    """Cancel each created id; try algo cancel first, fallback plain."""
    print(f"\n{'=' * 60}\n[cleanup] cancelling {len(created_ids)} created order(s)\n{'=' * 60}")
    for oid in created_ids:
        if not oid:
            continue
        cancelled = False
        # Try algo cancel first
        try:
            await client.cancel_order(
                oid, SYMBOL_UNIFIED,
                params={"stop": True, "trigger": True, "algoId": oid},
            )
            print(f"  ✓ algo-cancel {oid}")
            cancelled = True
        except Exception as e_algo:
            # Fallback to plain cancel
            try:
                await client.cancel_order(oid, SYMBOL_UNIFIED)
                print(f"  ✓ plain-cancel {oid}")
                cancelled = True
            except Exception as e_plain:
                print(f"  ✗ {oid} both cancel paths failed:")
                print(f"    algo: {e_algo}")
                print(f"    plain: {e_plain}")
                print(f"    → MANUAL CLEAN NEEDED on OKX web")
        if not cancelled:
            continue

    # Defensive: if any probe accidentally opened a position, close it
    try:
        positions = await client.fetch_positions([SYMBOL_UNIFIED])
        open_pos = [p for p in positions if float(p.get("contracts") or 0) > 0]
        if open_pos:
            print(f"\n[cleanup] ⚠ found {len(open_pos)} unexpected open position(s), closing")
            for p in open_pos:
                close_side = "sell" if p.get("side") == "long" else "buy"
                sz = float(p.get("contracts"))
                try:
                    await client.create_order(
                        SYMBOL_UNIFIED, "market", close_side, sz, None,
                        {"tdMode": TD_MODE, "reduceOnly": True},
                    )
                    print(f"  ✓ closed {p.get('side')} {sz}")
                except Exception as e:
                    print(f"  ✗ close failed: {e} → MANUAL CLEAN NEEDED")
    except Exception as e:
        print(f"[cleanup] position check failed: {e}")


async def main() -> int:
    _check_env()
    print("[init] OKX_SANDBOX=true + OKX_DEMO_* credentials confirmed")

    client = _build_client()
    created_ids: list[str] = []

    try:
        await client.load_markets()
        await _refuse_if_positions(client)

        # Set leverage (isolated requires per-symbol + mgnMode)
        try:
            await client.set_leverage(
                LEVERAGE, SYMBOL_UNIFIED,
                params={"mgnMode": TD_MODE},
            )
            print(f"[init] leverage {LEVERAGE}x isolated set on {SYMBOL_UNIFIED}")
        except Exception as e:
            print(f"[init] set_leverage warning: {e} (continuing)")

        ticker = await client.fetch_ticker(SYMBOL_UNIFIED)
        current = float(ticker["last"])
        sl_px = round(current * 0.7, 1)   # 30% below — SL for long, won't trigger
        tp_px = round(current * 1.3, 1)   # 30% above — TP for long, won't trigger
        print(f"[init] current={current}, sl_px={sl_px} (30% below), tp_px={tp_px} (30% above)")

        results: list[tuple[str, str]] = []

        # ── Probe A: current system call shape exactly ──
        order, err, oid = await _try_probe(
            client, "A — type=stop, price=sl_px, no extra params (system default)",
            "stop", "sell", AMOUNT_CONTRACTS, sl_px,
            {"tdMode": TD_MODE},
        )
        if oid: created_ids.append(oid)
        _dump_order("A", order, err)
        results.append(("A", _classify(order, err)))

        # ── Probe B: type=stop + explicit stopLossPrice ──
        order, err, oid = await _try_probe(
            client, "B — type=stop, params={stopLossPrice}",
            "stop", "sell", AMOUNT_CONTRACTS, sl_px,
            {"tdMode": TD_MODE, "stopLossPrice": sl_px},
        )
        if oid: created_ids.append(oid)
        _dump_order("B", order, err)
        results.append(("B", _classify(order, err)))

        # ── Probe C: attach pattern — market + stopLossPrice ──
        # NOTE: type=market would normally execute immediately; we use
        # reduceOnly=true to minimize risk of unexpected fill
        order, err, oid = await _try_probe(
            client, "C — type=market, price=None, params={stopLossPrice} (attach pattern)",
            "market", "sell", AMOUNT_CONTRACTS, None,
            {"tdMode": TD_MODE, "stopLossPrice": sl_px, "reduceOnly": True},
        )
        if oid: created_ids.append(oid)
        _dump_order("C", order, err)
        results.append(("C", _classify(order, err)))

        # ── Probe D: nested stopLoss dict (another CCXT pattern) ──
        order, err, oid = await _try_probe(
            client, "D — type=market, params={stopLoss: {triggerPrice, price}}",
            "market", "sell", AMOUNT_CONTRACTS, None,
            {
                "tdMode": TD_MODE,
                "reduceOnly": True,
                "stopLoss": {"triggerPrice": sl_px, "price": sl_px},
            },
        )
        if oid: created_ids.append(oid)
        _dump_order("D", order, err)
        results.append(("D", _classify(order, err)))

        # ── Probe E: type="take_profit" + takeProfitPrice (symmetric to B) ──
        # Verify TP write-path has same routing as SL. For long-position TP, sell
        # side triggers ABOVE current; tp_px = current * 1.3 (30% above, safe).
        order, err, oid = await _try_probe(
            client, "E — type=take_profit, price=tp_px, params={takeProfitPrice}",
            "take_profit", "sell", AMOUNT_CONTRACTS, tp_px,
            {"tdMode": TD_MODE, "takeProfitPrice": tp_px},
        )
        if oid: created_ids.append(oid)
        _dump_order("E", order, err)
        results.append(("E", _classify(order, err)))

        # ── Summary ──
        print(f"\n{'=' * 60}\n[SUMMARY] write-path routing probe results\n{'=' * 60}")
        print(f"Current price: {current}, sl_px: {sl_px}")
        print(f"\n{'Attempt':<10} {'Classification':<70}")
        print("-" * 82)
        for label, cls in results:
            print(f"  {label:<8} {cls}")
        print()
        print("Decision guide:")
        print("  • Any attempt returns ALGO (ordType=conditional, algoId non-empty)")
        print("    → that params combo is the correct CCXT unified path for Iter 2b")
        print("  • If A (current system call) returns ALGO → zero-code scope:")
        print("    system already produces correct OKX algo orders")
        print("  • If A returns PLAIN but B/C/D return ALGO → Iter 2b must add")
        print("    algo-routing in OKXExchange.create_order (translate order_type='stop'")
        print("    to the winning params combo)")
        print("  • If all ERROR → need different CCXT API or OKX endpoint")

        return 0

    except Exception as e:
        import traceback
        print(f"\n[error] {e}")
        traceback.print_exc()
        return 1

    finally:
        if created_ids:
            await _cleanup(client, created_ids)
        try:
            await client.close()
        except Exception:
            pass


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
