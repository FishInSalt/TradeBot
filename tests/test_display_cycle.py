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
    tool_calls = [
        {"tool_name": "get_market_data", "content": "=== Ticker (BTC/USDT:USDT) ===\nPrice: 84200.00 | Bid: 84190.00 | Ask: 84210.00\n\n=== Technical Indicators (15m) ===\nCurrent Price: 84200.00\n\nRSI(14): 62.30\n\n=== Market Context ===\nATR(14): 101.04 (0.12% of price, 15m candles)", "outcome": "success"},
        {"tool_name": "get_position", "content": "No open positions.", "outcome": "success"},
    ]
    result = format_cycle_output(
        cycle_id="a3f2e1b4",
        trigger_type="scheduled",
        tool_calls=tool_calls,
        agent_output="Market is quiet, no action taken.",
        tokens_used=1200,
        budget_remaining=48800,
    )
    assert "a3f2" in result
    assert "scheduled" in result
    assert "get_market_data" in result
    assert "get_position" in result
    assert "Agent:" in result
    assert "Market is quiet" in result
    assert "1,200" in result
    assert "48,800" in result


def test_format_cycle_output_with_memory():
    from src.cli.display import format_cycle_output
    tool_calls = [
        {"tool_name": "save_memory", "content": "Memory saved [lesson] (importance=0.8): Always wait for confirmation", "outcome": "success", "args": {"category": "lesson", "content": "Always wait for RSI confirmation before entry", "importance": 0.8}},
    ]
    result = format_cycle_output(
        cycle_id="b5c6d7e8",
        trigger_type="conditional",
        tool_calls=tool_calls,
        agent_output="Lesson recorded.",
        tokens_used=500,
        budget_remaining=49500,
    )
    assert "✎" in result
    assert "[lesson]" in result
    assert "Always wait for RSI confirmation" in result  # full content from args


def test_format_cycle_output_with_error():
    from src.cli.display import format_cycle_output
    tool_calls = [
        {"tool_name": "open_position", "content": "Trade rejected by human approval.", "outcome": "success"},
    ]
    result = format_cycle_output(
        cycle_id="c7d8e9f0",
        trigger_type="scheduled",
        tool_calls=tool_calls,
        agent_output="Trade was rejected.",
        tokens_used=800,
        budget_remaining=49200,
    )
    assert "✗" in result


def test_format_cycle_output_outcome_failed():
    from src.cli.display import format_cycle_output
    tool_calls = [
        {"tool_name": "get_market_data", "content": "Connection error", "outcome": "failed"},
    ]
    result = format_cycle_output(
        cycle_id="d1e2f3a4",
        trigger_type="scheduled",
        tool_calls=tool_calls,
        agent_output="Could not fetch data.",
        tokens_used=300,
        budget_remaining=49700,
    )
    assert "✗" in result


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
    """T-RR-3: thinking > 800 chars → truncate to 800 + '... [+N chars]' marker."""
    from src.cli.display import _render_reasoning
    text = "y" * 1547
    out = _render_reasoning(text)
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
