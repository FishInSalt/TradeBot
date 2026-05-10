"""P4 drift guards: Guard A (P4 field coverage) + Guard B (retry-loop prompt
invariant) + Guard C (max_wake helper consistency).
"""
import ast
from pathlib import Path

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


def test_all_agentcycle_inserts_include_user_prompt_snapshot():
    """Guard A: every AgentCycle(...) call in src/cli/app.py includes the
    user_prompt_snapshot keyword argument. Catches future code paths added
    without P4 capture coverage.

    Uses ast.parse instead of regex because AgentCycle(...) bodies contain
    nested calls (json.dumps, datetime arithmetic, etc.) that defeat
    paren-balance regex approaches.
    """
    src = Path("src/cli/app.py").read_text()
    tree = ast.parse(src)

    insert_calls = [
        node for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id == "AgentCycle"
    ]

    assert len(insert_calls) >= 3, (
        f"Expected ≥3 AgentCycle inserts, found {len(insert_calls)}. "
        f"src/cli/app.py 3 INSERT paths (happy / usage_limit / retry_exhausted) "
        f"are P4 forensic anchors — confirm they still exist."
    )

    for call in insert_calls:
        keyword_names = {kw.arg for kw in call.keywords}
        assert "user_prompt_snapshot" in keyword_names, (
            f"AgentCycle insert at line {call.lineno} missing "
            f"user_prompt_snapshot keyword — P4 capture incomplete. "
            f"Existing keywords: {sorted(keyword_names)}"
        )


def test_retry_loop_does_not_reassign_prompt():
    """Guard B: AC-10 invariant — retry loop body never reassigns 'prompt'.

    Retry loop is `for attempt in range(3):` — body must not contain any
    Assign / AugAssign / AnnAssign with target name 'prompt'. If a future
    iteration introduces ModelRetry-style prompt rewriting, P4 capture path
    must be redesigned (per-attempt snapshot or attempt-level field) before
    that change ships.
    """
    src = Path("src/cli/app.py").read_text()
    tree = ast.parse(src)

    retry_loops = [
        node for node in ast.walk(tree)
        if isinstance(node, ast.For)
        and isinstance(node.iter, ast.Call)
        and isinstance(node.iter.func, ast.Name)
        and node.iter.func.id == "range"
        and isinstance(node.target, ast.Name)
        and node.target.id == "attempt"
    ]

    assert len(retry_loops) >= 1, (
        "Expected retry loop 'for attempt in range(...)' not found in src/cli/app.py — "
        "spec §4.2 retry semantics may have shifted."
    )

    for loop in retry_loops:
        for stmt in ast.walk(loop):
            # Three assignment forms must all be checked: regular (=),
            # augmented (+=, etc.), annotated (: T = ...).
            targets: list[ast.expr] = []
            if isinstance(stmt, ast.Assign):
                targets.extend(stmt.targets)
            elif isinstance(stmt, ast.AugAssign):
                targets.append(stmt.target)
            elif isinstance(stmt, ast.AnnAssign):
                targets.append(stmt.target)
            else:
                continue
            for target in targets:
                if isinstance(target, ast.Name) and target.id == "prompt":
                    raise AssertionError(
                        f"retry loop body re-assigns 'prompt' at line {stmt.lineno} "
                        f"({type(stmt).__name__}) — violates AC-10 invariant. "
                        f"P4 user_prompt_snapshot will diverge from actually-sent prompt; "
                        f"P4 capture path must be rewritten (per-attempt capture / "
                        f"attempt-level field) before this change ships."
                    )
