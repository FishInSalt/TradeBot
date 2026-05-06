"""Tests for cycle display: tool summary parsers and cycle output formatting."""
from __future__ import annotations


# === Perception tool summary parsers ===

def test_summarize_get_market_data():
    from src.cli.display import summarize_tool
    content = (
        "=== Ticker (BTC/USDT:USDT) ===\n"
        "Price: 84200.50 | Bid: 84190.00 | Ask: 84210.00\n"
        "24h High: 85000.00 | Low: 83000.00 | Volume: 1234.56\n\n"
        "=== Technical Indicators (15m) ===\n"
        "RSI(14): 62.30\n"
        "MA(20): 84000.00 (price vs MA: +0.2%)\n"
        "MA(50): 83500.00 (price vs MA: +0.8%)\n"
        "MACD: 50.00 | Signal: 45.00 | Histogram: 5.00\n"
        "BB: 85000 / 84000 / 83000 (position: 60% of band width)\n\n"
        "=== Market Context ===\n"
        "ATR(14): 101.04 (0.12% of price, 15m candles)\n"
        "Volume: 500.0 (1.10x avg)\n"
        "50-candle Range: 83000 — 85000\n\n"
        "=== Recent Candles (15m, last 50) ===\n"
        "Time           Open       High        Low      Close        Vol\n"
        "12:00         84000.00  84300.00  83900.00  84200.50      100.0"
    )
    result = summarize_tool("get_market_data", content)
    assert "$84,200" in result or "$84200" in result
    assert "RSI" in result
    assert "ATR" in result


def test_summarize_get_position_with_position():
    from src.cli.display import summarize_tool
    content = (
        "Current Position:\n"
        "  LONG 0.500 contracts @ 83100.00 | 3x leverage\n"
        "  PnL: 5.50 USDT (+1.32% of initial capital)\n"
        "  Duration: 2h 30m\n"
        "\n"
        "Risk exposure:\n"
        "  Notional value: 41550.00 USDT (4.2% of equity 100000.00)\n"
        "  Margin used: 13850.00 USDT (13.9% of equity, from balance.used_usdt)\n"
        "  Liquidation: 55000.00 (34.7% away = 5.8× ATR(1h))\n"
        "\n"
        "Exit orders:\n"
        "  Stop loss: not set\n"
        "  Take profit: not set"
    )
    result = summarize_tool("get_position", content)
    assert "Long" in result or "LONG" in result
    assert "0.5" in result
    assert "83100" in result or "83,100" in result
    assert "1.32" in result


def test_summarize_get_position_no_position():
    from src.cli.display import summarize_tool
    result = summarize_tool("get_position", "No open positions.")
    assert "No open positions" in result


def test_summarize_get_account_balance():
    from src.cli.display import summarize_tool
    content = (
        "Account Balance:\n"
        "  Total: 10550.00 USDT (initial: 10000.00)\n"
        "  Return: +5.50% (+550.00 USDT) (incl. unrealized)\n"
        "  Free: 8000.00 USDT\n"
        "  Used: 2550.00 USDT"
    )
    result = summarize_tool("get_account_balance", content)
    assert "10550" in result or "10,550" in result
    assert "5.50" in result


def test_summarize_get_open_orders_with_orders():
    from src.cli.display import summarize_tool
    content = (
        "Pending Orders:\n"
        "  [STOP] sell 0.500 @ 81500.00 (-3.21% from current) | ID: abc\n"
        "  [TAKE_PROFIT] sell 0.500 @ 86000.00 (+2.14% from current) | ID: def"
    )
    result = summarize_tool("get_open_orders", content)
    assert "2" in result
    assert "order" in result.lower()
    assert "81,500" in result or "81500" in result
    assert "86,000" in result or "86000" in result


def test_summarize_get_open_orders_mixed_with_market():
    from src.cli.display import summarize_tool
    content = (
        "Pending Orders:\n"
        "  [STOP] sell 0.500 @ 81500.00 (-3.21% from current) | ID: abc\n"
        "  [PENDING] buy 0.100 market price | ID: xyz"
    )
    result = summarize_tool("get_open_orders", content)
    assert "2" in result
    assert "SL" in result
    assert "MKT" in result


def test_summarize_get_open_orders_none():
    from src.cli.display import summarize_tool
    result = summarize_tool("get_open_orders", "No pending orders.")
    assert "No pending orders" in result


def test_summarize_get_trade_journal():
    from src.cli.display import summarize_tool
    content = (
        "=== Performance Summary ===\n"
        "Total Trades: 12 | Win: 8 (66.7%) | Loss: 4\n"
        "Avg Win: +45.00 USDT | Avg Loss: 22.50 USDT\n"
        "Profit Factor: 4.00\n\n"
        "=== Trade Journal ===\n"
        "[04-15 10:00] open_position (long)\n  Reasoning: trend confirmed"
    )
    result = summarize_tool("get_trade_journal", content)
    assert "12" in result
    assert "66.7" in result


def test_summarize_get_trade_journal_empty():
    from src.cli.display import summarize_tool
    result = summarize_tool("get_trade_journal", "No trade journal entries yet.")
    assert "No trade" in result


def test_summarize_get_memories():
    from src.cli.display import summarize_tool
    content = (
        "=== Long-term Memory ===\n"
        "- [lesson] Always wait for confirmation\n"
        "- [market_pattern] BTC dumps on weekends\n"
        "- [trade_review] Last long was stopped out\n"
    )
    result = summarize_tool("get_memories", content)
    assert "3" in result


def test_summarize_get_memories_none():
    from src.cli.display import summarize_tool
    result = summarize_tool("get_memories", "No relevant memories.")
    assert "No relevant memories" in result


def test_summarize_get_active_alerts():
    from src.cli.display import summarize_tool
    content = (
        "=== Price Alert Settings ===\n"
        "Volatility alert: 5.0% in 60min window\n\n"
        "=== Active Price Level Alerts (2/20) ===\n"
        '  #1 above 86000.00 — "Resistance breakout"\n'
        '  #2 below 81000.00 — "Support breakdown"'
    )
    result = summarize_tool("get_active_alerts", content)
    assert "5.0" in result
    assert "60" in result
    assert "2" in result


def test_summarize_get_performance():
    from src.cli.display import summarize_tool
    content = (
        "=== Trading Performance ===\n"
        "Initial Balance: 10000.00 USDT\n"
        "Current Balance: 10550.00 USDT\n"
        "Total Return: +5.50% (+550.00 USDT) (incl. unrealized)\n"
        "Realized PnL: +500.00 USDT (gross, before fees)\n"
        "Total Fees: -10.00 USDT\n\n"
        "Total Trades: 12 | Win: 8 (66.7%) | Loss: 4\n"
        "Avg Win: +45.00 USDT | Avg Loss: 22.50 USDT\n"
        "Profit Factor: 4.00\n"
        "Max Drawdown: -2.5%\n"
        "Best Trade: +120.00 USDT | Worst Trade: -55.00 USDT"
    )
    result = summarize_tool("get_performance", content)
    assert "5.50" in result
    assert "12" in result
    assert "66.7" in result


def test_summarize_get_performance_no_trades():
    from src.cli.display import summarize_tool
    content = (
        "=== Trading Performance ===\n"
        "Initial Balance: 10000.00 USDT\n"
        "Current Balance: 10050.00 USDT\n"
        "Return: +0.50% (+50.00 USDT)\n\n"
        "No completed trades yet."
    )
    result = summarize_tool("get_performance", content)
    assert "0.50" in result
    assert "No trades yet" in result


def test_summarize_get_performance_no_metrics():
    from src.cli.display import summarize_tool
    content = (
        "=== Trading Performance ===\n"
        "Initial Balance: 10000.00 USDT\n"
        "Current Balance: 10000.00 USDT\n"
        "Return: +0.00% (+0.00 USDT)\n\n"
        "No metrics service available."
    )
    result = summarize_tool("get_performance", content)
    assert "0.00" in result
    assert "No trades yet" in result


def test_summarize_fallback_unknown_tool():
    from src.cli.display import summarize_tool
    result = summarize_tool("unknown_tool", "Some random return value that is quite long " * 5)
    assert len(result) <= 85  # 80 chars + possible ellipsis


def test_summarize_fallback_malformed():
    from src.cli.display import summarize_tool
    result = summarize_tool("get_market_data", "Error: connection timeout")
    # Should not crash, should return truncated fallback
    assert "Error" in result


# === Execution tool summary parsers ===

def test_summarize_open_position():
    from src.cli.display import summarize_tool
    content = (
        "Order submitted: long 0.050000 @ ~84200.50, 3x | ID: abc-123\n"
        "You will be notified when filled."
    )
    result = summarize_tool("open_position", content)
    assert "long" in result.lower()
    assert "0.05" in result
    assert "84" in result


def test_summarize_open_position_rejected():
    from src.cli.display import is_tool_error
    content = "Trade rejected by human approval."
    assert is_tool_error("open_position", content)


def test_summarize_close_position():
    from src.cli.display import summarize_tool
    content = (
        "Orders submitted: close 1 position(s) | IDs: xyz-456\n"
        "You will be notified when filled."
    )
    result = summarize_tool("close_position", content)
    assert "Close" in result or "close" in result
    assert "1" in result


def test_summarize_set_stop_loss():
    from src.cli.display import summarize_tool
    content = "Stop loss set at 81500.00 (-3.21% from current 84200.00) | Order: abc"
    result = summarize_tool("set_stop_loss", content)
    assert "SL" in result
    assert "81500" in result or "81,500" in result


def test_summarize_set_take_profit():
    from src.cli.display import summarize_tool
    content = "Take profit set at 87000.00 (+3.33% from current 84200.00) | Order: def"
    result = summarize_tool("set_take_profit", content)
    assert "TP" in result
    assert "87000" in result or "87,000" in result


def test_summarize_adjust_leverage():
    from src.cli.display import summarize_tool
    content = "Leverage adjusted to 5x for BTC/USDT:USDT"
    result = summarize_tool("adjust_leverage", content)
    assert "5x" in result


def test_summarize_place_limit_order():
    from src.cli.display import summarize_tool
    content = "Limit order placed: long 0.050000 @ 83000.00, 3x | ID: lmt-789"
    result = summarize_tool("place_limit_order", content)
    assert "Limit" in result or "limit" in result
    assert "long" in result.lower()
    assert "83000" in result or "83,000" in result


def test_summarize_cancel_order():
    from src.cli.display import summarize_tool
    content = "Order cancelled: stop sell 0.050000 @ 81500.00 | ID: abc"
    result = summarize_tool("cancel_order", content)
    assert "Cancelled" in result
    assert "stop" in result
    assert "0.050000" in result
    assert "81,500" in result or "81500" in result


def test_summarize_set_price_alert():
    from src.cli.display import summarize_tool
    content = "Price alert updated: threshold=5.0%, window=60min"
    result = summarize_tool("set_price_alert", content)
    assert "5.0" in result
    assert "60" in result


def test_summarize_add_price_level_alert():
    from src.cli.display import summarize_tool
    content = "Price level alert set: above 86000.00 (id=alert-1)"
    result = summarize_tool("add_price_level_alert", content)
    assert "above" in result
    assert "86000" in result or "86,000" in result


def test_summarize_set_next_wake():
    from src.cli.display import summarize_tool
    content = "Next wake set to 30 min. Reason: market quiet, no position"
    result = summarize_tool("set_next_wake", content)
    assert "30" in result
    assert "min" in result
    assert "Reason" not in result  # reasoning should be truncated


# === Memory tool ===

def test_summarize_save_memory():
    from src.cli.display import summarize_save_memory
    args = {"category": "lesson", "content": "Always wait for RSI confirmation before entering", "importance": 0.8}
    result = summarize_save_memory(args)
    assert "[lesson]" in result
    assert "Always wait for RSI confirmation" in result
    assert "0.8" in result


# === Success/failure detection ===

def test_is_tool_error_outcome_failed():
    from src.cli.display import is_tool_error
    assert is_tool_error("get_market_data", "any content", outcome="failed")


def test_is_tool_error_outcome_denied():
    from src.cli.display import is_tool_error
    assert is_tool_error("get_market_data", "any content", outcome="denied")


def test_is_tool_error_outcome_success_perception():
    from src.cli.display import is_tool_error
    assert not is_tool_error("get_market_data", "=== Ticker...", outcome="success")


def test_is_tool_error_business_rejection():
    from src.cli.display import is_tool_error
    assert is_tool_error("open_position", "Trade rejected by human approval.", outcome="success")
    assert is_tool_error("open_position", "Position too small: 0.00001 rounds to 0", outcome="success")
    assert is_tool_error("open_position", "A market order is already pending.", outcome="success")


def test_is_tool_error_execution_success():
    from src.cli.display import is_tool_error
    assert not is_tool_error("open_position", "Order submitted: long 0.05 @ ~84200", outcome="success")
    assert not is_tool_error("set_stop_loss", "Stop loss set at 81500.00", outcome="success")


def test_is_tool_error_add_price_level_alert_immediate_trigger():
    from src.cli.display import is_tool_error
    # Normal success
    assert not is_tool_error("add_price_level_alert", "Price level alert set: above 86000.00 (id=a1)", outcome="success")
    # Immediate trigger warning — still a success (alert was created)
    assert not is_tool_error(
        "add_price_level_alert",
        "Alert set (id=a1), but WARNING: current price (87000.00) already above 86000.00, may trigger immediately",
        outcome="success",
    )
    # Actual failure
    assert is_tool_error("add_price_level_alert", "Invalid direction: must be 'above' or 'below', got 'up'", outcome="success")


# === Cycle output formatting ===

def test_format_cycle_output_basic():
    from src.cli.display import format_cycle_output
    from tests.fixtures.cycle_fixtures import build_cycle_messages
    msgs = build_cycle_messages(
        thinking_segments=[None],
        tool_call_segments=[[
            ("get_market_data", {}, "=== Ticker (BTC/USDT:USDT) ===\nPrice: 84200.00 | Bid: 84190.00 | Ask: 84210.00\n\n=== Technical Indicators (15m) ===\nCurrent Price: 84200.00\n\nRSI(14): 62.30\n\n=== Market Context ===\nATR(14): 101.04 (0.12% of price, 15m candles)"),
            ("get_position", {}, "No open positions."),
        ]],
        final_text="Market is quiet, no action taken.",
    )
    out = format_cycle_output(_make_ctx(
        cycle_id="a3f2e1b4", trigger_type="scheduled",
        trigger_context={"type": "scheduled_tick"},
        messages=msgs, final_text="Market is quiet, no action taken.",
        cycle_tokens=1200,
    ))
    assert "a3f2" in out
    assert "SCHEDULED" in out  # uppercase per spec
    assert "get_market_data" in out
    assert "get_position" in out
    assert "Market is quiet" in out
    assert "1,200" in out


def test_format_cycle_output_with_memory():
    from src.cli.display import format_cycle_output
    from tests.fixtures.cycle_fixtures import build_cycle_messages
    msgs = build_cycle_messages(
        thinking_segments=[None],
        tool_call_segments=[[
            ("save_memory",
             {"category": "lesson", "content": "Always wait for RSI confirmation before entry", "importance": 0.8},
             "Memory saved [lesson] (importance=0.8): Always wait for confirmation"),
        ]],
        final_text="Lesson recorded.",
    )
    out = format_cycle_output(_make_ctx(
        cycle_id="b5c6d7e8", trigger_type="conditional",
        trigger_context={"type": "scheduled_tick"},
        messages=msgs, final_text="Lesson recorded.", cycle_tokens=500,
    ))
    assert "✎" in out
    assert "[lesson]" in out
    assert "Always wait for RSI confirmation" in out


def test_format_cycle_output_with_error():
    from src.cli.display import format_cycle_output
    from tests.fixtures.cycle_fixtures import build_cycle_messages
    msgs = build_cycle_messages(
        thinking_segments=[None],
        tool_call_segments=[[
            ("open_position", {}, "Trade rejected by human approval."),
        ]],
        final_text="Trade was rejected.",
    )
    out = format_cycle_output(_make_ctx(
        cycle_id="c7d8e9f0", trigger_type="scheduled",
        trigger_context={"type": "scheduled_tick"},
        messages=msgs, final_text="Trade was rejected.", cycle_tokens=800,
    ))
    assert "✗" in out


def test_format_cycle_output_outcome_failed():
    from src.cli.display import format_cycle_output
    from pydantic_ai.messages import (
        ModelRequest, ModelResponse, TextPart,
        ToolCallPart, ToolReturnPart,
    )
    tcp = ToolCallPart(tool_name="get_market_data", args={}, tool_call_id="c1")
    msgs = [
        ModelResponse(parts=[tcp, TextPart(content="Could not fetch data.")]),
        ModelRequest(parts=[
            ToolReturnPart(
                tool_name="get_market_data", tool_call_id="c1",
                content="Connection error", outcome="failed",
            ),
        ]),
    ]
    out = format_cycle_output(_make_ctx(
        cycle_id="d1e2f3a4", trigger_type="scheduled",
        trigger_context={"type": "scheduled_tick"},
        messages=msgs, final_text="Could not fetch data.", cycle_tokens=300,
    ))
    assert "✗" in out


# === R2-8a: Render helper unit tests (T-RH / T-RR / T-RA / T-RD / T-RF) ===

from datetime import datetime, timezone


def _make_state_snapshot(position=None, balance=None, errors=None):
    """Helper: minimal state_snapshot dict matching cycle_capture._capture_state_snapshot output."""
    return {
        "position": position,
        "balance": balance,
        "market": None,
        "pending_orders": [],
        "active_alerts": [],
        "_errors": errors or [],
        "_cycle_id": "test-cycle",
    }


# --- T-RH: _render_header ---


def test_render_header_full_alert_trigger():
    """T-RH-1: 完整字段 — ALERT trigger + 持仓 + balance."""
    from src.cli.display import _render_header
    from src.cli.session_state import SessionStats
    stats = SessionStats()
    stats.record_cycle(40_000, datetime(2026, 5, 2, 18, 2, 23, tzinfo=timezone.utc))
    out = _render_header(
        cycle_id="9f57abcd",
        trigger_type="alert",
        trigger_context={
            "type": "percentage_alert",
            "symbol": "BTC/USDT:USDT",
            "current_price": 75448.0,
            "reference_price": 76225.0,
            "change_pct": -1.6,
            "window_minutes": 10,
            "timestamp": "2026-05-02T18:14:23Z",
        },
        state_snapshot=_make_state_snapshot(
            position={
                "symbol": "BTC/USDT:USDT", "side": "short", "contracts": 0.265,
                "entry_price": 75350.0, "unrealized_pnl": 75.0,
                "leverage": 5, "liquidation_price": 0.0, "pnl_pct": 0.10,
            },
            balance={"total_usdt": 9990.0, "free_usdt": 9990.0, "used_usdt": 0.0},
        ),
        cycle_started_at=datetime(2026, 5, 2, 18, 14, 23, tzinfo=timezone.utc),
        stats=stats,
    )
    assert "9f57" in out
    assert "18:14:23 UTC" in out
    assert "+12 min from prev" in out
    assert "ALERT" in out
    assert "vol -1.6%/10min" in out
    assert "75,448" in out and "76,225" in out
    assert "Short 0.265 @ $75,350" in out
    assert "(5x)" in out
    assert "PnL +0.10%" in out
    assert "Balance $9,990" in out


def test_render_header_first_cycle():
    """T-RH-2: 首 cycle，stats.last_cycle_ended_at=None → '(first cycle)'."""
    from src.cli.display import _render_header
    from src.cli.session_state import SessionStats
    out = _render_header(
        cycle_id="aabbccdd",
        trigger_type="scheduled",
        trigger_context={"type": "scheduled_tick"},
        state_snapshot=_make_state_snapshot(),
        cycle_started_at=datetime(2026, 5, 2, 18, 14, 23, tzinfo=timezone.utc),
        stats=SessionStats(),
    )
    assert "(first cycle)" in out
    assert "+0 min" not in out


def test_render_header_trigger_context_none():
    """T-RH-3: trigger_context=None → 仅 {TYPE_UPPER} 不带详情."""
    from src.cli.display import _render_header
    from src.cli.session_state import SessionStats
    out = _render_header(
        cycle_id="aabbccdd",
        trigger_type="alert",
        trigger_context=None,
        state_snapshot=_make_state_snapshot(),
        cycle_started_at=datetime(2026, 5, 2, 18, 14, 23, tzinfo=timezone.utc),
        stats=SessionStats(),
    )
    assert "ALERT" in out
    assert "—" not in out.split("Trigger")[1].split("\n")[0]  # 无 em-dash 后缀


def test_render_header_scheduled_no_metadata():
    """spec §4.1.3: scheduled_tick verbatim "Trigger    SCHEDULED" 不带 em-dash 后缀."""
    from src.cli.display import _render_header
    from src.cli.session_state import SessionStats
    out = _render_header(
        cycle_id="aabbccdd",
        trigger_type="scheduled",
        trigger_context={"type": "scheduled_tick"},
        state_snapshot=_make_state_snapshot(),
        cycle_started_at=datetime(2026, 5, 2, 18, 14, 23, tzinfo=timezone.utc),
        stats=SessionStats(),
    )
    trigger_line = next(l for l in out.splitlines() if "Trigger" in l)
    assert trigger_line.strip().startswith("Trigger") and "SCHEDULED" in trigger_line
    assert "—" not in trigger_line


def test_render_header_flat_no_position():
    """§4.1.4: position=None → State 段渲染 'FLAT | Balance $X'."""
    from src.cli.display import _render_header
    from src.cli.session_state import SessionStats
    out = _render_header(
        cycle_id="aabbccdd",
        trigger_type="scheduled",
        trigger_context={"type": "scheduled_tick"},
        state_snapshot=_make_state_snapshot(
            balance={"total_usdt": 10000.0, "free_usdt": 10000.0, "used_usdt": 0.0},
        ),
        cycle_started_at=datetime(2026, 5, 2, 18, 14, 23, tzinfo=timezone.utc),
        stats=SessionStats(),
    )
    state_line = next(l for l in out.splitlines() if "State" in l)
    assert "FLAT" in state_line
    assert "Balance $10,000" in state_line


# --- T-RR: _render_reasoning ---


def test_render_reasoning_under_800():
    """T-RR-1: thinking < 800 chars → no truncation marker."""
    from src.cli.display import _render_reasoning
    text = "Position fine — limit short still pending at 75550."
    out = _render_reasoning(text)
    assert "▾ Reasoning" in out
    assert f"({len(text)} chars total)" in out
    assert "... [+" not in out
    assert text in out


def test_render_reasoning_at_800_exact():
    """T-RR-2: thinking == 800 chars → no marker."""
    from src.cli.display import _render_reasoning
    text = "x" * 800
    out = _render_reasoning(text)
    assert "(800 chars total)" in out
    assert "... [+" not in out


def test_render_reasoning_over_800_truncated():
    """T-RR-3: thinking > 800 chars → truncate to 800 + '... [+N chars]' marker.

    Note (R2-8c D10): default max_chars 800 → 2000; this test preserves the
    original 800 boundary intent by passing explicit max_chars=800.
    R2-8d D6: 2000 → 15000 default (this test still pins R2-8c boundary
    via explicit max_chars=800).
    """
    from src.cli.display import _render_reasoning
    text = "y" * 1547
    out = _render_reasoning(text, max_chars=800)
    assert "(1547 chars total)" in out
    assert "... [+747 chars]" in out
    # body length 800 chars + marker
    assert out.count("y") == 800


def test_render_reasoning_multiline_indent():
    """T-RR-4: thinking 含 \\n → 每行加 2-space indent."""
    from src.cli.display import _render_reasoning
    text = "Line 1.\nLine 2.\nLine 3."
    out = _render_reasoning(text)
    body_lines = [l for l in out.splitlines() if l.startswith("  ")]
    assert any("Line 1." in l for l in body_lines)
    assert any("Line 2." in l for l in body_lines)
    assert any("Line 3." in l for l in body_lines)


def test_render_reasoning_escape_rich_markup():
    """spec §4.2.2 P1 escape: thinking content 含 [red] / [bold] 等字面值需 escape，
    避免 console.print 解析为 markup 渲染错乱."""
    from src.cli.display import _render_reasoning
    text = "Discussing [red]error handling[/] in code."
    out = _render_reasoning(text)
    # rich.markup.escape 把 '[red]' → '\\[red]'，body 含 escaped form
    assert r"\[red]" in out


# --- T-RA: _render_action ---


def test_render_action_multi_tools():
    """T-RA-1: 3 ToolCallPart → '▾ Action (3 tools)' 复数."""
    from pydantic_ai.messages import ToolCallPart, ToolReturnPart
    from src.cli.display import _render_action
    calls = [
        ToolCallPart(tool_name="get_market_data", args={}, tool_call_id="c1"),
        ToolCallPart(tool_name="get_position", args={}, tool_call_id="c2"),
        ToolCallPart(tool_name="get_open_orders", args={}, tool_call_id="c3"),
    ]
    returns = {
        "c1": ToolReturnPart(tool_name="get_market_data", tool_call_id="c1",
                              content="=== Ticker ===\nPrice: 75212.0"),
        "c2": ToolReturnPart(tool_name="get_position", tool_call_id="c2",
                              content="No open positions."),
        "c3": ToolReturnPart(tool_name="get_open_orders", tool_call_id="c3",
                              content="No pending orders."),
    }
    out = _render_action(calls, returns, cycle_id="9f57abcd")
    assert "▾ Action (3 tools)" in out
    assert "get_market_data" in out
    assert "get_position" in out
    assert "get_open_orders" in out


def test_render_action_single_tool_singular():
    """T-RA-2: 1 ToolCallPart → '▾ Action (1 tool)' 单数."""
    from pydantic_ai.messages import ToolCallPart, ToolReturnPart
    from src.cli.display import _render_action
    calls = [ToolCallPart(tool_name="set_next_wake", args={"minutes": 5}, tool_call_id="c1")]
    returns = {
        "c1": ToolReturnPart(tool_name="set_next_wake", tool_call_id="c1",
                              content="Next wake set to 5 min"),
    }
    out = _render_action(calls, returns, cycle_id="9f57abcd")
    assert "▾ Action (1 tool)" in out
    assert "▾ Action (1 tools)" not in out


def test_render_action_missing_return_fallback():
    """T-TC-4: ret lookup miss → '⚙ {tool_name} [no return captured]' + 不抛."""
    from pydantic_ai.messages import ToolCallPart
    from src.cli.display import _render_action
    calls = [ToolCallPart(tool_name="get_market_data", args={}, tool_call_id="orphan")]
    out = _render_action(calls, returns_lookup={}, cycle_id="9f57abcd")
    assert "[no return captured]" in out
    assert "get_market_data" in out


# --- T-RD: _render_decision ---


def test_render_decision_multiline_markdown_indented():
    """T-RD-1: 完整 markdown 内嵌，每行 2-space indent."""
    from src.cli.display import _render_decision
    text = "## Title\n\n**Bold** text.\n- Item 1\n- Item 2"
    out = _render_decision(text)
    assert "▾ Decision" in out
    body_lines = [l for l in out.splitlines() if l and not l.startswith("▾")]
    for l in body_lines:
        assert l.startswith("  "), f"Decision body not indented: {l!r}"


def test_render_decision_escape_rich_markup():
    """spec §4.4.1 attack surface: result.output 含 [red] 字面值 → 强制 escape."""
    from src.cli.display import _render_decision
    text = "Result: [red]rejected[/] by approval."
    out = _render_decision(text)
    assert r"\[red]" in out


# --- T-RF: _render_footer ---


def test_render_footer_full_normal_path():
    """T-RF-1: 正常 cycle footer — 含 cycle_tokens / Session / Cache / Duration / Ended."""
    from src.cli.display import _render_footer, CycleRenderContext
    from src.cli.session_state import SessionStats
    stats = SessionStats()
    # Pretend 7 cycles already done (avg 47k each)
    for i in range(7):
        stats.record_cycle(47_000, datetime(2026, 5, 2, 18, i, 0, tzinfo=timezone.utc))
    ctx = CycleRenderContext(
        cycle_id="9f57abcd",
        trigger_type="alert",
        trigger_context={"type": "scheduled_tick"},
        state_snapshot=_make_state_snapshot(),
        messages=[],
        final_text="",
        cycle_tokens=41_947,
        stats=stats,
        cache_hit_rate=93.2,
        cycle_started_at=datetime(2026, 5, 2, 18, 14, 23, tzinfo=timezone.utc),
        cycle_ended_at=datetime(2026, 5, 2, 18, 14, 27, tzinfo=timezone.utc),
        forensic_reason=None,
    )
    out = _render_footer(ctx)
    assert "41,947 cycle" in out
    # Projected total = 7*47000 + 41947 = 370947 → 371k rounded
    assert "Session 371k" in out
    # Projected count = 8 cycles
    assert "8 cycles" in out
    # Projected avg = 370947 // 8 = 46368 → 46k rounded
    assert "avg 46k/cycle" in out
    assert "Cache    93.2% hit rate" in out
    assert "Duration 4.0s" in out
    assert "Ended 18:14:27 UTC" in out


def test_render_footer_forensic_path():
    """spec §6.4: forensic → Cache N/A (forensic) + cycle_tokens=0."""
    from src.cli.display import _render_footer, CycleRenderContext
    from src.cli.session_state import SessionStats
    ctx = CycleRenderContext(
        cycle_id="9f57abcd",
        trigger_type="alert",
        trigger_context={"type": "scheduled_tick"},
        state_snapshot=_make_state_snapshot(),
        messages=None,
        final_text=None,
        cycle_tokens=0,
        stats=SessionStats(),
        cache_hit_rate=None,
        cycle_started_at=datetime(2026, 5, 2, 18, 14, 23, tzinfo=timezone.utc),
        cycle_ended_at=datetime(2026, 5, 2, 18, 14, 27, tzinfo=timezone.utc),
        forensic_reason="usage_limit_exceeded",
    )
    out = _render_footer(ctx)
    assert "Cache    N/A (forensic)" in out
    assert "0 cycle" in out


def test_render_footer_aborted_path():
    """spec §6.5: retry-exhausted → Cache N/A (aborted)."""
    from src.cli.display import _render_footer, CycleRenderContext
    from src.cli.session_state import SessionStats
    ctx = CycleRenderContext(
        cycle_id="9f57abcd",
        trigger_type="alert",
        trigger_context={"type": "scheduled_tick"},
        state_snapshot=_make_state_snapshot(),
        messages=None,
        final_text=None,
        cycle_tokens=0,
        stats=SessionStats(),
        cache_hit_rate=None,
        cycle_started_at=datetime(2026, 5, 2, 18, 14, 23, tzinfo=timezone.utc),
        cycle_ended_at=datetime(2026, 5, 2, 18, 14, 30, tzinfo=timezone.utc),
        forensic_reason="aborted: ConnectionError: timeout",
    )
    out = _render_footer(ctx)
    assert "Cache    N/A (aborted)" in out


# === R2-8a: format_cycle_output(ctx) integration tests (T-INT-*) ===


from datetime import timedelta


def _make_ctx(
    cycle_id="9f57abcd",
    trigger_type="alert",
    trigger_context=None,
    state_snapshot=None,
    messages=None,
    final_text="",
    cycle_tokens=10_000,
    stats=None,
    cache_hit_rate=92.0,
    cycle_started_at=None,
    cycle_ended_at=None,
    forensic_reason=None,
):
    from src.cli.display import CycleRenderContext
    from src.cli.session_state import SessionStats
    if stats is None:
        stats = SessionStats()
    if cycle_started_at is None:
        cycle_started_at = datetime(2026, 5, 2, 18, 14, 23, tzinfo=timezone.utc)
    if cycle_ended_at is None:
        cycle_ended_at = cycle_started_at + timedelta(seconds=4)
    if state_snapshot is None:
        state_snapshot = _make_state_snapshot()
    if trigger_context is None:
        trigger_context = {"type": "scheduled_tick"}
    return CycleRenderContext(
        cycle_id=cycle_id, trigger_type=trigger_type,
        trigger_context=trigger_context, state_snapshot=state_snapshot,
        messages=messages, final_text=final_text, cycle_tokens=cycle_tokens,
        stats=stats, cache_hit_rate=cache_hit_rate,
        cycle_started_at=cycle_started_at, cycle_ended_at=cycle_ended_at,
        forensic_reason=forensic_reason,
    )


def test_int_1a_section_structure_via_builder():
    """T-INT-1a: 5 段架构结构断言 — Header / Reasoning / Action / Decision / Footer."""
    from src.cli.display import format_cycle_output
    from tests.fixtures.cycle_fixtures import build_cycle_messages
    msgs = build_cycle_messages(
        thinking_segments=["Initial assessment.", "Need more data.", "Decision time."],
        tool_call_segments=[
            [("get_market_data", {}, "=== Ticker (BTC/USDT:USDT) ===\nPrice: 75212.0")],
            [("get_position", {}, "No open positions.")],
            [],
        ],
        final_text="Hold position. 5min wake.",
    )
    out = format_cycle_output(_make_ctx(messages=msgs, final_text="Hold position. 5min wake."))
    # Header
    assert "Cycle 9f57" in out
    assert "Trigger" in out and "State" in out
    # Reasoning + Action 交织 (3 Reasoning, 2 Action segments — final has no tools)
    assert out.count("▾ Reasoning") == 3
    assert out.count("▾ Action") == 2
    # Decision precedes Footer
    decision_idx = out.find("▾ Decision")
    footer_idx = out.find("Tokens")
    assert decision_idx > 0 and footer_idx > decision_idx, "Decision must precede Footer"
    # Footer
    assert "Cache" in out and "Duration" in out


def test_int_1b_structural_fragments_vs_mockup():
    """T-INT-1b: Structural fragments check against spec §3.2 mockup (illustrative —
    not byte-equal verbatim per spec §3.2 注 'illustrative, non-byte-equal copy')."""
    from src.cli.display import format_cycle_output
    from tests.fixtures.cycle_fixtures import build_cycle_messages
    msgs = build_cycle_messages(
        thinking_segments=["Z" * 892, "X" * 1247, "Y" * 1567, "W" * 445],
        tool_call_segments=[
            [("get_market_data", {}, "BTC $75,212"),
             ("get_position", {}, "Short 0.265 @ $75,350"),
             ("get_open_orders", {}, "1 orders")],
            [("get_derivatives_data", {}, "Funding ..."),
             ("get_recent_trades", {}, "Recent ..."),
             ("get_higher_timeframe_view", {}, "HTF ..."),
             ("get_multi_timeframe_snapshot", {}, "MTF ...")],
            [("get_market_news", {}, "FGI Value: 26"),
             ("get_price_pivots", {}, "Pivots ..."),
             ("get_macro_context", {}, "BTC.D 58.00%")],
            [("add_price_level_alert", {}, "Price level alert set: below 74,890"),
             ("add_price_level_alert", {}, "Price level alert set: above 75,625"),
             ("set_next_wake", {}, "Next wake set to 10 min")],
        ],
        final_text="## Situation Assessment: BTC Flash Crash\n\n**What happened**: BTC dropped ~1.6% in 10 minutes",
    )
    out = format_cycle_output(_make_ctx(
        messages=msgs,
        final_text="## Situation Assessment: BTC Flash Crash\n\n**What happened**: BTC dropped ~1.6% in 10 minutes",
        cycle_tokens=41_947,
    ))
    assert "Cycle 9f57" in out
    assert "(892 chars total)" in out
    assert "(1247 chars total)" in out
    assert "▾ Action (3 tools)" in out
    assert "▾ Action (4 tools)" in out
    assert "Situation Assessment" in out
    assert "41,947 cycle" in out


def test_int_2_non_thinking_model():
    """T-INT-2: 非 thinking model → 跳过 ▾ Reasoning，▾ Action 紧接 Header."""
    from src.cli.display import format_cycle_output
    from tests.fixtures.cycle_fixtures import build_cycle_messages
    msgs = build_cycle_messages(
        thinking_segments=[None, None],
        tool_call_segments=[[("get_position", {}, "FLAT")], []],
        final_text="No action.",
    )
    out = format_cycle_output(_make_ctx(messages=msgs, final_text="No action."))
    assert "▾ Reasoning" not in out, "non-thinking model 不应渲染 Reasoning 段"
    assert "▾ Action" in out
    assert "▾ Decision" in out


def test_int_3_zero_tool_call_cycle():
    """T-INT-3: 0 tool call cycle → 仅 Reasoning + Decision，无 ▾ Action."""
    from src.cli.display import format_cycle_output
    from tests.fixtures.cycle_fixtures import build_cycle_messages
    msgs = build_cycle_messages(
        thinking_segments=["Direct decision, no info needed."],
        tool_call_segments=[[]],
        final_text="Hold.",
    )
    out = format_cycle_output(_make_ctx(messages=msgs, final_text="Hold."))
    assert "▾ Reasoning" in out
    assert "▾ Action" not in out
    assert "▾ Decision" in out


def test_int_4_forensic_usage_limit_exceeded():
    """T-INT-4: forensic 路径 → Header + Footer + 占位 Decision，Cache N/A (forensic)."""
    from src.cli.display import format_cycle_output
    out = format_cycle_output(_make_ctx(
        messages=None, final_text=None, cycle_tokens=0,
        cache_hit_rate=None, forensic_reason="usage_limit_exceeded",
    ))
    assert "▾ Reasoning" not in out, "forensic 不渲染 partial Reasoning"
    assert "▾ Action" not in out, "forensic 不渲染 partial Action"
    assert "[no decision — usage limit exceeded; partial messages unavailable]" in out
    assert "Cache    N/A (forensic)" in out


def test_int_5_retry_exhausted_path():
    """T-INT-5: retry-exhausted → 占位 Decision + Cache N/A (aborted)."""
    from src.cli.display import format_cycle_output
    out = format_cycle_output(_make_ctx(
        messages=None, final_text=None, cycle_tokens=0,
        cache_hit_rate=None,
        forensic_reason="aborted: ConnectionError: timeout",
    ))
    assert "[cycle aborted — 3 attempts failed: ConnectionError: timeout]" in out
    assert "Cache    N/A (aborted)" in out


def test_int_5b_retry_exhausted_with_markup_in_error():
    """T-INT-5b: retry-exhausted error message 含 markup 字面值 → 仅一次 escape，
    终端显示自然字面值无反斜杠 (spec §5.2 round-7 校准)."""
    from src.cli.display import format_cycle_output
    out = format_cycle_output(_make_ctx(
        messages=None, final_text=None, cycle_tokens=0,
        cache_hit_rate=None,
        forensic_reason="aborted: RuntimeError: [red]boom[/]",
    ))
    assert "RuntimeError" in out
    assert "boom" in out
    assert r"\\[red]" not in out  # double-escape signature


def test_int_6_session_stats_累计_with_forensic():
    """T-INT-6: 5 cycles 累加（含 1 forensic）→ footer Session 累计 / forensic 也计 cycle_count."""
    from src.cli.display import format_cycle_output
    from src.cli.session_state import SessionStats
    from tests.fixtures.cycle_fixtures import build_cycle_messages
    stats = SessionStats()
    base = datetime(2026, 5, 2, 18, 0, 0, tzinfo=timezone.utc)
    stats.record_cycle(40_000, base)
    stats.record_cycle(40_000, base + timedelta(minutes=5))
    stats.record_cycle(0, base + timedelta(minutes=10))
    stats.record_cycle(40_000, base + timedelta(minutes=15))
    msgs = build_cycle_messages(
        thinking_segments=["Decision."], tool_call_segments=[[]], final_text="OK.",
    )
    out = format_cycle_output(_make_ctx(
        messages=msgs, final_text="OK.", cycle_tokens=40_000, stats=stats,
        cycle_started_at=base + timedelta(minutes=20),
        cycle_ended_at=base + timedelta(minutes=20, seconds=4),
    ))
    assert "Session 160k" in out
    assert "5 cycles" in out
    assert "avg 32k/cycle" in out


def test_int_7_cache_hit_rate_normal_branch():
    """T-INT-7: cache_hit_rate=92.0 → footer 'Cache    92.0% hit rate'."""
    from src.cli.display import format_cycle_output
    from tests.fixtures.cycle_fixtures import build_cycle_messages
    msgs = build_cycle_messages(thinking_segments=["t"], tool_call_segments=[[]], final_text="d")
    out = format_cycle_output(_make_ctx(messages=msgs, final_text="d", cache_hit_rate=92.0))
    assert "Cache    92.0% hit rate" in out


def test_int_8_session_stats_no_cross_day_reset():
    """T-INT-8 / AC13: 跨日 last_cycle_ended_at 不重置 → Header 显示 +X min from prev."""
    from src.cli.display import format_cycle_output
    from src.cli.session_state import SessionStats
    from tests.fixtures.cycle_fixtures import build_cycle_messages
    stats = SessionStats()
    stats.record_cycle(40_000, datetime(2026, 5, 2, 23, 55, 0, tzinfo=timezone.utc))
    msgs = build_cycle_messages(thinking_segments=["t"], tool_call_segments=[[]], final_text="d")
    out = format_cycle_output(_make_ctx(
        messages=msgs, final_text="d", stats=stats,
        cycle_started_at=datetime(2026, 5, 3, 3, 55, 0, tzinfo=timezone.utc),
        cycle_ended_at=datetime(2026, 5, 3, 3, 55, 4, tzinfo=timezone.utc),
    ))
    assert "+240 min from prev" in out
    assert "(first cycle)" not in out


def test_int_9_first_cycle_short_label():
    """AC10 / T-RH-2 集成版：首 cycle Header '(first cycle)' 不带 +X min from prev."""
    from src.cli.display import format_cycle_output
    from tests.fixtures.cycle_fixtures import build_cycle_messages
    msgs = build_cycle_messages(thinking_segments=["t"], tool_call_segments=[[]], final_text="d")
    out = format_cycle_output(_make_ctx(messages=msgs, final_text="d"))
    assert "(first cycle)" in out


def test_int_11_decision_uses_ctx_final_text_not_textpart():
    """T-INT-11 (P1 reviewer 补): Decision 段 SoT = ctx.final_text，不依赖 messages 中
    TextPart 提取 (spec §4.4.2)."""
    from pydantic_ai.messages import ModelResponse, ThinkingPart
    from src.cli.display import format_cycle_output
    msgs = [ModelResponse(parts=[ThinkingPart(content="thought.")])]
    out = format_cycle_output(_make_ctx(
        messages=msgs, final_text="Synthesized decision from ctx.",
    ))
    assert "▾ Decision" in out
    assert "Synthesized decision from ctx." in out


def test_int_12_decision_empty_string_placeholder():
    """spec §4.4.3: ctx.final_text == "" → [empty decision text] 占位."""
    from src.cli.display import format_cycle_output
    from tests.fixtures.cycle_fixtures import build_cycle_messages
    msgs = build_cycle_messages(thinking_segments=["t"], tool_call_segments=[[]], final_text="")
    out = format_cycle_output(_make_ctx(messages=msgs, final_text=""))
    assert "[empty decision text]" in out


def test_int_10_unknown_trigger_type_fallback(caplog):
    """T-INT-10 / T-EH-2 (renumbered): trigger_context.type 未知 → fallback {TYPE_UPPER} 不带详情."""
    import logging
    from src.cli.display import format_cycle_output
    from tests.fixtures.cycle_fixtures import build_cycle_messages
    msgs = build_cycle_messages(thinking_segments=["t"], tool_call_segments=[[]], final_text="d")
    with caplog.at_level(logging.WARNING):
        out = format_cycle_output(_make_ctx(
            messages=msgs, final_text="d", trigger_type="alert",
            trigger_context={"type": "unknown_future_type"},
        ))
    trigger_line = next(l for l in out.splitlines() if "Trigger" in l)
    assert "ALERT" in trigger_line
    assert "—" not in trigger_line
    assert any("trigger_context.type unknown" in r.message for r in caplog.records)


# === R2-8a: Drift guards (T-DG-1/2/3) ===


def test_dg_1_extract_helpers_equivalent_at_smoke_baseline():
    """T-DG-1: smoke baseline 下 _extract_thinking_text(messages) 等价于
    "\\n\\n".join(_extract_reasoning_per_response 中非 None 项)."""
    from src.cli.app import _extract_thinking_text
    from src.cli.display import _extract_reasoning_per_response
    from tests.fixtures.cycle_fixtures import build_cycle_messages
    msgs = build_cycle_messages(
        thinking_segments=["alpha", "beta", "gamma"],
        tool_call_segments=[[("get_market_data", {}, "x")], [], []],
        final_text="done",
    )
    full_text = _extract_thinking_text(msgs)
    per_resp = _extract_reasoning_per_response(msgs)
    rejoined = "\n\n".join(t for t in per_resp if t)
    assert full_text == rejoined, (
        f"helper drift detected:\n  _extract_thinking_text => {full_text!r}\n"
        f"  rejoin per-resp        => {rejoined!r}"
    )


def test_dg_2_thinking_part_precedes_toolcall_in_smoke_baseline():
    """T-DG-2: smoke baseline 下 ThinkingPart 在 ToolCallPart 之前 (parts[0])."""
    from pydantic_ai.messages import ModelResponse, ThinkingPart, ToolCallPart
    from tests.fixtures.cycle_fixtures import build_cycle_messages
    msgs = build_cycle_messages(
        thinking_segments=["a", "b"],
        tool_call_segments=[[("get_market_data", {}, "x")], []],
        final_text="d",
    )
    for mr in [m for m in msgs if isinstance(m, ModelResponse)]:
        kinds = [type(p).__name__ for p in mr.parts]
        if "ThinkingPart" in kinds and "ToolCallPart" in kinds:
            assert kinds.index("ThinkingPart") < kinds.index("ToolCallPart"), (
                f"ThinkingPart 应先于 ToolCallPart: {kinds}"
            )


async def test_dg_3_state_snapshot_field_set_unchanged():
    """T-DG-3: state_snapshot 7 字段集合 = R2-7 contract.
    新增字段触发本测试 fail，提示 R2-8a 是否需消费."""
    expected = {
        "position", "balance", "market", "pending_orders",
        "active_alerts", "_errors", "_cycle_id",
    }
    from unittest.mock import AsyncMock, MagicMock
    from src.integrations.exchange.base import Balance, Ticker
    from src.services.cycle_capture import _capture_state_snapshot

    deps = MagicMock()
    deps.symbol = "BTC/USDT:USDT"
    deps.exchange = MagicMock()
    deps.exchange.fetch_positions = AsyncMock(return_value=[])
    deps.exchange.fetch_balance = AsyncMock(return_value=Balance(
        total_usdt=100.0, free_usdt=100.0, used_usdt=0.0,
    ))
    deps.exchange.fetch_open_orders = AsyncMock(return_value=[])
    deps.exchange.get_price_level_alerts = MagicMock(return_value=[])
    deps.market_data = MagicMock()
    deps.market_data.get_ticker = AsyncMock(return_value=Ticker(
        symbol="BTC/USDT:USDT", last=100.0, bid=99.0, ask=101.0,
        high=110.0, low=90.0, base_volume=1.0, timestamp=0,
    ))
    snapshot = await _capture_state_snapshot("test-cycle", deps)
    assert set(snapshot.keys()) == expected, (
        f"state_snapshot 字段集合漂移: actual={set(snapshot.keys())} expected={expected}\n"
        "  新增字段 → 检查 R2-8a 是否需消费 (header / footer / 段渲染)；\n"
        "  字段移除 → 检查 R2-8a 渲染 fallback 是否需更新。"
    )


# === R2-8a: Edge case 细化 ===


def test_eh_1_trigger_context_none_renders_bare_type():
    """T-EH-1: trigger_context=None → Header 'Trigger    {TYPE_UPPER}' 不带详情."""
    from src.cli.display import _format_trigger_detail
    out = _format_trigger_detail("alert", None)
    assert out == "ALERT"
    out = _format_trigger_detail("conditional", None)
    assert out == "CONDITIONAL"


def test_eh_3_conditional_fill_missing_price_partial_degrade():
    """T-EH-3 (spec §6.1): conditional fill 缺 fill_price → 部分降级保留 trigger_reason."""
    from src.cli.display import _format_trigger_detail
    out = _format_trigger_detail("conditional", {
        "type": "fill", "trigger_reason": "TP_FILL",
    })
    assert out == "CONDITIONAL — TP_FILL", (
        f"spec §6.1 T-EH-3 要求保留 trigger_reason 部分降级；实际 {out!r}"
    )


def test_eh_3b_conditional_fill_no_trigger_reason_full_fallback():
    """T-EH-3b: conditional fill 连 trigger_reason 都缺 → 全 fallback 到 {TYPE_UPPER}."""
    from src.cli.display import _format_trigger_detail
    out = _format_trigger_detail("conditional", {"type": "fill"})
    assert out == "CONDITIONAL"


def test_es_1_state_snapshot_none_unavailable():
    """T-ES-1: state_snapshot=None → State 段 [snapshot unavailable]."""
    from src.cli.display import _format_state_line
    assert _format_state_line(None) == "[snapshot unavailable]"


def test_es_2_position_none_renders_flat():
    """T-ES-2: position=None → 'FLAT'."""
    from src.cli.display import _format_state_line
    out = _format_state_line(_make_state_snapshot(
        balance={"total_usdt": 10000.0, "free_usdt": 10000.0, "used_usdt": 0.0},
    ))
    assert "FLAT" in out
    assert "Balance $10,000" in out


def test_es_3_balance_none_omits_balance_segment():
    """T-ES-3: balance=None → 省略 Balance 字段."""
    from src.cli.display import _format_state_line
    out = _format_state_line(_make_state_snapshot(
        position={
            "symbol": "BTC/USDT:USDT", "side": "short", "contracts": 0.265,
            "entry_price": 75350.0, "leverage": 5, "unrealized_pnl": 75.0,
            "liquidation_price": 0.0, "pnl_pct": 0.10,
        },
    ))
    assert "Short 0.265" in out
    assert "Balance" not in out


def test_es_5_position_pnl_pct_none_omits_pnl_segment():
    """T-ES-5: pnl_pct=None (notional 0 / 计算失败) → 省略 PnL 字段."""
    from src.cli.display import _format_state_line
    out = _format_state_line(_make_state_snapshot(
        position={
            "symbol": "BTC/USDT:USDT", "side": "short", "contracts": 0.265,
            "entry_price": 75350.0, "leverage": 5, "unrealized_pnl": 0.0,
            "liquidation_price": 0.0, "pnl_pct": None,
        },
        balance={"total_usdt": 10000.0, "free_usdt": 10000.0, "used_usdt": 0.0},
    ))
    assert "Short 0.265" in out
    assert "PnL" not in out


def test_re_2_thinking_empty_string_skipped():
    """T-RE-2: ThinkingPart content == "" → Reasoning 段省略."""
    from pydantic_ai.messages import ModelResponse, ThinkingPart, TextPart
    from src.cli.display import format_cycle_output
    msgs = [ModelResponse(parts=[ThinkingPart(content=""), TextPart(content="d")])]
    out = format_cycle_output(_make_ctx(messages=msgs, final_text="d"))
    assert "▾ Reasoning" not in out, "空 ThinkingPart 不应渲染 Reasoning 段"


def test_eh_3c_conditional_open_fill_pnl_none_keeps_full_context():
    """T-EH-3c (review 2nd round Important): 开仓 fill (trigger_reason=market, pnl=None) →
    前段 symbol/side/amount/fill_price 必保留，仅 PnL 段省略。

    spec §6.1 T-EH-3 部分降级原意是缺 fill_price 等字段时保留 trigger_reason；
    但 FillEvent.pnl 在开仓时**正常即 None**（不是缺字段），不应触发 fallback
    丢掉 symbol/side/amount 全部上下文。"""
    from src.cli.display import _format_trigger_detail
    out = _format_trigger_detail("conditional", {
        "type": "fill", "trigger_reason": "market",
        "symbol": "BTC/USDT:USDT", "side": "buy", "position_side": "long",
        "amount": 0.265, "fill_price": 75350.0,
        "fee": 5.0, "pnl": None,  # 开仓 fill: 已实现盈亏 None
        "order_id": "abc123", "timestamp": 0, "is_full_close": False,
    })
    # 前段必保留 — symbol / side / amount / fill_price
    assert "long" in out
    assert "BTC" in out
    assert "0.265" in out
    assert "$75,350" in out
    assert "market" in out  # trigger_reason
    # PnL 段省略 (pnl=None)
    assert "PnL" not in out, f"开仓 fill 不应渲染 PnL 段；实际 {out!r}"


def test_eh_3d_conditional_close_fill_with_pnl_renders_pnl_segment():
    """T-EH-3d: 平仓 fill (pnl 非 None) → 完整渲染含 PnL 段."""
    from src.cli.display import _format_trigger_detail
    out = _format_trigger_detail("conditional", {
        "type": "fill", "trigger_reason": "TP_FILL",
        "symbol": "BTC/USDT:USDT", "side": "sell", "position_side": "long",
        "amount": 0.265, "fill_price": 78000.0,
        "fee": 5.0, "pnl": 700.50,
        "order_id": "abc456", "timestamp": 0, "is_full_close": True,
    })
    assert "TP_FILL" in out
    assert "long" in out
    assert "$78,000" in out
    assert "PnL +700.50 USDT" in out


# === R2-8c helper tests ===

# --- T-PARSE: _parse_sections ---


def test_parse_sections_multi_sections():
    """T-PARSE-1: 多 sections 完整 parse — header + body 分组。"""
    from src.cli.display import _parse_sections, Section
    content = (
        "=== Ticker (BTC/USDT:USDT) ===\n"
        "Price: 75212.00\n"
        "Bid: 75200.00\n"
        "\n"
        "=== Technical Indicators (5m) ===\n"
        "RSI(14): 33.55\n"
        "MACD: -131"
    )
    out = _parse_sections(content)
    assert out == [
        Section(header="Ticker (BTC/USDT:USDT)",
                body=("Price: 75212.00", "Bid: 75200.00")),
        Section(header="Technical Indicators (5m)",
                body=("RSI(14): 33.55", "MACD: -131")),
    ]


def test_parse_sections_no_header_fallback():
    """T-PARSE-2: 无 header → 单 unnamed section (fallback path, get_memories case)."""
    from src.cli.display import _parse_sections, Section
    content = "Plain text line 1\nPlain text line 2"
    out = _parse_sections(content)
    assert out == [Section(header=None, body=("Plain text line 1", "Plain text line 2"))]


def test_parse_sections_empty_content():
    """T-PARSE-3: 空 content → 单 unnamed empty section。"""
    from src.cli.display import _parse_sections, Section
    out = _parse_sections("")
    assert out == [Section(header=None, body=())]


# --- T-CLIP: _clip_body ---


def test_clip_body_under_threshold_keep_all():
    """T-CLIP-1: body < 10 行 → keep all (D7 universal rule)."""
    from src.cli.display import _clip_body
    body = tuple(f"line {i}" for i in range(9))
    assert _clip_body(body) == body


def test_clip_body_at_or_above_threshold_head_tail():
    """T-CLIP-2: body ≥ 10 行 → head=2 + '[N rows omitted]' + tail=2 (D7 校准 head/tail=2)."""
    from src.cli.display import _clip_body
    body = tuple(f"line {i}" for i in range(15))
    out = _clip_body(body)
    assert out == (
        "line 0", "line 1",
        "[... 11 rows omitted ...]",
        "line 13", "line 14",
    )


def test_clip_body_exact_threshold_triggers_clipping():
    """T-CLIP-3: body == 10 行 (边界) → head/tail 触发 (>= n)."""
    from src.cli.display import _clip_body
    body = tuple(f"line {i}" for i in range(10))
    out = _clip_body(body)
    assert out == (
        "line 0", "line 1",
        "[... 6 rows omitted ...]",
        "line 8", "line 9",
    )


# --- T-RPT: _render_perception_tool ---


def test_render_perception_tool_single_section():
    """T-RPT-1: 单 section keep all → '  ⚙ tool\n    === Section ===\n    body...'."""
    from src.cli.display import _render_perception_tool
    content = (
        "=== Account Balance ===\n"
        "Total: 998.00 USDT\n"
        "Free: 800.00"
    )
    out = _render_perception_tool("get_account_balance", content)
    assert out == (
        "  ⚙ get_account_balance\n"
        "    === Account Balance ===\n"
        "    Total: 998.00 USDT\n"
        "    Free: 800.00"
    )


def test_render_perception_tool_multi_section_blank_separator():
    """T-RPT-2: 多 sections 间插入 display-only blank line。"""
    from src.cli.display import _render_perception_tool
    content = (
        "=== Sec A ===\n"
        "a1\n"
        "a2\n"
        "\n"
        "=== Sec B ===\n"
        "b1"
    )
    out = _render_perception_tool("get_market_data", content)
    assert out == (
        "  ⚙ get_market_data\n"
        "    === Sec A ===\n"
        "    a1\n"
        "    a2\n"
        "\n"
        "    === Sec B ===\n"
        "    b1"
    )


def test_render_perception_tool_dense_section_clipped():
    """T-RPT-3: section body ≥ 10 → head/tail clipping in render output."""
    from src.cli.display import _render_perception_tool
    body_lines = "\n".join(f"row {i}" for i in range(15))
    content = f"=== Recent Candles ===\n{body_lines}"
    out = _render_perception_tool("get_market_data", content)
    assert "    [... 11 rows omitted ...]" in out
    assert "    row 0" in out
    assert "    row 14" in out
    assert "    row 7" not in out  # middle row dropped


def test_render_perception_tool_fallback_no_header():
    """T-RPT-4: content 无 sections → unnamed section fallback (get_memories backend path)."""
    from src.cli.display import _render_perception_tool
    content = "Memory entry 1\nMemory entry 2"
    out = _render_perception_tool("get_memories", content)
    assert out == (
        "  ⚙ get_memories\n"
        "    Memory entry 1\n"
        "    Memory entry 2"
    )


# --- T-DG: drift guards ---


def test_dg_2_dispatch_sets_partition_all_registered_tools():
    """T-DG-2: 三层集合 + save_memory branch 互斥 + 完整覆盖 32 registered tools.

    Spec §4.4: _PERCEPTION_TOOL_NAMES (20) ∪ _EXECUTION_TOOL_NAMES (11) ∪ {save_memory}
    必须等于 REGISTERED_TOOL_NAMES (32)，且互不重叠。
    _SECTIONED_PERCEPTION_TOOL_NAMES (19) ⊂ _PERCEPTION_TOOL_NAMES（仅 get_memories 例外）。
    """
    from src.cli.display import (
        _PERCEPTION_TOOL_NAMES,
        _SECTIONED_PERCEPTION_TOOL_NAMES,
        _EXECUTION_TOOL_NAMES,
    )
    from src.agent.trader import REGISTERED_TOOL_NAMES

    perception = _PERCEPTION_TOOL_NAMES
    sectioned = _SECTIONED_PERCEPTION_TOOL_NAMES
    execution = _EXECUTION_TOOL_NAMES
    save = frozenset({"save_memory"})

    # Sectioned ⊂ perception, only get_memories excluded
    assert sectioned <= perception
    assert perception - sectioned == frozenset({"get_memories"})

    # 三层 + save_memory 互斥
    assert perception.isdisjoint(execution)
    assert perception.isdisjoint(save)
    assert execution.isdisjoint(save)

    # 完整覆盖 32 registered
    union = perception | execution | save
    declared = set(REGISTERED_TOOL_NAMES)
    assert union == declared, (
        f"Dispatch sets ≠ REGISTERED_TOOL_NAMES:\n"
        f"  Missing from dispatch: {declared - union}\n"
        f"  Extra in dispatch: {union - declared}"
    )

    # Counts per spec §4.4
    assert len(perception) == 20
    assert len(sectioned) == 19
    assert len(execution) == 11


def test_ec_11_unregistered_tool_falls_back_with_warning(caplog):
    """T-EC-11: 未注册 tool name → R2-8a single-line + warning log."""
    from pydantic_ai.messages import ToolCallPart, ToolReturnPart
    from src.cli.display import _render_action

    calls = [ToolCallPart(tool_name="get_unknown_drift", args={}, tool_call_id="c1")]
    returns = {
        "c1": ToolReturnPart(tool_name="get_unknown_drift", tool_call_id="c1",
                              content="some content"),
    }
    with caplog.at_level("WARNING", logger="src.cli.display"):
        out = _render_action(calls, returns, cycle_id="abcd1234")

    assert "get_unknown_drift" in out
    assert "some content" in out  # _fallback_summary kept
    assert any("not in" in r.getMessage() and "get_unknown_drift" in r.getMessage()
               for r in caplog.records)


def test_int_1_render_action_mixed_perception_execution():
    """T-INT-1: 完整 cycle render — perception 走 multi-line + execution 走 R2-8a single-line."""
    from pydantic_ai.messages import ToolCallPart, ToolReturnPart
    from src.cli.display import _render_action

    calls = [
        ToolCallPart(
            tool_name="get_account_balance", args={}, tool_call_id="c1",
        ),
        ToolCallPart(
            tool_name="set_next_wake", args={"minutes": 5}, tool_call_id="c2",
        ),
    ]
    returns = {
        "c1": ToolReturnPart(
            tool_name="get_account_balance", tool_call_id="c1",
            content="=== Account Balance ===\nTotal: 998.00 USDT",
        ),
        "c2": ToolReturnPart(
            tool_name="set_next_wake", tool_call_id="c2",
            content="Next wake set to 5 min",
        ),
    }
    out = _render_action(calls, returns, cycle_id="abcd1234")

    # Header
    assert "▾ Action (2 tools)" in out
    # Perception multi-line: 4-space indent + section
    assert "  ⚙ get_account_balance" in out
    assert "    === Account Balance ===" in out
    assert "    Total: 998.00 USDT" in out
    # Execution single-line + <22 padding (R2-8a 维持)
    assert "  ⚙ set_next_wake          5min" in out  # <22 padding 长度 22


# --- T-INT-3: thinking 截断升级 800→2000 (D10) ---


def test_int_3_thinking_1500_chars_keep_all():
    """T-INT-3a: 1500-char thinking < 2000 → keep all (no truncation suffix).

    R2-8d D6: default cap 2000→15000; this test still valid as 1500 < both.
    """
    from src.cli.display import _render_reasoning
    text = "x" * 1500
    out = _render_reasoning(text)
    assert "[+" not in out  # no truncation marker
    assert "1500 chars total" in out


def test_int_3_thinking_above_default_cap_truncated():
    """T-INT-3b (R2-8d D6): thinking > default cap → truncate + ' ... [+N chars]' suffix.

    Default cap 15000 (R2-8d); test 18000 chars input → 15000 kept + 3000 truncated.
    """
    from src.cli.display import _render_reasoning
    text = "y" * 18000
    out = _render_reasoning(text)
    assert "[+3000 chars]" in out
    assert "18000 chars total" in out


# === R2-8c per-tool snapshot fixtures ===

# Snapshot helper — invoke _render_perception_tool with raw tool content fixture
# and verify output matches expected. Inline fixtures (spec §5.2 plan决议).


def _assert_perception_render(tool_name: str, content: str, expected: str):
    """Helper: run _render_perception_tool and assert output equals expected."""
    from src.cli.display import _render_perception_tool
    actual = _render_perception_tool(tool_name, content)
    assert actual == expected, (
        f"Render mismatch for {tool_name}:\n"
        f"--- expected ---\n{expected}\n"
        f"--- actual ---\n{actual}"
    )


# --- Batch A: tier-1 high-frequency snapshots ---


def test_snapshot_get_market_data_happy_path():
    """Snapshot — get_market_data 4-section happy path render."""
    content = (
        "=== Ticker (BTC/USDT:USDT) ===\n"
        "Price: 75212.00 | Bid: 75200.00 | Ask: 75215.00\n"
        "24h High: 76225.00 | Low: 74893.00 | Volume: 8200.00\n"
        "\n"
        "=== Technical Indicators (5m) ===\n"
        "RSI(14): 33.55\n"
        "MACD: -131 (sig -98, hist -33)\n"
        "\n"
        "=== Market Context ===\n"
        "ATR(14): 218.50 (0.29% of price, 5m candles)\n"
        "\n"
        "=== Recent Candles (5m, last 3) ===\n"
        "Time         Open       High        Low      Close        Vol\n"
        "14:00     75250.00  75300.00  75180.00  75220.00     320.5\n"
        "14:05     75180.00  75220.00  75150.00  75212.00     310.2"
    )
    expected = (
        "  ⚙ get_market_data\n"
        "    === Ticker (BTC/USDT:USDT) ===\n"
        "    Price: 75212.00 | Bid: 75200.00 | Ask: 75215.00\n"
        "    24h High: 76225.00 | Low: 74893.00 | Volume: 8200.00\n"
        "\n"
        "    === Technical Indicators (5m) ===\n"
        "    RSI(14): 33.55\n"
        "    MACD: -131 (sig -98, hist -33)\n"
        "\n"
        "    === Market Context ===\n"
        "    ATR(14): 218.50 (0.29% of price, 5m candles)\n"
        "\n"
        "    === Recent Candles (5m, last 3) ===\n"
        "    Time         Open       High        Low      Close        Vol\n"
        "    14:00     75250.00  75300.00  75180.00  75220.00     320.5\n"
        "    14:05     75180.00  75220.00  75150.00  75212.00     310.2"
    )
    _assert_perception_render("get_market_data", content, expected)


def test_snapshot_get_higher_timeframe_view_happy_path():
    """Snapshot — get_higher_timeframe_view 4-section happy path (incl. 20-period Band header)."""
    content = (
        "=== Higher Timeframe View (BTC/USDT:USDT, 4h) ===\n"
        "Current Price: 75,212.00\n"
        "\n"
        "=== MA Distances ===\n"
        "MA50: 73,200.00 (price vs MA: +2.7%)\n"
        "MA100: 71,500.00 (price vs MA: +5.2%)\n"
        "MA200: 68,800.00 (price vs MA: +9.3%)\n"
        "\n"
        "=== Range Position ===\n"
        "100-period High: 78,500.00 (12 4h-bars ago)\n"
        "100-period Low:  68,200.00 (45 4h-bars ago)\n"
        "Current price within range: 68.1%\n"
        "\n"
        "=== 20-period Band ===\n"
        "20-period High: 76,800.00\n"
        "20-period Low:  74,100.00\n"
        "20-period range width: 3.6%"
    )
    expected = (
        "  ⚙ get_higher_timeframe_view\n"
        "    === Higher Timeframe View (BTC/USDT:USDT, 4h) ===\n"
        "    Current Price: 75,212.00\n"
        "\n"
        "    === MA Distances ===\n"
        "    MA50: 73,200.00 (price vs MA: +2.7%)\n"
        "    MA100: 71,500.00 (price vs MA: +5.2%)\n"
        "    MA200: 68,800.00 (price vs MA: +9.3%)\n"
        "\n"
        "    === Range Position ===\n"
        "    100-period High: 78,500.00 (12 4h-bars ago)\n"
        "    100-period Low:  68,200.00 (45 4h-bars ago)\n"
        "    Current price within range: 68.1%\n"
        "\n"
        "    === 20-period Band ===\n"
        "    20-period High: 76,800.00\n"
        "    20-period Low:  74,100.00\n"
        "    20-period range width: 3.6%"
    )
    _assert_perception_render("get_higher_timeframe_view", content, expected)


def test_snapshot_get_higher_timeframe_view_unavailable():
    """Snapshot — get_higher_timeframe_view L2 inline Error fallback (Option D)."""
    content = (
        "=== Higher Timeframe View (BTC/USDT:USDT, 4h) ===\n"
        "Error: Temporarily unavailable."
    )
    expected = (
        "  ⚙ get_higher_timeframe_view\n"
        "    === Higher Timeframe View (BTC/USDT:USDT, 4h) ===\n"
        "    Error: Temporarily unavailable."
    )
    _assert_perception_render("get_higher_timeframe_view", content, expected)


def test_snapshot_get_multi_timeframe_snapshot_happy_path():
    """Snapshot — get_multi_timeframe_snapshot single-section flat row layout."""
    content = (
        "=== Multi-TF Snapshot (BTC/USDT:USDT) ===\n"
        "Current price: 75212.00\n"
        "Columns: Momentum (price vs primary MA) | Structure (MA alignment) | "
        "Volatility (ATR as % of price) | Range pos (position within 20-bar high-low, "
        "0%=low / 100%=high)\n"
        "\n"
        "5m:  +0.5% vs MA20    | MA20 above MA50                          | "
        "ATR 0.29%   | range pos 60%\n"
        "1h:  +1.2% vs MA50    | MA50 above MA200                         | "
        "ATR 0.78%   | range pos 72%"
    )
    expected = (
        "  ⚙ get_multi_timeframe_snapshot\n"
        "    === Multi-TF Snapshot (BTC/USDT:USDT) ===\n"
        "    Current price: 75212.00\n"
        "    Columns: Momentum (price vs primary MA) | Structure (MA alignment) | "
        "Volatility (ATR as % of price) | Range pos (position within 20-bar high-low, "
        "0%=low / 100%=high)\n"
        "\n"
        "    5m:  +0.5% vs MA20    | MA20 above MA50                          | "
        "ATR 0.29%   | range pos 60%\n"
        "    1h:  +1.2% vs MA50    | MA50 above MA200                         | "
        "ATR 0.78%   | range pos 72%"
    )
    _assert_perception_render("get_multi_timeframe_snapshot", content, expected)


def test_snapshot_get_multi_timeframe_snapshot_unavailable():
    """Snapshot — get_multi_timeframe_snapshot L2 inline Error fallback (Option D)."""
    content = (
        "=== Multi-TF Snapshot (BTC/USDT:USDT) ===\n"
        "Error: Temporarily unavailable."
    )
    expected = (
        "  ⚙ get_multi_timeframe_snapshot\n"
        "    === Multi-TF Snapshot (BTC/USDT:USDT) ===\n"
        "    Error: Temporarily unavailable."
    )
    _assert_perception_render("get_multi_timeframe_snapshot", content, expected)


def test_snapshot_get_price_pivots_happy_path():
    """Snapshot — get_price_pivots 3-section happy path render."""
    content = (
        "=== Price Pivots (BTC/USDT:USDT, main TF: 5m) ===\n"
        "Current Price: 75,212.00\n"
        "\n"
        "=== Levels Above Current Price ===\n"
        "Swing High: 75,820.00 (+0.81%, 12 bars ago)\n"
        "Prior Daily H: 76,400.00 (+1.58%)\n"
        "\n"
        "=== Levels Below Current Price ===\n"
        "Swing Low: 74,680.00 (-0.71%, 8 bars ago)\n"
        "Prior Daily L: 74,200.00 (-1.35%)"
    )
    expected = (
        "  ⚙ get_price_pivots\n"
        "    === Price Pivots (BTC/USDT:USDT, main TF: 5m) ===\n"
        "    Current Price: 75,212.00\n"
        "\n"
        "    === Levels Above Current Price ===\n"
        "    Swing High: 75,820.00 (+0.81%, 12 bars ago)\n"
        "    Prior Daily H: 76,400.00 (+1.58%)\n"
        "\n"
        "    === Levels Below Current Price ===\n"
        "    Swing Low: 74,680.00 (-0.71%, 8 bars ago)\n"
        "    Prior Daily L: 74,200.00 (-1.35%)"
    )
    _assert_perception_render("get_price_pivots", content, expected)


def test_snapshot_get_price_pivots_unavailable():
    """Snapshot — get_price_pivots L2 inline Error fallback (ticker fail, Option D)."""
    content = (
        "=== Price Pivots (BTC/USDT:USDT, main TF: 5m) ===\n"
        "Error: Temporarily unavailable."
    )
    expected = (
        "  ⚙ get_price_pivots\n"
        "    === Price Pivots (BTC/USDT:USDT, main TF: 5m) ===\n"
        "    Error: Temporarily unavailable."
    )
    _assert_perception_render("get_price_pivots", content, expected)


def test_snapshot_get_price_pivots_with_swing_status_section():
    """Snapshot — get_price_pivots conditional Swing Status section per spec §4.2.4."""
    content = (
        "=== Price Pivots (BTC/USDT:USDT, main TF: 5m) ===\n"
        "Current Price: 75,212.00\n"
        "\n"
        "=== Levels Above Current Price ===\n"
        "Prior Daily H: 76,400.00 (+1.58%)\n"
        "\n"
        "=== Levels Below Current Price ===\n"
        "Prior Daily L: 74,200.00 (-1.35%)\n"
        "\n"
        "=== Swing Status ===\n"
        "(No swing pivots in 100-bar window)"
    )
    expected = (
        "  ⚙ get_price_pivots\n"
        "    === Price Pivots (BTC/USDT:USDT, main TF: 5m) ===\n"
        "    Current Price: 75,212.00\n"
        "\n"
        "    === Levels Above Current Price ===\n"
        "    Prior Daily H: 76,400.00 (+1.58%)\n"
        "\n"
        "    === Levels Below Current Price ===\n"
        "    Prior Daily L: 74,200.00 (-1.35%)\n"
        "\n"
        "    === Swing Status ===\n"
        "    (No swing pivots in 100-bar window)"
    )
    _assert_perception_render("get_price_pivots", content, expected)


def test_snapshot_get_price_pivots_with_prior_period_section():
    """Snapshot — get_price_pivots conditional Prior Period H/L section per spec §4.2.4."""
    content = (
        "=== Price Pivots (BTC/USDT:USDT, main TF: 5m) ===\n"
        "Current Price: 75,212.00\n"
        "\n"
        "=== Levels Above Current Price ===\n"
        "Swing High: 75,820.00 (+0.81%, 12 bars ago)\n"
        "Prior Daily H: 76,400.00 (+1.58%)\n"
        "\n"
        "=== Levels Below Current Price ===\n"
        "Swing Low: 74,680.00 (-0.71%, 8 bars ago)\n"
        "Prior Daily L: 74,200.00 (-1.35%)\n"
        "\n"
        "=== Prior Period H/L ===\n"
        "Prior Weekly H/L: temporarily unavailable\n"
        "Prior Monthly H/L: temporarily unavailable"
    )
    expected = (
        "  ⚙ get_price_pivots\n"
        "    === Price Pivots (BTC/USDT:USDT, main TF: 5m) ===\n"
        "    Current Price: 75,212.00\n"
        "\n"
        "    === Levels Above Current Price ===\n"
        "    Swing High: 75,820.00 (+0.81%, 12 bars ago)\n"
        "    Prior Daily H: 76,400.00 (+1.58%)\n"
        "\n"
        "    === Levels Below Current Price ===\n"
        "    Swing Low: 74,680.00 (-0.71%, 8 bars ago)\n"
        "    Prior Daily L: 74,200.00 (-1.35%)\n"
        "\n"
        "    === Prior Period H/L ===\n"
        "    Prior Weekly H/L: temporarily unavailable\n"
        "    Prior Monthly H/L: temporarily unavailable"
    )
    _assert_perception_render("get_price_pivots", content, expected)


def test_snapshot_get_recent_trades_happy_path():
    """Snapshot — get_recent_trades single-section bucket+total layout."""
    content = (
        "=== Recent Trades (BTC/USDT:USDT, last 300s, 5 × 60s buckets) ===\n"
        "  t-5min  buy 1.2300 / sell 0.4500  (net +0.7800)\n"
        "  t-4min  buy 0.8000 / sell 1.1000  (net -0.3000)\n"
        "  t-3min  buy 0.5500 / sell 0.6500  (net -0.1000)\n"
        "  t-2min  buy 0.7800 / sell 0.7900  (net -0.0100)\n"
        "  t-1min  buy 1.4500 / sell 0.6200  (net +0.8300)\n"
        "Total: buy 4.8100 / sell 3.6100 (net +1.2000, 57% taker buy)\n"
        "Trade count: 100 | Avg size: 0.0842 BTC"
    )
    # Body has 7 rows (< 10 clip threshold) → keep all rows verbatim.
    expected = (
        "  ⚙ get_recent_trades\n"
        "    === Recent Trades (BTC/USDT:USDT, last 300s, 5 × 60s buckets) ===\n"
        "      t-5min  buy 1.2300 / sell 0.4500  (net +0.7800)\n"
        "      t-4min  buy 0.8000 / sell 1.1000  (net -0.3000)\n"
        "      t-3min  buy 0.5500 / sell 0.6500  (net -0.1000)\n"
        "      t-2min  buy 0.7800 / sell 0.7900  (net -0.0100)\n"
        "      t-1min  buy 1.4500 / sell 0.6200  (net +0.8300)\n"
        "    Total: buy 4.8100 / sell 3.6100 (net +1.2000, 57% taker buy)\n"
        "    Trade count: 100 | Avg size: 0.0842 BTC"
    )
    _assert_perception_render("get_recent_trades", content, expected)


def test_snapshot_get_recent_trades_no_trades():
    """Snapshot — get_recent_trades L3 empty-state (single-section, NO Error: prefix)."""
    content = (
        "=== Recent Trades (BTC/USDT:USDT, last 300s) ===\n"
        "No trades in last 300s."
    )
    expected = (
        "  ⚙ get_recent_trades\n"
        "    === Recent Trades (BTC/USDT:USDT, last 300s) ===\n"
        "    No trades in last 300s."
    )
    _assert_perception_render("get_recent_trades", content, expected)


def test_snapshot_get_recent_trades_unavailable():
    """Snapshot — get_recent_trades L2 inline Error fallback (service exception, Option D)."""
    content = (
        "=== Recent Trades (BTC/USDT:USDT) ===\n"
        "Error: Temporarily unavailable."
    )
    expected = (
        "  ⚙ get_recent_trades\n"
        "    === Recent Trades (BTC/USDT:USDT) ===\n"
        "    Error: Temporarily unavailable."
    )
    _assert_perception_render("get_recent_trades", content, expected)


def test_snapshot_get_derivatives_data_happy_path():
    """Snapshot — get_derivatives_data single-section happy path (per §4.2.10)."""
    content = (
        "=== Derivatives Data (BTC/USDT:USDT) ===\n"
        "Funding Rate: +0.0125% (next settlement in 3h 42m)\n"
        "  Positive rate — longs pay shorts\n"
        "Open Interest: $4.82B\n"
        "Long/Short Ratio: 1.35 (57.4% long / 42.6% short)\n"
        "Data as of: 2026-04-16 14:30 UTC"
    )
    expected = (
        "  ⚙ get_derivatives_data\n"
        "    === Derivatives Data (BTC/USDT:USDT) ===\n"
        "    Funding Rate: +0.0125% (next settlement in 3h 42m)\n"
        "      Positive rate — longs pay shorts\n"
        "    Open Interest: $4.82B\n"
        "    Long/Short Ratio: 1.35 (57.4% long / 42.6% short)\n"
        "    Data as of: 2026-04-16 14:30 UTC"
    )
    _assert_perception_render("get_derivatives_data", content, expected)


def test_snapshot_get_derivatives_data_partial_failure():
    """Snapshot — get_derivatives_data per-field L3 fallback (1 ok, 2 fail)."""
    content = (
        "=== Derivatives Data (BTC/USDT:USDT) ===\n"
        "Funding Rate: (unavailable)\n"
        "Open Interest: $1.00B\n"
        "Long/Short Ratio: (unavailable)"
    )
    expected = (
        "  ⚙ get_derivatives_data\n"
        "    === Derivatives Data (BTC/USDT:USDT) ===\n"
        "    Funding Rate: (unavailable)\n"
        "    Open Interest: $1.00B\n"
        "    Long/Short Ratio: (unavailable)"
    )
    _assert_perception_render("get_derivatives_data", content, expected)


def test_snapshot_get_derivatives_data_all_failed():
    """Snapshot — get_derivatives_data L2 all-3-failed inline Error fallback (Option D)."""
    content = (
        "=== Derivatives Data (BTC/USDT:USDT) ===\n"
        "Error: Temporarily unavailable (all 3 data sources failed)."
    )
    expected = (
        "  ⚙ get_derivatives_data\n"
        "    === Derivatives Data (BTC/USDT:USDT) ===\n"
        "    Error: Temporarily unavailable (all 3 data sources failed)."
    )
    _assert_perception_render("get_derivatives_data", content, expected)


# --- Batch B: mid-frequency + implicit→explicit snapshots ---


def test_snapshot_get_account_balance_happy_path():
    """Snapshot — get_account_balance single-section render (R2-8c §4.2.12)."""
    content = (
        "=== Account Balance ===\n"
        "Total: 998.00 USDT (initial: 1000.00)\n"
        "Return: -0.20% (-2.00 USDT) (incl. unrealized)\n"
        "Free: 800.00 USDT\n"
        "Used: 198.00 USDT"
    )
    expected = (
        "  ⚙ get_account_balance\n"
        "    === Account Balance ===\n"
        "    Total: 998.00 USDT (initial: 1000.00)\n"
        "    Return: -0.20% (-2.00 USDT) (incl. unrealized)\n"
        "    Free: 800.00 USDT\n"
        "    Used: 198.00 USDT"
    )
    _assert_perception_render("get_account_balance", content, expected)


def test_snapshot_get_open_orders_empty():
    """Snapshot — get_open_orders no-orders empty-state, sectioned (§4.2.14)."""
    content = "=== Pending Orders ===\nNo pending orders."
    expected = (
        "  ⚙ get_open_orders\n"
        "    === Pending Orders ===\n"
        "    No pending orders."
    )
    _assert_perception_render("get_open_orders", content, expected)


def test_snapshot_get_open_orders_with_orders():
    """Snapshot — pending orders 1 OCO leg + 1 limit (§4.2.14)."""
    content = (
        "=== Pending Orders ===\n"
        "  [OCO] sell 0.025 stop 74000.00 (-1.60% from current) / "
        "tp 76500.00 (+1.73% from current) | algoId: oco-1 (cancel removes both legs)\n"
        "  [LIMIT] buy 0.025 @ 74500.00 (-0.93% from current) | ID: lim-1"
    )
    expected = (
        "  ⚙ get_open_orders\n"
        "    === Pending Orders ===\n"
        "      [OCO] sell 0.025 stop 74000.00 (-1.60% from current) / "
        "tp 76500.00 (+1.73% from current) | algoId: oco-1 (cancel removes both legs)\n"
        "      [LIMIT] buy 0.025 @ 74500.00 (-0.93% from current) | ID: lim-1"
    )
    _assert_perception_render("get_open_orders", content, expected)


def test_snapshot_get_position_no_position():
    """Snapshot — get_position no open positions empty-state, sectioned (§4.2.11)."""
    content = "=== Position ===\nNo open positions."
    expected = (
        "  ⚙ get_position\n"
        "    === Position ===\n"
        "    No open positions."
    )
    _assert_perception_render("get_position", content, expected)


def test_snapshot_get_position_with_stats():
    """Snapshot — get_position 4 sections (Position / PnL / Risk Exposure / Exit Orders)."""
    content = (
        "=== Position (BTC/USDT:USDT) ===\n"
        "Side: Long | Contracts: 0.025 | Entry: 78,518.00\n"
        "Leverage: 5x\n"
        "Liquidation: 70,666.00\n"
        "Unrealized: +0.20 USDT\n"
        "\n"
        "=== PnL ===\n"
        "PnL: +0.20 USDT (+0.02% of initial capital)\n"
        "Duration: 2h 30m\n"
        "\n"
        "=== Risk Exposure ===\n"
        "Notional value: 1962.95 USDT (4.2% of equity 998.00)\n"
        "Margin used: 392.59 USDT (39.3% of equity, from balance.used_usdt)\n"
        "Liquidation: 70666.00 (10.0% away = 5.8× ATR(1h))\n"
        "\n"
        "=== Exit Orders ===\n"
        "  Stop loss: not set\n"
        "  Take profit: not set"
    )
    expected = (
        "  ⚙ get_position\n"
        "    === Position (BTC/USDT:USDT) ===\n"
        "    Side: Long | Contracts: 0.025 | Entry: 78,518.00\n"
        "    Leverage: 5x\n"
        "    Liquidation: 70,666.00\n"
        "    Unrealized: +0.20 USDT\n"
        "\n"
        "    === PnL ===\n"
        "    PnL: +0.20 USDT (+0.02% of initial capital)\n"
        "    Duration: 2h 30m\n"
        "\n"
        "    === Risk Exposure ===\n"
        "    Notional value: 1962.95 USDT (4.2% of equity 998.00)\n"
        "    Margin used: 392.59 USDT (39.3% of equity, from balance.used_usdt)\n"
        "    Liquidation: 70666.00 (10.0% away = 5.8× ATR(1h))\n"
        "\n"
        "    === Exit Orders ===\n"
        "      Stop loss: not set\n"
        "      Take profit: not set"
    )
    _assert_perception_render("get_position", content, expected)


def test_snapshot_get_position_hard_failure_degradation():
    """Snapshot — get_position hard-failure: Position + PnL preserved,
    Risk Exposure + Exit Orders degraded to (unavailable) bodies (§4.2.11)."""
    content = (
        "=== Position (BTC/USDT:USDT) ===\n"
        "Side: Long | Contracts: 0.025 | Entry: 78,518.00\n"
        "Leverage: 5x\n"
        "Liquidation: 70,666.00\n"
        "Unrealized: +0.20 USDT\n"
        "\n"
        "=== PnL ===\n"
        "PnL: +0.20 USDT (+0.02% of initial capital)\n"
        "Duration: 2h 30m\n"
        "\n"
        "=== Risk Exposure ===\n"
        "(unavailable)\n"
        "\n"
        "=== Exit Orders ===\n"
        "(unavailable)"
    )
    expected = (
        "  ⚙ get_position\n"
        "    === Position (BTC/USDT:USDT) ===\n"
        "    Side: Long | Contracts: 0.025 | Entry: 78,518.00\n"
        "    Leverage: 5x\n"
        "    Liquidation: 70,666.00\n"
        "    Unrealized: +0.20 USDT\n"
        "\n"
        "    === PnL ===\n"
        "    PnL: +0.20 USDT (+0.02% of initial capital)\n"
        "    Duration: 2h 30m\n"
        "\n"
        "    === Risk Exposure ===\n"
        "    (unavailable)\n"
        "\n"
        "    === Exit Orders ===\n"
        "    (unavailable)"
    )
    _assert_perception_render("get_position", content, expected)


def test_snapshot_get_market_news_l2_not_configured():
    """Snapshot — news service=None L2 inline Error fallback under === News === (Option D)."""
    content = (
        "=== News ===\n"
        "Error: News service not configured."
    )
    expected = (
        "  ⚙ get_market_news\n"
        "    === News ===\n"
        "    Error: News service not configured."
    )
    _assert_perception_render("get_market_news", content, expected)


def test_snapshot_get_market_news_happy_short():
    """Snapshot — news happy path with FGI + 2 symbol headlines (body < 10, keep all)."""
    content = (
        "=== Fear & Greed Index ===\n"
        "Value: Fear (35)\n"
        "(Updated: 2026-05-03)\n"
        "\n"
        "=== Symbol News (BTC, 2) ===\n"
        "[2026-05-03 14:00] BTC tests $75k support\n"
        "  Source: CoinDesk | Currencies: BTC\n"
        "[2026-05-03 13:30] Funding rates flip negative\n"
        "  Source: The Block | Currencies: BTC, ETH"
    )
    expected = (
        "  ⚙ get_market_news\n"
        "    === Fear & Greed Index ===\n"
        "    Value: Fear (35)\n"
        "    (Updated: 2026-05-03)\n"
        "\n"
        "    === Symbol News (BTC, 2) ===\n"
        "    [2026-05-03 14:00] BTC tests $75k support\n"
        "      Source: CoinDesk | Currencies: BTC\n"
        "    [2026-05-03 13:30] Funding rates flip negative\n"
        "      Source: The Block | Currencies: BTC, ETH"
    )
    _assert_perception_render("get_market_news", content, expected)


def test_snapshot_get_market_news_dense_general_news_clipped():
    """Snapshot — General Crypto News with 12 entries (each 2 lines = 24 body lines)
    triggers head=2/tail=2 clipping. Multi-entry boundary trade-off (spec §4.3.2)
    acknowledged: head/tail may split entries — trader sees first 2 + last 2 lines.
    """
    entries = []
    for i in range(12):
        entries.append(f"[2026-05-03 1{i:02d}:00] Headline {i}\n  Source: src{i} | Currencies: ALT{i}")
    content = "=== General Crypto News (12) ===\n" + "\n".join(entries)
    # Body: 12 × 2 = 24 lines, ≥ 10 → head=2 + omitted + tail=2
    from src.cli.display import _render_perception_tool
    out = _render_perception_tool("get_market_news", content)
    assert "    === General Crypto News (12) ===" in out
    assert "    [2026-05-03 100:00] Headline 0" in out  # head[0]
    assert "      Source: src0 | Currencies: ALT0" in out  # head[1]
    assert "    [... 20 rows omitted ...]" in out
    # Last 2 lines of body — entry 11's two lines
    assert "    [2026-05-03 111:00] Headline 11" in out  # tail[-2]
    assert "      Source: src11 | Currencies: ALT11" in out  # tail[-1]


def test_snapshot_get_order_book_happy_path():
    """Snapshot — order book 2 sub-sections (Order Book + Depth) without concentrated."""
    content = (
        "=== Order Book (BTC/USDT:USDT) ===\n"
        "Best bid: 75200.00 × 0.5000 BTC  |  Best ask: 75205.00 × 0.4500 BTC\n"
        "Spread: 5.00 (0.007%)\n"
        "\n"
        "=== Depth (top 20 each side) ===\n"
        "  Bids cumulative: 5.4500 BTC over 75200.00 - 75150.00 (0.07% deep)\n"
        "  Asks cumulative: 6.2000 BTC over 75205.00 - 75260.00 (0.07% deep)\n"
        "  Bid share: ~50% (balanced)"
    )
    expected = (
        "  ⚙ get_order_book\n"
        "    === Order Book (BTC/USDT:USDT) ===\n"
        "    Best bid: 75200.00 × 0.5000 BTC  |  Best ask: 75205.00 × 0.4500 BTC\n"
        "    Spread: 5.00 (0.007%)\n"
        "\n"
        "    === Depth (top 20 each side) ===\n"
        "      Bids cumulative: 5.4500 BTC over 75200.00 - 75150.00 (0.07% deep)\n"
        "      Asks cumulative: 6.2000 BTC over 75205.00 - 75260.00 (0.07% deep)\n"
        "      Bid share: ~50% (balanced)"
    )
    _assert_perception_render("get_order_book", content, expected)


def test_snapshot_get_order_book_l2_unavailable():
    """Snapshot — order book L2 (service exception) inline Error fallback (Option D)."""
    content = (
        "=== Order Book (BTC/USDT:USDT) ===\n"
        "Error: Temporarily unavailable."
    )
    expected = (
        "  ⚙ get_order_book\n"
        "    === Order Book (BTC/USDT:USDT) ===\n"
        "    Error: Temporarily unavailable."
    )
    _assert_perception_render("get_order_book", content, expected)


def test_snapshot_get_active_alerts_with_alerts():
    """Snapshot — active alerts vol param + 2 price level alerts (§4.1.1 verified-no-change)."""
    content = (
        "=== Price Alert Settings ===\n"
        "Volatility alert: 1.5% in 10min window\n"
        "\n"
        "=== Active Price Level Alerts (2/20) ===\n"
        '  #1 (id=alert-1) above 76500.00 — "tactical resistance"\n'
        '  #2 (id=alert-2) below 74000.00 — "support break"'
    )
    expected = (
        "  ⚙ get_active_alerts\n"
        "    === Price Alert Settings ===\n"
        "    Volatility alert: 1.5% in 10min window\n"
        "\n"
        "    === Active Price Level Alerts (2/20) ===\n"
        '      #1 (id=alert-1) above 76500.00 — "tactical resistance"\n'
        '      #2 (id=alert-2) below 74000.00 — "support break"'
    )
    _assert_perception_render("get_active_alerts", content, expected)


# --- Batch C: long-tail snapshots (T7) ---


def test_snapshot_get_macro_context_l2_not_configured():
    """Snapshot — macro service=None L2 inline Error fallback (Option D)."""
    content = (
        "=== Macro Context ===\n"
        "Error: Macro service not configured."
    )
    expected = (
        "  ⚙ get_macro_context\n"
        "    === Macro Context ===\n"
        "    Error: Macro service not configured."
    )
    _assert_perception_render("get_macro_context", content, expected)


def test_snapshot_get_macro_context_happy_3_sections():
    """Snapshot — macro_context happy path 3 sections (Crypto Market + FRED + AV)."""
    content = (
        "=== Crypto Market ===\n"
        "BTC.D: 56.10% | ETH.D: 13.40% | Total Mcap: $2.45T (24h: +0.85%)\n"
        "\n"
        "=== US Macro (FRED) ===\n"
        "USD Index (Broad TW): 128.55 (as of 2026-04-25)\n"
        "VIX: 17.94 (as of 2026-04-30)\n"
        "10Y Treasury: 4.21% (as of 2026-04-30)\n"
        "2s10s Spread: +0.45% (as of 2026-04-30)\n"
        "10Y Inflation Expectation: 2.43% (as of 2026-04-30)\n"
        "\n"
        "=== US Equities (Alpha Vantage) ===\n"
        "SPY: $710.14 (+0.32%, as of 2026-04-30)\n"
        "QQQ: $648.85 (+0.55%, as of 2026-04-30)"
    )
    expected = (
        "  ⚙ get_macro_context\n"
        "    === Crypto Market ===\n"
        "    BTC.D: 56.10% | ETH.D: 13.40% | Total Mcap: $2.45T (24h: +0.85%)\n"
        "\n"
        "    === US Macro (FRED) ===\n"
        "    USD Index (Broad TW): 128.55 (as of 2026-04-25)\n"
        "    VIX: 17.94 (as of 2026-04-30)\n"
        "    10Y Treasury: 4.21% (as of 2026-04-30)\n"
        "    2s10s Spread: +0.45% (as of 2026-04-30)\n"
        "    10Y Inflation Expectation: 2.43% (as of 2026-04-30)\n"
        "\n"
        "    === US Equities (Alpha Vantage) ===\n"
        "    SPY: $710.14 (+0.32%, as of 2026-04-30)\n"
        "    QQQ: $648.85 (+0.55%, as of 2026-04-30)"
    )
    _assert_perception_render("get_macro_context", content, expected)


def test_snapshot_get_macro_context_partial_l3_fallback():
    """Snapshot — get_macro_context partial L3 (FRED ok, Crypto + Equities unavailable).

    Per spec §4.1.4 L3: per-source partial failure renders inline `Temporarily
    unavailable.` short-description in the affected section without `Error:` prefix
    (L2 error prefix reserved for whole-tool fallback).
    """
    content = (
        "=== Crypto Market ===\n"
        "Temporarily unavailable.\n"
        "\n"
        "=== US Macro (FRED) ===\n"
        "USD Index (Broad TW): 105.20 (as of 2026-04-30)\n"
        "VIX: 16.50 (as of 2026-05-02)\n"
        "10Y Treasury: 4.25% (as of 2026-05-02)\n"
        "\n"
        "=== US Equities (Alpha Vantage) ===\n"
        "Temporarily unavailable."
    )
    expected = (
        "  ⚙ get_macro_context\n"
        "    === Crypto Market ===\n"
        "    Temporarily unavailable.\n"
        "\n"
        "    === US Macro (FRED) ===\n"
        "    USD Index (Broad TW): 105.20 (as of 2026-04-30)\n"
        "    VIX: 16.50 (as of 2026-05-02)\n"
        "    10Y Treasury: 4.25% (as of 2026-05-02)\n"
        "\n"
        "    === US Equities (Alpha Vantage) ===\n"
        "    Temporarily unavailable."
    )
    _assert_perception_render("get_macro_context", content, expected)


def test_snapshot_get_macro_calendar_l2_not_configured():
    """Snapshot — macro_calendar news service=None L2 inline Error fallback (Option D)."""
    content = (
        "=== Upcoming Macro Events ===\n"
        "Error: News service not configured."
    )
    expected = (
        "  ⚙ get_macro_calendar\n"
        "    === Upcoming Macro Events ===\n"
        "    Error: News service not configured."
    )
    _assert_perception_render("get_macro_calendar", content, expected)


def test_snapshot_get_macro_calendar_happy_with_note():
    """Snapshot — macro_calendar happy path: 1 event + === Note === footer."""
    content = (
        "=== Upcoming Macro Events (next 12h) ===\n"
        "[2026-05-03 12:59] FOMC Meeting — Impact: High\n"
        "  Previous: N/A | Forecast: N/A\n"
        "\n"
        "=== Note ===\n"
        "Macro calendar covers current week only; "
        "Friday evening / weekend calls may miss next week's early events."
    )
    expected = (
        "  ⚙ get_macro_calendar\n"
        "    === Upcoming Macro Events (next 12h) ===\n"
        "    [2026-05-03 12:59] FOMC Meeting — Impact: High\n"
        "      Previous: N/A | Forecast: N/A\n"
        "\n"
        "    === Note ===\n"
        "    Macro calendar covers current week only; "
        "Friday evening / weekend calls may miss next week's early events."
    )
    _assert_perception_render("get_macro_calendar", content, expected)


def test_snapshot_get_macro_calendar_no_events_with_note():
    """Snapshot — macro_calendar empty events list + === Note === footer."""
    content = (
        "=== Upcoming Macro Events (next 12h) ===\n"
        "No upcoming macro events.\n"
        "\n"
        "=== Note ===\n"
        "Macro calendar covers current week only; "
        "Friday evening / weekend calls may miss next week's early events."
    )
    expected = (
        "  ⚙ get_macro_calendar\n"
        "    === Upcoming Macro Events (next 12h) ===\n"
        "    No upcoming macro events.\n"
        "\n"
        "    === Note ===\n"
        "    Macro calendar covers current week only; "
        "Friday evening / weekend calls may miss next week's early events."
    )
    _assert_perception_render("get_macro_calendar", content, expected)


def test_snapshot_get_etf_flows_l2_not_configured():
    """Snapshot — etf_flows service=None L2 inline Error fallback (Option D)."""
    content = (
        "=== BTC Spot ETF Flows (US) ===\n"
        "Error: ETF flows service not configured."
    )
    expected = (
        "  ⚙ get_etf_flows\n"
        "    === BTC Spot ETF Flows (US) ===\n"
        "    Error: ETF flows service not configured."
    )
    _assert_perception_render("get_etf_flows", content, expected)


def test_snapshot_get_etf_flows_happy_with_note():
    """Snapshot — etf_flows happy path: BTC + ETH sections + === Note === footer."""
    content = (
        "=== BTC Spot ETF Flows (US) ===\n"
        "2026-04-17: +$100.00M  (cum: $57.70B, AUM: $100.00B)\n"
        "2026-04-16: -$200.00M\n"
        "2026-04-15: +$150.00M\n"
        "3-day net: +$50.00M\n"
        "\n"
        "=== ETH Spot ETF Flows (US) ===\n"
        "2026-04-17: +$25.00M  (cum: $9.80B, AUM: $12.00B)\n"
        "2026-04-16: +$10.00M\n"
        "2026-04-15: -$5.00M\n"
        "3-day net: +$30.00M\n"
        "\n"
        "=== Note ===\n"
        "Past 3 trading days (weekends/holidays excluded). "
        "Issuer-reported; today's value may be revised T+1."
    )
    expected = (
        "  ⚙ get_etf_flows\n"
        "    === BTC Spot ETF Flows (US) ===\n"
        "    2026-04-17: +$100.00M  (cum: $57.70B, AUM: $100.00B)\n"
        "    2026-04-16: -$200.00M\n"
        "    2026-04-15: +$150.00M\n"
        "    3-day net: +$50.00M\n"
        "\n"
        "    === ETH Spot ETF Flows (US) ===\n"
        "    2026-04-17: +$25.00M  (cum: $9.80B, AUM: $12.00B)\n"
        "    2026-04-16: +$10.00M\n"
        "    2026-04-15: -$5.00M\n"
        "    3-day net: +$30.00M\n"
        "\n"
        "    === Note ===\n"
        "    Past 3 trading days (weekends/holidays excluded). "
        "Issuer-reported; today's value may be revised T+1."
    )
    _assert_perception_render("get_etf_flows", content, expected)


def test_snapshot_get_stablecoin_supply_l2_not_configured():
    """Snapshot — stablecoin onchain service=None L2 inline Error fallback (Option D)."""
    content = (
        "=== Stablecoin Supply ===\n"
        "Error: Onchain service not configured."
    )
    expected = (
        "  ⚙ get_stablecoin_supply\n"
        "    === Stablecoin Supply ===\n"
        "    Error: Onchain service not configured."
    )
    _assert_perception_render("get_stablecoin_supply", content, expected)


def test_snapshot_get_stablecoin_supply_happy_path():
    """Snapshot — stablecoin happy path: USDT + USDC + total Mcap (single section)."""
    content = (
        "=== Stablecoin Supply ===\n"
        "USDT: $186.62B (7d: +$2.33B, +1.27%)\n"
        "USDC: $42.18B (7d: +$0.51B, +1.22%)\n"
        "Total Stablecoin Mcap: $228.80B (7d: +$2.84B, +1.26%)"
    )
    expected = (
        "  ⚙ get_stablecoin_supply\n"
        "    === Stablecoin Supply ===\n"
        "    USDT: $186.62B (7d: +$2.33B, +1.27%)\n"
        "    USDC: $42.18B (7d: +$0.51B, +1.22%)\n"
        "    Total Stablecoin Mcap: $228.80B (7d: +$2.84B, +1.26%)"
    )
    _assert_perception_render("get_stablecoin_supply", content, expected)


def test_snapshot_get_exchange_announcements_l2_not_configured():
    """Snapshot — exchange_announcements service=None L2 inline Error fallback (Option D)."""
    content = (
        "=== Exchange Announcements ===\n"
        "Error: News service not configured."
    )
    expected = (
        "  ⚙ get_exchange_announcements\n"
        "    === Exchange Announcements ===\n"
        "    Error: News service not configured."
    )
    _assert_perception_render("get_exchange_announcements", content, expected)


def test_snapshot_get_exchange_announcements_happy_short():
    """Snapshot — exchange_announcements happy path with 2 announcements."""
    content = (
        "=== Exchange Announcements (past 24h) ===\n"
        "[2026-05-03 12:00] Delisting XYZ\n"
        "[2026-05-03 09:30] Maintenance scheduled for spot trading"
    )
    expected = (
        "  ⚙ get_exchange_announcements\n"
        "    === Exchange Announcements (past 24h) ===\n"
        "    [2026-05-03 12:00] Delisting XYZ\n"
        "    [2026-05-03 09:30] Maintenance scheduled for spot trading"
    )
    _assert_perception_render("get_exchange_announcements", content, expected)


def test_snapshot_get_trade_journal_with_entries():
    """Snapshot — trade_journal happy path: Performance Summary + Trade Journal."""
    content = (
        "=== Performance Summary ===\n"
        "Total Trades: 2 | Win: 1 (50.0%) | Loss: 1\n"
        "Avg Win: +30.00 USDT | Avg Loss: -10.00 USDT\n"
        "Profit Factor: 3.00\n"
        "\n"
        "=== Trade Journal ===\n"
        "[05-01 09:30] open_position (long)\n"
        "  Reasoning: RSI oversold\n"
        "[05-01 11:45] order_filled (long) @ 60200.00, fee=0.0300 [closed], pnl=30.00\n"
        "  Reasoning: market exit"
    )
    # Rich markup escape: '[closed]' → '\[closed]' (display escape per §4.3.3).
    expected = (
        "  ⚙ get_trade_journal\n"
        "    === Performance Summary ===\n"
        "    Total Trades: 2 | Win: 1 (50.0%) | Loss: 1\n"
        "    Avg Win: +30.00 USDT | Avg Loss: -10.00 USDT\n"
        "    Profit Factor: 3.00\n"
        "\n"
        "    === Trade Journal ===\n"
        "    [05-01 09:30] open_position (long)\n"
        "      Reasoning: RSI oversold\n"
        "    [05-01 11:45] order_filled (long) @ 60200.00, fee=0.0300 \\[closed], pnl=30.00\n"
        "      Reasoning: market exit"
    )
    _assert_perception_render("get_trade_journal", content, expected)


def test_snapshot_get_trade_journal_no_db_engine():
    """Snapshot — trade_journal db_engine=None: sectioned empty-state (L3, NOT Error)."""
    content = (
        "=== Trade Journal ===\n"
        "No trade journal entries yet."
    )
    expected = (
        "  ⚙ get_trade_journal\n"
        "    === Trade Journal ===\n"
        "    No trade journal entries yet."
    )
    _assert_perception_render("get_trade_journal", content, expected)


def test_snapshot_get_trade_journal_no_actions():
    """Snapshot — trade_journal no actions returned: same sectioned empty-state."""
    content = (
        "=== Trade Journal ===\n"
        "No trade journal entries yet."
    )
    expected = (
        "  ⚙ get_trade_journal\n"
        "    === Trade Journal ===\n"
        "    No trade journal entries yet."
    )
    _assert_perception_render("get_trade_journal", content, expected)


def test_snapshot_get_performance_no_metrics_service():
    """Snapshot — performance metrics=None split: Trading Performance + empty Stats."""
    content = (
        "=== Trading Performance ===\n"
        "Initial Balance: 10000.00 USDT\n"
        "Current Balance: 10000.00 USDT\n"
        "Return: +0.00% (+0.00 USDT)\n"
        "\n"
        "=== Trade Stats ===\n"
        "No metrics service available."
    )
    expected = (
        "  ⚙ get_performance\n"
        "    === Trading Performance ===\n"
        "    Initial Balance: 10000.00 USDT\n"
        "    Current Balance: 10000.00 USDT\n"
        "    Return: +0.00% (+0.00 USDT)\n"
        "\n"
        "    === Trade Stats ===\n"
        "    No metrics service available."
    )
    _assert_perception_render("get_performance", content, expected)


def test_snapshot_get_performance_happy_path():
    """Snapshot — performance happy path with both Trading Performance + Trade Stats sections."""
    content = (
        "=== Trading Performance ===\n"
        "Initial Balance: 10000.00 USDT\n"
        "Current Balance: 10023.00 USDT\n"
        "Total Return: +0.23% (+23.00 USDT) (incl. unrealized)\n"
        "Realized PnL: +23.00 USDT (gross, before fees)\n"
        "Total Fees: -0.80 USDT\n"
        "\n"
        "=== Trade Stats ===\n"
        "Total Trades: 5 | Win: 3 (60.0%) | Loss: 2\n"
        "Avg Win: +20.00 USDT | Avg Loss: -10.00 USDT\n"
        "Profit Factor: 3.00\n"
        "Max Drawdown: -2.5%\n"
        "Best Trade: +50.00 USDT | Worst Trade: -15.00 USDT"
    )
    expected = (
        "  ⚙ get_performance\n"
        "    === Trading Performance ===\n"
        "    Initial Balance: 10000.00 USDT\n"
        "    Current Balance: 10023.00 USDT\n"
        "    Total Return: +0.23% (+23.00 USDT) (incl. unrealized)\n"
        "    Realized PnL: +23.00 USDT (gross, before fees)\n"
        "    Total Fees: -0.80 USDT\n"
        "\n"
        "    === Trade Stats ===\n"
        "    Total Trades: 5 | Win: 3 (60.0%) | Loss: 2\n"
        "    Avg Win: +20.00 USDT | Avg Loss: -10.00 USDT\n"
        "    Profit Factor: 3.00\n"
        "    Max Drawdown: -2.5%\n"
        "    Best Trade: +50.00 USDT | Worst Trade: -15.00 USDT"
    )
    _assert_perception_render("get_performance", content, expected)


# === R2-8c edge cases (T-EC) ===


def test_ec_1_no_section_header_fallback():
    """T-EC-1: tool 输出无 === Section === → unnamed section render (legacy / get_memories)."""
    from src.cli.display import _render_perception_tool
    out = _render_perception_tool("get_memories", "Memory entry 1\nMemory entry 2")
    assert "  ⚙ get_memories" in out
    assert "    Memory entry 1" in out


def test_ec_2_l1_failure_single_line_x_icon():
    """T-EC-2: outcome != success → R2-8a single-line ✗ icon (不进 multi-line)."""
    from pydantic_ai.messages import ToolCallPart, ToolReturnPart
    from src.cli.display import _render_action

    calls = [ToolCallPart(tool_name="get_market_data", args={}, tool_call_id="c1")]
    returns = {
        "c1": ToolReturnPart(
            tool_name="get_market_data", tool_call_id="c1",
            content="ConnectionError: upstream timeout",
            outcome="error",  # outcome != success → L1
        ),
    }
    out = _render_action(calls, returns, cycle_id="abcd1234")
    assert "  ✗ get_market_data" in out
    # Multi-line markers should NOT appear (L1 不进 multi-line)
    assert "    ===" not in out  # no section header indent line


def test_ec_3_l2_error_inline_in_multi_line():
    """T-EC-3: tool 内捕获异常 + success outcome 返回 Option D 'Error:' inline → 进 multi-line, body 显示。"""
    from src.cli.display import _render_perception_tool
    content = (
        "=== Higher Timeframe View (BTC/USDT:USDT, 4h) ===\n"
        "Error: Temporarily unavailable."
    )
    out = _render_perception_tool("get_higher_timeframe_view", content)
    assert "    === Higher Timeframe View (BTC/USDT:USDT, 4h) ===" in out
    assert "    Error: Temporarily unavailable." in out


def test_ec_4_section_body_one_line():
    """T-EC-4: section body 仅 1 行 → keep all (< 10)."""
    from src.cli.display import _render_perception_tool
    content = "=== Account Balance ===\nTotal: 998.00 USDT"
    out = _render_perception_tool("get_account_balance", content)
    assert out == (
        "  ⚙ get_account_balance\n"
        "    === Account Balance ===\n"
        "    Total: 998.00 USDT"
    )


def test_ec_5_section_body_zero_lines_render_header_only():
    """T-EC-5: section body 0 行 → header alone."""
    from src.cli.display import _render_perception_tool
    content = "=== Empty Section ===\n"
    out = _render_perception_tool("get_market_data", content)
    assert out == (
        "  ⚙ get_market_data\n"
        "    === Empty Section ==="
    )


def test_ec_6_section_header_markup_literal_escaped():
    """T-EC-6: section header 含 markup 字面值 → escape 为 \\[red]Critical[/]."""
    from src.cli.display import _render_perception_tool
    content = "=== [red]Critical[/] ===\nbody"
    out = _render_perception_tool("get_market_news", content)
    assert r"\[red]" in out  # rich.markup.escape 转 \[red]


def test_ec_7_section_body_markup_literal_escaped():
    """T-EC-7: section body 含 markup 字面值（如新闻 [bold]）→ escape."""
    from src.cli.display import _render_perception_tool
    content = "=== Symbol News ===\nHeadline: [bold]BREAKING[/] something"
    out = _render_perception_tool("get_market_news", content)
    assert r"\[bold]" in out


def test_ec_8_long_url_line_no_wrapping_in_helper():
    """T-EC-8: 极长单行（如 URL ≥ terminal width）— helper 不主动 wrap, 由 Rich render 处理."""
    from src.cli.display import _render_perception_tool
    long_url = "https://example.com/" + "x" * 200
    content = f"=== Symbol News ===\n{long_url}"
    out = _render_perception_tool("get_market_news", content)
    assert long_url in out


def test_ec_9_orphan_tool_call_id_no_return_captured():
    """T-EC-9: 无 tool_call_id 关联 → R2-8a [no return captured]."""
    from pydantic_ai.messages import ToolCallPart
    from src.cli.display import _render_action
    calls = [ToolCallPart(tool_name="get_market_data", args={}, tool_call_id="orphan")]
    out = _render_action(calls, returns_lookup={}, cycle_id="abcd1234")
    assert "[no return captured]" in out


# === R2-8c T-INT-2: failed tool ✗ icon regression guard ===


def test_int_2_failed_tool_x_icon_regression_guard():
    """T-INT-2: failed perception tool → R2-8a single-line ✗ icon, no multi-line break."""
    from pydantic_ai.messages import ToolCallPart, ToolReturnPart
    from src.cli.display import _render_action

    calls = [ToolCallPart(tool_name="get_market_data", args={}, tool_call_id="c1")]
    returns = {
        "c1": ToolReturnPart(
            tool_name="get_market_data", tool_call_id="c1",
            content="ConnectionError to upstream", outcome="error",
        ),
    }
    out = _render_action(calls, returns, cycle_id="abcd1234")
    assert "  ✗ get_market_data" in out
    assert "ConnectionError" in out  # fallback summary kept


# === R2-8c T-BE-1: byte-equal Section model invariant ===


def test_be_1_byte_equal_section_model_invariant():
    """T-BE-1: parsed_display Section model == [Section(escape(h), tuple(escape(l) for l in clip(body)))]
    per spec §4.6 P1.2 校准 (Section model 比较, 非 raw bytes)."""
    from src.cli.display import (
        Section,
        _parse_sections,
        _clip_body,
        _render_perception_tool,
    )
    from rich.markup import escape

    content = (
        "=== Sec A ===\n"
        + "\n".join(f"row {i}" for i in range(15))  # 15 rows → triggers clipping
        + "\n\n=== Sec B ===\nshort body"
    )

    # Re-parse the rendered output (strip 4-space indent + tool_name line)
    rendered = _render_perception_tool("get_market_data", content)
    rendered_lines = rendered.split("\n")
    assert rendered_lines[0] == "  ⚙ get_market_data"
    # Strip 4-space indent from all subsequent lines, then re-parse
    body_lines = [l[4:] if l.startswith("    ") else l for l in rendered_lines[1:]]
    rendered_content = "\n".join(body_lines)
    parsed_display = _parse_sections(rendered_content)

    expected = [
        Section(
            header=escape(s.header) if s.header else None,
            body=tuple(escape(line) for line in _clip_body(s.body)),
        )
        for s in _parse_sections(content)
    ]

    assert parsed_display == expected, (
        f"Byte-equal Section model mismatch:\n"
        f"--- expected ---\n{expected}\n"
        f"--- parsed_display ---\n{parsed_display}"
    )


# === R2-8c T-DG-1: sectioning convention lint (Path A + Path B real tool invocation) ===
#
# T-DG-1 必须真实 invoke 每个 perception tool（不能只 lint 静态 fixture）——参考既有
# tests/test_perception_tools_n3.py 的 MockDeps + AsyncMock 模式。
import re as _re_dg
from dataclasses import dataclass as _dataclass_dg
from unittest.mock import AsyncMock as _AsyncMock_dg, MagicMock as _MagicMock_dg

import pytest


# Reuse MockDeps shape from tests/test_perception_tools_n3.py — duplicated here
# rather than imported because per-test minimal customization differs (review F5 校准:
# inline copy keeps T-DG-1 isolated from cross-test-file fixture survival).
@_dataclass_dg
class _MockDeps:
    symbol: str = "BTC/USDT:USDT"
    timeframe: str = "5m"
    market_data: object = None
    exchange: object = None
    technical: object = None
    memory: object = None
    session_id: str = "test"
    db_engine: object = None
    initial_balance: float = 1000.0
    metrics: object = None
    news: object = None
    macro: object = None
    crypto_etf: object = None
    onchain: object = None
    approval_gate: object = None
    approval_enabled: bool = False
    wake_min_minutes: int = 1
    wake_max_minutes: int = 60
    set_next_wake_fn: object = None


def _mock_exchange_minimal(positions=None, balance_total=998.0,
                           open_orders=None, alert_params=None,
                           price_level_alerts=None):
    """Build a MagicMock+AsyncMock exchange covering the methods Path-A/B tools call."""
    exchange = _MagicMock_dg()
    exchange.fetch_positions = _AsyncMock_dg(return_value=positions or [])
    exchange.fetch_open_orders = _AsyncMock_dg(return_value=open_orders or [])
    balance = _MagicMock_dg()
    balance.total_usdt = balance_total
    balance.free_usdt = balance_total * 0.8
    balance.used_usdt = balance_total * 0.2
    exchange.fetch_balance = _AsyncMock_dg(return_value=balance)
    exchange.get_alert_params = _MagicMock_dg(return_value=alert_params)
    exchange.get_price_level_alerts = _MagicMock_dg(return_value=price_level_alerts or [])
    return exchange


# Path A — service=None / empty-state L2 path (no market_data/exchange mocks needed beyond minimal)
PATH_A_TOOLS = [
    "get_market_news", "get_exchange_announcements", "get_macro_calendar",
    "get_macro_context", "get_etf_flows", "get_stablecoin_supply",
    "get_trade_journal", "get_performance", "get_active_alerts",
    "get_position",
]


@pytest.mark.parametrize("tool_name", PATH_A_TOOLS)
async def test_dg_1_path_a_service_none_or_empty_state(tool_name):
    """T-DG-1 Path A: service=None / empty-state path → returns starts with `=== `.

    Tools rely on tool-internal early return when service dep is None or
    primary state is empty (no positions / no metrics service / etc.).
    After T5-T7 refactor these paths emit === Section === / Option D inline `Error:`.
    """
    import src.agent.tools_perception as tp
    fn = getattr(tp, tool_name)
    deps = _MockDeps(
        # Path-A tools that rely on exchange even when their own service=None
        exchange=_mock_exchange_minimal(),  # for get_active_alerts / get_position
    )
    out = await fn(deps)
    assert out.startswith("=== "), (
        f"{tool_name} (Path A) did not start with section header: {out[:120]!r}"
    )


# Path B — minimum mock for tools that must hit market_data / exchange happy path


def _make_ohlcv_df_local(n_rows: int, last_close: float = 75_234.50):
    """Local copy of tests/test_perception_tools_n3.py:_make_ohlcv_df — inline
    duplicated to avoid cross-test-file import coupling (review F5 校准).
    """
    import pandas as pd
    base = last_close - (n_rows - 1) * 50
    rows = []
    for i in range(n_rows):
        close = base + i * 50
        rows.append({
            "timestamp": 1_776_000_000 + i * 86_400_000,
            "open": close - 10, "high": close + 500, "low": close - 500,
            "close": close, "volume": 1000.0,
        })
    return pd.DataFrame(rows)


async def _invoke_path_b(tool_name: str) -> str:
    """Build minimum mocks per tool and invoke. Each branch sets up only what
    the tool calls — keeps mock surface area small + readable per tool."""
    import src.agent.tools_perception as tp

    fn = getattr(tp, tool_name)

    if tool_name == "get_market_data":
        market_data = _AsyncMock_dg()
        ticker = _MagicMock_dg()
        ticker.last = 75200.0
        ticker.bid = 75195.0
        ticker.ask = 75205.0
        ticker.high = 76000.0
        ticker.low = 74800.0
        ticker.base_volume = 1000.0
        market_data.get_ticker.return_value = ticker
        market_data.get_ohlcv_dataframe.return_value = _make_ohlcv_df_local(150)
        technical = _MagicMock_dg()
        technical.compute_indicators.return_value = {"atr_14": 100.0, "volume_ratio": 1.0}
        technical.format_for_llm.return_value = "RSI(14): 50.0\nMACD: bullish"
        deps = _MockDeps(market_data=market_data, technical=technical)
        return await fn(deps)

    if tool_name == "get_higher_timeframe_view":
        market_data = _AsyncMock_dg()
        market_data.get_ohlcv_dataframe.return_value = _make_ohlcv_df_local(250)
        return await fn(_MockDeps(market_data=market_data), timeframe="4h")

    if tool_name == "get_multi_timeframe_snapshot":
        market_data = _AsyncMock_dg()
        ticker = _MagicMock_dg()
        ticker.last = 75200.0
        market_data.get_ticker.return_value = ticker
        market_data.get_ohlcv_dataframe.return_value = _make_ohlcv_df_local(250)
        technical = _MagicMock_dg()
        technical.compute_indicators.return_value = {"atr_14": 100.0}
        deps = _MockDeps(market_data=market_data, technical=technical)
        return await fn(deps)

    if tool_name == "get_price_pivots":
        market_data = _AsyncMock_dg()
        ticker = _MagicMock_dg()
        ticker.last = 75200.0
        market_data.get_ticker.return_value = ticker
        market_data.get_ohlcv_dataframe.return_value = _make_ohlcv_df_local(100)
        return await fn(_MockDeps(market_data=market_data))

    if tool_name == "get_recent_trades":
        market_data = _AsyncMock_dg()
        market_data.get_recent_trades.return_value = []  # no-trades L3 path → still sectioned
        return await fn(_MockDeps(market_data=market_data))

    if tool_name == "get_order_book":
        market_data = _AsyncMock_dg()
        market_data.get_order_book.side_effect = Exception("upstream")  # L2 unavailable
        return await fn(_MockDeps(market_data=market_data))

    if tool_name == "get_derivatives_data":
        # Force all-3-failure → L2 inline `Error:` form (Option D)
        market_data = _AsyncMock_dg()
        market_data.get_funding_rate.side_effect = Exception()
        market_data.get_open_interest.side_effect = Exception()
        market_data.get_long_short_ratio.side_effect = Exception()
        return await fn(_MockDeps(market_data=market_data))

    if tool_name == "get_account_balance":
        return await fn(_MockDeps(exchange=_mock_exchange_minimal()))

    if tool_name == "get_open_orders":
        return await fn(_MockDeps(exchange=_mock_exchange_minimal(open_orders=[])))

    raise AssertionError(f"unhandled Path-B tool {tool_name}")


PATH_B_TOOLS = [
    "get_market_data", "get_higher_timeframe_view",
    "get_multi_timeframe_snapshot", "get_price_pivots",
    "get_recent_trades", "get_order_book", "get_derivatives_data",
    "get_account_balance", "get_open_orders",
]
# Note: get_market_news is NOT in PATH_B_TOOLS — its happy-path multi-source
# (FGI + Symbol/General News) requires more elaborate mocking than other tools.
# Coverage: inline snapshot tests (test_snapshot_get_market_news_*) cover happy
# + L2 + L3 + dense-clip; PATH_A test (service=None / news=None early return)
# covers the L2 fallback path.


@pytest.mark.parametrize("tool_name", PATH_B_TOOLS)
async def test_dg_1_path_b_minimum_mock_happy_or_l2(tool_name):
    """T-DG-1 Path B: minimum-mock invocation (happy path or L2 unavailable) →
    returns starts with `=== `.
    """
    out = await _invoke_path_b(tool_name)
    assert out.startswith("=== "), (
        f"{tool_name} (Path B) did not start with section header: {out[:120]!r}"
    )


# === T-DG-1b: 结构性 sectioning lint (every === ... === line is canonical) ===


_TDG1B_SECTION_LINE_RE = _re_dg.compile(r"^=== (.+) ===$")


def _assert_no_half_sectioned(tool_name: str, out: str):
    """Every line that looks like a section header (starts with '=== ') must
    close with ' ===' (canonical pattern). Catches half-sectioned drift like
    'Pending Orders:' appearing after a proper === Section === header.
    """
    for i, line in enumerate(out.split("\n")):
        if line.startswith("=== "):
            assert _TDG1B_SECTION_LINE_RE.match(line), (
                f"{tool_name} line {i} not canonical section header: {line!r}"
            )


@pytest.mark.parametrize("tool_name", PATH_A_TOOLS)
async def test_dg_1b_path_a_canonical_section_lines(tool_name):
    """T-DG-1b Path A: every === ...-prefixed line must be canonical."""
    import src.agent.tools_perception as tp
    fn = getattr(tp, tool_name)
    deps = _MockDeps(exchange=_mock_exchange_minimal())
    out = await fn(deps)
    _assert_no_half_sectioned(tool_name, out)


@pytest.mark.parametrize("tool_name", PATH_B_TOOLS)
async def test_dg_1b_path_b_canonical_section_lines(tool_name):
    """T-DG-1b Path B: every === ...-prefixed line must be canonical."""
    out = await _invoke_path_b(tool_name)
    _assert_no_half_sectioned(tool_name, out)


# === T-DG-1c: 关键字段白名单 (spec §11 risk mitigation) ===
#
# Per spec §11 风险表: "T-DG-1 lint 检查无关键字段消失（白名单校验 RSI / MACD / MA20 /
# Bollinger / Funding / OI / L/S / FGI 等核心字段在重构后仍存在）". 仅 Path B
# happy path 需此白名单（Path A 是 service=None / empty-state，没有数据字段）。
#
# Option D adopted (commit 99ad60e): L2 fallback uses inline 'Error:' prefix
# instead of '=== Error ===' section, so whitelist uses 'Error:' for L2 paths.
_CRITICAL_FIELDS_PATH_B: dict[str, list[str]] = {
    "get_market_data": ["Ticker", "Technical Indicators", "Market Context",
                        "Recent Candles", "RSI", "MACD", "ATR"],
    "get_higher_timeframe_view": ["Higher Timeframe View", "MA Distances",
                                  "MA50", "MA100", "MA200"],
    "get_multi_timeframe_snapshot": ["Multi-TF Snapshot", "Current price",
                                     "Momentum", "Structure", "Volatility"],
    # get_price_pivots: Swing Status / Prior Period H/L are conditional sections —
    # tested via dedicated snapshots, not Path B happy-fixture lint.
    "get_price_pivots": ["Price Pivots", "Current Price",
                         "Levels Above Current Price",
                         "Levels Below Current Price"],
    # Path-B tools that exercise L2 path (Option D inline 'Error:'):
    "get_recent_trades": ["Recent Trades"],
    "get_order_book": ["Order Book", "Error:"],  # forced L2 in _invoke_path_b
    "get_derivatives_data": ["Derivatives Data", "Error:"],  # forced all-fail L2
    "get_account_balance": ["Account Balance", "Total", "Return", "Free", "Used"],
    "get_open_orders": ["Pending Orders"],
}

# Path A 也有少量必现字段（service=None / empty-state 默认文案 + section header）:
_CRITICAL_FIELDS_PATH_A: dict[str, list[str]] = {
    "get_market_news": ["News", "Error:", "not configured"],
    "get_exchange_announcements": ["Exchange Announcements", "Error:", "not configured"],
    "get_macro_calendar": ["Upcoming Macro Events", "Error:", "not configured"],
    "get_macro_context": ["Macro Context", "Error:", "not configured"],
    "get_etf_flows": ["BTC Spot ETF Flows", "Error:", "not configured"],
    "get_stablecoin_supply": ["Stablecoin Supply", "Error:", "not configured"],
    "get_trade_journal": ["Trade Journal", "No trade journal entries yet"],
    "get_performance": ["Trading Performance", "Initial Balance", "Current Balance"],
    "get_active_alerts": ["Price Alert Settings", "Volatility alert"],
    "get_position": ["Position", "No open positions"],
}


@pytest.mark.parametrize("tool_name", PATH_A_TOOLS)
async def test_dg_1c_path_a_critical_fields_present(tool_name):
    """T-DG-1c Path A: critical default-state fields/headers present in output."""
    import src.agent.tools_perception as tp
    fn = getattr(tp, tool_name)
    deps = _MockDeps(exchange=_mock_exchange_minimal())
    out = await fn(deps)
    for field in _CRITICAL_FIELDS_PATH_A[tool_name]:
        assert field in out, (
            f"{tool_name} missing critical field {field!r} in Path A output:\n"
            f"{out[:400]!r}"
        )


@pytest.mark.parametrize("tool_name", PATH_B_TOOLS)
async def test_dg_1c_path_b_critical_fields_present(tool_name):
    """T-DG-1c Path B: critical happy-path / L2 fields/headers present in output."""
    out = await _invoke_path_b(tool_name)
    for field in _CRITICAL_FIELDS_PATH_B[tool_name]:
        assert field in out, (
            f"{tool_name} missing critical field {field!r} in Path B output:\n"
            f"{out[:400]!r}"
        )


# === T-DG-1d: 参数顺序 lint (spec §4.1.1) ===
@pytest.mark.parametrize("tool_name,expected_pattern", [
    ("get_higher_timeframe_view", r"=== Higher Timeframe View \(BTC/USDT:USDT, 4h\) ==="),
    ("get_price_pivots", r"=== Price Pivots \(BTC/USDT:USDT, main TF: \w+\) ==="),
])
async def test_dg_1d_param_order_convention(tool_name, expected_pattern):
    """T-DG-1d: §4.1.1 multi-arg header param order — symbol-first convention."""
    out = await _invoke_path_b(tool_name)
    assert _re_dg.search(expected_pattern, out), (
        f"{tool_name} header param order violates §4.1.1: pattern {expected_pattern!r} "
        f"not found in:\n{out[:400]!r}"
    )
