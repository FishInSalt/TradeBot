"""Unit tests for _format_args_as_call (spec §3.2)."""

import pytest
from src.cli.display import _format_args_as_call


def test_empty_args_renders_parens():
    assert _format_args_as_call("get_position", None) == "get_position()"
    assert _format_args_as_call("get_position", {}) == "get_position()"


def test_str_value_double_quoted():
    assert _format_args_as_call("t", {"timeframe": "15m"}) == 't(timeframe="15m")'


def test_int_value_raw():
    assert _format_args_as_call("t", {"new_price": 76860}) == "t(new_price=76860)"


def test_float_value_preserved_precision():
    assert _format_args_as_call("t", {"threshold_pct": 0.5}) == "t(threshold_pct=0.5)"


def test_bool_value_python_literal():
    assert _format_args_as_call("t", {"force": True}) == "t(force=True)"
    assert _format_args_as_call("t", {"force": False}) == "t(force=False)"


def test_none_value():
    assert _format_args_as_call("t", {"reasoning": None}) == "t(reasoning=None)"


def test_list_str_quoted_inner():
    assert (
        _format_args_as_call("t", {"timeframes": ["1h", "4h", "1d"]})
        == 't(timeframes=["1h", "4h", "1d"])'
    )


def test_list_int_raw_inner():
    assert (
        _format_args_as_call("t", {"levels": [76800, 76900]})
        == "t(levels=[76800, 76900])"
    )


def test_dict_short_inline():
    assert (
        _format_args_as_call("t", {"meta": {"a": 1, "b": "x"}})
        == 't(meta={a: 1, b: "x"})'
    )


def test_dict_long_truncated():
    long_dict = {"meta": {"a": "x" * 50}}
    assert _format_args_as_call("t", long_dict) == "t(meta={...})"


def test_field_order_preserved_from_dict_iteration():
    # Helper preserves dict iteration order; pydantic-ai schema-order is
    # LLM-output dependent (see spec §3.2 / §7.4 — not pydantic-ai contract).
    args = {"a": 1, "b": 2, "c": 3}
    assert _format_args_as_call("t", args) == "t(a=1, b=2, c=3)"


def test_invalid_json_key_fallback():
    """pydantic-ai messages.INVALID_JSON_KEY in args → fallback to tool_name(...)."""
    from pydantic_ai.messages import INVALID_JSON_KEY
    args = {INVALID_JSON_KEY: "<unparseable raw>"}
    assert _format_args_as_call("t", args) == "t(...)"


def test_multi_arg_mixed_types():
    args = {"alert_id": "bf2a9786", "new_price": 76860, "reasoning": "trail up"}
    assert (
        _format_args_as_call("update_price_level_alert", args)
        == 'update_price_level_alert(alert_id="bf2a9786", new_price=76860, reasoning="trail up")'
    )


def test_str_with_embedded_quote_is_escaped():
    """reasoning containing " must not break function-call syntax."""
    args = {"reasoning": 'trail "after" MA reclaim'}
    # json.dumps escapes " → \" so output is parseable
    assert _format_args_as_call("t", args) == 't(reasoning="trail \\"after\\" MA reclaim")'


def test_str_with_backslash_escaped():
    args = {"path": "a\\b"}
    assert _format_args_as_call("t", args) == 't(path="a\\\\b")'


def test_str_with_newline_escaped():
    args = {"text": "line1\nline2"}
    assert _format_args_as_call("t", args) == 't(text="line1\\nline2")'
