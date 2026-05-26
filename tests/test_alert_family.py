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
async def test_update_success_overwrites_price_and_reasoning_keeps_direction_and_id(
    engine, session_with_row,
):
    """Spec amend §3.3 + AC-4: update is in-place — id preserved, direction
    preserved, price + reasoning overwritten. The tool calls
    BaseExchange.update_price_level_alert once with the new in-place signature.
    """
    from src.agent.tools_execution import update_price_level_alert
    from src.services.tool_call_recorder import ToolCallRecorder

    recorder = ToolCallRecorder()
    deps = make_deps(engine, session_with_row)
    deps.exchange.get_price_level_alerts.return_value = [{
        "id": "a3f2b8c1", "price": 82100.0, "direction": "above",
        "symbol": "BTC/USDT:USDT", "reasoning": "4h structural high",
        "created_at": 1700000000.0,
    }]
    deps.exchange.update_price_level_alert.return_value = True

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
    # Single id (no transition) — id-stability.
    assert "id=a3f2b8c1" in result
    assert "id=a3f2b8c1 → id=" not in result
    # Direction preserved (still "above"), single direction token.
    assert "above 82100.00 → 82500.00" in result

    # BaseExchange.update_price_level_alert called once with the new signature.
    deps.exchange.update_price_level_alert.assert_called_once_with(
        "a3f2b8c1", 82500.0, "trail up after breakout",
    )
    # The old remove+add path must NOT be used.
    deps.exchange.remove_price_level_alert.assert_not_called()
    deps.exchange.add_price_level_alert.assert_not_called()


@pytest.mark.asyncio
async def test_update_not_found_rejects(engine, session_with_row):
    """Spec amend §3.3 + AC-5: update of absent alert_id returns biz_error
    'alert_not_found' with directive to use add_price_level_alert.

    The not-found rejection short-circuits before any BaseExchange mutation.
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
    assert "add_price_level_alert" in result

    async with get_session(engine) as db:
        rows = (await db.execute(select(ToolCall))).scalars().all()
    assert len(rows) == 1
    assert rows[0].status == "biz_error"
    assert rows[0].error_type == "alert_not_found"

    # No exchange-level mutation on either the new path or the legacy path.
    deps.exchange.update_price_level_alert.assert_not_called()
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

    # No exchange-level mutation
    deps.exchange.update_price_level_alert.assert_not_called()
    deps.exchange.remove_price_level_alert.assert_not_called()
    deps.exchange.add_price_level_alert.assert_not_called()


@pytest.mark.asyncio
async def test_update_immediate_trigger_allowed(engine, session_with_row):
    """Spec amend §3.3 + AC-7: new_price on the trigger-side of current is accepted
    without warning/block (the agent uses immediate-trigger as a strategic re-wake;
    see R2-Next-E §1.4 audit). This is also a drift guard against future addition
    of an immediate-trigger warning in this tool.
    """
    from src.agent.tools_execution import update_price_level_alert
    from src.services.tool_call_recorder import ToolCallRecorder

    recorder = ToolCallRecorder()
    deps = make_deps(engine, session_with_row)
    # Above-alert at 82,100; move to 82,200.
    deps.exchange.get_price_level_alerts.return_value = [{
        "id": "a3f2b8c1", "price": 82100.0, "direction": "above",
        "symbol": "BTC/USDT:USDT", "reasoning": "spring breakout",
        "created_at": 1700000000.0,
    }]
    deps.exchange.update_price_level_alert.return_value = True

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
    assert "may trigger immediately" not in result
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
    # New return shape (post iter-session-log-args-visibility §3.6):
    #   "Price level alert updated (id=AAAA): above 82100.00 → 82500.00"
    sample = (
        'Price level alert updated (id=a3f2b8c1): '
        'above 82100.00 → 82500.00'
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

    # Update success — must NOT be error (new in-place return shape)
    update_success = (
        'Price level alert updated (id=a3f2b8c1): '
        'above 82100.00 → 82500.00'
    )
    assert is_tool_error(
        "update_price_level_alert", update_success, outcome="success",
    ) is False


@pytest.mark.asyncio
async def test_update_view_chain_connected_after_id_stability(engine, session_with_row):
    """Spec amend §3.3 + AC-11: id-stability in update_price_level_alert means
    the v_alert_lifecycle view naturally connects add → update → cancel via the
    stable alert_id — the view's registers CTE catches the add row, the cancels
    CTE catches the cancel row, and they join cleanly. No orphan branch.

    The view SQL is unchanged in this iter; the resolution is structural
    (the same alert_id flows through both CTEs because update preserves the id).
    """
    from sqlalchemy import text
    from src.storage import views
    from src.storage.database import get_session
    from src.storage.models import TradeAction

    # Structural confirmation: the view still filters by the two original
    # action literals (no special update branch was added).
    view_sql = getattr(views, "V_ALERT_LIFECYCLE_SQL", None)
    assert view_sql is not None
    assert "action='add_price_level_alert'" in view_sql
    assert "action='cancel_price_level_alert'" in view_sql
    # No update-specific CTE branch needed because id stays the same.
    assert "action='update_price_level_alert'" not in view_sql

    # End-to-end chain assertion: same alert_id across add → update → cancel
    # appears as a single row in the view with final_status='cancelled'.
    #
    # TradeAction schema (verified against src/storage/models.py:59-77):
    #   - session_id: str (FK to sessions.id; session_with_row fixture
    #     returns the str "sess-test" directly — see tests/conftest.py:33-40)
    #   - cycle_id: str | None (per-cycle correlation; nullable)
    #   - action: str (literal action name)
    #   - alert_id: str | None (8-char hex)
    #   - symbol: str (NOT NULL, no default — must be provided)
    #   - reasoning: str | None
    #   - created_at: datetime with default=_utcnow — omit kwarg to use default
    alert_id = "a3f2b8c1"
    async with get_session(engine) as db:
        db.add(TradeAction(
            session_id=session_with_row,
            cycle_id="cyc-test-1",
            action="add_price_level_alert",
            alert_id=alert_id,
            symbol="BTC/USDT:USDT",
            reasoning="above 82100 | initial",
        ))
        db.add(TradeAction(
            session_id=session_with_row,
            cycle_id="cyc-test-2",
            action="update_price_level_alert",
            alert_id=alert_id,
            symbol="BTC/USDT:USDT",
            reasoning="price 82100 → 82500 | tighten",
        ))
        db.add(TradeAction(
            session_id=session_with_row,
            cycle_id="cyc-test-3",
            action="cancel_price_level_alert",
            alert_id=alert_id,
            symbol="BTC/USDT:USDT",
            reasoning="thesis invalidated",
        ))
        await db.commit()

    async with get_session(engine) as db:
        result = await db.execute(
            text(
                "SELECT alert_id, final_status FROM v_alert_lifecycle "
                "WHERE session_id = :sid"
            ),
            {"sid": session_with_row},
        )
        rows = result.fetchall()

    assert len(rows) == 1
    assert rows[0][0] == alert_id
    assert rows[0][1] == "cancelled"
