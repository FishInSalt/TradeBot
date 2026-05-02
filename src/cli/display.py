from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from datetime import datetime

from pydantic_ai.messages import (
    ModelRequest,
    ModelResponse,
    TextPart,
    ThinkingPart,
    ToolCallPart,
    ToolReturnPart,
)
from rich.markup import escape
from rich.panel import Panel

from src.cli.session_state import SessionStats
from src.services.metrics import PerformanceMetrics

logger = logging.getLogger(__name__)


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
    "cancel_price_level_alert": "Price level alert cancelled",
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


@dataclass(frozen=True)
class CycleRenderContext:
    """Single-arg context for format_cycle_output(ctx). Constructed by run_agent_cycle
    once per cycle; 3 paths (normal / forensic / retry-exhausted) share this dataclass.

    Field nullability semantics:
        messages / final_text: None for forensic (UsageLimitExceeded — agent.run raised, result=None)
                                and retry-exhausted (3 attempts failed)
        cycle_tokens: 0 for forensic / retry-exhausted (per spec §4.5.3 caveat — not physical 0)
        cache_hit_rate: None triggers footer "N/A (forensic)" / "N/A (aborted)" branch
        forensic_reason: "usage_limit_exceeded" | "aborted: <error class>: <msg[:200]>" | None
    """
    cycle_id: str
    trigger_type: str               # "scheduled" / "conditional" / "alert"
    trigger_context: dict | None    # in-memory dict from _capture_trigger_context
    state_snapshot: dict | None     # in-memory dict from _capture_state_snapshot
    messages: list | None
    final_text: str | None
    cycle_tokens: int
    stats: SessionStats
    cache_hit_rate: float | None
    cycle_started_at: datetime
    cycle_ended_at: datetime
    forensic_reason: str | None


# === R2-8a: Cycle log narrative render helpers (spec §4) ===


def _extract_reasoning_per_response(messages: list) -> list[str | None]:
    """每个 ModelResponse 仅取首个 ThinkingPart 的 content（与 pre-impl smoke baseline 一致）。

    返回 list 长度 = ModelResponse 数；None = 该 Response 无 ThinkingPart。
    与 src.cli.app._extract_thinking_text 行为分离：
    - 渲染层（本 helper）接受 '每 Response 首 ThinkingPart' 限缩 — 时序渲染消费
    - DB 写入层（_extract_thinking_text）保持全收集 — agent_cycles.reasoning 列写入
    spec §4.2.3 drift guard T-DG-1 兜底两 helper 在 smoke baseline 行为等价。

    Placement note: spec §5.3 列在 app.py，本 plan 改放 display.py（消费者所在层）
    避免 display→app 循环 import；helper 唯一使用方是 format_cycle_output。
    """
    out: list[str | None] = []
    for msg in messages:
        if isinstance(msg, ModelResponse):
            thinking_parts = [p for p in msg.parts if isinstance(p, ThinkingPart)]
            if thinking_parts:
                if len(thinking_parts) > 1:
                    # spec §6.3 T-RE-6 + spec §4.2.4: smoke baseline 是每 Response 1 ThinkingPart;
                    # 多 ThinkingPart per Response 出现 → drift signal (R2-8c / N12 议题接管)
                    logger.warning(
                        "ModelResponse has %d ThinkingParts (smoke baseline = 1); "
                        "renderer takes only parts[0] — see spec §4.2.4 / R2-8c",
                        len(thinking_parts),
                    )
                out.append(thinking_parts[0].content)
            else:
                out.append(None)
    return out


_TRIGGER_LINE_PREFIX = "  Trigger    "
_STATE_LINE_PREFIX = "  State      "


def _format_trigger_detail(trigger_type: str, ctx: dict | None) -> str:
    """Format Header 'Trigger    ...' line per spec §4.1.3.

    Returns the entire content after the column prefix; e.g.,
        "ALERT — vol -1.6%/10min fired (BTC 76,225 → 75,448)"
        "SCHEDULED"
    """
    type_upper = trigger_type.upper()
    if not ctx:
        return type_upper

    ctx_type = ctx.get("type")

    if ctx_type == "scheduled_tick":
        # spec §4.1.3 verbatim: "Trigger    SCHEDULED" — 无 em-dash 后缀
        return type_upper

    if ctx_type == "fill":
        # spec §6.1 T-EH-3 partial degradation: 缺 fill_price / 其他字段 → 保留 trigger_reason
        # （TP/SL/liquidation/market_close 区分是 conditional cycle 排查关键信息）
        tr = ctx.get("trigger_reason")
        if tr is None:
            return type_upper  # 连 trigger_reason 都缺 → 全 fallback
        try:
            symbol_short = (ctx.get("symbol") or "").split("/")[0]
            return (
                f"{type_upper} — {tr} {ctx['position_side']} "
                f"{symbol_short} {ctx['amount']} @ ${ctx['fill_price']:,.0f}, "
                f"PnL {ctx['pnl']:+.2f} USDT"
            )
        except (KeyError, TypeError):
            return f"{type_upper} — {tr}"  # spec §6.1 T-EH-3: 部分降级保留 trigger_reason

    if ctx_type == "price_level_alert":
        try:
            symbol_short = (ctx.get("symbol") or "").split("/")[0]
            return (
                f"{type_upper} — {symbol_short} reached "
                f"{ctx['current_price']:,.0f} ({ctx['direction']} "
                f"${ctx['target_price']:,.0f} alert)"
            )
        except (KeyError, TypeError):
            return type_upper

    if ctx_type == "percentage_alert":
        try:
            symbol_short = (ctx.get("symbol") or "").split("/")[0]
            return (
                f"{type_upper} — vol {ctx['change_pct']:+.1f}%/{ctx['window_minutes']}min "
                f"fired ({symbol_short} {ctx['reference_price']:,.0f} → "
                f"{ctx['current_price']:,.0f})"
            )
        except (KeyError, TypeError):
            return type_upper

    # Unknown type (schema drift) — fallback to bare type
    logger.warning(
        "trigger_context.type unknown: %r (keys=%r)",
        ctx_type, list(ctx.keys()) if ctx else None,
    )
    return type_upper


def _format_state_line(state_snapshot: dict | None) -> str:
    """Format Header 'State    ...' line per spec §4.1.4.

    Examples:
        持仓: "Short 0.265 @ $75,350 (5x) | PnL +0.10% | Balance $9,990"
        无仓: "FLAT | Balance $10,000"
        snapshot=None: "[snapshot unavailable]"
    """
    if state_snapshot is None:
        return "[snapshot unavailable]"

    pos = state_snapshot.get("position")
    bal = state_snapshot.get("balance")
    parts: list[str] = []

    if pos is None:
        parts.append("FLAT")
    else:
        try:
            side = pos["side"].capitalize()
            contracts = pos["contracts"]
            entry = pos["entry_price"]
            leverage = pos.get("leverage")
            piece = f"{side} {contracts} @ ${entry:,.0f}"
            if leverage:
                piece += f" ({leverage}x)"
            parts.append(piece)
            pnl_pct = pos.get("pnl_pct")
            if pnl_pct is not None:
                parts.append(f"PnL {pnl_pct:+.2f}%")
        except (KeyError, TypeError):
            parts.append("[position malformed]")

    if bal is not None:
        try:
            parts.append(f"Balance ${bal['total_usdt']:,.0f}")
        except (KeyError, TypeError):
            pass  # 缺字段 → 静默省略 Balance 段（spec §4.1.4）

    return " | ".join(parts) if parts else "[snapshot unavailable]"


def _render_header(
    cycle_id: str,
    trigger_type: str,
    trigger_context: dict | None,
    state_snapshot: dict | None,
    cycle_started_at: datetime,
    stats: SessionStats,
) -> str:
    """Render Header section per spec §4.1.1."""
    short_id = cycle_id[:4]
    start_ts = cycle_started_at.strftime("%H:%M:%S UTC")
    if stats.last_cycle_ended_at is None:
        delta_segment = "(first cycle)"
    else:
        delta_min = int((cycle_started_at - stats.last_cycle_ended_at).total_seconds() / 60)
        delta_segment = f"+{delta_min} min from prev"

    sep_top = "═" * 75
    sep_mid = "─" * 75

    trigger_line = _format_trigger_detail(trigger_type, trigger_context)
    state_line = _format_state_line(state_snapshot)

    return (
        f"{sep_top}\n"
        f"  Cycle {short_id}  •  {start_ts}  •  {delta_segment}\n"
        f"{sep_mid}\n"
        f"{_TRIGGER_LINE_PREFIX}{trigger_line}\n"
        f"{_STATE_LINE_PREFIX}{state_line}\n"
        f"{sep_top}"
    )


def _render_reasoning(thinking_text: str, max_chars: int = 800) -> str:
    """Render Reasoning section per spec §4.2.1-§4.2.2.

    Hard-truncate body to max_chars + ' ... [+N chars]' marker. Body must be
    rich.markup.escape()'d — thinking content is LLM output, attack surface
    of same shape as Decision body.
    """
    total = len(thinking_text)
    if total <= max_chars:
        body = thinking_text
        suffix = ""
    else:
        body = thinking_text[:max_chars]
        remaining = total - max_chars
        suffix = f" ... [+{remaining} chars]"
    indented = "\n".join(f"  {escape(line)}" for line in body.splitlines())
    return f"\n▾ Reasoning ({total} chars total)\n{indented}{suffix}"


def _render_action(
    tool_calls: list,
    returns_lookup: dict,
    cycle_id: str,
) -> str:
    """Render Action section per spec §4.3.

    `tool_calls` is list[ToolCallPart], `returns_lookup` is dict[tool_call_id, ToolReturnPart].
    Tool summary line uses existing resolve_tool_display() (parser layer is R2-8c scope).
    """
    n = len(tool_calls)
    plural = "tool" if n == 1 else "tools"
    lines = [f"\n▾ Action ({n} {plural})"]

    for tcp in tool_calls:
        try:
            args = tcp.args_as_dict()
        except Exception:
            args = None

        ret = returns_lookup.get(tcp.tool_call_id)
        if ret is None:
            logger.warning(
                "tool_call_id mismatch for %s in cycle %s",
                tcp.tool_name, cycle_id,
            )
            line = f"  ⚙ {tcp.tool_name:<22} [no return captured]"
        else:
            content_str = str(ret.content)
            outcome = getattr(ret, "outcome", "success")
            icon, summary = resolve_tool_display(tcp.tool_name, content_str, outcome, args)
            # body escape 防 markup attack（summary 来自 tool return content；
            # 框架 markup icon / column padding 在 prefix 部分不动）
            line = f"  {icon} {tcp.tool_name:<22} {escape(summary)}"
        lines.append(line)

    return "\n".join(lines)


def _render_decision(text: str) -> str:
    """Render Decision section per spec §4.4.1.

    Full markdown body inlined with 2-space indent. Rich markup escape forced —
    LLM output may contain [red]/[bold] literals that would otherwise be parsed
    as Rich markup (attack surface widened by 'full markdown inlined' vs. legacy
    short agent_output).
    """
    indented = "\n".join(f"  {line}" for line in escape(text).splitlines())
    return f"\n▾ Decision\n{indented}"


def _render_footer(ctx: "CycleRenderContext") -> str:
    """Render Footer section per spec §4.5.1.

    Projected stats (含当前 cycle): footer renders BEFORE stats.record_cycle is called,
    so we add cycle_tokens / +1 cycle inline (spec §4.5.3 P1 fix — avoid lifecycle reorder
    to prevent last_cycle_ended_at self-reference).
    """
    sep_mid = "─" * 75
    sep_bot = "═" * 75

    proj_total = ctx.stats.total_tokens + ctx.cycle_tokens
    proj_count = ctx.stats.cycle_count + 1
    proj_avg = proj_total // proj_count if proj_count > 0 else 0
    session_total_k = round(proj_total / 1000)
    session_avg_k = round(proj_avg / 1000)

    # Cache line: forensic / aborted → N/A; normal → percentage
    if ctx.cache_hit_rate is None:
        if ctx.forensic_reason and ctx.forensic_reason.startswith("aborted"):
            cache_line = "Cache    N/A (aborted)"
        else:
            cache_line = "Cache    N/A (forensic)"
    else:
        cache_line = f"Cache    {ctx.cache_hit_rate:.1f}% hit rate"

    duration = (ctx.cycle_ended_at - ctx.cycle_started_at).total_seconds()
    end_ts = ctx.cycle_ended_at.strftime("%H:%M:%S UTC")

    return (
        f"\n{sep_mid}\n"
        f"  Tokens   {ctx.cycle_tokens:,} cycle  |  Session {session_total_k}k "
        f"(avg {session_avg_k}k/cycle, {proj_count} cycles)\n"
        f"  {cache_line}\n"
        f"  Duration {duration:.1f}s  |  Ended {end_ts}\n"
        f"{sep_bot}"
    )


def format_cycle_output(ctx: CycleRenderContext) -> str:
    """Format a complete cycle's output for terminal/session log display.

    spec §5.2 algorithm: 时序遍历 ModelResponse 分组，每段 think→act→think→act→decision
    交织。Forensic / retry-exhausted (messages=None) 短路渲染 Header + Footer + 占位 Decision.
    """
    lines = [_render_header(
        cycle_id=ctx.cycle_id, trigger_type=ctx.trigger_type,
        trigger_context=ctx.trigger_context, state_snapshot=ctx.state_snapshot,
        cycle_started_at=ctx.cycle_started_at, stats=ctx.stats,
    )]

    # === Forensic / retry-exhausted 短路 ===
    if ctx.messages is None:
        if ctx.forensic_reason and ctx.forensic_reason.startswith("aborted"):
            err_part = ctx.forensic_reason[len("aborted: "):]
            placeholder = f"[cycle aborted — 3 attempts failed: {err_part}]"
        else:  # usage_limit_exceeded
            placeholder = "[no decision — usage limit exceeded; partial messages unavailable]"
        # 仅一次 escape (spec §5.2 round-7 校准 — 不 pre-escape err_part 避免双 escape
        # 显示反斜杠 \[red]boom\[/])
        lines.append(f"\n▾ Decision\n  {escape(placeholder)}")
        lines.append(_render_footer(ctx))
        return "\n".join(lines)

    # === Build tool_call_id → ToolReturnPart map ===
    tool_returns_lookup: dict = {}
    for msg in ctx.messages:
        if isinstance(msg, ModelRequest):
            for part in msg.parts:
                if isinstance(part, ToolReturnPart):
                    tool_returns_lookup[part.tool_call_id] = part

    # === ②③ 时序段 ===
    response_msgs = [m for m in ctx.messages if isinstance(m, ModelResponse)]

    # spec §4.2.3: 渲染层 thinking 提取 SoT 由 _extract_reasoning_per_response 集中
    reasoning_per_response = _extract_reasoning_per_response(ctx.messages)

    for i, mr in enumerate(response_msgs):
        thinking = reasoning_per_response[i]
        tool_calls = [p for p in mr.parts if isinstance(p, ToolCallPart)]

        if thinking:
            lines.append(_render_reasoning(thinking))

        if tool_calls:
            lines.append(_render_action(tool_calls, tool_returns_lookup, ctx.cycle_id))

    # === Decision 段 ===
    # spec §4.4.2: 数据源 = ctx.final_text (= result.output) — 单源真相，
    # 不从 messages 重新提取 TextPart (避免双源真相 + ctx.final_text 死字段)
    if ctx.final_text:
        lines.append(_render_decision(ctx.final_text))
    elif ctx.final_text == "":
        lines.append("\n▾ Decision\n  [empty decision text]")
    else:  # None
        lines.append("\n▾ Decision\n  [no decision text]")

    lines.append(_render_footer(ctx))
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
