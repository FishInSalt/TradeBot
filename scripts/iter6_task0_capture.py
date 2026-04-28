"""Iter 6 Task 0 — OKX demo fill event capture (HARD GATE for §4.3).

Three independent sub-experiments to verify spec §4.3 OKX three-source
fusion assumption (especially signal 1: info.reduceOnly auto-fill on
market close path):

  1A: open long → immediate market close → capture fill event
  1B: open NEW long → set SL @ -0.1% → wait for trigger (4h timeout)
  1C: open NEW long → set TP @ +0.1% → wait for trigger (4h timeout)

Each sub-experiment uses its own fresh position because OKX
auto-cancels associated algo orders when a position closes (so we
cannot reuse one position to capture all three).

Method: monkey-patch OKXExchange._parse_fill_event to dump raw
order_data BEFORE parsing. Captured events are written to fixture JSON
files. No source code modification needed (revert step in plan unneeded).

Usage:
  cp .env.example .env  # fill OKX_DEMO_* if not already
  OKX_SANDBOX=true uv run python scripts/iter6_task0_capture.py --scenario 1A
  OKX_SANDBOX=true uv run python scripts/iter6_task0_capture.py --scenario 1B
  OKX_SANDBOX=true uv run python scripts/iter6_task0_capture.py --scenario 1C

  # Or run all sequentially (1A is fast; 1B/1C may take up to 4h each)
  OKX_SANDBOX=true uv run python scripts/iter6_task0_capture.py --scenario all

Exit codes:
  0  — capture successful, fixture written
  1  — env guard failed (OKX_SANDBOX != 'true')
  2  — balance gate failed (USDT <= 0)
  3  — timeout waiting for SL/TP trigger (4h)
  4  — unexpected error during scenario
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
import time
from pathlib import Path

# Ensure repo root on PYTHONPATH
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from dotenv import load_dotenv

from src.integrations.exchange.okx import OKXExchange


SYMBOL = "BTC/USDT:USDT"
NOTIONAL_BTC = 0.01  # 0.01 BTC per OKX docs minimum tick
TIMEOUT_SEC = 4 * 3600  # 4h hard timeout per spec §4.3.1
POLL_INTERVAL_SEC = 5

FIXTURE_DIR = Path(__file__).resolve().parent.parent / "tests" / "fixtures"

SCENARIO_CONFIG = {
    "1A": {
        "name": "market_close",
        "fixture": "okx_watch_orders_market_close.json",
        "description": "open long → immediate market close",
    },
    "1B": {
        "name": "sl_fill",
        "fixture": "okx_watch_orders_sl_fill.json",
        "description": "open long → set SL @ -0.1% → wait for trigger",
    },
    "1C": {
        "name": "tp_fill",
        "fixture": "okx_watch_orders_tp_fill.json",
        "description": "open long → set TP @ +0.1% → wait for trigger",
    },
    "1D": {
        "name": "market_close_with_reduce_only",
        "fixture": "okx_watch_orders_market_close_reduce_only.json",
        "description": "open long → market close with params={'reduceOnly': True} (Remediation A validation)",
    },
}


# ============ monkey-patch capture ============

# Module-level capture buffer; one element per close fill event seen
captured_events: list[dict] = []


def install_capture_hook() -> None:
    """Patch OKXExchange._parse_fill_event to dump raw order_data."""
    original_parse = OKXExchange._parse_fill_event

    async def capturing_parse(self, order_data):
        # Deep-copy via JSON round-trip so subsequent mutations don't taint capture
        snapshot = json.loads(json.dumps(order_data, default=str))
        captured_events.append(snapshot)
        info = order_data.get("info", {}) or {}
        print(
            f"[CAPTURE] id={order_data.get('id')} "
            f"status={order_data.get('status')} "
            f"type={order_data.get('type')} "
            f"side={order_data.get('side')} "
            f"info.reduceOnly={info.get('reduceOnly')!r} "
            f"info.posSide={info.get('posSide')!r} "
            f"info.ordType={info.get('ordType')!r}"
        )
        return await original_parse(self, order_data)

    OKXExchange._parse_fill_event = capturing_parse


def install_parse_plain_workaround() -> None:
    """Workaround for OKX market order create response missing 'amount' field.

    OKX swap market order create response unified data sometimes returns
    amount=None (size info lives in info.sz instead). _parse_plain in
    src/integrations/exchange/okx.py does float(data["amount"]) → TypeError.
    Fall back to info.sz or filled.

    UPSTREAM TODO: this should be fixed in _parse_plain in
    src/integrations/exchange/okx.py as a defensive fallback (separate PR,
    not Iter 6 scope).
    """
    original = OKXExchange._parse_plain

    def safe_parse_plain(self, data):
        if data.get("amount") is None:
            info = data.get("info") or {}
            sz = info.get("sz") or data.get("filled") or 0
            data = {**data, "amount": float(sz) if sz else 0.0}
            print(f"[PATCH] _parse_plain amount=None → fallback to info.sz={sz}")
        return original(self, data)

    OKXExchange._parse_plain = safe_parse_plain


# ============ scenario runners ============


async def _wait_position_gone(ex: OKXExchange, *, timeout_sec: int) -> bool:
    """Poll fetch_positions until empty or timeout. Returns True if position closed."""
    start = time.monotonic()
    while time.monotonic() - start < timeout_sec:
        positions = await ex.fetch_positions(SYMBOL)
        if not positions:
            return True
        elapsed = int(time.monotonic() - start)
        print(f"[WAIT] position still open ({elapsed}s elapsed, contracts={positions[0].contracts})...")
        await asyncio.sleep(POLL_INTERVAL_SEC)
    return False


async def _ensure_no_position(ex: OKXExchange) -> None:
    """Pre-flight: refuse to run if a position already exists (could pollute capture)."""
    positions = await ex.fetch_positions(SYMBOL)
    if positions:
        raise RuntimeError(
            f"Pre-flight failed: existing position on {SYMBOL} "
            f"(side={positions[0].side}, contracts={positions[0].contracts}). "
            f"Close it manually in OKX demo UI before running this scenario."
        )


async def _open_long(ex: OKXExchange) -> None:
    """Open a fresh long position via market order."""
    print(f"[OPEN] placing market buy {NOTIONAL_BTC} BTC...")
    order = await ex.create_order(SYMBOL, "buy", "market", NOTIONAL_BTC)
    print(f"[OPEN] order submitted: id={order.id}")
    # Wait for position to materialize
    for _ in range(20):
        positions = await ex.fetch_positions(SYMBOL)
        if positions and positions[0].side == "long":
            print(f"[OPEN] position confirmed: contracts={positions[0].contracts}, "
                  f"entry={positions[0].entry_price:.2f}")
            return
        await asyncio.sleep(1)
    raise RuntimeError("Open order submitted but position did not materialize within 20s")


async def scenario_1A_market_close(ex: OKXExchange) -> bool:
    """1A: open long + immediate market close.

    Expected wall-time: ~5-30s. Captures the close fill event.
    """
    await _ensure_no_position(ex)
    await _open_long(ex)

    captured_events.clear()  # reset before close so we only capture the close fill
    print("[CLOSE] placing market sell to close position...")
    order = await ex.create_order(SYMBOL, "sell", "market", NOTIONAL_BTC)
    print(f"[CLOSE] order submitted: id={order.id}")

    closed = await _wait_position_gone(ex, timeout_sec=60)
    if not closed:
        print("[ERROR] position not closed within 60s — abnormal")
        return False
    # Give a moment for any trailing fill events
    await asyncio.sleep(3)
    return len(captured_events) > 0


async def _fetch_mark_price(ex: OKXExchange) -> float:
    """Fetch OKX mark price via raw public endpoint.

    Demo ticker.last drifts up to 1.67% from mark price (verified via
    iter6_diag_ticker.py); OKX algo trigger validation uses mark price
    despite the 51280 error message saying "last price". Always compute
    triggers from mark price.
    """
    raw = await ex._client.public_get_public_mark_price(
        {"instType": "SWAP", "instId": "BTC-USDT-SWAP"}
    )
    if not raw.get("data"):
        raise RuntimeError(f"mark price fetch returned empty: {raw}")
    return float(raw["data"][0]["markPx"])


async def _place_algo(ex: OKXExchange, side: str, order_type: str, pct: float):
    """Place algo order with single attempt at given buffer percentage.

    Trigger computed from mark price (NOT ticker.last) because OKX algo
    validation uses mark price internally. Buffer 0.6% chosen empirically.
    """
    mark = await _fetch_mark_price(ex)
    ticker = await ex.fetch_ticker(SYMBOL)
    if order_type == "stop":
        trigger = mark * (1 - pct)
    else:  # take_profit
        trigger = mark * (1 + pct)
    label = "SL" if order_type == "stop" else "TP"
    sign = "-" if order_type == "stop" else "+"
    print(
        f"[{label}] placing at {trigger:.2f} "
        f"(mark={mark:.2f}, ticker.last={ticker.last:.2f}, drift={(ticker.last-mark)/mark*100:+.2f}%, "
        f"{sign}{pct*100:.2f}% from mark)..."
    )
    return await ex.create_order(SYMBOL, side, order_type, NOTIONAL_BTC, price=trigger)


async def scenario_1B_sl_trigger(ex: OKXExchange) -> bool:
    """1B: open long + only SL @ -0.1% from mark + wait for trigger."""
    await _ensure_no_position(ex)
    await _open_long(ex)

    sl_order = await _place_algo(ex, "sell", "stop", 0.001)
    print(f"[SL] algo order submitted: id={sl_order.id} is_algo={sl_order.is_algo}")

    captured_events.clear()  # reset before wait so we only capture the SL fill
    print(f"[SL] waiting for trigger (timeout {TIMEOUT_SEC // 3600}h)...")
    closed = await _wait_position_gone(ex, timeout_sec=TIMEOUT_SEC)

    if not closed:
        print(f"[TIMEOUT] SL did not trigger within {TIMEOUT_SEC // 3600}h")
        # Cleanup: cancel SL + close position manually
        try:
            await ex.cancel_order(sl_order.id, SYMBOL, is_algo=True)
            print(f"[CLEANUP] cancelled SL {sl_order.id}")
        except Exception as e:
            print(f"[CLEANUP] failed to cancel SL: {e}")
        try:
            await ex.create_order(SYMBOL, "sell", "market", NOTIONAL_BTC)
            print(f"[CLEANUP] manually closed position via market sell")
        except Exception as e:
            print(f"[CLEANUP] failed to close position: {e}")
        return False

    await asyncio.sleep(3)
    return len(captured_events) > 0


async def scenario_1D_market_close_with_reduce_only(ex: OKXExchange) -> bool:
    """1D: open long + market close with params={'reduceOnly': True}.

    Validates Remediation A hypothesis: does OKX echo info.reduceOnly=true
    in the fill event when the close order was submitted with reduceOnly param?

    If yes → Remediation A works (signal 1 fires for market close path).
    If no  → Remediation A insufficient → must use Remediation B (cache).

    Bypasses OKXExchange.create_order (which hardcodes params={"tdMode":"isolated"})
    by calling ex._client.create_order directly with merged params.
    """
    await _ensure_no_position(ex)
    await _open_long(ex)

    captured_events.clear()  # only capture the close fill
    print("[CLOSE] placing market sell with params={'reduceOnly': True}...")
    data = await ex._client.create_order(
        SYMBOL, "market", "sell", NOTIONAL_BTC,
        params={"tdMode": "isolated", "reduceOnly": True},
    )
    print(f"[CLOSE] order submitted: id={data.get('id')}")

    closed = await _wait_position_gone(ex, timeout_sec=60)
    if not closed:
        print("[ERROR] position not closed within 60s")
        return False
    await asyncio.sleep(3)
    return len(captured_events) > 0


async def scenario_1C_tp_trigger(ex: OKXExchange) -> bool:
    """1C: open long + only TP @ +0.1% from mark + wait for trigger."""
    await _ensure_no_position(ex)
    await _open_long(ex)

    tp_order = await _place_algo(ex, "sell", "take_profit", 0.001)
    print(f"[TP] algo order submitted: id={tp_order.id} is_algo={tp_order.is_algo}")

    captured_events.clear()
    print(f"[TP] waiting for trigger (timeout {TIMEOUT_SEC // 3600}h)...")
    closed = await _wait_position_gone(ex, timeout_sec=TIMEOUT_SEC)

    if not closed:
        print(f"[TIMEOUT] TP did not trigger within {TIMEOUT_SEC // 3600}h")
        try:
            await ex.cancel_order(tp_order.id, SYMBOL, is_algo=True)
            print(f"[CLEANUP] cancelled TP {tp_order.id}")
        except Exception as e:
            print(f"[CLEANUP] failed to cancel TP: {e}")
        try:
            await ex.create_order(SYMBOL, "sell", "market", NOTIONAL_BTC)
            print(f"[CLEANUP] manually closed position via market sell")
        except Exception as e:
            print(f"[CLEANUP] failed to close position: {e}")
        return False

    await asyncio.sleep(3)
    return len(captured_events) > 0


# ============ orchestration ============


SCENARIO_RUNNERS = {
    "1A": scenario_1A_market_close,
    "1B": scenario_1B_sl_trigger,
    "1C": scenario_1C_tp_trigger,
    "1D": scenario_1D_market_close_with_reduce_only,
}


def save_capture(scenario: str) -> Path:
    """Write captured events for a scenario to its fixture file."""
    config = SCENARIO_CONFIG[scenario]
    fixture_path = FIXTURE_DIR / config["fixture"]
    fixture_path.parent.mkdir(parents=True, exist_ok=True)

    if len(captured_events) == 1:
        # Single event — write as bare object (matches CCXT order_data shape)
        payload = captured_events[0]
    else:
        # Multiple events captured — write as list (caller picks the relevant one)
        payload = captured_events

    fixture_path.write_text(json.dumps(payload, indent=2, default=str))
    print(f"[SAVED] {len(captured_events)} event(s) → {fixture_path}")
    return fixture_path


async def run_scenario(scenario: str, ex: OKXExchange) -> int:
    """Run one scenario; returns exit code."""
    config = SCENARIO_CONFIG[scenario]
    print(f"\n{'='*60}")
    print(f"[SCENARIO {scenario}] {config['description']}")
    print(f"{'='*60}\n")

    try:
        ok = await SCENARIO_RUNNERS[scenario](ex)
    except Exception as e:
        print(f"[ERROR] scenario {scenario} failed: {e}")
        import traceback
        traceback.print_exc()
        return 4

    if not ok:
        print(f"[FAIL] scenario {scenario} did not capture any close fill event")
        if scenario in ("1B", "1C"):
            return 3  # timeout
        return 4

    save_capture(scenario)
    return 0


async def main() -> int:
    parser = argparse.ArgumentParser(description="Iter 6 Task 0 OKX capture")
    parser.add_argument(
        "--scenario", choices=["1A", "1B", "1C", "1D", "all"], required=True,
        help="Which scenario to run",
    )
    args = parser.parse_args()

    load_dotenv(".env")
    if os.environ.get("OKX_SANDBOX", "").lower() != "true":
        print("ABORT: OKX_SANDBOX must be 'true' for capture (demo only)")
        return 1

    install_capture_hook()
    print(f"[INIT] capture hook installed on OKXExchange._parse_fill_event")
    install_parse_plain_workaround()
    print(f"[INIT] _parse_plain amount=None workaround installed (upstream TODO)")

    ex = OKXExchange(
        api_key=os.environ["OKX_DEMO_API_KEY"],
        secret=os.environ["OKX_DEMO_SECRET"],
        password=os.environ["OKX_DEMO_PASSWORD"],
        symbol=SYMBOL,
        sandbox=True,
    )

    try:
        await ex.start()
        bal = await ex.fetch_balance()
        print(f"[INIT] balance USDT total={bal.total_usdt:.2f} free={bal.free_usdt:.2f}")
        if bal.total_usdt <= 0:
            print("ABORT: USDT=0 (top up demo account before running)")
            return 2

        if args.scenario == "all":
            for scenario in ("1A", "1B", "1C"):
                code = await run_scenario(scenario, ex)
                if code != 0 and scenario == "1A":
                    # 1A failure aborts whole run (it's the cheap one)
                    print(f"[ABORT] scenario 1A failed, skipping 1B/1C")
                    return code
                # 1B/1C timeout doesn't block subsequent scenarios
            return 0
        else:
            return await run_scenario(args.scenario, ex)

    finally:
        await ex.close()


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
