"""P4 drift guards: Guard A (P4 field coverage) + Guard B (retry-loop prompt
invariant) + Guard C (max_wake helper consistency).

Guard A and Guard B added in Task 5 — only Guard C in this initial scaffold.
"""
import pytest


def test_p4_runtime_config_matches_build_services():
    """Guard C: max_wake helper output equals the formula used in build_services
    (defensive against the formula being changed in only one of the two call sites).

    Note: this test pins the formula explicitly. If product policy changes
    (e.g. ceiling moves from 180 to 240), update both the helper and this test.
    """
    from src.cli.app import _compute_max_wake

    # 4 representative inputs spanning the floor / mid / ceiling regimes
    cases = [
        (5, 60),    # 4 * 5 = 20 → max(20, 60) = 60 → min(60, 180) = 60 (floor)
        (15, 60),   # 4 * 15 = 60 → 60 (floor exactly)
        (30, 120),  # 4 * 30 = 120 → 120 (mid)
        (60, 180),  # 4 * 60 = 240 → max(240, 60) = 240 → min(240, 180) = 180 (ceiling)
    ]
    for scheduler_interval_min, expected in cases:
        actual = _compute_max_wake(scheduler_interval_min)
        assert actual == expected, (
            f"_compute_max_wake({scheduler_interval_min}) = {actual}, "
            f"expected {expected}. Helper drifted from build_services formula."
        )
