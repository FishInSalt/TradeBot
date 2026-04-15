from __future__ import annotations

import re

from rich.panel import Panel

from src.services.metrics import PerformanceMetrics


def format_metrics(metrics: PerformanceMetrics) -> str:
    pos = metrics.current_position.upper() if metrics.current_position != "none" else "FLAT"
    return (
        f"Return: {metrics.total_return_pct:+.2f}% ({metrics.total_pnl:+.2f} USDT)\n"
        f"Win Rate: {metrics.win_rate * 100:.1f}% ({metrics.winning_trades}W / {metrics.losing_trades}L)\n"
        f"Max Drawdown: -{metrics.max_drawdown_pct:.2f}%\n"
        f"Profit Factor: {metrics.profit_factor:.2f}\n"
        f"Total Trades: {metrics.total_trades}\n"
        f"Position: {pos}"
    )


def display_metrics(metrics: PerformanceMetrics, console) -> None:
    color = "green" if metrics.total_pnl >= 0 else "red"
    console.print(Panel(format_metrics(metrics), title="[bold]Performance[/]", border_style=color))


# === Tool summary parsers ===


def _fallback_summary(content: str, max_len: int = 80) -> str:
    """Fallback: first max_len chars of content (per spec: 'display first 80 chars')."""
    text = " ".join(str(content).split())  # collapse whitespace/newlines
    if len(text) <= max_len:
        return text
    return text[:max_len] + "..."


def _summarize_get_market_data(content: str) -> str:
    price_m = re.search(r"Price:\s*([\d.]+)", content)
    if not price_m:
        return _fallback_summary(content)
    rsi_m = re.search(r"RSI\(14\):\s*([\d.]+)", content)
    atr_m = re.search(r"ATR\(14\):\s*[\d.]+\s*\(([\d.]+)%\s*of price", content)
    symbol_m = re.search(r"Ticker\s*\(([^)]+)\)", content)

    symbol = symbol_m.group(1).split("/")[0] if symbol_m else ""
    price = f"${float(price_m.group(1)):,.0f}"
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
    type_map = {"STOP": "SL", "TAKE_PROFIT": "TP", "LIMIT": "LMT", "PENDING": "MKT"}
    # Extract all orders — with price (@ ...) or without (market orders)
    order_parts = []
    for m in re.finditer(r"\[(STOP|TAKE_PROFIT|LIMIT|PENDING)\]", content):
        label = type_map.get(m.group(1), m.group(1))
        # Look for price after the tag on the same line
        rest = content[m.end():content.index("\n", m.end())] if "\n" in content[m.end():] else content[m.end():]
        price_m = re.search(r"@\s*([\d.]+)", rest)
        if price_m:
            order_parts.append(f"{label} ${float(price_m.group(1)):,.0f}")
        else:
            order_parts.append(label)
    if order_parts:
        return f"{len(order_parts)} orders ({' / '.join(order_parts)})"
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


# === Execution tool parsers ===


def _summarize_open_position(content: str) -> str:
    m = re.search(r"Order submitted:\s*(\w+)\s+([\d.]+)\s*@\s*~?([\d.]+),\s*(\d+)x", content)
    if m:
        return f"{m.group(1)} {m.group(2)} @ ~${float(m.group(3)):,.0f}, {m.group(4)}x"
    return _fallback_summary(content)


def _summarize_close_position(content: str) -> str:
    # "No positions to close." is a business rejection — is_tool_error catches it
    # before this parser runs, so no need to handle it here.
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
    m = re.search(r"Order cancelled:\s*(\w+)\s+(\w+)\s+([\d.]+)\s*@\s*([\d.]+)", content)
    if m:
        return f"Cancelled {m.group(1)} {m.group(2)} {m.group(3)} @ ${float(m.group(4)):,.0f}"
    # Fallback: no price (e.g., market orders without price)
    m2 = re.search(r"Order cancelled:\s*(\w+)\s+(\w+)", content)
    if m2:
        return f"Cancelled {m2.group(1)} {m2.group(2)}"
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
    "add_price_level_alert": ("Price level alert set:", "Alert set"),
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
        # prefix can be a str or tuple of str (multiple success prefixes)
        if isinstance(prefix, tuple):
            return not any(str(content).startswith(p) for p in prefix)
        return not str(content).startswith(prefix)
    return False


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


def resolve_tool_display(
    tool_name: str,
    content: str,
    outcome: str = "success",
    args: dict | None = None,
) -> tuple[str, str]:
    """Resolve icon and summary for a single tool call.

    Returns:
        (icon, summary) tuple. Used by both terminal display and system log.
    """
    if is_tool_error(tool_name, content, outcome):
        return "✗", _fallback_summary(content)
    if tool_name == "save_memory":
        if args and isinstance(args, dict):
            return "✎", summarize_save_memory(args)
        return "✎", summarize_tool(tool_name, content)
    return "⚙", summarize_tool(tool_name, content)


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

        icon, summary = resolve_tool_display(name, content, outcome, args)
        lines.append(f"{icon} {name:<22} {summary}")

    # Agent output
    lines.append(f"\n[bold cyan]Agent:[/]\n{agent_output}")

    # Footer
    lines.append(f"\n[dim]tokens: {tokens_used:,} | budget: {budget_remaining:,} remaining[/]")
    lines.append(f"[dim]{'─' * 44}[/]")

    return "\n".join(lines)


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
