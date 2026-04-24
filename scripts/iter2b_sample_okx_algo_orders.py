"""Iter 2b Pre-work — Sample OKX demo-account algo order raw payloads.

Purpose
-------
Iter 2b normalizes `_parse_order` to expose OKX algo-order semantics (conditional
SL, OCO SL/TP pair) so `get_position` can render real stop-loss / take-profit
entries instead of "not set" (spec §2.4). Normalization is done against the
raw dict shape OKX actually returns — not what the CCXT unified doc claims.

This script captures two ground-truth samples from a demo account, archives
them as versioned JSON fixtures, and probes which layer (CCXT unified
`params.stopLossPrice` vs OKX raw `info.slTriggerPx`) carries the trigger
fields. Fixtures land under tests/fixtures/ and become the mock source for
Iter 2b normalization tests (avoiding the "手写 mock 凭直觉" anti-pattern
that caused Iter 2 Round-4 bugs).

It also probes the OKX account's position mode (`posMode`) so Iter 2b
scope #5 (fail-fast on non-net-mode / non-isolated accounts) can be
designed against the real account-config response shape.

Outputs
-------
  tests/fixtures/okx_fetch_open_orders_conditional_sl_raw.json       (OKX raw archive)
  tests/fixtures/okx_fetch_open_orders_oco_raw.json                  (OKX raw archive)
  tests/fixtures/okx_fetch_open_orders_conditional_sl_unified.json   (CCXT unified; _parse_order test input)
  tests/fixtures/okx_fetch_open_orders_oco_unified.json              (CCXT unified; _parse_order test input)
  tests/fixtures/okx_account_config.json
  Stdout: field-layer probe summary + account mode report

Fixture duality rationale
-------------------------
OKX `/trade/orders-algo-pending` returns a raw dict (top-level algoId / slTriggerPx
/ state / ...). But `_parse_order` in exchange code consumes the CCXT-unified
wrapper (returned by `fetch_open_orders(symbol, params={stop, ordType})`) — that
one has `id`/`symbol`/`amount`/`status` at top level and OKX raw nested under
`info`. Tests must load the unified fixture to match real code input; the raw
fixture is kept as schema archive for future reference.

Safety guards
-------------
- Refuses to run unless OKX_SANDBOX=true (hard guard against live account).
- Reads only OKX_DEMO_* credentials. OKX_API_KEY / SECRET / PASSWORD are
  never touched by this script.
- Refuses to run if demo account has any existing open positions on the
  target symbol (residual positions cannot be safely attributed to this
  script's cleanup scope).
- Idempotent startup: cancels any pre-existing algo orders whose clOrdId
  begins with "iter2bsample" before creating new ones.
- try/finally cleanup: created algo orders are cancelled on exit; if
  fallback B (open-position) path was used, the position is also closed.
  Cleanup failures print the IDs needed for manual OKX-web intervention.

Execution
---------
  Prerequisites in .env:
    OKX_SANDBOX=true
    OKX_DEMO_API_KEY=<your demo key>
    OKX_DEMO_SECRET=<your demo secret>
    OKX_DEMO_PASSWORD=<your demo passphrase>

  Demo account:
    - Position mode: one-way (net_mode)
    - Sufficient balance (~1000 USDT recommended; fallback B path opens a
      ~$200 position briefly)

  Run:
    uv run python scripts/iter2b_sample_okx_algo_orders.py
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

# ─── Configuration ──────────────────────────────────────────────────────────

SYMBOL_UNIFIED = "BTC/USDT:USDT"         # CCXT unified symbol
SYMBOL_OKX = "BTC-USDT-SWAP"             # OKX raw instId
CONTRACTS_SZ = "1"                        # 1 张 = 0.01 BTC (OKX swap contract size)
LEVERAGE = 3                              # Conservative; must be set per symbol in isolated mode
TD_MODE = "isolated"                      # System-recommended margin mode

CLORD_PREFIX = "iter2bsample"
CLORD_CONDITIONAL = f"{CLORD_PREFIX}01"
CLORD_OCO = f"{CLORD_PREFIX}02"

FIXTURE_DIR = Path("tests/fixtures")
# OKX raw — schema archive (from private_get_trade_orders_algo_pending)
FIXTURE_CONDITIONAL_RAW = FIXTURE_DIR / "okx_fetch_open_orders_conditional_sl_raw.json"
FIXTURE_OCO_RAW = FIXTURE_DIR / "okx_fetch_open_orders_oco_raw.json"
# CCXT unified — _parse_order() test input (what exchange code actually consumes)
FIXTURE_CONDITIONAL_UNIFIED = FIXTURE_DIR / "okx_fetch_open_orders_conditional_sl_unified.json"
FIXTURE_OCO_UNIFIED = FIXTURE_DIR / "okx_fetch_open_orders_oco_unified.json"
FIXTURE_ACCOUNT = FIXTURE_DIR / "okx_account_config.json"

# ─── Helpers ────────────────────────────────────────────────────────────────


def _fail(msg: str) -> None:
    print(f"[REFUSE] {msg}", file=sys.stderr)
    sys.exit(1)


def _check_env() -> None:
    load_dotenv()
    sandbox = os.environ.get("OKX_SANDBOX", "").lower()
    if sandbox != "true":
        _fail(
            f"OKX_SANDBOX must be 'true' to run this script (got {sandbox!r}). "
            "Script refuses to talk to live OKX account."
        )
    for key in ("OKX_DEMO_API_KEY", "OKX_DEMO_SECRET", "OKX_DEMO_PASSWORD"):
        if not os.environ.get(key):
            _fail(f"{key} is not set in .env — cannot authenticate demo account.")


def _build_client() -> ccxt.okx:
    client = ccxt.okx({
        "apiKey": os.environ["OKX_DEMO_API_KEY"],
        "secret": os.environ["OKX_DEMO_SECRET"],
        "password": os.environ["OKX_DEMO_PASSWORD"],
        "options": {
            "defaultType": "swap",
            # Only load swap markets. CCXT default loads spot+margin+swap+future+option
            # concurrently (5 requests); OPTION endpoint is prone to SSL resets on
            # demo, and we only need BTC-USDT-SWAP metadata anyway.
            "fetchMarkets": ["swap"],
        },
        "timeout": 30000,
    })
    # OKX demo trading: sets the x-simulated-trading: 1 header
    client.set_sandbox_mode(True)
    return client


def _write_fixture(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w") as f:
        json.dump(data, f, indent=2, sort_keys=True, ensure_ascii=False, default=str)
    print(f"[fixture] wrote {path}")


_OKX_KNOWN_KEYS = {
    "instId", "instType", "algoId", "algoClOrdId", "clOrdId", "ordId",
    "ordType", "state", "side", "posSide", "tdMode",
    "sz", "reduceOnly", "lever", "ccy",
    "slTriggerPx", "slTriggerPxType", "slOrdPx",
    "tpTriggerPx", "tpTriggerPxType", "tpOrdPx",
    "triggerPx", "triggerPxType", "ordPx",
    "cTime", "uTime", "tag", "last",
}


def _probe_fields_okx_raw(label: str, raw: dict) -> None:
    """Probe OKX-native raw dict (from private_get_trade_orders_algo_pending).

    OKX returns all fields at top-level — no CCXT unified wrapper.
    """
    print(f"\n[probe] {label}")
    print(f"  ── OKX raw — non-empty known fields ──")
    for key in _OKX_KNOWN_KEYS:
        val = raw.get(key)
        if val not in (None, "", "0"):
            print(f"    {key}: {val!r}")
    extras = {k: v for k, v in raw.items()
              if k not in _OKX_KNOWN_KEYS and v not in (None, "", "0", [], {})}
    if extras:
        print(f"  ── OKX raw — other non-empty fields (schema discovery) ──")
        for k, v in extras.items():
            print(f"    {k}: {v!r}")


def _probe_fields_unified(label: str, raw: dict) -> None:
    """Probe CCXT-unified wrapper dict (from fetch_open_orders)."""
    params = raw.get("params") or {}
    info = raw.get("info") or {}
    print(f"\n[probe] {label}")
    print(f"  ── CCXT unified top-level ──")
    for k in ("id", "clientOrderId", "symbol", "type", "side", "status",
              "price", "stopPrice", "triggerPrice", "amount"):
        v = raw.get(k)
        if v is not None:
            print(f"    {k}: {v!r}")
    nonempty_params = {k: v for k, v in params.items() if v not in (None, "")}
    if nonempty_params:
        print(f"  ── params (CCXT-added extras) ──")
        for k, v in nonempty_params.items():
            print(f"    {k}: {v!r}")
    nonempty_info = {k: v for k, v in info.items() if v not in (None, "", "0")}
    if nonempty_info:
        print(f"  ── info (OKX raw nested inside CCXT) ──")
        for k, v in nonempty_info.items():
            print(f"    {k}: {v!r}")


# ─── Account / pre-flight checks ────────────────────────────────────────────


async def _probe_account_config(client: ccxt.okx) -> dict:
    """Fetch OKX account config — used for Iter 2b scope #5 design."""
    resp = await client.private_get_account_config()
    data = (resp.get("data") or [{}])[0]
    pos_mode = data.get("posMode")
    print(f"\n[account] posMode = {pos_mode!r}")
    print(f"[account] acctLv  = {data.get('acctLv')!r}")
    print(f"[account] uid     = {data.get('uid')!r}")
    if pos_mode != "net_mode":
        _fail(
            f"Demo account posMode is {pos_mode!r}, script expects 'net_mode' "
            "(one-way, system design assumption). Change in OKX web → Settings."
        )
    return data


async def _refuse_if_positions(client: ccxt.okx) -> None:
    positions = await client.fetch_positions([SYMBOL_UNIFIED])
    open_pos = [p for p in positions if float(p.get("contracts") or 0) > 0]
    if open_pos:
        details = [
            f"{p.get('symbol')} side={p.get('side')} contracts={p.get('contracts')}"
            for p in open_pos
        ]
        _fail(
            "Demo account has existing open position(s); script cannot safely "
            "attribute cleanup:\n  " + "\n  ".join(details) +
            "\nClose manually in OKX web, then re-run."
        )


# ─── Algo order primitives (OKX raw API) ────────────────────────────────────


async def _fetch_pending_algo_raw(client: ccxt.okx) -> list[dict]:
    """Fetch open algo orders via OKX raw endpoint — returns list of algo dicts.

    OKX /trade/orders-algo-pending requires ordType per call and accepts only
    one value at a time. We fetch conditional + oco separately and merge.
    """
    merged: list[dict] = []
    for ord_type in ("conditional", "oco"):
        resp = await client.private_get_trade_orders_algo_pending({
            "instType": "SWAP",
            "instId": SYMBOL_OKX,
            "ordType": ord_type,
        })
        merged.extend(resp.get("data") or [])
    return merged


async def _cancel_algo_by_id(client: ccxt.okx, algo_id: str, label: str = "") -> bool:
    """Cancel an algo order via CCXT unified cancel_order.

    OKX /trade/cancel-algos expects a list body, not {"data": [...]}.
    CCXT's cancel_order(id, symbol, params={'stop': True, 'trigger': True})
    handles the body serialization correctly. The 'trigger' / 'stop' hints
    route CCXT to the algo-cancel endpoint instead of the plain one.
    """
    try:
        await client.cancel_order(
            algo_id, SYMBOL_UNIFIED,
            params={"stop": True, "trigger": True, "algoId": algo_id},
        )
        print(f"[cleanup] cancelled algo {algo_id} {label}")
        return True
    except Exception as e:
        print(f"[cleanup] cancel algo {algo_id} FAILED: {e}  → MANUAL CLEAN NEEDED")
        return False


async def _cleanup_stale(client: ccxt.okx) -> int:
    pending = await _fetch_pending_algo_raw(client)
    stale = [o for o in pending
             if (o.get("algoClOrdId") or o.get("clOrdId") or "").startswith(CLORD_PREFIX)]
    if not stale:
        return 0
    print(f"[cleanup] found {len(stale)} stale test algo orders, cancelling")
    cancelled = 0
    for o in stale:
        if await _cancel_algo_by_id(client, o["algoId"], f"(stale {o.get('algoClOrdId')})"):
            cancelled += 1
    return cancelled


async def _place_algo(
    client: ccxt.okx,
    *,
    ord_type: str,                    # "conditional" or "oco"
    side: str,                         # "sell" (SL on imagined long)
    reduce_only: bool,
    clord_id: str,
    sl_trigger: str | None = None,
    tp_trigger: str | None = None,
) -> tuple[str, dict]:
    """Place a standalone algo order via OKX raw API.

    Returns (algoId, raw response data entry). Raises RuntimeError if OKX rejects.
    """
    body: dict[str, Any] = {
        "instId": SYMBOL_OKX,
        "tdMode": TD_MODE,
        "side": side,
        "ordType": ord_type,
        "sz": CONTRACTS_SZ,
        "reduceOnly": "true" if reduce_only else "false",
        "algoClOrdId": clord_id,       # OKX algo clOrdId is 'algoClOrdId' in v5
    }
    # NOTE: posSide is omitted — one-way (net) mode rejects it
    if sl_trigger is not None:
        body["slTriggerPx"] = sl_trigger
        body["slOrdPx"] = "-1"         # -1 = market
    if tp_trigger is not None:
        body["tpTriggerPx"] = tp_trigger
        body["tpOrdPx"] = "-1"

    resp = await client.private_post_trade_order_algo(body)
    data_list = resp.get("data") or [{}]
    data = data_list[0]
    s_code = data.get("sCode")
    if s_code != "0":
        raise RuntimeError(
            f"OKX rejected {ord_type} algo (reduceOnly={reduce_only}): "
            f"sCode={s_code} sMsg={data.get('sMsg')!r}"
        )
    return data["algoId"], data


# ─── Fallback B: open position then place reduceOnly algos ──────────────────


async def _open_small_long(client: ccxt.okx) -> None:
    """Fallback B step 1: open a ~$200 long position."""
    resp = await client.private_post_trade_order({
        "instId": SYMBOL_OKX,
        "tdMode": TD_MODE,
        "side": "buy",
        "ordType": "market",
        "sz": CONTRACTS_SZ,
    })
    data = (resp.get("data") or [{}])[0]
    if data.get("sCode") != "0":
        raise RuntimeError(f"fallback open-long failed: {data}")
    print(f"[fallback-B] opened long {CONTRACTS_SZ} contract(s), ordId={data.get('ordId')}")
    await asyncio.sleep(1)   # let OKX register the position


async def _close_position_market(client: ccxt.okx) -> None:
    """Fallback B cleanup: market-close any remaining long position."""
    try:
        resp = await client.private_post_trade_close_position({
            "instId": SYMBOL_OKX,
            "mgnMode": TD_MODE,
        })
        data = (resp.get("data") or [{}])[0]
        print(f"[fallback-B cleanup] closed position, resp sCode={data.get('sCode')}")
    except Exception as e:
        print(f"[fallback-B cleanup] close position FAILED: {e}  → MANUAL CLEAN NEEDED")


# ─── Main flow ──────────────────────────────────────────────────────────────


async def main() -> int:
    _check_env()
    print("[init] OKX_SANDBOX=true confirmed, using OKX_DEMO_* credentials")

    client = _build_client()

    created_algo_ids: list[str] = []
    fallback_b_opened = False

    try:
        await client.load_markets()
        await _probe_account_config(client)
        await _refuse_if_positions(client)

        # Write account config fixture (scope #5 design reference)
        acct_raw = await client.private_get_account_config()
        _write_fixture(FIXTURE_ACCOUNT, acct_raw)

        # Set leverage (isolated mode requires per-symbol leverage)
        try:
            await client.private_post_account_set_leverage({
                "instId": SYMBOL_OKX,
                "lever": str(LEVERAGE),
                "mgnMode": TD_MODE,
            })
            print(f"[init] leverage set to {LEVERAGE}x on {SYMBOL_OKX} ({TD_MODE})")
        except Exception as e:
            print(f"[init] set_leverage warning: {e} (continuing — may already be set)")

        ticker = await client.fetch_ticker(SYMBOL_UNIFIED)
        current_price = float(ticker["last"])
        print(f"[init] current price {SYMBOL_UNIFIED} = {current_price}")

        stale = await _cleanup_stale(client)
        if stale:
            print(f"[cleanup] removed {stale} stale test orders from previous run")

        sl_px = str(round(current_price * 0.7, 1))
        tp_px = str(round(current_price * 1.3, 1))
        print(f"[plan] SL trigger = {sl_px}, TP trigger = {tp_px}")

        # ─── Quick path: standalone algo (reduceOnly=false) ─────────────
        used_fallback_b = False
        try:
            print("\n[place-quick] conditional SL (reduceOnly=false)")
            cond_id, _ = await _place_algo(
                client,
                ord_type="conditional", side="sell", reduce_only=False,
                clord_id=CLORD_CONDITIONAL, sl_trigger=sl_px,
            )
            created_algo_ids.append(cond_id)

            print("[place-quick] OCO (reduceOnly=false)")
            oco_id, _ = await _place_algo(
                client,
                ord_type="oco", side="sell", reduce_only=False,
                clord_id=CLORD_OCO, sl_trigger=sl_px, tp_trigger=tp_px,
            )
            created_algo_ids.append(oco_id)

        except RuntimeError as e:
            print(f"[place-quick] rejected → switching to fallback B: {e}")
            # Cancel anything the quick path managed to create
            for aid in created_algo_ids:
                await _cancel_algo_by_id(client, aid, "(quick-path partial)")
            created_algo_ids.clear()

            # Fallback B: open position, then SL/TP with reduceOnly=true
            await _open_small_long(client)
            fallback_b_opened = True
            used_fallback_b = True

            print("[place-fallback-B] conditional SL (reduceOnly=true)")
            cond_id, _ = await _place_algo(
                client,
                ord_type="conditional", side="sell", reduce_only=True,
                clord_id=CLORD_CONDITIONAL, sl_trigger=sl_px,
            )
            created_algo_ids.append(cond_id)

            print("[place-fallback-B] OCO (reduceOnly=true)")
            oco_id, _ = await _place_algo(
                client,
                ord_type="oco", side="sell", reduce_only=True,
                clord_id=CLORD_OCO, sl_trigger=sl_px, tp_trigger=tp_px,
            )
            created_algo_ids.append(oco_id)

        print(f"\n[path] used fallback B = {used_fallback_b}")
        await asyncio.sleep(2)   # let OKX register the orders

        # ─── Fetch raw payloads ─────────────────────────────────────────
        raw_orders = await _fetch_pending_algo_raw(client)
        by_clord: dict[str, dict] = {}
        for o in raw_orders:
            clord = o.get("algoClOrdId") or o.get("clOrdId") or ""
            if clord.startswith(CLORD_PREFIX):
                by_clord[clord] = o

        cond_raw = by_clord.get(CLORD_CONDITIONAL)
        oco_raw = by_clord.get(CLORD_OCO)

        if cond_raw is None:
            print(f"[warn] conditional SL (clOrdId={CLORD_CONDITIONAL}) not in fetched list")
        else:
            _write_fixture(FIXTURE_CONDITIONAL_RAW, cond_raw)
            _probe_fields_okx_raw("conditional SL — OKX raw (orders-algo-pending)", cond_raw)

        if oco_raw is None:
            print(f"[warn] OCO (clOrdId={CLORD_OCO}) not in fetched list")
        else:
            _write_fixture(FIXTURE_OCO_RAW, oco_raw)
            _probe_fields_okx_raw("OCO — OKX raw (orders-algo-pending)", oco_raw)

        # ─── Probe CCXT unified layer — does it merge algo orders? ──────
        print("\n" + "=" * 70)
        print("[probe] CCXT unified fetch_open_orders routing")
        print("=" * 70)

        print("\n[probe-A] fetch_open_orders(symbol) — no params (plain pending)")
        try:
            unified = await client.fetch_open_orders(SYMBOL_UNIFIED)
            print(f"  returned {len(unified)} orders")
            for u in unified:
                cid = u.get("clientOrderId") or ""
                if cid.startswith(CLORD_PREFIX):
                    _probe_fields_unified(f"unified[no-params] clOrdId={cid}", u)
                    break
            else:
                print(f"  ✗ none of our {CLORD_PREFIX}* orders surfaced here (expected — algo live in different endpoint)")
        except Exception as e:
            print(f"  fetch_open_orders (no params) failed: {e}")

        # Route-to-algo via CCXT: OKX adapter requires BOTH stop=True AND ordType
        # Write first returned order as _parse_order test fixture (this IS the
        # real code input form — see docstring "Fixture duality rationale").
        for ord_type, fixture_path in (
            ("conditional", FIXTURE_CONDITIONAL_UNIFIED),
            ("oco", FIXTURE_OCO_UNIFIED),
        ):
            tag = f"probe-D-{ord_type}"
            print(f"\n[{tag}] fetch_open_orders(symbol, params={{'stop': True, 'ordType': '{ord_type}'}})")
            try:
                unified = await client.fetch_open_orders(
                    SYMBOL_UNIFIED, params={"stop": True, "ordType": ord_type}
                )
                print(f"  returned {len(unified)} orders")
                # Probe every returned order unconditionally — demo account
                # only has our test algo, so no risk of noise.
                for i, u in enumerate(unified):
                    label = (
                        f"unified[stop=True, ordType={ord_type}] "
                        f"[{i}] id={u.get('id')!r} clientOrderId={u.get('clientOrderId')!r}"
                    )
                    _probe_fields_unified(label, u)
                    if i == 0:
                        _write_fixture(fixture_path, u)
            except Exception as e:
                print(f"  failed: {e}")

        # ─── Summary ────────────────────────────────────────────────────
        print("\n" + "=" * 70)
        print("[SUMMARY] Iter 2b normalization design inputs")
        print("=" * 70)
        print(f"  used fallback-B (open-position):               {used_fallback_b}")
        print(f"  raw conditional/oco fixtures captured:         {cond_raw is not None and oco_raw is not None}")
        print(f"  unified conditional/oco fixtures captured:     see [fixture] lines above")
        print(f"  account_config fixture captured:               True")
        print(f"\nNext steps:")
        print(f"  1. Review fixtures in {FIXTURE_DIR}/")
        print(f"     - *_unified.json = what _parse_order() consumes (test input)")
        print(f"     - *_raw.json     = OKX-layer archive (schema reference only)")
        print(f"  2. Write Iter 2b spec / tests against unified fixtures")

        return 0

    except Exception as e:
        import traceback
        print(f"\n[error] {e}")
        traceback.print_exc()
        return 1

    finally:
        # ─── Cleanup: algo orders first, then (if fallback B used) close position ─
        print("\n" + "=" * 70)
        print("[cleanup-final] cancelling created test orders")
        print("=" * 70)
        for aid in created_algo_ids:
            await _cancel_algo_by_id(client, aid, "(final)")

        if fallback_b_opened:
            print("[cleanup-final] fallback B was used → closing position")
            await asyncio.sleep(0.5)
            await _close_position_market(client)

        try:
            await client.close()
        except Exception:
            pass


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
