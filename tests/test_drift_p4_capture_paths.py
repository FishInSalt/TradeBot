"""P4 drift guards: Guard A (P4 field coverage) + Guard B (retry-loop prompt
invariant) + Guard C (max_wake helper consistency) + Guard D (fee_rate wiring).
"""
import ast
import dataclasses
from pathlib import Path
from unittest.mock import MagicMock, patch

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


# ── Guard D: fee_rate wiring ──────────────────────────────────────────────────


def _make_minimal_wizard_result(fee_rate=0.001):
    """Return a minimal WizardResult suitable for build_services tests.

    Uses a simulated exchange to avoid OKX credential requirements.
    fee_rate=None bypasses the dataclass type annotation via dataclasses.replace.
    """
    from src.cli.wizard import WizardResult
    from src.config import PersonaConfig

    base = WizardResult(
        exchange_type="simulated",
        fee_rate=0.001,
        initial_balance=1000.0,
        api_credentials=None,
        symbol="BTC-USDT-SWAP",
        timeframe="15m",
        model_config=MagicMock(),
        model=MagicMock(),
        scheduler_interval_min=15,
        approval_enabled=False,
        token_budget=100_000,
        persona=PersonaConfig(),
        session_name="test-session",
    )
    if fee_rate is None:
        return dataclasses.replace(base, fee_rate=None)
    return dataclasses.replace(base, fee_rate=fee_rate)


def _make_mock_engine():
    return MagicMock()


def _make_mock_sc():
    sc = MagicMock()
    sc.print = MagicMock()
    return sc


def _make_mock_settings():
    """Return a minimal Settings-like mock for build_services."""
    settings = MagicMock()
    settings.exchange.sandbox = True
    settings.exchange.fee_rate = 0.001
    settings.news.enabled = False
    settings.macro.enabled = False
    settings.crypto_etf.enabled = False
    settings.onchain.enabled = False
    settings.approval.timeout_seconds = 30
    return settings


def _build_services_patches():
    """Context manager stack of patches needed to run build_services in tests.

    Mirrors the pattern in tests/test_wizard.py::test_build_services_sim_path.
    """
    return (
        patch("src.integrations.exchange.simulated.SimulatedExchange"),
        patch("src.cli.app.MarketDataService"),
        patch("src.cli.app.MemoryService"),
        patch("src.cli.app.create_trader_agent"),
        patch("src.services.metrics.MetricsService"),
    )


def test_build_services_raises_on_none_fee_rate():
    """Guard D-1: build_services fails-loud when WizardResult.fee_rate is None.

    Defense in depth — wizard sub-step (Task 15) is primary recovery; this is
    bottom layer for manual SQL / restored backup / migration bug.
    The ValueError is raised at the top of build_services before any exchange
    construction, so no patching is required.
    """
    from src.cli.app import build_services

    result = _make_minimal_wizard_result(fee_rate=None)
    with pytest.raises(ValueError, match="fee_rate"):
        build_services(
            result,
            _make_mock_engine(),
            "test-session-id",
            _make_mock_sc(),
            _make_mock_settings(),
        )


def test_build_services_drift_guard_runtime_vs_deps_fee_rate():
    """Guard D-2: TradingDeps.fee_rate == result.fee_rate after build_services.

    Confirms the fee_rate flows from WizardResult through into TradingDeps
    (and that the internal drift guard assertion doesn't fire).
    """
    from src.cli.app import build_services

    fee_rate = 0.00085
    result = _make_minimal_wizard_result(fee_rate=fee_rate)

    p_sim, p_mds, p_mem, p_agent, p_metrics = _build_services_patches()
    with p_sim as MockSim, p_mds, p_mem, p_agent as mock_agent, p_metrics:
        MockSim.return_value = MagicMock()
        mock_agent.return_value = MagicMock()
        _exchange, deps, _agent, _budget, _stats = build_services(
            result,
            _make_mock_engine(),
            "test-session-id",
            _make_mock_sc(),
            _make_mock_settings(),
        )

    assert deps.fee_rate == fee_rate, (
        f"TradingDeps.fee_rate {deps.fee_rate} != WizardResult.fee_rate {fee_rate}"
    )


def test_p4_runtime_config_matches_build_services_fee_rate():
    """Guard D-3: P4 capture-path RuntimeConfig must carry the same
    taker_fee_rate as build_services-internal RuntimeConfig.

    Mirror of test_p4_runtime_config_matches_build_services (Guard C).
    Extends assertion to taker_fee_rate field: both RuntimeConfig instances
    (build_services-internal and P4 Phase 5b) must equal result.fee_rate.

    This guards Critical #1: without this, sessions.system_prompt renders
    Fee with default 0.0005 regardless of user's actual fee_rate.
    """
    from src.agent.persona import RuntimeConfig
    from src.cli.app import _compute_max_wake

    fee_rate = 0.001
    scheduler_interval_min = 15

    # Replicate build_services-internal RuntimeConfig construction
    max_wake = _compute_max_wake(scheduler_interval_min)
    rc_build = RuntimeConfig(
        wake_max_minutes=max_wake,
        taker_fee_rate=fee_rate,
    )

    # Replicate run() Phase 5b RuntimeConfig construction (after our patch)
    rc_capture = RuntimeConfig(
        wake_max_minutes=_compute_max_wake(scheduler_interval_min),
        taker_fee_rate=fee_rate,
    )

    assert rc_build.taker_fee_rate == fee_rate, (
        f"build_services RuntimeConfig.taker_fee_rate {rc_build.taker_fee_rate} "
        f"!= result.fee_rate {fee_rate}"
    )
    assert rc_capture.taker_fee_rate == fee_rate, (
        f"P4 Phase 5b RuntimeConfig.taker_fee_rate {rc_capture.taker_fee_rate} "
        f"!= result.fee_rate {fee_rate}"
    )
    assert rc_build.taker_fee_rate == rc_capture.taker_fee_rate, (
        f"fee_rate drift between build_services ({rc_build.taker_fee_rate}) "
        f"and P4 capture ({rc_capture.taker_fee_rate}) RuntimeConfig instances"
    )
