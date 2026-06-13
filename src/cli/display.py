from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass
from datetime import datetime

from pydantic_ai.messages import (
    INVALID_JSON_KEY,
    ModelRequest,
    ModelResponse,
    RetryPromptPart,
    TextPart,
    ThinkingPart,
    ToolCallPart,
    ToolReturnPart,
)
from rich.markup import escape
from rich.panel import Panel

from src.cli.session_state import SessionStats
from src.services.metrics import PerformanceMetrics
from src.services.midcycle_injector import INJECTION_HEADER_PREFIX

logger = logging.getLogger(__name__)


def format_metrics(metrics: PerformanceMetrics) -> str:
    pos = metrics.current_position.upper() if metrics.current_position != "none" else "FLAT"
    return (
        f"Return: {metrics.total_return_pct:+.2f}% ({metrics.total_pnl:+.2f} USDT)\n"
        f"Win Rate: {metrics.win_rate * 100:.1f}% ({metrics.winning_trades}W / {metrics.losing_trades}L"
        f"{f' / {metrics.break_even_trades}B' if metrics.break_even_trades > 0 else ''})\n"
        f"Max Drawdown: {('0.00%' if metrics.max_drawdown_pct == 0 else f'-{metrics.max_drawdown_pct:.2f}%')} (net equity)\n"
        f"Profit Factor: {'N/A (no losses)' if metrics.profit_factor is None else f'{metrics.profit_factor:.2f}'}\n"
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
    m = re.search(r"(?:Order submitted|Filled):\s*(\w+)\s+([\d.]+)\s*@\s*~?([\d.]+),\s*(\d+)x", content)
    if m:
        return f"{m.group(1)} {m.group(2)} @ ~${float(m.group(3)):,.0f}, {m.group(4)}x"
    return _fallback_summary(content)


def _summarize_close_position(content: str) -> str:
    # "No positions to close." is a business rejection — is_tool_error catches it
    # before this parser runs, so no need to handle it here.
    # Async path: "Orders submitted: close N position(s)" / sync path: "Closed N position(s)".
    m = re.search(r"[Cc]lose(?:d)?\s+(\d+)\s+position", content)
    if m:
        return f"Close {m.group(1)} position(s)"
    return _fallback_summary(content)


def _summarize_set_stop_loss(content: str) -> str:
    # First try dual-value shape (post iter-session-log-args-visibility update path):
    #   "Stop loss set at 77100.00 → 76950.00 (+0.05% from ...) | ..."
    # group(1) = old, group(2) = new, group(3) = distance
    m = re.search(r"Stop loss set at\s+([\d.]+)\s*→\s*([\d.]+)\s*\(([^)]+)\)", content)
    if m:
        new_price = float(m.group(2))
        return f"SL @ ${new_price:,.0f} ({m.group(3).split('from')[0].strip()})"
    # Fallback to single-value shape (first-set path / pre-iter return):
    m1 = re.search(r"Stop loss set at\s+([\d.]+)\s*\(([^)]+)\)", content)
    if m1:
        return f"SL @ ${float(m1.group(1)):,.0f} ({m1.group(2).split('from')[0].strip()})"
    m2 = re.search(r"Stop loss set at\s+([\d.]+)", content)
    if m2:
        return f"SL @ ${float(m2.group(1)):,.0f}"
    return _fallback_summary(content)


def _summarize_set_take_profit(content: str) -> str:
    # Dual-value shape first (update path)
    m = re.search(r"Take profit set at\s+([\d.]+)\s*→\s*([\d.]+)\s*\(([^)]+)\)", content)
    if m:
        new_price = float(m.group(2))
        return f"TP @ ${new_price:,.0f} ({m.group(3).split('from')[0].strip()})"
    # Fallback to single-value
    m1 = re.search(r"Take profit set at\s+([\d.]+)\s*\(([^)]+)\)", content)
    if m1:
        return f"TP @ ${float(m1.group(1)):,.0f} ({m1.group(2).split('from')[0].strip()})"
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


def _summarize_set_price_volatility_alert(content: str) -> str:
    m = re.search(r"threshold=([\d.]+)%.*window=(\d+)min", content)
    if m:
        return f"threshold={m.group(1)}%, window={m.group(2)}min"
    return _fallback_summary(content)


def _summarize_add_price_level_alert(content: str) -> str:
    m = re.search(r"(above|below)\s+([\d.]+)", content)
    if m:
        return f"{m.group(1)} ${float(m.group(2)):,.0f}"
    return _fallback_summary(content)


def _summarize_update_price_level_alert(content: str) -> str:
    # Matches success-return shape (post iter-tool-opt-alert-age):
    #   "Price level alert updated (id=AAAA): above 82100.00 → 82500.00 — \"reasoning\""
    m = re.search(r"(above|below)\s+([\d.]+)\s*→\s*([\d.]+)", content)
    if m:
        return (
            f"{m.group(1)} ${float(m.group(2)):,.0f} → "
            f"${float(m.group(3)):,.0f}"
        )
    return _fallback_summary(content)


def _summarize_set_next_wake(content: str) -> str:
    m = re.search(r"(\d+)\s*min", content)
    if m:
        return f"{m.group(1)}min"
    return _fallback_summary(content)


def _summarize_set_next_wake_at(content: str) -> str:
    """Parse 'Next wake set for YYYY-MM-DD HH:MM UTC (in N min)'."""
    m = re.search(r"\(in (\d+)\s*min\)", content)
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
    "set_price_volatility_alert": _summarize_set_price_volatility_alert,
    "add_price_level_alert": _summarize_add_price_level_alert,
    "update_price_level_alert": _summarize_update_price_level_alert,
    "set_next_wake": _summarize_set_next_wake,
    "set_next_wake_at": _summarize_set_next_wake_at,
}

# Success prefix whitelist for execution tools (business rejection detection)
_EXECUTION_SUCCESS_PREFIXES = {
    "open_position": ("Order submitted:", "Filled:"),
    "close_position": ("Orders submitted:", "Closed"),
    "set_stop_loss": "Stop loss set at",
    "set_take_profit": "Take profit set at",
    "adjust_leverage": "Leverage adjusted to",
    "place_limit_order": "Limit order placed:",
    "cancel_order": "Order cancelled:",
    "set_price_volatility_alert": (
        "Price volatility alert set:",        # first-time create
        "Price volatility alert replaced:",   # replace existing
    ),
    "add_price_level_alert": ("Price level alert set:", "Alert set"),
    "cancel_price_level_alert": (
        "Price level alert cancelled",   # cancel success (real removal)
        "Alert ",                         # cancel idempotent ok ("Alert {id} no longer active ...")
    ),
    "update_price_level_alert": "Price level alert updated",
    "set_next_wake": "Next wake set to",
    "set_next_wake_at": "Next wake set for",
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

# === System log perception parsers (R2-8c review P2-1 namespace narrowing) ===
#
# 8 parser functions kept post-R2-8c — consumed ONLY by:
#   - resolve_tool_display() / summarize_tool() (this file) — system log INFO 摘要
#     (cli/app.py:332 `icon, summary = resolve_tool_display(...)` → line 335
#     `logger.info(f"  {icon} {part.tool_name}: {summary}")`); 注意 cli/app.py:337
#     的 `logger.debug return={content_str[:500]}` 是独立 raw dump，不走 parser chain
#   - scripts/tool_call_summary.py (offline analysis 脚本)
#
# NOT consumed by _render_tool_body (multi-line render) — that path
# bypasses parser layer entirely and reads raw section content via _parse_sections.
#
# 重构这些 parser 应保持向后兼容（system log 形态不破），不影响 R2-8c display 路径。
_SYSTEM_LOG_PERCEPTION_PARSERS = {
    "get_market_data": _summarize_get_market_data,
    "get_position": _summarize_get_position,
    "get_account_balance": _summarize_get_account_balance,
    "get_open_orders": _summarize_get_open_orders,
    "get_trade_journal": _summarize_get_trade_journal,
    "get_memories": _summarize_get_memories,
    "get_active_alerts": _summarize_get_active_alerts,
    "get_performance": _summarize_get_performance,
}


# === R2-8c: Section parsing & clipping helpers (spec §4.3) ===


@dataclass(frozen=True)
class Section:
    """Parsed tool output section (spec §4.3.1)."""
    header: str | None  # None = unnamed (fallback for tool output without `=== Section ===`)
    body: tuple[str, ...]  # immutable for frozen dataclass equality / set membership


_SECTION_HEADER_RE = re.compile(r"^=== (.+) ===$")


def _parse_sections(content: str) -> list[Section]:
    """Parse tool content into sections by '=== {name} ===' headers (spec §4.3.1).

    Algorithm:
      1. Split content by '\n'
      2. Lines matching r'^=== (.+) ===$' are section starts
      3. Lines until next header form the section body
      4. Strip blank lines at start/end of each body
      5. No header in entire content → [Section(header=None, body=lines stripped)]
      6. Empty content → [Section(header=None, body=())]
    """
    if not content:
        return [Section(header=None, body=())]

    lines = content.split("\n")
    sections: list[tuple[str | None, list[str]]] = []
    current_header: str | None = None
    current_body: list[str] = []

    for line in lines:
        m = _SECTION_HEADER_RE.match(line)
        if m:
            # flush previous
            sections.append((current_header, current_body))
            current_header = m.group(1)
            current_body = []
        else:
            current_body.append(line)
    sections.append((current_header, current_body))

    # First entry is "before any header" — drop only when it has no header AND empty body
    # (otherwise it's a legitimate fallback section per T-PARSE-2)
    if sections and sections[0][0] is None and not _strip_blanks(sections[0][1]):
        if len(sections) > 1:
            sections = sections[1:]

    return [Section(header=h, body=tuple(_strip_blanks(b))) for h, b in sections]


def _strip_blanks(lines: list[str]) -> list[str]:
    """Remove leading + trailing blank lines (preserve internal blanks)."""
    start = 0
    end = len(lines)
    while start < end and lines[start].strip() == "":
        start += 1
    while end > start and lines[end - 1].strip() == "":
        end -= 1
    return lines[start:end]


# === iter-session-log-structured-clip: by-anchor heuristic ===
# Anchor row 识别正则。
# Pattern 解释:
#   ^\[            行首立即是 `[`（无 leading whitespace）
#   (?!\.\.\.)     负向 lookahead 排除 [... omitted ...] / [...]
#   [^\]\s]        `[` 后第 1 字符不是 `]` 也不是 whitespace（确保 [<word>] 有内容）
_ANCHOR_RE = re.compile(r'^\[(?!\.\.\.)[^\]\s]')


def _is_anchor(line: str) -> bool:
    """Return True iff line starts with [<word>] prefix (not [... omitted ...]).

    Used by _clip_body to detect structured-row mode (≥ 2 anchor rows).
    """
    return bool(_ANCHOR_RE.match(line))


def _group_by_anchor(
    body: tuple[str, ...] | list[str],
) -> list[tuple[str, list[str]]]:
    """Split body into groups: each anchor line starts a new group;
    non-anchor lines (blanks + plain text + continuation) attach to the
    current group's continuation list.

    Assumes body has had leading/trailing blanks stripped upstream by
    `_strip_blanks` (display.py:433-441). I.e. body[0] is non-blank,
    avoiding undefined "blank attaches to previous group" at body start.

    Prelude rule (R4): body lines before the first anchor each form a
    single-row group (head = the line itself, continuation = []).
    A blank that appears between prelude lines and the first anchor
    attaches to the LAST prelude group's continuation (per R3).

    Returns list of (head_line, [continuation_lines]) tuples.
    - In anchor-group: head is the anchor line.
    - In prelude single-row group: head is the prelude line itself
      (not a true anchor — semantically "group head line").
    """
    groups: list[tuple[str, list[str]]] = []
    in_anchor_zone = False
    for line in body:
        if _is_anchor(line):
            in_anchor_zone = True
            groups.append((line, []))
        else:
            if in_anchor_zone and groups:
                # Inside anchor zone: attach to current anchor group's continuation
                groups[-1][1].append(line)
            else:
                # Prelude zone (no anchor seen yet): each non-anchor line is its own
                # 1-row group, except blanks which attach to the last prelude group.
                if groups and not line:
                    groups[-1][1].append(line)
                else:
                    groups.append((line, []))
    return groups


def _clip_body(
    body: tuple[str, ...] | list[str],
    n: int = 10,
    group_cap: int = 12,
) -> tuple[str, ...]:
    """Three-tier clip dispatch (per spec §2.3 / §4.3):

    1. structured-row mode  (anchor_count >= 2)
       → group-level handling: len(groups) <= group_cap 全展，
         otherwise _flatten(head[:3]) + "[... N groups omitted ...]" + _flatten(tail[-3:])

    2. list-like mode       (len(body) >= n, anchor_count < 2)
       → existing D4 row-clip unchanged: (body[0], body[1],
         "[... N rows omitted ...]", body[-2], body[-1])

    3. short mode           (len(body) < n, anchor_count < 2)
       → keep all (unchanged)

    Symmetric head=3 / tail=3 design (structured-row cap-exceeded):
    Renderer does not pre-assume per-tool semantic priority. Class A tools
    have different internal ordering (news newest-first; trade_journal
    oldest-first via reversed(actions); macro_calendar upcoming chronological).
    Symmetric preserves both ends regardless of tool semantics.

    Omission marker forms (semantically distinct, grep should differentiate):
    - list-like:    "[... N rows omitted ...]"   (rows = line count)
    - cap-exceeded: "[... N groups omitted ...]" (groups = group count)
    """
    # Branch detection
    groups = _group_by_anchor(body)
    anchor_count = sum(1 for g in groups if _is_anchor(g[0]))

    if anchor_count >= 2:
        # Branch 1: structured-row mode
        if len(groups) <= group_cap:
            # Full expansion
            return tuple(_flatten_groups(groups))
        else:
            # cap-exceeded: head[:3] + omitted + tail[-3:]
            omitted_count = len(groups) - 6
            head_lines = _flatten_groups(groups[:3])
            tail_lines = _flatten_groups(groups[-3:])
            return tuple(head_lines + [f"[... {omitted_count} groups omitted ...]"] + tail_lines)

    if len(body) >= n:
        # Branch 2: list-like mode (D4 unchanged)
        return (
            body[0], body[1],
            f"[... {len(body) - 4} rows omitted ...]",
            body[-2], body[-1],
        )

    # Branch 3: short mode (unchanged)
    return tuple(body)


def _flatten_groups(groups: list[tuple[str, list[str]]]) -> list[str]:
    """Flatten groups → flat line list: [head, *continuation, head, *continuation, ...]"""
    out: list[str] = []
    for head, continuation in groups:
        out.append(head)
        out.extend(continuation)
    return out


# === iter-taker-flow-render: full-keep sections (session-log 渲染豁免) ===
# 这些 section 的 body 整段保留、不走 _clip_body 折叠：核心小表格的逐 bar
# 序列被 list-like 行折叠后会失去意义（reviewer 无法复现 agent 逐 bar 引用所
# 依据的数据）。匹配 _parse_sections 出的 section.header 文本前缀 —— by-content
# （契合本文件既有 by-content sectioned/plain dispatch 哲学），不靠 tool_name
# frozenset。get_taker_flow header = "Taker Flow (BTC/USDT:USDT · 5m bars · @ …)"；
# GMD K-line header = "Recent Closed Candles (…)" 前缀不匹配 → 不受影响、仍折叠。
# "NEW EVENTS TRIGGERED" — mid-cycle 注入事件块（iter-midcycle-event-injection §8）：
# 事件行 = forensic 主信号，折叠即失去复现价值；header 前缀与 midcycle_injector
# INJECTION_HEADER_PREFIX 逐字同源。
_FULL_KEEP_SECTION_PREFIXES: tuple[str, ...] = ("Taker Flow", "NEW EVENTS TRIGGERED")


def _is_full_keep_section(header: str | None) -> bool:
    """Return True iff this section should bypass _clip_body folding entirely.

    header is the _parse_sections-extracted section header text (None for an
    unnamed fallback section). Matched by prefix so the volatile suffix
    (symbol · period · @ timestamp) does not affect the decision.
    """
    return header is not None and header.startswith(_FULL_KEEP_SECTION_PREFIXES)


def _render_tool_body(
    tool_name: str,
    content: str,
    *,
    head_icon: str = "⚙",
    head_args: str | None = None,
) -> str:
    """Multi-line section render for tool body (by-content sectioned-or-plain).

    Used by unified _render_action dispatch (spec §3.1 / §3.3). Body
    dispatch is by content (presence of `=== ... ===` markers), not by
    tool class — _render_tool_body works for any tool's return.

    head_args: function-syntax args string (e.g. 'tool(k=v)'). If None,
    falls back to bare tool_name() (used only by orphan / pre-refactor
    call sites; new dispatch always passes head_args).

    Output format:
      "  {icon} {head_args}\n"               # head (function syntax)
      "    === {section.header} ===\n"       # (if present)
      "    {body line 1}\n"
      ...
      "\n"                                   # blank between sections
      "    === {next section.header} ===\n"
      ...
    """
    head = head_args if head_args is not None else f"{tool_name}()"
    # escape head: when head_args is supplied by _render_action, it contains
    # LLM-written reasoning that may include Rich markup ([bold] / [red] etc.)
    lines = [f"  {head_icon} {escape(head)}"]
    lines.extend(_render_sections(content))
    return "\n".join(lines)


def _render_sections(content: str) -> list[str]:
    """Render parsed sections → indented display lines (spec §4.3 body render).

    Shared by _render_tool_body (tool output) and _render_action's mid-cycle
    injection append path so section-header / full-keep / clip / indent treatment is
    single-sourced (iter-session-log-render-fidelity Issue 1). Blank line between
    adjacent sections; full-keep sections (e.g. NEW EVENTS TRIGGERED) bypass _clip_body.
    """
    lines: list[str] = []
    for i, section in enumerate(_parse_sections(content)):
        if i > 0:
            lines.append("")
        if section.header is not None:
            lines.append(f"    === {escape(section.header)} ===")
        clipped = (
            section.body
            if _is_full_keep_section(section.header)
            else _clip_body(section.body)
        )
        for row in clipped:
            lines.append("" if row == "" else f"    {escape(row)}")
    return lines


def _split_injection_block(content: str) -> tuple[str, str | None]:
    """Split a tool return into (tool_result, mid-cycle injection block | None).

    MidCycleEventInjector appends `\\n\\n=== {INJECTION_HEADER_PREFIX} (...) ===\\n...`
    to the tool return (midcycle_injector.py:53). Splitting it off before _render_action's
    branch dispatch lets the tool result render on its normal path (error single-line OR
    happy multi-line) while the injection always renders as its own full-keep section —
    decoupling injection render from the tool's success/error branch, which was the root
    cause of the error-path collapse (iter-session-log-render-fidelity Issue 1). No anchor
    → (content, None), behaviourally identical to the pre-iter pass-through.
    """
    anchor = "\n\n=== " + INJECTION_HEADER_PREFIX
    idx = content.find(anchor)
    if idx == -1:
        return content, None
    return content[:idx], content[idx:].lstrip("\n")


# === R2-8c: dispatch sets (spec §4.4) ===

_PERCEPTION_TOOL_NAMES: frozenset[str] = frozenset({
    # Tier-1 high frequency (B2 ≥ 70%)
    "get_market_data",
    "get_higher_timeframe_view",
    "get_multi_timeframe_snapshot",
    "get_price_pivots",
    "get_recent_trades",
    "get_taker_flow",
    "get_derivatives_data",
    # Mid (B2 50-70%)
    "get_market_news",
    "get_order_book",
    # Long-tail
    "get_macro_context",
    "get_position",
    "get_account_balance",
    "get_open_orders",
    "get_trade_journal",
    "get_active_alerts",
    "get_performance",
    "get_exchange_announcements",
    "get_macro_calendar",
    "get_etf_flows",
    "get_stablecoin_supply",
})

# Legacy alias retained for drift-guard partition test (test_dg_2_*).
# Post-iter-session-log-args-visibility: dispatch is by-content, this set
# no longer drives sectioned/plain rendering. Field kept (not deleted)
# because partition test asserts frozenset coverage of all registered tools.
_SECTIONED_PERCEPTION_TOOL_NAMES: frozenset[str] = _PERCEPTION_TOOL_NAMES

_EXECUTION_TOOL_NAMES: frozenset[str] = frozenset({
    "open_position",
    "close_position",
    "set_stop_loss",
    "set_take_profit",
    "adjust_leverage",
    "place_limit_order",
    "cancel_order",
    "set_price_volatility_alert",
    "cancel_price_volatility_alert",
    "add_price_level_alert",
    "cancel_price_level_alert",
    "update_price_level_alert",
    "set_next_wake",
    "set_next_wake_at",
})


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
    # Retired tool: iter-w2r3-memory-disable — dispatch branch kept for revert path.
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
        cache_hit_rate: percentage on 0-100 scale (e.g. 92.0 = 92%), NOT 0-1 fraction.
                        Caller (app.py:301) computes `cache_hit / input_total * 100`;
                        footer formats with `:.1f` directly. None triggers footer "N/A
                        (forensic)" / "N/A (aborted)" branch.
        forensic_reason: "usage_limit_exceeded" | "aborted: <error class>: <msg[:200]>" | None
    """
    cycle_id: str
    trigger_type: str               # "scheduled" / "conditional" / "alert"
    trigger_context: list[dict | None] | dict | None  # batch list (spec 2026-06-08); legacy single dict tolerated
    state_snapshot: dict | None     # in-memory dict from _capture_state_snapshot
    messages: list | None
    final_text: str | None
    cycle_tokens: int
    stats: SessionStats
    cache_hit_rate: float | None    # 0-100 scale percentage (NOT 0-1 fraction); see class docstring
    cycle_started_at: datetime
    cycle_ended_at: datetime
    forensic_reason: str | None
    user_prompt_snapshot: str | None = None  # spec 2026-05-31: Context 段唯一数据源；None → 整段省略


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


def _format_trigger_detail(trigger_type: str, ctx) -> str:
    """Format Header 'Trigger    ...' line (spec 2026-06-08 §3): type + count only.

    Per-event detail (fill PnL / alert summary) lives in the ▾ Context section now,
    not the Header (the Header can't fit N events; detail is preserved losslessly in
    Context). Accepts the new batch list `list[dict|None]`, a legacy single dict, or None.

    Returns:
        N<=1 (incl. legacy single-object / None) → bare type, e.g. "ALERT" / "SCHEDULED".
        N>1 → "<TYPE> +<N-1> (<breakdown>)", e.g. "CONDITIONAL +2 (1 fill, 2 alerts)".
    """
    type_upper = trigger_type.upper()
    if ctx is None:
        events = []
    elif isinstance(ctx, dict):
        events = [ctx]                       # legacy single-object row
    else:
        events = list(ctx)
    n = len(events)
    if n <= 1:
        return type_upper
    # None entries (per-event capture failure) are counted in n / the +{n-1} total but
    # not in the type breakdown — the breakdown reflects recognized types only.
    n_fill = sum(1 for e in events if isinstance(e, dict) and e.get("type") == "fill")
    n_alert = sum(
        1 for e in events
        if isinstance(e, dict) and e.get("type") in ("price_level_alert", "percentage_alert")
    )
    parts: list[str] = []
    if n_fill:
        parts.append(f"{n_fill} fill{'s' if n_fill > 1 else ''}")
    if n_alert:
        parts.append(f"{n_alert} alert{'s' if n_alert > 1 else ''}")
    breakdown = ", ".join(parts) if parts else f"{n} events"
    return f"{type_upper} +{n - 1} ({breakdown})"


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
            pnl_pct = pos.get("pnl_pct_of_notional")
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
    trigger_context: list[dict | None] | dict | None,
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

    try:
        trigger_line = _format_trigger_detail(trigger_type, trigger_context)
    except Exception:
        # _render_header is NOT inside a try in format_cycle_output; a raise here
        # propagates out of the whole renderer to on_tick's except → misleading
        # "Agent cycle failed" even though the cycle already committed. Degrade like
        # _render_context does (spec 2026-06-08 §3).
        logger.warning("Trigger header render failed; falling back to bare type", exc_info=True)
        trigger_line = trigger_type.upper()
    state_line = _format_state_line(state_snapshot)

    return (
        f"{sep_top}\n"
        f"  Cycle {short_id}  •  {start_ts}  •  {delta_segment}\n"
        f"{sep_mid}\n"
        f"{_TRIGGER_LINE_PREFIX}{trigger_line}\n"
        f"{_STATE_LINE_PREFIX}{state_line}\n"
        f"{sep_top}"
    )


def _render_reasoning(thinking_text: str, max_chars: int = 15000) -> str:
    """Render Reasoning section per spec §4.2.1-§4.2.2.

    R2-8d D6: 2000 → 15000 (sim #7 max 9492, median ~6500;
    2000 cap 实测截断率 ~91% 远超 R2-8c 预测 25%; 15000 覆盖 max + 58% 缓冲
    给 W2 长尾留充分余量, 免后续 N12c hot-fix 调参).
    R2-8c D10 (800 → 2000) 历史: smoke #6 B3 截断率 47/80 = 58.8% @ 800.

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


# === Context section (carried into cycle) — spec 2026-05-31 ===
#
# 整段从已存的 user_prompt_snapshot 派生（零新 DB 字段 / 渲染层零查询 / 零 replay）。
# format_cycle_output 在 header 后、forensic 短路前调用 _render_context（fail-isolated）。

# 与 app._render_recent_summaries 的 header_top 逐字一致（格式耦合，由 Task 9 round-trip drift-guard 兜底）
_SUMMARIES_MARKER = "Your prior cycle summaries (most recent N=3, from this session):"

# conditional/alert 唤醒切片里的变量事件文本前缀，须与 app.py 三处生产端逐字一致：
# IMPORTANT EVENT（conditional fill）/ PRICE LEVEL ALERT（_format_price_level_alert_trigger）
# / PRICE VOLATILITY ALERT（percentage alert）。改任一端必同步另一端。
# 向后兼容（wontfix, by-design）：改名只更新生产端 + 本切分端，已落库的旧
# user_prompt_snapshot 冻结改名前字面量（如 PRICE ALERT / PRICE LEVEL）。CLI 回放
# （_render_context）旧 conditional/alert session 时新前缀匹配不到 → event_lines 空
# → Woke-by 事件行整段省略。接受此退化：forensic 主路径走 raw DB/log grep（旧字面量
# 仍在原始数据、不受影响），仅 CLI display 回放改名前 alert session 受影响、属低频。
# 与 _extract_scheduled_wake_suffix 的 legacy-clause 兼容做法有意不对称（scheduled 后缀
# 缺失仍渲 label；alert 前缀不匹配则整行省略）。
_EVENT_PREFIXES = ("IMPORTANT EVENT", "PRICE VOLATILITY ALERT", "PRICE LEVEL ALERT")

# 字段 marker 的 4 种 cosmetic 写法（均行首）：**(N) Field / (N) **Field / (N) Field / ### (N) Field
_FIELD_MARKER_RE = re.compile(r"(?m)^(?:#{1,6}\s*)?\**\s*\(([1-5])\)\s*")

# 字段名 header（persona.py:116/126 模板 `(N) Name — content` —— marker 后紧跟字段名）。
# _FIELD_MARKER_RE 只吃到 `(N) `，字段名仍留在 value 里（fields[1]="Stance — ..."）；
# render 须先剥它再 prepend 归一标签，否则双标签 `Stance — Stance — ...`。
# 锚定到 5 个已知字段名才剥（不用泛化 `^.{0,40}[sep]`）：泛化形态会把无字段名、
# 但内容早期含 — / : 的退化写法（如 `(N) flat — watching` / `(N) conviction: low`）
# 误当 label 静默吃掉前半句——对 forensic log 是最坏失败模式。锚定后任何输入都不会吃内容，
# 未知字段名 fall-through 不剥（→ 可见双标签而非静默丢失，顺带成 persona 字段名 drift-guard）。
_FIELD_LABEL_RE = re.compile(
    r"^(?:Stance|Active commitments|This cycle delta"
    r"|Thesis(?:\s*&\s*invalidation)?|Watch(?:\s*list)?)\s*[—–:]\s*",
    re.IGNORECASE,
)

# 注入块头两变体：valid `[cycle <id8> · <trig> · <utc> (<ago>) · <N> words]`
# / NULL-forensic `[cycle <id8> · <trig> · <utc> (<ago>)]`（无 `· N words`）。
# 捕获组 1 = id（8 hex），组 2 = ago 文本（去括号）。
_BLOCK_HEADER_RE = re.compile(
    r"\[cycle\s+([0-9a-fA-F]+)\s+·\s+[^·]+·\s+[^(]+\(([^)]+)\)"
    r"(?:\s+·\s+\d+\s+words)?\]"
)

# 长度安全网（spec §3.6）—— 实测均不触发，仅防病态长文 / 未来新写法落兜底
_CONTEXT_THESIS_CAP = 1500     # 最近一条 Thesis（实测 ④ max 1185）
_CONTEXT_EVENT_CAP = 500       # Woke-by 事件行（实测最长事件行 ~150c）
_CONTEXT_FALLBACK_CAP = 500    # 兜底 whole-block（尤其 earlier-slot，防整条长文跨 cycle 重复）


def _split_wake_prompt(snapshot: str) -> tuple[str, str]:
    """Split user_prompt_snapshot at the injected-summaries marker.

    Returns (wake_half, summaries_half). 标记缺失（首 cycle 无 prior）→
    summaries_half 为 ""。标记行本身被丢弃（不进任一半）。
    """
    idx = snapshot.find(_SUMMARIES_MARKER)
    if idx == -1:
        return snapshot, ""
    return snapshot[:idx], snapshot[idx + len(_SUMMARIES_MARKER):]


def _truncate_with_marker(text: str, max_chars: int) -> str:
    """Hard-truncate to max_chars + ASCII ' ... [+N chars]'（与 _render_reasoning 一致）。"""
    if len(text) <= max_chars:
        return text
    return text[:max_chars] + f" ... [+{len(text) - max_chars} chars]"


def _extract_event_lines(wake_half: str, trigger_type: str) -> list[str]:
    """Extract the verbatim variable event text(s) from the wake prompt (spec 2026-06-08 §3).

    scheduled → [] (no variable event line; pure boilerplate). conditional/alert →
    split `wake_half` at each known prefix (IMPORTANT EVENT / PRICE VOLATILITY ALERT / PRICE LEVEL ALERT)
    into one segment per event, preserving alert id / reasoning / fee / PnL / age clause,
    collapsing whitespace and truncating **each event individually** to `_CONTEXT_EVENT_CAP`.
    No prefix found → [].
    """
    if trigger_type == "scheduled":
        return []
    pattern = re.compile("|".join(re.escape("\n\n" + p) for p in _EVENT_PREFIXES))
    positions = [m.start() for m in pattern.finditer(wake_half)]
    if not positions:
        return []
    out: list[str] = []
    for i, start in enumerate(positions):
        end = positions[i + 1] if i + 1 < len(positions) else len(wake_half)
        seg = re.sub(r"\s+", " ", wake_half[start:end]).strip()
        out.append(_truncate_with_marker(seg, _CONTEXT_EVENT_CAP))
    return out


def _extract_scheduled_wake_suffix(wake_half: str) -> str:
    """Pull the ' — fired {UTC} ({age})' clause off the scheduled wake header line.

    scheduled has no variable event line, but its header carries the wake-time clause
    (spec 2026-06-08). Returns "" for legacy snapshots without the clause so the Context
    section keeps rendering the bare `Woke by — SCHEDULED` label (backward-compatible).
    """
    first_line = wake_half.split("\n", 1)[0]
    marker = "scheduled trigger — "
    idx = first_line.find(marker)
    if idx == -1:
        return ""
    clause = first_line[idx + len(marker):].strip()
    if clause.endswith("."):
        clause = clause[:-1]
    return f" — {clause}" if clause else ""


def _extract_scheduled_wake_context(wake_half: str) -> str:
    """Pull the agent's set_next_wake reasoning off the SCHEDULED WAKE CONTEXT block.

    event_render._render_event_block injects `\\n\\nSCHEDULED WAKE CONTEXT (you set last
    cycle): {reasoning}` into the scheduled wake prompt (spec 2026-06-11). The Context
    section echoes it inline on the Woke-by line (parenthetical before the time clause) so
    the reader sees WHY this cycle woke now — symmetry with the alert event line, which
    likewise embeds the agent's set-time reasoning inline.
    The block is the last segment of wake_half (summaries already split off), so the
    reasoning runs from the marker to the next blank line. Returns "" when the block is
    absent (agent set no reasoning / legacy snapshot).
    """
    marker = "SCHEDULED WAKE CONTEXT (you set last cycle): "
    idx = wake_half.find(marker)
    if idx == -1:
        return ""
    rest = wake_half[idx + len(marker):]
    # Take the whole remainder. The SCHEDULED WAKE CONTEXT block is always the LAST
    # segment of wake_half (summaries already split off by _split_wake_prompt), so a
    # `\n\n` here is an internal blank line of the agent's multi-paragraph reasoning, not
    # a section boundary — the prior `.split("\n\n", 1)[0]` truncated multi-paragraph
    # reasons (iter-session-log-render-fidelity Issue 2). Collapse internal whitespace
    # (incl. stray newlines in the agent's zero-cleaned free-text) so the inline Woke-by
    # line stays single-line — same treatment as the alert event line (_extract_event_lines).
    return re.sub(r"\s+", " ", rest).strip()


def _clean_field(text: str) -> str:
    """Strip markdown bold + collapse internal whitespace（log 渲 plain text）。"""
    return re.sub(r"\s+", " ", text.replace("**", "")).strip()


def _strip_field_label(text: str) -> str:
    """Remove a leading '<FieldName> — ' header（persona `(N) Name — content`）。

    _extract_summary_fields 切片只去 `(N) ` marker，字段名（Stance / Thesis &
    invalidation …）仍留在 value 开头。render 须先剥它再 prepend 归一标签
    `Stance —` / `Thesis —`（④ 缩写归一同时落地），否则双标签
    `Stance — Stance — ...`。仅当 value 以 5 个已知字段名之一开头时才剥；其余
    （未知字段名 / 无字段名的退化写法）原样返回——保证绝不吃内容（含早期 colon / em-dash
    的交易笔记，如 `conviction: low` / `flat — watching`）。
    """
    return _FIELD_LABEL_RE.sub("", text, count=1)


def _extract_summary_fields(body: str) -> dict[int, str]:
    """Position-slice a summary body into {field_num: raw_content}（spec §3.4）。

    容忍 4 种 cosmetic marker 写法（_FIELD_MARKER_RE）。按相邻 marker 位置切片，
    每段以"下一个 marker 或 block 末"定界（故仅 ①④ 在的退化情形 ④ 自动以末尾兜底）。
    切片保留字段名（`Stance — ...`）—— render 经 _strip_field_label 去名后再 prepend
    归一标签。无任何 (N) marker（terse / forensic system body）→ {}（caller 走整条兜底）。
    """
    marks = [(m.start(), int(m.group(1)), m.end()) for m in _FIELD_MARKER_RE.finditer(body)]
    if not marks:
        return {}
    out: dict[int, str] = {}
    for i, (_, num, end) in enumerate(marks):
        nxt = marks[i + 1][0] if i + 1 < len(marks) else len(body)
        out[num] = body[end:nxt].strip()
    return out


def _parse_injected_summaries(summaries_half: str) -> list[tuple[str, str, str]]:
    """Slice the injected block into per-cycle (id4, ago, body), newest-first（spec §3.4）。

    源序 ASC（最旧在前，app._render_recent_summaries）→ 反转为 newest-first 对齐
    Header 'Cycle' 阅读序。块头两变体（有/无 '· N words'）均容忍。无块头 → []。
    id 由块头 id8 再切 4 字符；ago 去括号。
    """
    marks = [
        (m.start(), m.group(1)[:4], m.group(2).strip(), m.end())
        for m in _BLOCK_HEADER_RE.finditer(summaries_half)
    ]
    if not marks:
        return []
    blocks: list[tuple[str, str, str]] = []
    for i, (_, id4, ago, end) in enumerate(marks):
        nxt = marks[i + 1][0] if i + 1 < len(marks) else len(summaries_half)
        blocks.append((id4, ago, summaries_half[end:nxt].strip()))
    blocks.reverse()  # ASC → newest-first
    return blocks


def _render_carried_block(id4: str, ago: str, body: str, is_newest: bool) -> list[str]:
    """Render one carried-cycle block → indented lines（spec §3.4）。

    结构化路径（①④ 均可定位）—— 字段名经 _strip_field_label 剥离后 prepend 归一标签：
        <id4> · <ago>
          Stance — <① 去名内容>
          Thesis — <④ 去名内容>        # 仅 is_newest（④ Thesis & invalidation 归一为 Thesis）
          (+N more)                    # 独占行，N = len(fields) − rendered
    兜底路径（无 ①④ — terse / forensic body，含 is_newest）—— 不剥标签（无字段名可剥）：
        <id4> · <ago>
          <cleaned whole body, capped>
    """
    out = [f"    {id4} · {ago}"]
    fields = _extract_summary_fields(body)
    if 1 in fields and 4 in fields:
        rendered = 1
        stance = _strip_field_label(_clean_field(fields[1]))
        out.append(f"      Stance — {escape(stance)}")
        if is_newest:
            thesis = _truncate_with_marker(
                _strip_field_label(_clean_field(fields[4])), _CONTEXT_THESIS_CAP,
            )
            out.append(f"      Thesis — {escape(thesis)}")
            rendered = 2
        n_more = len(fields) - rendered
        if n_more > 0:
            out.append(f"      (+{n_more} more)")
    else:
        whole = _truncate_with_marker(_clean_field(body), _CONTEXT_FALLBACK_CAP)
        out.append(f"      {escape(whole)}")
    return out


def _render_context(
    user_prompt_snapshot: str | None,
    trigger_type: str,
    is_first_cycle: bool = False,
) -> str:
    """Render the '▾ Context (carried into this cycle)' section (spec §3).

    数据源 = user_prompt_snapshot（agent 本轮实读那份）。`is_first_cycle` 为权威
    首-cycle 信号（caller 传 `stats.last_cycle_ended_at is None`，与 Header
    '(first cycle)' 同源），用于在无 prior 时渲准确占位行。snapshot None（legacy
    NULL 行）→ ""。fail-isolated：任何解析异常降级为空，绝不阻断整 cycle 渲染（spec §5）。

    Woke by 行：conditional/alert 渲变量事件行；scheduled 渲类型标签
    `Woke by — SCHEDULED`（镜像 Header `Trigger SCHEDULED`，跨 trigger 类型一致）。
    Carried thesis：有 prior → 实块；无 prior 且 is_first_cycle → 显式占位行。
    """
    if not user_prompt_snapshot:
        return ""
    try:
        wake_half, summaries_half = _split_wake_prompt(user_prompt_snapshot)
        event_lines = _extract_event_lines(wake_half, trigger_type)
        blocks = _parse_injected_summaries(summaries_half)

        lines: list[str] = []
        if len(event_lines) == 1:
            lines.append(f"  Woke by — {escape(event_lines[0])}")
        elif len(event_lines) > 1:
            # Batch wake (spec 2026-06-08 §3): one bullet per event, each truncated
            # individually (Header carries only type+count; Context owns the detail).
            lines.append(f"  Woke by — {len(event_lines)} events:")
            for el in event_lines:
                lines.append(f"    • {escape(el)}")
        elif trigger_type == "scheduled":
            # scheduled 无变量事件行；仍渲类型标签 + header 唤醒时间后缀，使 Context 段自包含。
            # 回显 agent 上轮 set_next_wake 的 reasoning（注入 prompt 却原先不上屏）—— inline
            # 括号续于类型标签后、时间子句前，与 alert 的 inline reasoning 对齐，让"本轮为何
            # 现在醒"在 Woke-by 行自解释。无 reasoning → SCHEDULED 后直接续时间子句。
            wake_reason = _extract_scheduled_wake_context(wake_half)
            reason_clause = (
                " (reason: "
                + escape(_truncate_with_marker(wake_reason, _CONTEXT_EVENT_CAP))
                + ")"
            ) if wake_reason else ""
            lines.append(
                f"  Woke by — SCHEDULED{reason_clause}"
                f"{_extract_scheduled_wake_suffix(wake_half)}"
            )

        if blocks:
            n = len(blocks)
            lines.append(
                f"  Carried thesis — last {n} cycle{'s' if n > 1 else ''} (newest first):"
            )
            for slot, (id4, ago, body) in enumerate(blocks):
                lines.extend(_render_carried_block(id4, ago, body, is_newest=(slot == 0)))
        elif is_first_cycle:
            # 首 cycle 确无 prior 可 carry → 显式占位行（而非整段省略），保证视觉一致。
            # 仅当权威信号 is_first_cycle 为真才声明；blocks 空亦可能是后续 cycle 的
            # summary 构建失败，那种情形不谎称首 cycle、不渲此行。
            lines.append("  Carried thesis — none (first cycle in this session)")

        if not lines:
            return ""
        return "\n▾ Context (carried into this cycle)\n" + "\n".join(lines)
    except Exception:
        logger.warning("Context section render failed; omitting", exc_info=True)
        return ""


def _render_action(
    tool_calls: list,
    returns_lookup: dict,
    cycle_id: str,
    retry_lookup: dict | None = None,
) -> str:
    """Render Action section per spec §3.1 unified dispatch.

    Dispatch:
      1a. ret None + retry present → `✗ tool_name() [invalid call: <first line>]`
          (pydantic-ai _wrap_error_as_retry — unknown tool / arg-validation 等)
      1b. ret None + retry absent → `⚙ tool_name() [no return captured]`
          (genuine orphan — tool_call_id mismatch; should not happen)
      2. is_tool_error → error single-line: `✗ tool_name(args) {fallback}`
      3. happy path (perception / execution / save_memory / drift) →
         unified head `{icon} {args_call}` + body (sectioned or plain
         by content). icon = ✎ for save_memory, ⚙ otherwise.
      4. Drift signal: tool_name not in any registered frozenset → log
         warning (no rendering change; frozenset is drift guard only,
         not dispatch driver — spec §3.1).

    retry_lookup: 可选 {tool_call_id → RetryPromptPart} map. `format_cycle_output`
    构建后传入;既有 testsite 不传 → 默认 None → 走 1b 真 orphan 分支保持原行为.
    """
    n = len(tool_calls)
    plural = "tool" if n == 1 else "tools"
    lines = [f"\n▾ Action ({n} {plural})"]
    retry_lookup = retry_lookup or {}

    for tcp in tool_calls:
        ret = returns_lookup.get(tcp.tool_call_id)
        if ret is None:
            retry = retry_lookup.get(tcp.tool_call_id)
            if retry is not None:
                # pydantic-ai 拒绝该 call (unknown tool / arg-validation 等;
                # 均经 _wrap_error_as_retry → RetryPromptPart 同一路径).
                # content 类型为 list[ErrorDetails] | str (messages.py:1321):
                #   ModelRetry     → content: str
                #   ValidationError → content: list[ErrorDetails]
                content = retry.content
                if isinstance(content, list):
                    first_line = "; ".join(
                        f"{'.'.join(map(str, e.get('loc', ())))}: {e.get('msg', '?')}"
                        for e in content[:3]
                    )[:100]
                else:
                    first_line = content.split("\n")[0][:100]
                lines.append(
                    f"  ✗ {escape(tcp.tool_name)}() "
                    f"{escape(f'[invalid call: {first_line}]')}"
                )
            else:
                logger.warning(
                    "tool_call_id mismatch for %s in cycle %s",
                    tcp.tool_name, cycle_id,
                )
                lines.append(
                    f"  ⚙ {escape(tcp.tool_name)}() "
                    f"{escape('[no return captured]')}"
                )
            continue

        content_str = str(ret.content)
        outcome = getattr(ret, "outcome", "success")
        args = tcp.args_as_dict()
        args_call = _format_args_as_call(tcp.tool_name, args)

        # Split off any mid-cycle injection block (iter-session-log-render-fidelity
        # Issue 1) so it renders as its own full-keep section after whichever branch
        # renders the tool result. injection is None for the common no-injection case
        # → behaviour identical to the pre-iter pass-through.
        tool_result, injection = _split_injection_block(content_str)

        # Branch 2: L1 error single-line + ✗. Classify on tool_result — the injection
        # is appended AFTER the result, so it never affects the success-prefix / outcome
        # check; passing the pre-split result also keeps it out of the collapsed summary.
        # escape args_call: reasoning is LLM-written, may contain Rich markup
        # like [bold] / [red] — must not be parsed as markup.
        if is_tool_error(tcp.tool_name, tool_result, outcome):
            rendered = f"  ✗ {escape(args_call)} {escape(_fallback_summary(tool_result))}"
        else:
            # Drift guard: warn for tools not in any registered frozenset
            # (per spec §3.1 — frozenset is guard only, doesn't drive render).
            if (
                tcp.tool_name != "save_memory"
                and tcp.tool_name not in _EXECUTION_TOOL_NAMES
                and tcp.tool_name not in _PERCEPTION_TOOL_NAMES
            ):
                logger.warning(
                    "tool_name %s not in any registered frozenset "
                    "(perception / execution / save_memory) — drift signal",
                    tcp.tool_name,
                )
            # Unified head + body for all happy-path tools
            icon = "✎" if tcp.tool_name == "save_memory" else "⚙"
            rendered = _render_tool_body(
                tcp.tool_name, tool_result,
                head_icon=icon, head_args=args_call,
            )

        # Mid-cycle injection: append as its own full-keep section (single-sourced via
        # _render_sections) so it survives the error single-line path intact.
        if injection:
            rendered += "\n\n" + "\n".join(_render_sections(injection))
        lines.append(rendered)

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

    # spec 2026-05-31: Context 段插在 Header 后、Reasoning/forensic 短路前
    # （success + forensic 两路径共用此处，因 user_prompt_snapshot 在两路径均已落库）
    # is_first_cycle 与 Header '(first cycle)' 同源（stats.last_cycle_ended_at is None；
    # record_cycle 在 format_cycle_output 之后调用，故渲染时仍反映上一 cycle）。
    context_section = _render_context(
        ctx.user_prompt_snapshot, ctx.trigger_type,
        is_first_cycle=ctx.stats.last_cycle_ended_at is None,
    )
    if context_section:
        lines.append(context_section)

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

    # === Build tool_call_id → ToolReturnPart / RetryPromptPart maps ===
    # RetryPromptPart 出现在 ModelRequest.parts (同位置不同类型 vs ToolReturnPart),
    # 由 pydantic-ai _wrap_error_as_retry 在 unknown tool / arg-validation 等 reject
    # 场景生成,需独立 capture 让 _render_action 可区分 retry-reject vs 真 orphan.
    tool_returns_lookup: dict = {}
    retry_lookup: dict = {}
    for msg in ctx.messages:
        if isinstance(msg, ModelRequest):
            for part in msg.parts:
                if isinstance(part, ToolReturnPart):
                    tool_returns_lookup[part.tool_call_id] = part
                elif isinstance(part, RetryPromptPart) and part.tool_call_id is not None:
                    retry_lookup[part.tool_call_id] = part

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
            lines.append(_render_action(
                tool_calls, tool_returns_lookup, ctx.cycle_id,
                retry_lookup=retry_lookup,
            ))

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


def _format_arg_value(v: object) -> str:
    """Format a single arg value per spec §3.2.

    Strings use json.dumps for proper escaping of embedded quotes / control
    chars (e.g. reasoning='trail "after" MA reclaim' must not break syntax).
    """
    if v is None:
        return "None"
    if isinstance(v, bool):
        return "True" if v else "False"
    if isinstance(v, (int, float)):
        return repr(v)
    if isinstance(v, str):
        # json.dumps handles " / \ / control-char escape + outputs double-quoted
        return json.dumps(v, ensure_ascii=False)
    if isinstance(v, list):
        return "[" + ", ".join(_format_arg_value(item) for item in v) + "]"
    if isinstance(v, dict):
        inner = ", ".join(f"{k}: {_format_arg_value(val)}" for k, val in v.items())
        if len(inner) > 40:
            return "{...}"
        return "{" + inner + "}"
    return repr(v)


def _format_args_as_call(tool_name: str, args: dict | None) -> str:
    """Format tool call as Python-like function syntax: tool_name(k=v, k=v).

    Empty args → tool_name(). INVALID_JSON_KEY (pydantic-ai unparseable
    arg) → tool_name(...). reasoning is uniformly retained in head per
    spec §3.2 (known divergence with tool_call_recorder.py:138 DB strip).

    `tool_name` is currently only used for fallback display; future
    extension point for per-tool customization (e.g. PII redaction).
    """
    if not args:
        return f"{tool_name}()"
    if INVALID_JSON_KEY in args:
        logger.warning(
            "tool %s args unparseable JSON: %r",
            tool_name, args[INVALID_JSON_KEY],
        )
        return f"{tool_name}(...)"

    parts = [f"{k}={_format_arg_value(v)}" for k, v in args.items()]
    return f"{tool_name}({', '.join(parts)})"


def summarize_tool(tool_name: str, content: str) -> str:
    """Summarize a tool's return value into a one-line display string.

    Used by system log INFO 摘要 path only (cli/app.py:332 resolve_tool_display
    → line 335 logger.info chain). Display path uses _render_tool_body
    directly, bypassing this function.
    """
    content_str = str(content)
    parser = (
        _SYSTEM_LOG_PERCEPTION_PARSERS.get(tool_name)
        or _EXECUTION_PARSERS.get(tool_name)
    )
    if parser:
        try:
            return parser(content_str)
        except Exception:
            return _fallback_summary(content_str)
    return _fallback_summary(content_str)
