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
    """Fallback: first line or first max_len chars."""
    first_line = str(content).split("\n")[0].strip()
    if len(first_line) <= max_len:
        return first_line
    return first_line[:max_len] + "..."


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
