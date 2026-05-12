"""Iter w2r2-next-e Alert family treatment tests.

See: docs/superpowers/specs/2026-05-12-iter-w2r2-next-e-alert-family-design.md
"""
from __future__ import annotations

import inspect
from unittest.mock import AsyncMock, MagicMock

import pytest
from sqlalchemy import select

from src.storage.database import get_session
from src.storage.models import ToolCall
from tests.test_tool_call_recorder import make_call, make_ctx, make_deps


# ============ Task 1: _lookup_alert helper ============

def test_lookup_alert_returns_dict_when_present():
    """_lookup_alert returns the full alert dict when id matches."""
    from src.agent.tools_execution import _lookup_alert

    exchange = MagicMock()
    exchange.get_price_level_alerts.return_value = [
        {"id": "a3f2b8c1", "price": 82100.0, "direction": "above",
         "symbol": "BTC/USDT:USDT", "reasoning": "4h high"},
        {"id": "d7c2e9f4", "price": 81720.0, "direction": "below",
         "symbol": "BTC/USDT:USDT", "reasoning": "1h low"},
    ]

    result = _lookup_alert(exchange, "a3f2b8c1")
    assert result == {
        "id": "a3f2b8c1", "price": 82100.0, "direction": "above",
        "symbol": "BTC/USDT:USDT", "reasoning": "4h high",
    }


def test_lookup_alert_returns_none_when_absent():
    """_lookup_alert returns None when id not in the list."""
    from src.agent.tools_execution import _lookup_alert

    exchange = MagicMock()
    exchange.get_price_level_alerts.return_value = [
        {"id": "a3f2b8c1", "price": 82100.0, "direction": "above",
         "symbol": "BTC/USDT:USDT", "reasoning": "4h high"},
    ]

    result = _lookup_alert(exchange, "ffffffff")
    assert result is None


# ============ Task 2: cancel idempotent + F-A3 reasoning ============

@pytest.mark.asyncio
async def test_cancel_idempotent_not_found(engine, session_with_row):
    """Spec §3.2: cancel of an absent alert_id returns ok with idempotent note,
    no biz_error recorded. Closes F-F1 (sim #8 40% biz_error rate).
    """
    from src.agent.tools_execution import cancel_price_level_alert
    from src.services.tool_call_recorder import ToolCallRecorder

    recorder = ToolCallRecorder()
    deps = make_deps(engine, session_with_row)
    deps.exchange.get_price_level_alerts.return_value = []  # empty: id absent
    deps.exchange.remove_price_level_alert.return_value = False

    async def handler(args):
        return await cancel_price_level_alert(
            deps, alert_id="a3f2b8c1", reasoning="auto-cleared check",
        )

    result = await recorder.wrap_tool_execute(
        make_ctx(deps),
        call=make_call("cancel_price_level_alert"),
        tool_def=MagicMock(),
        args={},
        handler=handler,
    )

    assert "no longer active" in result
    assert "already triggered or removed" in result

    async with get_session(engine) as db:
        rows = (await db.execute(select(ToolCall))).scalars().all()
    assert len(rows) == 1
    assert rows[0].status == "ok"  # idempotent ok — NOT biz_error
    assert rows[0].error_type is None


@pytest.mark.asyncio
async def test_cancel_format_invalid_still_rejects(engine, session_with_row):
    """Spec §3.2: format-invalid alert_id (non-hex / wrong length) still
    records biz_error 'invalid_alert_id_format' — idempotency does not apply.
    """
    from src.agent.tools_execution import cancel_price_level_alert
    from src.services.tool_call_recorder import ToolCallRecorder

    recorder = ToolCallRecorder()
    deps = make_deps(engine, session_with_row)

    async def handler(args):
        return await cancel_price_level_alert(
            deps, alert_id="NOT-HEX!", reasoning="t",
        )

    result = await recorder.wrap_tool_execute(
        make_ctx(deps),
        call=make_call("cancel_price_level_alert"),
        tool_def=MagicMock(),
        args={},
        handler=handler,
    )

    assert "Invalid alert_id format" in result

    async with get_session(engine) as db:
        rows = (await db.execute(select(ToolCall))).scalars().all()
    assert len(rows) == 1
    assert rows[0].status == "biz_error"
    assert rows[0].error_type == "invalid_alert_id_format"


@pytest.mark.asyncio
async def test_cancel_success_includes_reasoning(engine, session_with_row):
    """Spec §3.5 F-A3: cancel success return includes original alert reasoning."""
    from src.agent.tools_execution import cancel_price_level_alert
    from src.services.tool_call_recorder import ToolCallRecorder

    recorder = ToolCallRecorder()
    deps = make_deps(engine, session_with_row)
    deps.exchange.get_price_level_alerts.return_value = [{
        "id": "a3f2b8c1", "price": 82100.0, "direction": "above",
        "symbol": "BTC/USDT:USDT", "reasoning": "4h structural high",
    }]
    deps.exchange.remove_price_level_alert.return_value = True

    async def handler(args):
        return await cancel_price_level_alert(
            deps, alert_id="a3f2b8c1", reasoning="invalidated by regime shift",
        )

    result = await recorder.wrap_tool_execute(
        make_ctx(deps),
        call=make_call("cancel_price_level_alert"),
        tool_def=MagicMock(),
        args={},
        handler=handler,
    )

    assert "Price level alert cancelled" in result
    assert "id=a3f2b8c1" in result
    # F-A3: original reasoning surfaced in output
    assert '— "4h structural high"' in result


# ============ Task 3: update_price_level_alert new tool ============

@pytest.mark.asyncio
async def test_update_success_preserves_direction_and_reasoning(engine, session_with_row):
    """Spec §4.2 step 5+6 + AC-4: update preserves original direction and reasoning
    on the new alert; return string shows id transition and original reasoning.
    """
    from src.agent.tools_execution import update_price_level_alert
    from src.services.tool_call_recorder import ToolCallRecorder

    recorder = ToolCallRecorder()
    deps = make_deps(engine, session_with_row)
    deps.exchange.get_price_level_alerts.return_value = [{
        "id": "a3f2b8c1", "price": 82100.0, "direction": "above",
        "symbol": "BTC/USDT:USDT", "reasoning": "4h structural high",
    }]
    deps.exchange.remove_price_level_alert.return_value = True
    deps.exchange.add_price_level_alert.return_value = "d7c2e9f4"

    async def handler(args):
        return await update_price_level_alert(
            deps, alert_id="a3f2b8c1", new_price=82500.0,
            reasoning="trail up after breakout",
        )

    result = await recorder.wrap_tool_execute(
        make_ctx(deps),
        call=make_call("update_price_level_alert"),
        tool_def=MagicMock(),
        args={},
        handler=handler,
    )

    assert "Price level alert updated" in result
    assert "id=a3f2b8c1" in result
    assert "id=d7c2e9f4" in result
    # Direction preserved (still "above")
    assert "above 82100.00 → above 82500.00" in result
    # Reasoning preserved
    assert '— "4h structural high"' in result

    # Exchange add was called with original_direction + original_reasoning
    call_kwargs_args = deps.exchange.add_price_level_alert.call_args.args
    # signature: add_price_level_alert(price, direction, symbol, reasoning)
    assert call_kwargs_args[0] == 82500.0
    assert call_kwargs_args[1] == "above"
    assert call_kwargs_args[3] == "4h structural high"


@pytest.mark.asyncio
async def test_update_not_found_rejects(engine, session_with_row):
    """Spec §4.2 step 2 + AC-5: update of absent alert_id returns biz_error
    'alert_not_found' with directive to use add_price_level_alert.
    """
    from src.agent.tools_execution import update_price_level_alert
    from src.services.tool_call_recorder import ToolCallRecorder

    recorder = ToolCallRecorder()
    deps = make_deps(engine, session_with_row)
    deps.exchange.get_price_level_alerts.return_value = []  # absent

    async def handler(args):
        return await update_price_level_alert(
            deps, alert_id="a3f2b8c1", new_price=82500.0,
            reasoning="trail",
        )

    result = await recorder.wrap_tool_execute(
        make_ctx(deps),
        call=make_call("update_price_level_alert"),
        tool_def=MagicMock(),
        args={},
        handler=handler,
    )

    assert "Alert a3f2b8c1 not found" in result
    assert "add_price_level_alert" in result  # directive present

    async with get_session(engine) as db:
        rows = (await db.execute(select(ToolCall))).scalars().all()
    assert len(rows) == 1
    assert rows[0].status == "biz_error"
    assert rows[0].error_type == "alert_not_found"

    # No mutation on the exchange
    deps.exchange.remove_price_level_alert.assert_not_called()
    deps.exchange.add_price_level_alert.assert_not_called()


@pytest.mark.asyncio
async def test_update_format_invalid(engine, session_with_row):
    """Spec §4.2 step 1 + AC-6: non-hex alert_id rejects with invalid_alert_id_format."""
    from src.agent.tools_execution import update_price_level_alert
    from src.services.tool_call_recorder import ToolCallRecorder

    recorder = ToolCallRecorder()
    deps = make_deps(engine, session_with_row)

    async def handler(args):
        return await update_price_level_alert(
            deps, alert_id="NOT-HEX!", new_price=82500.0, reasoning="t",
        )

    result = await recorder.wrap_tool_execute(
        make_ctx(deps),
        call=make_call("update_price_level_alert"),
        tool_def=MagicMock(),
        args={},
        handler=handler,
    )

    assert "Invalid alert_id format" in result

    async with get_session(engine) as db:
        rows = (await db.execute(select(ToolCall))).scalars().all()
    assert len(rows) == 1
    assert rows[0].status == "biz_error"
    assert rows[0].error_type == "invalid_alert_id_format"


@pytest.mark.asyncio
async def test_update_immediate_trigger_allowed(engine, session_with_row):
    """Spec §4.3 + AC-7: new_price on the trigger-side of current is accepted
    without warning/block (per §1.4 audit — agent strategic re-wake).
    Above-alert moved to a price that would trigger immediately on next tick
    must not produce a warning string. Acts as a drift guard against future
    addition of immediate-trigger warning logic in this tool: current impl
    has no distance/position check on new_price, so this test reads as a
    'no warning was added' invariant.
    """
    from src.agent.tools_execution import update_price_level_alert
    from src.services.tool_call_recorder import ToolCallRecorder

    recorder = ToolCallRecorder()
    deps = make_deps(engine, session_with_row)
    # Original above-alert at 82,100; move to 82,200 — if anyone adds a
    # vs-current-price warning, this assertion fires.
    deps.exchange.get_price_level_alerts.return_value = [{
        "id": "a3f2b8c1", "price": 82100.0, "direction": "above",
        "symbol": "BTC/USDT:USDT", "reasoning": "spring breakout",
    }]
    deps.exchange.remove_price_level_alert.return_value = True
    deps.exchange.add_price_level_alert.return_value = "d7c2e9f4"

    async def handler(args):
        return await update_price_level_alert(
            deps, alert_id="a3f2b8c1", new_price=82200.0,
            reasoning="tighten level",
        )

    result = await recorder.wrap_tool_execute(
        make_ctx(deps),
        call=make_call("update_price_level_alert"),
        tool_def=MagicMock(),
        args={},
        handler=handler,
    )

    # No warning / block — return is plain success
    assert "Price level alert updated" in result
    assert "may trigger immediately" not in result  # no warning
    assert "WARNING" not in result

    async with get_session(engine) as db:
        rows = (await db.execute(select(ToolCall))).scalars().all()
    assert len(rows) == 1
    assert rows[0].status == "ok"
    assert rows[0].error_type is None


# ============ Task 5: display.py dispatch surfaces drift guard ============

def test_update_display_dispatch_registered():
    """Spec §5.1.4 + AC-12 + AC-15: update_price_level_alert must be present in
    all three display.py dispatch structures (frozenset / parsers / prefixes).
    """
    from src.cli.display import (
        _EXECUTION_PARSERS,
        _EXECUTION_SUCCESS_PREFIXES,
        _EXECUTION_TOOL_NAMES,
    )

    # 5.1.4.1: frozenset membership (required for test_dg_2 partition)
    assert "update_price_level_alert" in _EXECUTION_TOOL_NAMES

    # 5.1.4.2: parser registered + correctly extracts direction + prices
    assert "update_price_level_alert" in _EXECUTION_PARSERS
    parser = _EXECUTION_PARSERS["update_price_level_alert"]
    sample = (
        "Price level alert updated (id=a3f2b8c1 → id=d7c2e9f4):\n"
        "  above 82100.00 → above 82500.00 — \"4h structural high\""
    )
    summary = parser(sample)
    assert "above" in summary
    assert "$82,100" in summary
    assert "$82,500" in summary

    # 5.1.4.3: success-prefix entry registered (single string for update)
    assert _EXECUTION_SUCCESS_PREFIXES["update_price_level_alert"] == (
        "Price level alert updated"
    )


# ============ Task 6: drift guards — classification + sync invariant ============

def test_cancel_idempotent_not_classified_as_error():
    """Spec §5.1.4.3 + AC-14: cancel idempotent ok must NOT be misclassified
    as tool error by is_tool_error (the post-v5 review A1 finding). Without the
    prefix-tuple fix, the idempotent return string would fail prefix match
    and is_tool_error would return True, defeating the whole idempotent design.
    """
    from src.cli.display import is_tool_error

    # Cancel idempotent ok return — must NOT be error
    idempotent_ok = "Alert a3f2b8c1 no longer active (already triggered or removed)"
    assert is_tool_error(
        "cancel_price_level_alert", idempotent_ok, outcome="success",
    ) is False

    # Cancel real success — must NOT be error
    cancel_success = 'Price level alert cancelled (id=a3f2b8c1) — "4h structural high"'
    assert is_tool_error(
        "cancel_price_level_alert", cancel_success, outcome="success",
    ) is False

    # Update success — must NOT be error
    update_success = (
        "Price level alert updated (id=a3f2b8c1 → id=d7c2e9f4):\n"
        "  above 82100.00 → above 82500.00 — \"4h structural high\""
    )
    assert is_tool_error(
        "update_price_level_alert", update_success, outcome="success",
    ) is False


def test_update_atomicity_sync_invariant():
    """Spec §5.4 test #9 + AC-13: BaseExchange.add_price_level_alert and
    .remove_price_level_alert must be sync (not async). Pins the §4.2 step 4
    'no yield points' atomicity invariant.
    """
    from src.integrations.exchange.base import BaseExchange

    assert not inspect.iscoroutinefunction(BaseExchange.add_price_level_alert), (
        "BaseExchange.add_price_level_alert must be sync — "
        "update_price_level_alert atomicity depends on this invariant"
    )
    assert not inspect.iscoroutinefunction(BaseExchange.remove_price_level_alert), (
        "BaseExchange.remove_price_level_alert must be sync — "
        "update_price_level_alert atomicity depends on this invariant"
    )


def test_update_view_known_orphan_limitation():
    """Spec §4.2 step 8 + §9: v_alert_lifecycle view sees neither side of an
    update — old id stays as final_status='active' orphan (no cancel CTE row)
    and new id is entirely absent from the view (registers CTE filters
    action='add_price_level_alert' which doesn't match 'update_price_level_alert').

    This test pins the known limitation. If a future change adds dual-emit
    _record_action (candidate (a) in §9 follow-up) or extends the view CTEs,
    the assertion shape changes and the future PR author must consciously
    update this pin (forcing them to confirm the new contract).
    """
    # The action constants documented to NOT trigger view-visibility for update:
    # - 'add_price_level_alert' (registers CTE filter, views.py:99-100)
    # - 'cancel_price_level_alert' (cancels CTE filter, views.py:117-118)
    #
    # update_price_level_alert writes action='update_price_level_alert' which
    # is neither — both CTEs filter it out by construction.

    update_action_literal = "update_price_level_alert"
    add_action_literal = "add_price_level_alert"
    cancel_action_literal = "cancel_price_level_alert"

    # The contract pinned: update's action_name is distinct from the view's
    # filter literals, so the view cannot see update rows on either side.
    assert update_action_literal != add_action_literal
    assert update_action_literal != cancel_action_literal

    # Read the view source and confirm it still filters by the two original
    # literals exclusively (no 'update_price_level_alert' branch added).
    from src.storage import views

    view_sql = getattr(views, "V_ALERT_LIFECYCLE_SQL", None)
    assert view_sql is not None, "V_ALERT_LIFECYCLE_SQL constant not found"
    assert f"action='{add_action_literal}'" in view_sql
    assert f"action='{cancel_action_literal}'" in view_sql
    assert f"action='{update_action_literal}'" not in view_sql, (
        "If V_ALERT_LIFECYCLE_SQL now references 'update_price_level_alert', "
        "the §4.2 step 8 known limitation has been resolved — update this "
        "pin to assert the new contract (e.g., new CTE or dual-emit rows)."
    )
