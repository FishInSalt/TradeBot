# Agent Experience Optimization Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add Agent process visibility (tool call summaries in terminal) and redesign system prompt from instruction manual to trader thinking framework.

**Architecture:** A2 adds a display layer that post-processes pydantic-ai message history into structured tool call summaries across three output channels (terminal, session log, system log). A1 rewrites `generate_system_prompt` with a three-layer architecture (identity → thinking framework → strategy preferences). Both changes are code-independent.

**Tech Stack:** Python 3.13, pydantic-ai >= 1.0, Rich (terminal formatting)

---

## File Structure

**A2 (Process Visibility):**
- Modify: `src/cli/display.py` — add tool summary parsers and cycle output formatter
- Modify: `src/cli/app.py` — integrate message extraction and display into `run_agent_cycle`
- Create: `tests/test_display_cycle.py` — unit tests for all summary parsers and cycle formatting

**A1+P0 (Prompt Redesign):**
- Modify: `src/agent/persona.py` — rewrite `generate_system_prompt` with three-layer architecture
- Modify: `src/cli/wizard.py` — comment out numerical parameter prompts, simplify summary
- Modify: `tests/test_persona.py` — update tests for new prompt structure
- Modify: `tests/test_wizard.py` — update tests for simplified wizard

---

## Task 1: Tool Summary Parsers — Perception Tools

**Files:**
- Create: `tests/test_display_cycle.py`
- Modify: `src/cli/display.py`

- [ ] **Step 1: Write failing tests for perception tool parsers**

```python
# tests/test_display_cycle.py
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
        "Current Price: 84200.50\n\n"
        "RSI(14): 62.30 (neutral)\n"
        "MA(20): 84000.00\nMA(50): 83500.00\n"
        "MACD: 50.00\nMACD Signal: 45.00\nMACD Histogram: 5.00\n"
        "Bollinger Upper: 85000.00\nBollinger Middle: 84000.00\nBollinger Lower: 83000.00\n\n"
        "=== Market Context ===\n"
        "ATR(14): 101.04 (0.12% of price — moderate)\n"
        "Volume: 500.0 (1.10x avg — normal)\n"
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
        "  Liquidation: 55000.00 (34.7% away)\n"
        "  Duration: 2h 30m"
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


def test_summarize_fallback_unknown_tool():
    from src.cli.display import summarize_tool
    result = summarize_tool("unknown_tool", "Some random return value that is quite long " * 5)
    assert len(result) <= 85  # 80 chars + possible ellipsis


def test_summarize_fallback_malformed():
    from src.cli.display import summarize_tool
    result = summarize_tool("get_market_data", "Error: connection timeout")
    # Should not crash, should return truncated fallback
    assert "Error" in result
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_display_cycle.py -v`
Expected: FAIL — `summarize_tool` not defined

- [ ] **Step 3: Implement perception tool parsers**

```python
# Add to src/cli/display.py after existing code
from __future__ import annotations

import re


def _fallback_summary(content: str, max_len: int = 80) -> str:
    """Fallback: first line or first max_len chars."""
    first_line = str(content).split("\n")[0].strip()
    if len(first_line) <= max_len:
        return first_line
    return first_line[:max_len] + "..."


def _summarize_get_market_data(content: str) -> str:
    price_m = re.search(r"Price:\s*([\d.]+)", content)
    rsi_m = re.search(r"RSI\(14\):\s*([\d.]+)", content)
    atr_m = re.search(r"ATR\(14\):\s*[\d.]+\s*\(([\d.]+)%\s*of price", content)
    symbol_m = re.search(r"Ticker\s*\(([^)]+)\)", content)

    symbol = symbol_m.group(1).split("/")[0] if symbol_m else ""
    price = f"${float(price_m.group(1)):,.0f}" if price_m else "N/A"
    rsi = f"RSI {rsi_m.group(1)}" if rsi_m else ""
    atr = f"ATR {atr_m.group(1)}%" if atr_m else ""

    parts = [p for p in [f"{symbol} {price}".strip(), rsi, atr] if p]
    return " | ".join(parts) if parts else _fallback_summary(content)


def _summarize_get_position(content: str) -> str:
    if "No open positions" in content:
        return "No open positions."
    side_m = re.search(r"(LONG|SHORT)\s+([\d.]+)\s+contracts\s+@\s+([\d.]+)", content)
    pnl_m = re.search(r"\(([+-]?[\d.]+)%\s+of initial capital\)", content)
    if side_m:
        side = side_m.group(1).capitalize()
        contracts = side_m.group(2)
        entry = f"${float(side_m.group(3)):,.0f}"
        pnl = f" | PnL {pnl_m.group(1)}%" if pnl_m else ""
        return f"{side} {contracts} @ {entry}{pnl}"
    return _fallback_summary(content)


def _summarize_get_account_balance(content: str) -> str:
    total_m = re.search(r"Total:\s*([\d.]+)\s*USDT", content)
    ret_m = re.search(r"Return:\s*([+-]?[\d.]+)%", content)
    if total_m:
        total = f"${float(total_m.group(1)):,.0f}"
        ret = f" ({ret_m.group(1)}%)" if ret_m else ""
        return f"{total}{ret}"
    return _fallback_summary(content)


def _summarize_get_open_orders(content: str) -> str:
    if "No pending orders" in content:
        return "No pending orders."
    tags = re.findall(r"\[(STOP|TAKE_PROFIT|LIMIT|PENDING)\]", content)
    if tags:
        type_map = {"STOP": "SL", "TAKE_PROFIT": "TP", "LIMIT": "LMT", "PENDING": "MKT"}
        types = " / ".join(type_map.get(t, t) for t in tags)
        return f"{len(tags)} orders ({types})"
    return _fallback_summary(content)


def _summarize_get_trade_journal(content: str) -> str:
    if "No trade journal" in content:
        return "No trade journal entries."
    total_m = re.search(r"Total Trades:\s*(\d+)", content)
    win_m = re.search(r"Win:\s*\d+\s*\(([\d.]+)%\)", content)
    pf_m = re.search(r"Profit Factor:\s*([\d.]+|N/A)", content)
    if total_m:
        parts = [f"{total_m.group(1)} trades"]
        if win_m:
            parts.append(f"Win {win_m.group(1)}%")
        if pf_m:
            parts.append(f"PF {pf_m.group(1)}")
        return " | ".join(parts)
    return _fallback_summary(content)


def _summarize_get_memories(content: str) -> str:
    if "No relevant memories" in content:
        return "No relevant memories."
    entries = re.findall(r"^- \[", content, re.MULTILINE)
    return f"{len(entries)} memories" if entries else _fallback_summary(content)


def _summarize_get_active_alerts(content: str) -> str:
    threshold_m = re.search(r"([\d.]+)%\s+in\s+(\d+)min", content)
    count_m = re.search(r"Alerts\s*\((\d+)/", content)
    parts = []
    if threshold_m:
        parts.append(f"Vol: {threshold_m.group(1)}%/{threshold_m.group(2)}min")
    elif "OFF" in content:
        parts.append("Vol: OFF")
    count = count_m.group(1) if count_m else "0"
    parts.append(f"{count} price alerts")
    return " | ".join(parts)


def _summarize_get_performance(content: str) -> str:
    if "No completed trades" in content or "No metrics" in content:
        ret_m = re.search(r"Return:\s*([+-]?[\d.]+)%", content)
        return f"Return {ret_m.group(1)}% | No trades yet" if ret_m else _fallback_summary(content)
    ret_m = re.search(r"Total Return:\s*([+-]?[\d.]+)%", content)
    total_m = re.search(r"Total Trades:\s*(\d+)", content)
    win_m = re.search(r"Win:\s*\d+\s*\(([\d.]+)%\)", content)
    parts = []
    if ret_m:
        parts.append(f"Return {ret_m.group(1)}%")
    if total_m:
        parts.append(f"{total_m.group(1)} trades")
    if win_m:
        parts.append(f"Win {win_m.group(1)}%")
    return " | ".join(parts) if parts else _fallback_summary(content)


# === Public API ===

_PERCEPTION_PARSERS = {
    "get_market_data": _summarize_get_market_data,
    "get_position": _summarize_get_position,
    "get_account_balance": _summarize_get_account_balance,
    "get_open_orders": _summarize_get_open_orders,
    "get_trade_journal": _summarize_get_trade_journal,
    "get_memories": _summarize_get_memories,
    "get_active_alerts": _summarize_get_active_alerts,
    "get_performance": _summarize_get_performance,
}


def summarize_tool(tool_name: str, content: str) -> str:
    """Summarize a tool's return value into a one-line display string."""
    content_str = str(content)
    parser = _PERCEPTION_PARSERS.get(tool_name)
    if parser:
        try:
            return parser(content_str)
        except Exception:
            return _fallback_summary(content_str)
    # Not a known perception tool — fallback for now (execution parsers added in Task 2)
    return _fallback_summary(content_str)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_display_cycle.py -v`
Expected: All perception tests PASS, `test_summarize_fallback_unknown_tool` and `test_summarize_fallback_malformed` PASS

- [ ] **Step 5: Commit**

```bash
git add src/cli/display.py tests/test_display_cycle.py
git commit -m "feat(A2): add perception tool summary parsers

Implement summarize_tool() with dedicated parsers for 8 perception
tools (get_market_data, get_position, get_account_balance, etc.)
and fallback truncation for unknown tools."
```

---

## Task 2: Tool Summary Parsers — Execution & Memory Tools

**Files:**
- Modify: `tests/test_display_cycle.py`
- Modify: `src/cli/display.py`

- [ ] **Step 1: Write failing tests for execution and memory tool parsers**

Add to `tests/test_display_cycle.py`:

```python
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
    from src.cli.display import summarize_tool, is_tool_error
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
    assert "cancel" in result.lower() or "Cancelled" in result


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
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_display_cycle.py -v -k "execution or memory or error"`
Expected: FAIL — `is_tool_error`, `summarize_save_memory`, execution parsers not defined

- [ ] **Step 3: Implement execution parsers, memory parser, and error detection**

Add to `src/cli/display.py`:

```python
# === Execution tool parsers ===

def _summarize_open_position(content: str) -> str:
    m = re.search(r"Order submitted:\s*(\w+)\s+([\d.]+)\s*@\s*~?([\d.]+),\s*(\d+)x", content)
    if m:
        return f"{m.group(1)} {m.group(2)} @ ~${float(m.group(3)):,.0f}, {m.group(4)}x"
    return _fallback_summary(content)


def _summarize_close_position(content: str) -> str:
    if "No positions to close" in content:
        return "No positions to close."
    m = re.search(r"close\s+(\d+)\s+position", content)
    if m:
        return f"Close {m.group(1)} position(s)"
    return _fallback_summary(content)


def _summarize_set_stop_loss(content: str) -> str:
    m = re.search(r"Stop loss set at\s+([\d.]+)\s*\(([^)]+)\)", content)
    if m:
        return f"SL @ ${float(m.group(1)):,.0f} ({m.group(2).split('from')[0].strip()})"
    m2 = re.search(r"Stop loss set at\s+([\d.]+)", content)
    if m2:
        return f"SL @ ${float(m2.group(1)):,.0f}"
    return _fallback_summary(content)


def _summarize_set_take_profit(content: str) -> str:
    m = re.search(r"Take profit set at\s+([\d.]+)\s*\(([^)]+)\)", content)
    if m:
        return f"TP @ ${float(m.group(1)):,.0f} ({m.group(2).split('from')[0].strip()})"
    m2 = re.search(r"Take profit set at\s+([\d.]+)", content)
    if m2:
        return f"TP @ ${float(m2.group(1)):,.0f}"
    return _fallback_summary(content)


def _summarize_adjust_leverage(content: str) -> str:
    m = re.search(r"(\d+)x\s+for\s+(\S+)", content)
    if m:
        return f"{m.group(1)}x for {m.group(2)}"
    return _fallback_summary(content)


def _summarize_place_limit_order(content: str) -> str:
    m = re.search(r"Limit order placed:\s*(\w+)\s+([\d.]+)\s*@\s*([\d.]+),\s*(\d+)x", content)
    if m:
        return f"Limit {m.group(1)} {m.group(2)} @ ${float(m.group(3)):,.0f}, {m.group(4)}x"
    return _fallback_summary(content)


def _summarize_cancel_order(content: str) -> str:
    m = re.search(r"Order cancelled:\s*(\w+)\s+(\w+)\s+([\d.]+)", content)
    if m:
        return f"Cancelled {m.group(1)} {m.group(2)} {m.group(3)}"
    if "not found" in content.lower():
        return _fallback_summary(content)
    return _fallback_summary(content)


def _summarize_set_price_alert(content: str) -> str:
    m = re.search(r"threshold=([\d.]+)%.*window=(\d+)min", content)
    if m:
        return f"threshold={m.group(1)}%, window={m.group(2)}min"
    return _fallback_summary(content)


def _summarize_add_price_level_alert(content: str) -> str:
    m = re.search(r"(above|below)\s+([\d.]+)", content)
    if m:
        return f"{m.group(1)} ${float(m.group(2)):,.0f}"
    return _fallback_summary(content)


def _summarize_set_next_wake(content: str) -> str:
    m = re.search(r"(\d+)\s*min", content)
    if m:
        return f"{m.group(1)}min"
    return _fallback_summary(content)


# Register execution parsers
_EXECUTION_PARSERS = {
    "open_position": _summarize_open_position,
    "close_position": _summarize_close_position,
    "set_stop_loss": _summarize_set_stop_loss,
    "set_take_profit": _summarize_set_take_profit,
    "adjust_leverage": _summarize_adjust_leverage,
    "place_limit_order": _summarize_place_limit_order,
    "cancel_order": _summarize_cancel_order,
    "set_price_alert": _summarize_set_price_alert,
    "add_price_level_alert": _summarize_add_price_level_alert,
    "set_next_wake": _summarize_set_next_wake,
}

# Success prefix whitelist for execution tools (business rejection detection)
_EXECUTION_SUCCESS_PREFIXES = {
    "open_position": "Order submitted:",
    "close_position": "Orders submitted:",
    "set_stop_loss": "Stop loss set at",
    "set_take_profit": "Take profit set at",
    "adjust_leverage": "Leverage adjusted to",
    "place_limit_order": "Limit order placed:",
    "cancel_order": "Order cancelled:",
    "set_price_alert": "Price alert updated:",
    "add_price_level_alert": "Price level alert set:",
    "set_next_wake": "Next wake set to",
}


def summarize_save_memory(args: dict) -> str:
    """Summarize save_memory from ToolCallPart.args (full content, not truncated return)."""
    category = args.get("category", "unknown")
    content = args.get("content", "")
    importance = args.get("importance", 0.5)
    return f"[{category}] {content} (importance: {importance})"


def is_tool_error(tool_name: str, content: str, outcome: str = "success") -> bool:
    """Detect if a tool call resulted in an error or business rejection.

    Two-layer detection:
    1. outcome != 'success' → always error (pydantic-ai level)
    2. For execution tools with outcome='success' → check success prefix whitelist
    """
    if outcome != "success":
        return True
    prefix = _EXECUTION_SUCCESS_PREFIXES.get(tool_name)
    if prefix is not None:
        return not str(content).startswith(prefix)
    return False
```

Update `summarize_tool` to include execution parsers:

```python
def summarize_tool(tool_name: str, content: str) -> str:
    """Summarize a tool's return value into a one-line display string."""
    content_str = str(content)
    parser = _PERCEPTION_PARSERS.get(tool_name) or _EXECUTION_PARSERS.get(tool_name)
    if parser:
        try:
            return parser(content_str)
        except Exception:
            return _fallback_summary(content_str)
    return _fallback_summary(content_str)
```

- [ ] **Step 4: Run all display tests**

Run: `pytest tests/test_display_cycle.py -v`
Expected: All PASS

- [ ] **Step 5: Commit**

```bash
git add src/cli/display.py tests/test_display_cycle.py
git commit -m "feat(A2): add execution/memory tool parsers and error detection

Implement parsers for 10 execution tools + save_memory (from args).
Add is_tool_error() with two-layer detection: outcome check + success
prefix whitelist for business rejections."
```

---

## Task 3: Cycle Output Formatter

**Files:**
- Modify: `tests/test_display_cycle.py`
- Modify: `src/cli/display.py`

- [ ] **Step 1: Write failing tests for format_cycle_output**

Add to `tests/test_display_cycle.py`:

```python
# === Cycle output formatting ===

def test_format_cycle_output_basic():
    from src.cli.display import format_cycle_output
    tool_calls = [
        {"tool_name": "get_market_data", "content": "=== Ticker (BTC/USDT:USDT) ===\nPrice: 84200.00 | Bid: 84190.00 | Ask: 84210.00\n\n=== Technical Indicators (15m) ===\nCurrent Price: 84200.00\n\nRSI(14): 62.30 (neutral)\n\n=== Market Context ===\nATR(14): 101.04 (0.12% of price — moderate)", "outcome": "success"},
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
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_display_cycle.py::test_format_cycle_output_basic -v`
Expected: FAIL — `format_cycle_output` not defined

- [ ] **Step 3: Implement format_cycle_output**

Add to `src/cli/display.py`:

```python
def format_cycle_output(
    cycle_id: str,
    trigger_type: str,
    tool_calls: list[dict],
    agent_output: str,
    tokens_used: int,
    budget_remaining: int,
) -> str:
    """Format a complete cycle's output for terminal/session log display.

    Args:
        cycle_id: Full 8-char cycle ID (first 4 shown in header).
        trigger_type: "scheduled", "conditional", or "alert".
        tool_calls: List of dicts with keys: tool_name, content, outcome, args (optional).
        agent_output: Final agent text (from result.output).
        tokens_used: Token count for this cycle.
        budget_remaining: Remaining daily token budget.

    Returns:
        Formatted string with Rich markup for terminal display.
    """
    lines = []

    # Header
    short_id = cycle_id[:4]
    lines.append(f"[dim]── Cycle {short_id} ({trigger_type}) {'─' * 30}[/]")

    # Tool call summaries
    for tc in tool_calls:
        name = tc["tool_name"]
        content = tc.get("content", "")
        outcome = tc.get("outcome", "success")
        args = tc.get("args")

        # Determine icon
        if name == "save_memory":
            icon = "✎"
            # Extract full content from args (ToolReturnPart truncates to 80 chars)
            if args and isinstance(args, dict):
                summary = summarize_save_memory(args)
            else:
                summary = summarize_tool(name, content)
        elif is_tool_error(name, content, outcome):
            icon = "✗"
            summary = _fallback_summary(content)
        else:
            icon = "⚙"
            summary = summarize_tool(name, content)

        # Format: icon + tool_name (padded) + summary
        lines.append(f"{icon} {name:<22} {summary}")

    # Agent output
    lines.append(f"\n[bold cyan]Agent:[/]\n{agent_output}")

    # Footer
    lines.append(f"\n[dim]tokens: {tokens_used:,} | budget: {budget_remaining:,} remaining[/]")
    lines.append(f"[dim]{'─' * 44}[/]")

    return "\n".join(lines)
```

- [ ] **Step 4: Run all display tests**

Run: `pytest tests/test_display_cycle.py -v`
Expected: All PASS

- [ ] **Step 5: Commit**

```bash
git add src/cli/display.py tests/test_display_cycle.py
git commit -m "feat(A2): add format_cycle_output for terminal display

Renders cycle header, tool call summaries with icons (⚙/✗/✎),
agent output text, and token usage footer."
```

---

## Task 4: Integrate into run_agent_cycle

**Files:**
- Modify: `src/cli/app.py`

- [ ] **Step 1: Modify run_agent_cycle to extract and display messages**

In `src/cli/app.py`, replace the current output section (lines 147-167) with message extraction, display formatting, and logging:

```python
# After result = await agent.run(prompt, **run_kwargs) and retry logic (line 146):

    tokens = result.usage().total_tokens if result.usage() else 0
    budget.record(tokens)

    # === A2: Extract tool calls from message history ===
    tool_calls = []
    _call_args_by_id: dict[str, dict | None] = {}

    from pydantic_ai.messages import (
        ModelRequest, ModelResponse,
        ToolCallPart, ToolReturnPart,
    )

    for msg in result.new_messages():
        if isinstance(msg, ModelResponse):
            for part in msg.parts:
                if isinstance(part, ToolCallPart):
                    args = part.args
                    if isinstance(args, str):
                        import json as _json
                        try:
                            args = _json.loads(args)
                        except (ValueError, TypeError):
                            args = None
                    elif not isinstance(args, dict):
                        args = None
                    _call_args_by_id[part.tool_call_id] = args
        elif isinstance(msg, ModelRequest):
            for part in msg.parts:
                if isinstance(part, ToolReturnPart):
                    content_str = str(part.content)
                    outcome = getattr(part, "outcome", "success")
                    args = _call_args_by_id.get(part.tool_call_id)
                    if args is None:
                        # Fallback: sequential matching (log warning)
                        logger.warning(
                            f"tool_call_id mismatch for {part.tool_name}, using fallback"
                        )
                    tool_calls.append({
                        "tool_name": part.tool_name,
                        "content": content_str,
                        "outcome": outcome,
                        "args": args,
                    })

                    # System log: INFO summary, DEBUG full content
                    from src.cli.display import summarize_tool
                    summary = summarize_tool(part.tool_name, content_str)
                    logger.info(f"  ⚙ {part.tool_name}: {summary}")
                    logger.debug(
                        f"  Tool {part.tool_name} args={args} "
                        f"return={content_str[:500]}"
                    )

    # === Record to database ===
    async with get_session(engine) as session:
        session.add(
            DecisionLog(
                session_id=deps.session_id,
                cycle_id=cycle_id,
                trigger_type=trigger_type,
                decision="completed",
                reasoning=result.output[:500],
                model_used=getattr(model, 'model_name', str(model)) if model else str(agent.model),
                tokens_used=tokens,
            )
        )
        await session.commit()

    logger.info(f"Cycle {cycle_id}: {tokens} tokens ({budget.remaining} remaining)")

    # === A2: Display formatted cycle output ===
    if console is not None:
        from src.cli.display import format_cycle_output
        output = format_cycle_output(
            cycle_id=cycle_id,
            trigger_type=trigger_type,
            tool_calls=tool_calls,
            agent_output=result.output,
            tokens_used=tokens,
            budget_remaining=budget.remaining,
        )
        console.print(output)

    return result
```

- [ ] **Step 2: Run existing tests to verify no regressions**

Run: `pytest tests/ -v --timeout=30`
Expected: All 352 tests PASS

- [ ] **Step 3: Commit**

```bash
git add src/cli/app.py
git commit -m "feat(A2): integrate message extraction and cycle display

run_agent_cycle now extracts tool calls from result.new_messages(),
formats structured summaries for terminal via format_cycle_output,
and writes INFO/DEBUG logs to system.log."
```

---

## Task 5: System Prompt Redesign — Three-Layer Architecture

**Files:**
- Modify: `src/agent/persona.py`
- Modify: `tests/test_persona.py`

- [ ] **Step 1: Write new tests for three-layer prompt**

Replace `tests/test_persona.py` entirely:

```python
# tests/test_persona.py
from src.config import PersonaConfig


def test_prompt_contains_layer1_identity():
    from src.agent.persona import generate_system_prompt
    prompt = generate_system_prompt(PersonaConfig())
    prompt_lower = prompt.lower()
    # Market context
    assert "perpetual" in prompt_lower
    assert "one-way" in prompt_lower or "single direction" in prompt_lower or "close position first" in prompt_lower
    # Fill timing
    assert "fill" in prompt_lower
    # Multi-timeframe (P0)
    assert "timeframe" in prompt_lower
    # Memory
    assert "save_memory" in prompt_lower or "memory" in prompt_lower
    # Dynamic wake
    assert "set_next_wake" in prompt_lower or "wake" in prompt_lower


def test_prompt_contains_layer2_thinking_framework():
    from src.agent.persona import generate_system_prompt
    prompt = generate_system_prompt(PersonaConfig())
    prompt_lower = prompt.lower()
    # Thinking dimensions
    assert "market structure" in prompt_lower
    assert "risk" in prompt_lower and "reward" in prompt_lower
    assert "support" in prompt_lower or "resistance" in prompt_lower
    assert "position" in prompt_lower and "management" in prompt_lower or "sizing" in prompt_lower


def test_prompt_no_must_never_constraints():
    from src.agent.persona import generate_system_prompt
    prompt = generate_system_prompt(PersonaConfig())
    # Must not contain MUST/NEVER/ALWAYS as hard imperatives
    assert "You MUST" not in prompt
    assert "MUST NOT" not in prompt
    assert "NEVER go" not in prompt
    assert "NEVER exceed" not in prompt


def test_prompt_no_fixed_step_workflow():
    from src.agent.persona import generate_system_prompt
    prompt = generate_system_prompt(PersonaConfig())
    # Must not have fixed "Step 1: ... Step 2: ..." workflow
    assert "Step 1" not in prompt or "step 1" not in prompt.lower()


def test_prompt_no_numerical_params():
    from src.agent.persona import generate_system_prompt
    config = PersonaConfig(
        max_position_pct=30, preferred_leverage=3,
        stop_loss_pct=3.0, take_profit_pct=6.0,
    )
    prompt = generate_system_prompt(config)
    # Numerical params should NOT appear in prompt
    assert "30%" not in prompt
    assert "3x" not in prompt or "3x leverage" not in prompt.lower()
    assert "3.0%" not in prompt
    assert "6.0%" not in prompt


def test_prompt_contains_trading_style_trend():
    from src.agent.persona import generate_system_prompt
    prompt = generate_system_prompt(PersonaConfig(trading_style="trend_following"))
    prompt_lower = prompt.lower()
    assert "trend" in prompt_lower
    assert "confirmation" in prompt_lower or "follow" in prompt_lower


def test_prompt_contains_trading_style_swing():
    from src.agent.persona import generate_system_prompt
    prompt = generate_system_prompt(PersonaConfig(trading_style="swing"))
    prompt_lower = prompt.lower()
    assert "swing" in prompt_lower
    assert "range" in prompt_lower or "pullback" in prompt_lower


def test_prompt_contains_trading_style_breakout():
    from src.agent.persona import generate_system_prompt
    prompt = generate_system_prompt(PersonaConfig(trading_style="breakout"))
    prompt_lower = prompt.lower()
    assert "breakout" in prompt_lower
    assert "consolidation" in prompt_lower or "volume" in prompt_lower


def test_prompt_styles_are_distinct():
    from src.agent.persona import generate_system_prompt
    p1 = generate_system_prompt(PersonaConfig(trading_style="trend_following"))
    p2 = generate_system_prompt(PersonaConfig(trading_style="swing"))
    p3 = generate_system_prompt(PersonaConfig(trading_style="breakout"))
    # Each style should produce meaningfully different content
    assert p1 != p2
    assert p2 != p3


def test_prompt_contains_risk_tolerance():
    from src.agent.persona import generate_system_prompt
    prompt = generate_system_prompt(PersonaConfig(risk_tolerance="conservative"))
    prompt_lower = prompt.lower()
    assert "capital preservation" in prompt_lower or "conservative" in prompt_lower


def test_prompt_risk_tolerances_are_distinct():
    from src.agent.persona import generate_system_prompt
    p1 = generate_system_prompt(PersonaConfig(risk_tolerance="conservative"))
    p2 = generate_system_prompt(PersonaConfig(risk_tolerance="moderate"))
    p3 = generate_system_prompt(PersonaConfig(risk_tolerance="aggressive"))
    assert p1 != p2
    assert p2 != p3


def test_prompt_is_in_english():
    from src.agent.persona import generate_system_prompt
    prompt = generate_system_prompt(PersonaConfig())
    # Should not contain Chinese characters
    import re
    chinese_chars = re.findall(r'[\u4e00-\u9fff]', prompt)
    assert len(chinese_chars) == 0, f"Found Chinese characters: {chinese_chars[:5]}"


def test_prompt_minimum_length():
    from src.agent.persona import generate_system_prompt
    prompt = generate_system_prompt(PersonaConfig())
    # Three-layer prompt should be substantial
    assert len(prompt) > 500
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_persona.py -v`
Expected: Multiple failures (old prompt doesn't match new expectations)

- [ ] **Step 3: Rewrite generate_system_prompt**

Replace `src/agent/persona.py` entirely:

```python
from src.config import PersonaConfig


def generate_system_prompt(config: PersonaConfig) -> str:
    """Generate a three-layer system prompt based on persona configuration.

    Layer 1: Identity & Tools — who you are, key tool usage notes
    Layer 2: Trader Thinking Framework — how to think (generic)
    Layer 3: Strategy Preferences — what style to trade (injection point)
    """
    layer1 = _build_layer1()
    layer2 = _build_layer2()
    layer3 = _build_layer3(config)
    return f"{layer1}\n\n{layer2}\n\n{layer3}"


def _build_layer1() -> str:
    return """You are a cryptocurrency trader operating autonomously. You analyze markets, manage positions, and make trading decisions using the tools available to you.

## Market Context

You trade USDT-margined perpetual futures (no expiry date). The exchange uses one-way position mode — you cannot hold long and short positions on the same symbol simultaneously. To reverse direction, close your current position first. Leverage cannot be changed while holding a position.

## Tool Usage Notes

- **Fill timing**: After submitting a market order, you will be notified when it fills via a separate trigger. Set stop loss and take profit only after receiving fill confirmation — do not attempt in the same cycle as order submission.
- **Multi-timeframe analysis**: You can call get_market_data with different timeframe parameters (e.g., "1h" for the bigger picture, "5m" for entry timing). Use multiple timeframes to build conviction before acting.
- **Memory**: Use save_memory to record trade reviews, market patterns, and lessons learned. Check your memories to avoid repeating past mistakes.
- **Dynamic wake interval**: Use set_next_wake to control how soon you check the market again. Shorten the interval when you have an open position or expect volatility; lengthen it when the market is quiet and you have no exposure.
- **Limit orders**: Use place_limit_order to enter at specific price levels (e.g., buy at support). Not every entry needs to be a market order.
- **Price level alerts**: Use add_price_level_alert to set one-shot alerts at key support/resistance levels you identify. You will be woken up when these levels are reached."""


def _build_layer2() -> str:
    return """## How to Think

Rather than following a fixed sequence of steps, consider these dimensions of analysis and apply whichever are relevant to the current situation:

**Market Structure**
What is the dominant trend across timeframes? Is the market trending or ranging? Where are the key support and resistance levels? Are higher timeframes aligned with lower timeframes?

**Signal & Confirmation**
Are technical indicators showing confluence? Does price action confirm the signal? Is volume supporting the move, or diverging? Are there any warning signs (divergences, exhaustion candles)?

**Risk-Reward**
What is the risk-to-reward ratio of this potential trade? Where is the logical stop loss — at a structural level, not an arbitrary percentage? Is the potential reward worth the risk? Would a better entry improve the ratio?

**Position Management**
How much capital is currently at risk? Is there a reason to scale in or scale out? Should stops be trailed as the trade develops? Is the position sized appropriately for the conviction level?

**Self-Review**
What happened in similar market conditions before? Are there relevant lessons in your memory? What can you learn from this cycle, regardless of whether you take a trade?

You do not need to address every dimension in every cycle. If the market is quiet and you have no position, a brief structural overview and a decision to wait may be sufficient. If you have an active position in a volatile market, focus on position management and risk."""


def _build_layer3(config: PersonaConfig) -> str:
    style_content = _STYLE_DESCRIPTIONS.get(
        config.trading_style, _STYLE_DESCRIPTIONS["trend_following"]
    )
    risk_content = _RISK_DESCRIPTIONS.get(
        config.risk_tolerance, _RISK_DESCRIPTIONS["moderate"]
    )
    return f"""## Your Trading Approach

### Style: {config.trading_style.replace('_', ' ').title()}

{style_content}

### Risk Profile: {config.risk_tolerance.capitalize()}

{risk_content}"""


_STYLE_DESCRIPTIONS = {
    "trend_following": (
        "You look for established trends and trade in their direction. "
        "Wait for trend confirmation — moving average alignment, a sequence of higher highs "
        "and higher lows (or the reverse for downtrends) — before entering. "
        "Be patient; avoid counter-trend trades unless the evidence of reversal is strong. "
        "Trail your stops as the trend develops to lock in gains. "
        "Exit when the trend structure breaks — a lower low in an uptrend, a higher high in a "
        "downtrend — rather than at an arbitrary profit target."
    ),
    "swing": (
        "You capture price swings within established ranges or during pullbacks in broader trends. "
        "Identify swing points using support/resistance levels and price action patterns. "
        "Enter at value areas — near support in an uptrend, near resistance in a downtrend — "
        "rather than chasing extended moves. "
        "Set profit targets at the opposite boundary of the range or prior swing highs/lows. "
        "Be willing to take partial profits and re-enter on the next pullback."
    ),
    "breakout": (
        "You watch for consolidation patterns and key level breakouts. "
        "Enter on confirmed breakouts — price closes beyond the level with supporting volume. "
        "Be aware that false breakouts are common; manage risk tightly with stops placed just "
        "inside the broken level. "
        "Once momentum confirms the breakout direction, trail stops aggressively to protect gains. "
        "Volume is your primary confirmation tool — a breakout without volume is suspect."
    ),
}


_RISK_DESCRIPTIONS = {
    "conservative": (
        "You prioritize capital preservation above all else. "
        "Prefer high-probability setups with clearly defined invalidation levels. "
        "Use smaller position sizes and tighter stops. "
        "It is perfectly acceptable to miss an opportunity rather than take a low-conviction trade. "
        "When in doubt, stay out."
    ),
    "moderate": (
        "You balance opportunity with risk management. "
        "Use standard position sizes appropriate to the setup quality. "
        "Willing to accept moderate drawdowns in pursuit of reasonable returns. "
        "Take trades when the analysis supports them, but do not force trades in unclear conditions."
    ),
    "aggressive": (
        "You are comfortable taking larger positions when conviction is high. "
        "Willing to accept wider stops and larger drawdowns for the potential of outsized returns. "
        "Actively seek asymmetric risk-reward opportunities where the upside significantly exceeds "
        "the downside. "
        "Still respect risk — aggression does not mean recklessness."
    ),
}
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_persona.py -v`
Expected: All PASS

- [ ] **Step 5: Run full test suite for regressions**

Run: `pytest tests/ -v --timeout=30`
Expected: All tests PASS (persona tests updated, other tests unaffected)

- [ ] **Step 6: Commit**

```bash
git add src/agent/persona.py tests/test_persona.py
git commit -m "feat(A1): rewrite system prompt with three-layer architecture

Layer 1: Identity + market context + tool usage notes (incl. P0 multi-timeframe)
Layer 2: Trader thinking framework (market structure, risk-reward, etc.)
Layer 3: Strategy preferences per trading_style + risk_tolerance

Removes MUST/NEVER hard rules and fixed Step 1-4 workflow.
Numerical params (position%, leverage, SL%, TP%) not injected into prompt."
```

---

## Task 6: Wizard Simplification

**Files:**
- Modify: `src/cli/wizard.py`
- Modify: `tests/test_wizard.py`

- [ ] **Step 1: Comment out numerical params in wizard and simplify summary**

In `src/cli/wizard.py`, modify `_step_persona` (line 267-291):

```python
def _step_persona(trader_defaults: TraderConfig, console: Console) -> dict:
    """Step 5: Persona configuration."""
    console.print("\n[bold]Step 5: Persona[/]")
    p = trader_defaults.persona
    risk = Prompt.ask(
        "  Risk tolerance", choices=["conservative", "moderate", "aggressive"],
        default=p.risk_tolerance, console=console,
    )
    style = Prompt.ask(
        "  Trading style", choices=["trend_following", "swing", "breakout"],
        default=p.trading_style, console=console,
    )
    # Numerical params commented out — not injected into prompt in MVP stage.
    # Agent decides position sizing, leverage, SL/TP based on its own analysis.
    # Uncomment when implementing P3 (hard risk controls) for live trading.
    # max_pos = FloatPrompt.ask("  Max position (%)", default=p.max_position_pct, console=console)
    # leverage = IntPrompt.ask("  Leverage", default=p.preferred_leverage, console=console)
    # stop_loss = FloatPrompt.ask("  Stop loss (%)", default=p.stop_loss_pct, console=console)
    # take_profit = FloatPrompt.ask("  Take profit (%)", default=p.take_profit_pct, console=console)
    persona = PersonaConfig(
        risk_tolerance=risk,
        trading_style=style,
    )
    return {"persona": persona}
```

In `_show_summary` (line 302-339), remove the Risk Params row (lines 330-334):

```python
    p = data["persona"]
    table.add_row("Persona", f"{p.risk_tolerance} / {p.trading_style}")
    # Risk Params row removed — numerical params not used in MVP stage.
    # Uncomment when implementing P3 (hard risk controls) for live trading.
    # table.add_row(
    #     "Risk Params",
    #     f"pos {p.max_position_pct:.0f}% / {p.preferred_leverage}x / "
    #     f"SL {p.stop_loss_pct:.0f}% / TP {p.take_profit_pct:.0f}%",
    # )
```

- [ ] **Step 2: Update wizard tests if any reference the removed fields**

Check `tests/test_wizard.py` for tests that assert on `max_position_pct`, `stop_loss_pct`, etc. in wizard output. Update as needed — the `PersonaConfig` in `WizardResult` will now use code defaults for numerical fields.

- [ ] **Step 3: Run tests**

Run: `pytest tests/test_wizard.py tests/test_persona.py -v`
Expected: All PASS

- [ ] **Step 4: Run full test suite**

Run: `pytest tests/ -v --timeout=30`
Expected: All tests PASS

- [ ] **Step 5: Commit**

```bash
git add src/cli/wizard.py tests/test_wizard.py
git commit -m "feat(A1): simplify wizard — comment out numerical persona params

Risk tolerance and trading style remain as interactive prompts.
Max position%, leverage, SL%, TP% commented out with note to
re-enable for P3 (hard risk controls) before live trading.
Risk Params summary row also commented out."
```

---

## Task 7: Final Verification

**Files:** None (verification only)

- [ ] **Step 1: Run full test suite**

Run: `pytest tests/ -v --timeout=30`
Expected: All tests PASS (count should be 352 + new display tests)

- [ ] **Step 2: Verify no import errors**

Run: `python -c "from src.cli.display import summarize_tool, format_cycle_output, is_tool_error, summarize_save_memory; print('OK')"`
Expected: `OK`

Run: `python -c "from src.agent.persona import generate_system_prompt; from src.config import PersonaConfig; p = generate_system_prompt(PersonaConfig()); print(f'Prompt length: {len(p)} chars'); assert len(p) > 500; print('OK')"`
Expected: `Prompt length: XXXX chars` then `OK`

- [ ] **Step 3: Commit if any fixes were needed**

If all green, no commit needed. Otherwise fix and commit.
