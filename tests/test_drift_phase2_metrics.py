"""Drift guards for Phase 2 cross-sim analytics."""
from datetime import datetime, timezone
from pathlib import Path

import pytest
from sqlalchemy import Integer

from scripts._sim_metrics import METRIC_GROUPS, R2_7_MERGED_AT, exit_type_distribution
from src.storage.models import SimOrder


def test_metric_groups_inventory_28():
    """Single source: METRIC_GROUPS list has exactly 28 group keys.

    Future additions intentionally break this test → reviewer must
    update spec §3 + METRIC_GROUPS together.
    """
    assert len(METRIC_GROUPS) == 28
    assert len(set(METRIC_GROUPS)) == 28


def test_metric_groups_split_into_3_dimensions():
    """10 PnL + 8 Cost + 10 Behavior verified by group key partition."""
    pnl_keys = {"win_rate", "total_pnl_net", "roundtrip_count",
                "avg_fifo_pnl_per_roundtrip",
                "avg_roundtrip_duration_min", "median_roundtrip_duration_min",
                "max_drawdown_pct", "exit_type_distribution",
                "largest_win_loss", "profit_factor"}
    cost_keys = {"total_input_tokens", "total_output_tokens",
                 "total_cache_read_tokens", "avg_cache_hit_rate",
                 "tokens_per_cycle_percentile", "avg_wall_time_ms",
                 "llm_tool_avg_pair", "per_tool_call_top10"}
    behavior_keys = {"total_cycles", "ok_vs_forensic_count",
                     "triggered_by_distribution", "decision_type_distribution",
                     "five_field_complete_rate", "per_field_hit_rate",
                     "decision_length_avg_p95", "retraction_rate",
                     "reasoning_avg_pair", "alert_lifecycle_summary"}
    assert len(pnl_keys) == 10
    assert len(cost_keys) == 8
    assert len(behavior_keys) == 10
    assert set(METRIC_GROUPS) == (pnl_keys | cost_keys | behavior_keys)


def test_caveat_templates_match_section_6_3():
    """Spec §6.3 lists 10 caveat templates. Verify all 10 substrings present
    in scripts/_sim_metrics.py source (covering both per-side + diff-only helpers)."""
    src = Path("scripts/_sim_metrics.py").read_text()
    expected_fragments = [
        "Session has 0 ok cycles",
        "0 closed roundtrips",
        "unclosed lot(s) at session end",
        "invariant violation(s)",
        "liquidation event(s)",
        "stale close amount(s)",
        "forensic cycle(s)",
        "rows with NULL",
        "WARNING: A and B refer to same session",
        "WARNING: A=",
    ]
    for frag in expected_fragments:
        assert frag in src, f"caveat template missing in caveat helpers: {frag!r}"
    assert len(expected_fragments) == 10  # spec §6.3 inventory


def test_section_order_pnl_behavior_cost_caveats():
    """Markdown output: ## PnL → ## Behavior → ## Cost → ## Caveats.

    Uses rfind to land on the call sites (last occurrence), not the
    function definitions which appear earlier in the file.
    """
    src = Path("scripts/analyze_sim.py").read_text()
    pnl = src.rfind("_render_pnl(")
    beh = src.rfind("_render_behavior(")
    cost = src.rfind("_render_cost(")
    cav = src.rfind("_render_caveats(")
    assert 0 < pnl < beh < cost < cav, \
        f"section render call order wrong: pnl={pnl} beh={beh} cost={cost} cav={cav}"


def test_exit_type_5_enum():
    dist = exit_type_distribution([])
    assert set(dist.keys()) == {"market", "stop", "take_profit", "limit", "liquidation"}


def test_r2_7_merged_at_constant_matches_pr35():
    assert R2_7_MERGED_AT == datetime(2026, 5, 2, tzinfo=timezone.utc)


def test_v_order_lifecycle_originated_cycle_id_column_present():
    content = Path("src/storage/views.py").read_text()
    assert "v_order_lifecycle" in content
    assert "originated_cycle_id" in content


def test_v_alert_lifecycle_cancel_attempt_count_column_present():
    """alert_lifecycle_summary reads cancel_attempt_count (not cancel_attempts);
    drift if view renames the column."""
    content = Path("src/storage/views.py").read_text()
    assert "cancel_attempt_count" in content


def test_simorder_id_is_int_pk():
    """§4.2 same-tick tiebreaker `ORDER BY filled_at, id` requires Integer PK."""
    assert isinstance(SimOrder.__table__.c.id.type, Integer)
