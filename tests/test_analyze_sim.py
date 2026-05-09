"""End-to-end tests for scripts/analyze_sim.py via subprocess."""
from __future__ import annotations
from datetime import datetime, timedelta, timezone

import pytest
import subprocess
import sys

from tests._sim_fixtures import (
    make_session, make_session_id, make_cycle, make_open_lot, make_close_fill,
    _resolve_db_path,
)
from scripts._sim_metrics import R2_7_MERGED_AT


def _run_analyze(*args, db_path):
    cmd = [sys.executable, "scripts/analyze_sim.py", *args, "--db", str(db_path)]
    return subprocess.run(cmd, capture_output=True, text=True)


async def test_analyze_session_not_found_exit_1(db_engine):
    db_path = _resolve_db_path(db_engine)
    await make_session(db_engine, name="real")
    r = _run_analyze("--session", "typo", db_path=db_path)
    assert r.returncode == 1
    assert "Session 'typo' not found" in r.stderr


async def test_analyze_db_file_missing_exit_1(tmp_path):
    r = _run_analyze("--session", "any", db_path=tmp_path / "nonexistent.db")
    assert r.returncode == 1
    assert "Database file not found" in r.stderr


async def test_analyze_session_by_name_resolves(db_engine):
    db_path = _resolve_db_path(db_engine)
    sid = await make_session(db_engine, name="my_friendly_name")
    await make_cycle(db_engine, sid, "c1")
    r = _run_analyze("--session", "my_friendly_name", db_path=db_path)
    assert r.returncode == 0
    assert "my_friendly_name" in r.stdout


async def test_analyze_out_dir_missing_exit_1(db_engine, tmp_path):
    db_path = _resolve_db_path(db_engine)
    await make_session(db_engine)
    r = _run_analyze("--session", "test_sim",
                     "--out", str(tmp_path / "noexist" / "x.md"), db_path=db_path)
    assert r.returncode == 1
    assert "Output dir" in r.stderr


async def test_analyze_runs_on_minimal_session(db_engine):
    db_path = _resolve_db_path(db_engine)
    await make_session(db_engine)
    sid = await make_session_id(db_engine, "test_sim")
    await make_cycle(db_engine, sid, "c1")
    r = _run_analyze("--session", "test_sim", db_path=db_path)
    assert r.returncode == 0
    for hdr in ["## PnL", "## Behavior", "## Cost", "## Caveats"]:
        assert hdr in r.stdout


async def test_analyze_renders_partial_close_correctly(db_engine):
    db_path = _resolve_db_path(db_engine)
    await make_session(db_engine)
    sid = await make_session_id(db_engine, "test_sim")
    for c in ["c1", "c2"]:
        await make_cycle(db_engine, sid, c)
    await make_open_lot(db_engine, sid, cycle_id="c1", amount=0.2)
    await make_close_fill(db_engine, sid, cycle_id="c2", amount=0.05, pnl_gross=100.0)
    r = _run_analyze("--session", "test_sim", db_path=db_path)
    assert r.returncode == 0
    assert "roundtrip_count" in r.stdout
    # 1 closed rt produced; lot still has remaining → caveat for unclosed
    assert "unclosed lot(s)" in r.stdout


async def test_analyze_renders_liquidation_in_exit_distribution(db_engine):
    db_path = _resolve_db_path(db_engine)
    await make_session(db_engine)
    sid = await make_session_id(db_engine, "test_sim")
    for c in ["c1", "c2"]:
        await make_cycle(db_engine, sid, c)
    await make_open_lot(db_engine, sid, cycle_id="c1")
    await make_close_fill(db_engine, sid, cycle_id="c2", exit_type="liquidation",
                          pnl_gross=-50.0)
    r = _run_analyze("--session", "test_sim", db_path=db_path)
    assert "exit_type[liquidation]" in r.stdout
    assert "liquidation event(s)" in r.stdout


async def test_analyze_markdown_section_order_pnl_behavior_cost_caveats(db_engine):
    db_path = _resolve_db_path(db_engine)
    await make_session(db_engine)
    sid = await make_session_id(db_engine, "test_sim")
    await make_cycle(db_engine, sid, "c1")
    r = _run_analyze("--session", "test_sim", db_path=db_path)
    out = r.stdout
    pnl = out.find("## PnL")
    beh = out.find("## Behavior")
    cost = out.find("## Cost")
    cav = out.find("## Caveats")
    assert 0 < pnl < beh < cost < cav


async def test_analyze_emits_all_28_metric_groups(db_engine):
    """Every key in METRIC_GROUPS shows up as a row label in stdout."""
    db_path = _resolve_db_path(db_engine)
    await make_session(db_engine)
    sid = await make_session_id(db_engine, "test_sim")
    decision = ("(1) Stance: bull\n(2) Active commitments: x\n"
                "(3) This cycle delta: x\n(4) Thesis invalidation: x\n(5) Watch list: x")
    base = R2_7_MERGED_AT + timedelta(days=2)
    for i in range(10):
        await make_cycle(db_engine, sid, f"c{i}", decision=decision,
                         state_snapshot=f'{{"balance":{{"total_usdt":{100+i}}}}}')
    for i in range(3):
        oc, cc = f"c{2*i}", f"c{2*i+1}"
        await make_open_lot(db_engine, sid, cycle_id=oc,
                            filled_at=base + timedelta(minutes=i*20))
        await make_close_fill(db_engine, sid, cycle_id=cc, pnl_gross=10.0,
                              filled_at=base + timedelta(minutes=i*20+5))
    r = _run_analyze("--session", "test_sim", db_path=db_path)
    assert r.returncode == 0
    # Every METRIC_GROUPS key (or its split sub-rows) is present.
    expected_substrings = [
        # PnL
        "win_rate", "total_pnl_net", "roundtrip_count",
        "avg_fifo_pnl_per_roundtrip", "avg_roundtrip_duration_min",
        "median_roundtrip_duration_min", "max_drawdown_pct",
        "exit_type[market]", "largest_win", "largest_loss", "profit_factor",
        # Cost
        "total_input_tokens", "total_output_tokens", "total_cache_read_tokens",
        "avg_cache_hit_rate", "tokens_per_cycle_p50", "tokens_per_cycle_p95",
        "avg_wall_time_ms", "avg_llm_call_ms", "avg_tool_total_ms",
        "per_tool_call_top10",
        # Behavior
        "total_cycles", "ok_count", "forensic_count",
        "triggered_by[", "decision_type[",
        "5field_complete_rate", "has_stance",
        "avg_decision_length_chars", "decision_length_p95",
        "retraction_rate", "avg_reasoning_tokens", "avg_thinking_chars",
        # alert_lifecycle_summary expands to 3 sub-rows
        "alert_triggered_rate", "alert_cancelled_rate",
        "alert_avg_cancel_attempt_count",
    ]
    for s in expected_substrings:
        assert s in r.stdout, f"missing render: {s!r}"
