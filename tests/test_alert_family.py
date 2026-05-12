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
