"""Shared event-block renderers — wake prompt 与 mid-cycle injection 双路径单源.

iter-midcycle-event-injection §3: 这些函数原住 src/cli/app.py（wake prompt 专用）；
注入路径（src/services/midcycle_injector.py）需要逐字同构的事件块——信号唯一权威
来源，fee/PnL/equiv-round-trip 计算只存在一份，注入块与 wake 块数字永不打架。

时间基准形参统一为中性名 `now`：wake 路径传 cycle_started_at，注入路径传注入时刻
（spec §3——避免把"注入时刻"塞进名为 cycle_started_at 的参数造成语义重载）。
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from src.integrations.exchange.base import PriceLevelAlertInfo


def _format_relative_time(now: datetime, then: datetime) -> str:
    """Format a delta as '8 min ago' / '2 hours 15 min ago' / '1 day ago'.

    SQLite returns naive datetime even when schema is DateTime(timezone=True);
    normalize to UTC-aware before subtraction (same pattern as
    session_manager.py:294-295).
    """
    if then.tzinfo is None:
        then = then.replace(tzinfo=timezone.utc)
    delta = now - then
    secs = int(delta.total_seconds())
    if secs < 60:
        return f"{secs} sec ago"
    mins = secs // 60
    if mins < 60:
        return f"{mins} min ago"
    hours = mins // 60
    if hours < 24:
        # Retain sub-hour minutes once age crosses 1h: with 30–60min cycle cadence,
        # whole-hour truncation collapsed adjacent priors to the same '1 hour ago'
        # (sim #18). Whole-hour ages drop the '0 min' tail.
        h_label = f"{hours} hour{'s' if hours > 1 else ''}"
        rem_min = mins % 60
        return f"{h_label} {rem_min} min ago" if rem_min else f"{h_label} ago"
    days = hours // 24
    return f"{days} day{'s' if days > 1 else ''} ago"


def _format_event_age(now: datetime, then: datetime) -> str | None:
    """Age of a wake event for the prompt: None when the event timestamp is ahead of
    `now` (clock skew / sleep artifact — caller renders UTC only), "just now" when <2s,
    otherwise the existing second-granular ladder.

    `then` is always tz-aware on the wake-event path (built from an int-ms epoch), so no
    tz-naive normalization is exercised here — see spec 2026-06-08.
    """
    if then > now:
        return None
    if (now - then).total_seconds() < 2:
        return "just now"
    return _format_relative_time(now, then)


def _wake_time_suffix(verb: str, event_ts_ms: int, now: datetime) -> str:
    """Assemble the wake-event time clause ` — {verb} {abs-UTC} ({age})`.

    Owns the int-ms→datetime conversion. When the event timestamp is ahead of `now`
    (skew / sleep artifact) the relative age is dropped, leaving ` — {verb} {abs-UTC}`.
    Pure + sync — `now` is the cycle-start anchor passed by the caller (spec 2026-06-08).
    """
    then = datetime.fromtimestamp(event_ts_ms / 1000, tz=timezone.utc)
    abs_utc = then.strftime("%Y-%m-%d %H:%M UTC")
    age = _format_event_age(now, then)
    if age is None:
        return f" — {verb} {abs_utc}"
    return f" — {verb} {abs_utc} ({age})"


def _format_price_level_alert_trigger(context: PriceLevelAlertInfo, now: datetime) -> str:
    """Build the PRICE LEVEL ALERT trigger suffix exposing alert_id for lifecycle joins.

    `now` is the cycle-start anchor for the trailing event-age clause (spec 2026-06-08).
    """
    return (
        f"\n\nPRICE LEVEL ALERT: {context.symbol} reached {context.current_price:.2f} "
        f"(alert id={context.alert_id} {context.direction} {context.target_price:.2f} "
        f"— {context.reasoning})"
        + _wake_time_suffix("fired", context.timestamp, now)
    )


def _format_event_breakdown(events: list[tuple[str, Any]]) -> str:
    """Breakdown 拼接唯一权威来源（spec §3）：`1 fill` / `2 alerts` / `1 fill, 2 alerts`，
    fill 在前（匹配堆优先级 conditional < alert）；无已知类型 → `N events` fallback。

    自 app.py _wake_header_line N>1 分支提取；wake header 与 §4 注入
    header 共用，零漂移面。
    """
    n_fill = sum(1 for tt, _ in events if tt == "conditional")
    n_alert = sum(1 for tt, _ in events if tt == "alert")
    parts: list[str] = []
    if n_fill:
        parts.append(f"{n_fill} fill{'s' if n_fill > 1 else ''}")
    if n_alert:
        parts.append(f"{n_alert} alert{'s' if n_alert > 1 else ''}")
    return ", ".join(parts) if parts else f"{len(events)} events"


async def _render_event_block(deps, trigger_type: str, context, now: datetime) -> str:
    """Render one event's prompt block (spec 2026-06-08 §2), verbatim with the prior
    inline assembly so N==1 prompts are byte-identical.

    Async + IO: the full-close fill branch awaits `deps.exchange.get_contract_size` and
    reads `deps.fee_rate` (symbol from `context.symbol`). scheduled / context-None → "".

    scheduled + non-empty context: echo the agent's set_next_wake reasoning verbatim
    as a `SCHEDULED WAKE CONTEXT (you set last cycle):` block (spec 2026-06-11),
    structurally consistent with the other event blocks. context here is a plain str,
    not a dataclass. Empty-string reasoning renders nothing (truthy guard — no dangling
    label).

    `now` is the rendering time anchor — wake path passes cycle_started_at, injection
    path passes the injection moment (spec §3).
    """
    if trigger_type == "scheduled" and context:
        return f"\n\nSCHEDULED WAKE CONTEXT (you set last cycle): {context}"
    if trigger_type == "conditional" and context is not None:
        msg = (
            f"\n\nIMPORTANT EVENT: {context.trigger_reason} triggered "
            f"— {context.symbol} {context.amount} @ {context.fill_price}"
        )
        if context.pnl is None:
            # Open fill — fee only
            msg += f", Fee: {-context.fee:+.2f} USDT"
        elif context.is_full_close and context.entry_price is not None:
            # Full close fill — fee + gross + equiv-round-trip net.
            # contract_size factor required for USDT-denominated entry_fee — matches
            # tools_perception.py / tools_execution.py convention.
            _contract_size = await deps.exchange.get_contract_size(context.symbol)
            entry_fee_recompute = (
                context.entry_price * context.amount * _contract_size * deps.fee_rate
            )
            round_trip_net = -entry_fee_recompute + context.pnl - context.fee
            msg += (
                f", Fee: {-context.fee:+.2f} USDT, "
                f"PnL: {context.pnl:+.2f} USDT (gross) / "
                f"{round_trip_net:+.2f} USDT (this fill, equiv-round-trip)"
            )
        else:
            # Part close, OR full close with no entry_price (OKX cache miss —
            # e.g., SL/TP placed in a prior process before restart). fact-provider
            # principle: emit hint so agent knows why round-trip line is absent
            # on full-close fills, distinguishing from part-close design.
            base = (
                f", Fee: {-context.fee:+.2f} USDT, "
                f"PnL: {context.pnl:+.2f} USDT (gross)"
            )
            if context.is_full_close and context.entry_price is None:
                base += " [round-trip net unavailable: entry_price not cached]"
            msg += base
        msg += _wake_time_suffix("filled", context.timestamp, now)
        return msg
    if trigger_type == "alert" and context is not None:
        if isinstance(context, PriceLevelAlertInfo):
            return _format_price_level_alert_trigger(context, now)
        direction = "dropped" if context.change_pct < 0 else "surged"
        return (
            f"\n\nPRICE VOLATILITY ALERT: {context.symbol} {direction} {abs(context.change_pct):.1f}% "
            f"in {context.window_minutes}min ({context.reference_price:.2f} → {context.current_price:.2f})"
            + _wake_time_suffix("fired", context.timestamp, now)
        )
    return ""
