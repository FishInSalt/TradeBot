"""End-to-end tests for scripts/diff_sim.py via subprocess."""
from __future__ import annotations
from datetime import datetime, timedelta, timezone

import pytest
import re
import subprocess
import sys

from tests._sim_fixtures import (
    make_session, make_cycle, make_open_lot, make_close_fill,
    make_session_id, _resolve_db_path,
)
from scripts._sim_metrics import R2_7_MERGED_AT


def _run_diff(*args, db_path):
    cmd = [sys.executable, "scripts/diff_sim.py", *args, "--db", str(db_path)]
    return subprocess.run(cmd, capture_output=True, text=True)


def _row_for(out: str, metric: str) -> str | None:
    """Find the row containing | <metric> |. Returns the line or None."""
    for line in out.splitlines():
        if line.startswith("|") and f"| {metric} " in line:
            return line
    return None


async def _seed_pnl_session(engine, name, total_pnl):
    """Single roundtrip session with controlled total_pnl_net.

    Auto-fee per C-2: open_fee = 0.1*80000*0.0005 = 4.0; close_fee = 0.1*82000*0.0005 = 4.1;
    rt.fee_total = 8.1. P2 = sim_realized_gross - rt.fee_total.
    Set pnl_gross = total_pnl + 8.1 → P2 == total_pnl exactly.

    Full close (lot 0.1 consumed entirely) → 1 rt, no unclosed lot.
    """
    sid = await make_session(engine, name=name)
    await make_cycle(engine, sid, "c1")
    await make_cycle(engine, sid, "c2")
    await make_open_lot(engine, sid, cycle_id="c1")
    await make_close_fill(engine, sid, cycle_id="c2",
                          pnl_gross=total_pnl + 8.1)
    return sid


async def test_diff_basic_two_sessions(db_engine):
    """A 2 cycles, B 3 cycles → total_cycles row Δ=+1, Δ%=+50%, flag=🔴."""
    db_path = _resolve_db_path(db_engine)
    await make_session(db_engine, name="sim_a")
    await make_session(db_engine, name="sim_b")
    sid_a = await make_session_id(db_engine, "sim_a")
    sid_b = await make_session_id(db_engine, "sim_b")
    for c in ["c1", "c2"]:
        await make_cycle(db_engine, sid_a, c)
    for c in ["c1", "c2", "c3"]:
        await make_cycle(db_engine, sid_b, c)
    r = _run_diff("--a", "sim_a", "--b", "sim_b", db_path=db_path)
    assert r.returncode == 0
    assert "| Sim A | Sim B | Δ | Δ% | Flag |" in r.stdout
    row = _row_for(r.stdout, "total_cycles")
    assert row is not None
    assert "+1" in row
    assert "+50.0%" in row
    assert "🔴" in row


async def test_diff_pnl_negative_to_positive_returns_na_pct(db_engine):
    """sim_a PnL≈-81, sim_b PnL≈+120 → Δ%='n/a', |Δ|=201 ≥ 200 → 🔴."""
    db_path = _resolve_db_path(db_engine)
    await _seed_pnl_session(db_engine, "neg", -81.0)
    await _seed_pnl_session(db_engine, "pos", 120.0)
    r = _run_diff("--a", "neg", "--b", "pos", db_path=db_path)
    assert r.returncode == 0
    row = _row_for(r.stdout, "total_pnl_net")
    assert "n/a" in row
    assert "🔴" in row


async def test_diff_zero_divisor_returns_na_pct(db_engine):
    """sim_a roundtrip_count=0, sim_b=2 → Δ=+2 Δ%='n/a'; non-PnL → ⚠️ (|Δ|>0)."""
    db_path = _resolve_db_path(db_engine)
    await make_session(db_engine, name="empty")
    sid_e = await make_session_id(db_engine, "empty")
    await make_cycle(db_engine, sid_e, "c1")
    await _seed_pnl_session(db_engine, "with_rts", 100.0)
    r = _run_diff("--a", "empty", "--b", "with_rts", db_path=db_path)
    row = _row_for(r.stdout, "roundtrip_count")
    assert "n/a" in row
    assert "⚠️" in row


async def test_diff_threshold_warn_at_10pct_inclusive(db_engine):
    """A 10 cycles, B 11 cycles → +10.0% inclusive → ⚠️."""
    db_path = _resolve_db_path(db_engine)
    await make_session(db_engine, name="sim_10")
    await make_session(db_engine, name="sim_11")
    sid_a = await make_session_id(db_engine, "sim_10")
    sid_b = await make_session_id(db_engine, "sim_11")
    for i in range(10):
        await make_cycle(db_engine, sid_a, f"c{i}")
    for i in range(11):
        await make_cycle(db_engine, sid_b, f"c{i}")
    r = _run_diff("--a", "sim_10", "--b", "sim_11", db_path=db_path)
    row = _row_for(r.stdout, "total_cycles")
    assert "+10.0%" in row
    assert "⚠️" in row and "🔴" not in row


async def test_diff_threshold_crit_at_30pct_inclusive(db_engine):
    """A 10, B 13 → +30.0% inclusive → 🔴."""
    db_path = _resolve_db_path(db_engine)
    await make_session(db_engine, name="sim_x10")
    await make_session(db_engine, name="sim_x13")
    sid_a = await make_session_id(db_engine, "sim_x10")
    sid_b = await make_session_id(db_engine, "sim_x13")
    for i in range(10):
        await make_cycle(db_engine, sid_a, f"c{i}")
    for i in range(13):
        await make_cycle(db_engine, sid_b, f"c{i}")
    r = _run_diff("--a", "sim_x10", "--b", "sim_x13", db_path=db_path)
    row = _row_for(r.stdout, "total_cycles")
    assert "+30.0%" in row
    assert "🔴" in row


async def test_diff_pnl_threshold_50_usdt_inclusive(db_engine):
    """sim_a PnL=0, sim_b PnL=+50 → |Δ|=50 inclusive → ⚠️."""
    db_path = _resolve_db_path(db_engine)
    await _seed_pnl_session(db_engine, "p0", 0.0)
    await _seed_pnl_session(db_engine, "p50", 50.0)
    r = _run_diff("--a", "p0", "--b", "p50", db_path=db_path)
    row = _row_for(r.stdout, "total_pnl_net")
    assert "⚠️" in row and "🔴" not in row


async def test_diff_pnl_threshold_200_usdt_inclusive(db_engine):
    """sim_a PnL=0, sim_b PnL=+200 → |Δ|=200 inclusive → 🔴."""
    db_path = _resolve_db_path(db_engine)
    await _seed_pnl_session(db_engine, "p0_2", 0.0)
    await _seed_pnl_session(db_engine, "p200", 200.0)
    r = _run_diff("--a", "p0_2", "--b", "p200", db_path=db_path)
    row = _row_for(r.stdout, "total_pnl_net")
    assert "🔴" in row


# === T14: rate flag + _compute_row_flag dispatch unit tests ===


def test_flag_rate_91_to_92_no_flag():
    from scripts.diff_sim import _flag_by_rate
    assert _flag_by_rate(1.0, 1.1) == "—"


def test_flag_rate_91_to_96_warn_via_pp_only():
    from scripts.diff_sim import _flag_by_rate
    assert _flag_by_rate(5.0, 5.5) == "⚠️"


def test_flag_rate_5_to_10_crit_via_pct_promotes():
    from scripts.diff_sim import _flag_by_rate
    assert _flag_by_rate(5.0, 100.0) == "🔴"


def test_flag_rate_50_to_35_crit_inclusive():
    from scripts.diff_sim import _flag_by_rate
    assert _flag_by_rate(-15.0, -30.0) == "🔴"


def test_flag_rate_below_5pp_no_flag():
    from scripts.diff_sim import _flag_by_rate
    assert _flag_by_rate(3.0, 3.2) == "—"


def test_flag_pct_inclusive_at_10():
    from scripts.diff_sim import _flag_by_pct
    assert _flag_by_pct(10.0) == "⚠️"
    assert _flag_by_pct(9.9) == "—"


def test_flag_pct_inclusive_at_30():
    from scripts.diff_sim import _flag_by_pct
    assert _flag_by_pct(30.0) == "🔴"
    assert _flag_by_pct(29.9) == "⚠️"


def test_flag_pnl_abs_inclusive_at_50_200():
    from scripts.diff_sim import _flag_by_pnl_abs
    assert _flag_by_pnl_abs(50.0) == "⚠️"
    assert _flag_by_pnl_abs(49.9) == "—"
    assert _flag_by_pnl_abs(200.0) == "🔴"
    assert _flag_by_pnl_abs(199.9) == "⚠️"


# _compute_row_flag — spec §5.5 missing-value dispatch.

def test_compute_row_flag_both_none():
    from scripts.diff_sim import _compute_row_flag
    assert _compute_row_flag(None, None, "count") == "—"


def test_compute_row_flag_a_has_b_none_signal_lost():
    from scripts.diff_sim import _compute_row_flag
    assert _compute_row_flag(0.5, None, "rate") == "⚠️"


def test_compute_row_flag_a_none_b_has_signal_new():
    from scripts.diff_sim import _compute_row_flag
    assert _compute_row_flag(None, 0.5, "rate") == "⚠️"


def test_compute_row_flag_zero_divisor_non_pnl_warn():
    """Spec §5.5: a=0 (non-PnL), |Δ|>0 → ⚠️ regardless of Δ%='n/a'."""
    from scripts.diff_sim import _compute_row_flag
    assert _compute_row_flag(0, 2, "count") == "⚠️"
    assert _compute_row_flag(0, 0, "count") == "—"


def test_compute_row_flag_pnl_cross_zero_uses_abs():
    """Spec §5.4: PnL uses |Δ| absolute even when Δ% n/a (cross-zero)."""
    from scripts.diff_sim import _compute_row_flag
    # a=-81, b=120 → |Δ|=201 ≥ 200 → 🔴
    assert _compute_row_flag(-81.0, 120.0, "sum_pnl") == "🔴"
    # a=0, b=50 → |Δ|=50 inclusive → ⚠️
    assert _compute_row_flag(0.0, 50.0, "sum_pnl") == "⚠️"


def test_compute_row_flag_count_uses_pct():
    """Counts judged by Δ% threshold."""
    from scripts.diff_sim import _compute_row_flag
    assert _compute_row_flag(10, 11, "count") == "⚠️"   # +10%
    assert _compute_row_flag(10, 13, "count") == "🔴"   # +30%


def test_compute_row_flag_avg_pnl_prefers_pct_falls_back_to_abs():
    """Spec §5.3: avg_pnl prefers Δ%; falls back to PnL |Δ| when Δ% n/a."""
    from scripts.diff_sim import _compute_row_flag
    # divergent +30%: Δ% triggers 🔴 ahead of |Δ|
    assert _compute_row_flag(10.0, 13.0, "avg_pnl") == "🔴"
    # cross-zero: Δ% n/a, fall back to PnL abs (|Δ|=10 < 50 → —)
    assert _compute_row_flag(-5.0, 5.0, "avg_pnl") == "—"
    # cross-zero |Δ|=60 ≥ 50 → ⚠️
    assert _compute_row_flag(-30.0, 30.0, "avg_pnl") == "⚠️"


# === T15: distributions + missing values + caveats reuse ===


async def test_diff_distribution_expansion(db_engine):
    """exit_type with key only on one side → key union, missing → 0%."""
    db_path = _resolve_db_path(db_engine)
    # A: 1 market roundtrip
    await make_session(db_engine, name="exit_a")
    sid_a = await make_session_id(db_engine, "exit_a")
    for c in ["c1", "c2"]:
        await make_cycle(db_engine, sid_a, c)
    await make_open_lot(db_engine, sid_a, cycle_id="c1")
    await make_close_fill(db_engine, sid_a, cycle_id="c2", exit_type="market", pnl_gross=10.0)
    # B: 1 liquidation
    await make_session(db_engine, name="exit_b")
    sid_b = await make_session_id(db_engine, "exit_b")
    for c in ["c1", "c2"]:
        await make_cycle(db_engine, sid_b, c)
    await make_open_lot(db_engine, sid_b, cycle_id="c1")
    await make_close_fill(db_engine, sid_b, cycle_id="c2", exit_type="liquidation",
                          pnl_gross=-50.0)
    r = _run_diff("--a", "exit_a", "--b", "exit_b", db_path=db_path)
    assert r.returncode == 0
    assert "exit_type[market]" in r.stdout
    assert "exit_type[liquidation]" in r.stdout


async def test_diff_a_equals_b_warning(db_engine):
    db_path = _resolve_db_path(db_engine)
    await make_session(db_engine, name="same")
    sid = await make_session_id(db_engine, "same")
    await make_cycle(db_engine, sid, "c1")
    r = _run_diff("--a", "same", "--b", "same", db_path=db_path)
    assert "WARNING: A and B refer to same session" in r.stdout


async def test_diff_cross_symbol_warning(db_engine):
    db_path = _resolve_db_path(db_engine)
    await make_session(db_engine, name="btc_sim", symbol="BTC/USDT:USDT")
    await make_session(db_engine, name="eth_sim", symbol="ETH/USDT:USDT")
    sid_a = await make_session_id(db_engine, "btc_sim")
    sid_b = await make_session_id(db_engine, "eth_sim")
    await make_cycle(db_engine, sid_a, "c1")
    await make_cycle(db_engine, sid_b, "c1")
    r = _run_diff("--a", "btc_sim", "--b", "eth_sim", db_path=db_path)
    assert "A=BTC/USDT:USDT, B=ETH/USDT:USDT" in r.stdout
    assert r.returncode == 0


async def test_diff_caveats_aggregated_per_side(db_engine):
    """A 1 unclosed lot, B 0 → caveats prefixed [A] / [B]."""
    db_path = _resolve_db_path(db_engine)
    await make_session(db_engine, name="A_unclosed")
    sid_a = await make_session_id(db_engine, "A_unclosed")
    await make_cycle(db_engine, sid_a, "c1")
    await make_open_lot(db_engine, sid_a, cycle_id="c1")  # no close → unclosed
    await make_session(db_engine, name="B_clean")
    sid_b = await make_session_id(db_engine, "B_clean")
    await make_cycle(db_engine, sid_b, "c1")
    r = _run_diff("--a", "A_unclosed", "--b", "B_clean", db_path=db_path)
    assert "[A] 1 unclosed lot(s)" in r.stdout


async def test_diff_missing_value_a_has_b_none(db_engine):
    """A has retraction_rate (≥1 valid pair), B has 0 cycles → flag=⚠️."""
    db_path = _resolve_db_path(db_engine)
    await make_session(db_engine, name="A_with")
    sid_a = await make_session_id(db_engine, "A_with")
    await make_cycle(db_engine, sid_a, "c1", decision="(1) Stance: bull")
    await make_cycle(db_engine, sid_a, "c2", decision="(1) Stance: bear")
    await make_session(db_engine, name="B_empty")
    r = _run_diff("--a", "A_with", "--b", "B_empty", db_path=db_path)
    row = _row_for(r.stdout, "retraction_rate")
    assert "⚠️" in row


async def test_diff_missing_value_a_none_b_has(db_engine):
    """Symmetric: A 0 cycles / B has data → flag=⚠️."""
    db_path = _resolve_db_path(db_engine)
    await make_session(db_engine, name="A_empty2")
    await make_session(db_engine, name="B_with2")
    sid_b = await make_session_id(db_engine, "B_with2")
    await make_cycle(db_engine, sid_b, "c1", decision="(1) Stance: bull")
    await make_cycle(db_engine, sid_b, "c2", decision="(1) Stance: bear")
    r = _run_diff("--a", "A_empty2", "--b", "B_with2", db_path=db_path)
    row = _row_for(r.stdout, "retraction_rate")
    assert "⚠️" in row
