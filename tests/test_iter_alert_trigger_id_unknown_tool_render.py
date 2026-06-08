"""iter-alert-trigger-id-unknown-tool-render — Fix A (#2) + Fix B (#1).

Fix A: alert-triggered cycle's user prompt exposes `alert_id` for lifecycle joins.
       Helper `_format_price_level_alert_trigger` makes the format unit-testable.

Fix B: `_render_action` orphan path splits into:
  - retry present → ✗ rejected (pydantic-ai validation/unknown-tool reject)
  - retry absent → ⚙ [no return captured] (genuine orphan, escape()-protected)

content: list[ErrorDetails] | str dual-form handled per pydantic_ai/messages.py:1321.

Spec: docs/superpowers/specs/2026-05-29-iter-alert-trigger-id-unknown-tool-render-design.md
"""
from __future__ import annotations

import io
from datetime import datetime, timezone

import pytest
from pydantic_ai.messages import (
    ModelRequest, ModelResponse, RetryPromptPart, ToolCallPart,
)
from rich.console import Console

from src.cli.display import _render_action


# === Fix A (#2): alert trigger prompt surfaces alert_id =========================


def test_format_price_level_alert_trigger_includes_alert_id():
    """Fix A: helper output carries alert_id + direction + price + reasoning."""
    from src.cli.app import _format_price_level_alert_trigger
    from src.integrations.exchange.base import PriceLevelAlertInfo

    context = PriceLevelAlertInfo(
        symbol="BTC/USDT:USDT", target_price=76470.0, direction="above",
        current_price=76482.5,
        reasoning="Reclaim of 17:15 candle high — early warning before SL.",
        timestamp=1779800855603, alert_id="725cfc9f",
    )
    out = _format_price_level_alert_trigger(context, datetime.now(timezone.utc))

    assert "id=725cfc9f" in out
    assert "above 76470.00" in out
    assert "76482.50" in out  # current_price
    assert "Reclaim of 17:15 candle high" in out
    assert "BTC/USDT:USDT" in out


def test_format_price_level_alert_trigger_drops_pronoun():
    """Fix A cosmetic: drop 'your' pronoun (prompt context already implies ownership).
    Avoids double-possessive noise: was '(your alert: above ...)', now '(alert id=... above ...)'.
    """
    from src.cli.app import _format_price_level_alert_trigger
    from src.integrations.exchange.base import PriceLevelAlertInfo

    context = PriceLevelAlertInfo(
        symbol="BTC/USDT:USDT", target_price=76470.0, direction="above",
        current_price=76482.5, reasoning="x", timestamp=0, alert_id="abc12345",
    )
    out = _format_price_level_alert_trigger(context, datetime.now(timezone.utc))

    assert "your alert" not in out
    assert "(alert id=" in out  # new form starts with `(alert id=`


# === Fix B-1 (#1): [no return captured] survives Rich markup =====================


def test_orphan_no_return_captured_survives_rich_markup():
    """Fix B-1 regression: orphan path renders [no return captured] as literal
    (Rich console doesn't strip the brackets as a markup tag).
    """
    calls = [ToolCallPart(tool_name="get_active_alert", args={}, tool_call_id="c1")]
    raw_out = _render_action(calls, returns_lookup={}, cycle_id="abc")

    buf = io.StringIO()
    Console(file=buf, no_color=True, width=120).print(raw_out)
    rendered = buf.getvalue()

    assert "[no return captured]" in rendered
    assert "get_active_alert" in rendered
    assert " ⚙ " in rendered


# === Fix B-2 (#1): RetryPromptPart → ✗ [invalid call: ...] =======================


def test_retry_prompt_renders_as_invalid_call():
    """Fix B-2 str path: ModelRetry / unknown-tool reject with `content: str`.
    Renders as ✗ + [invalid call: <first line>] (NOT ⚙ + [no return captured]).
    """
    calls = [ToolCallPart(tool_name="get_active_alert", args={}, tool_call_id="c1")]
    retry = RetryPromptPart(
        tool_name="get_active_alert", tool_call_id="c1",
        content=(
            "Unknown tool name: 'get_active_alert'. "
            "Available tools: 'get_active_alerts', 'get_position', 'get_market_data'"
        ),
    )
    raw_out = _render_action(
        calls, returns_lookup={}, cycle_id="abc",
        retry_lookup={"c1": retry},
    )

    assert "✗" in raw_out
    assert " ⚙ " not in raw_out
    assert "get_active_alert" in raw_out
    assert "[invalid call:" in raw_out
    assert "Unknown tool name" in raw_out
    assert "[no return captured]" not in raw_out


def test_retry_prompt_list_content_formats_loc_and_msg():
    """Fix B-2 list path: ValidationError reject with `content: list[ErrorDetails]`.
    Extracts loc + msg per error (≤3), avoiding ugly str(list) dict repr.
    """
    calls = [ToolCallPart(tool_name="open_position", args={}, tool_call_id="c1")]
    retry = RetryPromptPart(
        tool_name="open_position", tool_call_id="c1",
        content=[
            {"type": "missing", "loc": ("symbol",), "msg": "Field required", "input": {}},
            {"type": "int_parsing", "loc": ("amount",),
             "msg": "Input should be a valid integer", "input": "abc"},
        ],
    )
    raw_out = _render_action(
        calls, returns_lookup={}, cycle_id="abc",
        retry_lookup={"c1": retry},
    )

    assert "✗" in raw_out
    assert "open_position" in raw_out
    assert "symbol: Field required" in raw_out
    assert "amount: Input should be a valid integer" in raw_out
    # Avoid ugly str(list) — should NOT contain raw dict repr
    assert "{'type':" not in raw_out
    assert "'loc':" not in raw_out


def test_retry_prompt_str_first_line_capped_at_100_chars():
    """Fix B-2 str edge: long single-line content truncates at 100 chars."""
    long_content = "Validation failed: " + ("x" * 500)
    calls = [ToolCallPart(tool_name="bad_tool", args={}, tool_call_id="c1")]
    retry = RetryPromptPart(
        tool_name="bad_tool", tool_call_id="c1", content=long_content,
    )
    raw_out = _render_action(
        calls, returns_lookup={}, cycle_id="abc", retry_lookup={"c1": retry},
    )

    assert "Validation failed:" in raw_out
    # "Validation failed: " = 19 chars → 81 'x's fit in 100-char cap
    assert "x" * 81 in raw_out
    assert "x" * 82 not in raw_out


def test_retry_prompt_str_multiline_keeps_first_line_only():
    """Fix B-2 str edge: multiline content surfaces only first line in the orphan
    row; later lines (e.g. 'Available tools' enumeration) folded out."""
    multi_content = "First line summary.\nSecond line detail.\nThird line context."
    calls = [ToolCallPart(tool_name="bad_tool", args={}, tool_call_id="c1")]
    retry = RetryPromptPart(
        tool_name="bad_tool", tool_call_id="c1", content=multi_content,
    )
    raw_out = _render_action(
        calls, returns_lookup={}, cycle_id="abc", retry_lookup={"c1": retry},
    )

    assert "First line summary." in raw_out
    assert "Second line detail." not in raw_out
    assert "Third line context." not in raw_out


def test_retry_prompt_list_caps_at_3_errors():
    """Fix B-2 list edge: ≥ 4 ErrorDetails → only first 3 surface, avoids overlong row.
    """
    calls = [ToolCallPart(tool_name="multi_err", args={}, tool_call_id="c1")]
    retry = RetryPromptPart(
        tool_name="multi_err", tool_call_id="c1",
        content=[
            {"type": "missing", "loc": ("first",),  "msg": "msg_first"},
            {"type": "missing", "loc": ("second",), "msg": "msg_second"},
            {"type": "missing", "loc": ("third",),  "msg": "msg_third"},
            {"type": "missing", "loc": ("FOURTH",), "msg": "msg_FOURTH"},
            {"type": "missing", "loc": ("FIFTH",),  "msg": "msg_FIFTH"},
        ],
    )
    raw_out = _render_action(
        calls, returns_lookup={}, cycle_id="abc", retry_lookup={"c1": retry},
    )

    assert "first: msg_first" in raw_out
    assert "second: msg_second" in raw_out
    assert "third: msg_third" in raw_out
    assert "FOURTH" not in raw_out
    assert "FIFTH" not in raw_out


def test_format_cycle_output_captures_retry_prompt_part():
    """Fix B-2 integration: format_cycle_output builds retry_lookup from
    ModelRequest.parts and threads it to _render_action.
    """
    from src.cli.display import CycleRenderContext, format_cycle_output
    from src.cli.session_state import SessionStats

    tool_call = ToolCallPart(
        tool_name="get_active_alert", args={}, tool_call_id="c1",
    )
    retry = RetryPromptPart(
        tool_name="get_active_alert", tool_call_id="c1",
        content="Unknown tool name: 'get_active_alert'.",
    )
    messages = [
        ModelResponse(parts=[tool_call]),
        ModelRequest(parts=[retry]),
    ]

    ctx = CycleRenderContext(
        cycle_id="abcd1234",
        trigger_type="scheduled",
        trigger_context=None,
        state_snapshot=None,
        messages=messages,
        final_text="done",
        cycle_tokens=0,
        stats=SessionStats(),
        cache_hit_rate=None,
        cycle_started_at=datetime(2026, 5, 29, tzinfo=timezone.utc),
        cycle_ended_at=datetime(2026, 5, 29, tzinfo=timezone.utc),
        forensic_reason=None,
    )

    out = format_cycle_output(ctx)

    assert "✗" in out
    assert "get_active_alert" in out
    assert "[invalid call: Unknown tool name" in out
