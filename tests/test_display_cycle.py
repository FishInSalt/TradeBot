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
