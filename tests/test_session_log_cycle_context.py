"""session-log-cycle-context iter (2026-05-31) — Context 段渲染测试。

覆盖 spec §3 设计 / §5 降级 / §6 风险 / §8 测试策略。断言锚定渲染输出
文本结构（行为）而非内部正则。
"""
from __future__ import annotations

import pytest


def test_cycle_render_context_user_prompt_snapshot_defaults_none():
    """新字段默认 None（保现有构造点不 TypeError）；可显式赋值。"""
    from datetime import datetime, timezone
    from src.cli.display import CycleRenderContext
    from src.cli.session_state import SessionStats

    started = datetime(2026, 5, 31, 7, 35, 0, tzinfo=timezone.utc)
    # 不传 user_prompt_snapshot —— 应默认 None
    ctx = CycleRenderContext(
        cycle_id="06e9abcd", trigger_type="scheduled",
        trigger_context={"type": "scheduled_tick"}, state_snapshot=None,
        messages=None, final_text=None, cycle_tokens=0,
        stats=SessionStats(), cache_hit_rate=None,
        cycle_started_at=started, cycle_ended_at=started,
        forensic_reason=None,
    )
    assert ctx.user_prompt_snapshot is None
    # 显式赋值
    ctx2 = CycleRenderContext(
        cycle_id="06e9abcd", trigger_type="scheduled",
        trigger_context={"type": "scheduled_tick"}, state_snapshot=None,
        messages=None, final_text=None, cycle_tokens=0,
        stats=SessionStats(), cache_hit_rate=None,
        cycle_started_at=started, cycle_ended_at=started,
        forensic_reason=None, user_prompt_snapshot="hello",
    )
    assert ctx2.user_prompt_snapshot == "hello"


def test_split_wake_prompt_with_marker():
    """有注入块 → 前半=唤醒 scaffold+事件行，后半=注入 summary 块（标记行被丢弃）。"""
    from src.cli.display import _split_wake_prompt
    snapshot = (
        "You have been woken up by a alert trigger.\n"
        "Trading pair: BTC/USDT:USDT | Timeframe: 5m\n"
        "Assess the situation and decide what to do.\n\n"
        "PRICE LEVEL: BTC/USDT:USDT reached 73384.00 (alert id=934c above 73384.00 — x)\n\n"
        "Your prior cycle summaries (most recent N=3, from this session):\n\n"
        "[cycle 00f7abcd · alert · 2026-05-31 07:27 UTC (8 min ago) · 96 words]\n"
        "body here"
    )
    wake, summaries = _split_wake_prompt(snapshot)
    assert "PRICE LEVEL" in wake
    assert "Your prior cycle summaries" not in wake
    assert "Your prior cycle summaries" not in summaries  # 标记行本身已丢弃
    assert "[cycle 00f7abcd" in summaries


def test_split_wake_prompt_no_marker():
    """无注入块（首 cycle 无 prior）→ 后半为空字符串。"""
    from src.cli.display import _split_wake_prompt
    snapshot = (
        "You have been woken up by a scheduled trigger.\n"
        "Trading pair: BTC/USDT:USDT | Timeframe: 5m\n"
        "Assess the situation and decide what to do."
    )
    wake, summaries = _split_wake_prompt(snapshot)
    assert wake == snapshot
    assert summaries == ""


def test_extract_event_line_scheduled_returns_none():
    """scheduled → 事件行整体省略（spec §3.3）。"""
    from src.cli.display import _extract_event_line
    wake = (
        "You have been woken up by a scheduled trigger.\n"
        "Trading pair: BTC/USDT:USDT | Timeframe: 5m\n"
        "Assess the situation and decide what to do."
    )
    assert _extract_event_line(wake, "scheduled") is None


def test_extract_event_line_price_level_verbatim():
    """price-level alert → 保 alert id + reasoning，空白 collapse。"""
    from src.cli.display import _extract_event_line
    wake = (
        "You have been woken up by a alert trigger.\n"
        "Trading pair: BTC/USDT:USDT | Timeframe: 5m\n"
        "Assess the situation and decide what to do.\n\n"
        "PRICE LEVEL: BTC/USDT:USDT reached 73384.00 "
        "(alert id=934cfd above 73384.00 — MA20 reclaim: bounce)"
    )
    line = _extract_event_line(wake, "alert")
    assert line is not None
    assert line.startswith("PRICE LEVEL:")
    assert "alert id=934cfd" in line
    assert "MA20 reclaim: bounce" in line
    assert "You have been woken up" not in line  # scaffold 已剥离


def test_extract_event_line_conditional_fill():
    """conditional fill → 保 fee/PnL 段。"""
    from src.cli.display import _extract_event_line
    wake = (
        "You have been woken up by a conditional trigger.\n"
        "Trading pair: BTC/USDT:USDT | Timeframe: 5m\n"
        "Assess the situation and decide what to do.\n\n"
        "IMPORTANT EVENT: take_profit triggered — BTC/USDT:USDT 0.265 @ 75350.0, "
        "Fee: -2.10 USDT, PnL: +12.40 USDT (gross) / +8.20 USDT (this fill, equiv-round-trip)"
    )
    line = _extract_event_line(wake, "conditional")
    assert line.startswith("IMPORTANT EVENT: take_profit triggered")
    assert "PnL: +12.40 USDT (gross)" in line


def test_extract_event_line_no_known_prefix_returns_none():
    """alert 但无任何已知前缀（识别不到）→ None（不渲 Woke by）。"""
    from src.cli.display import _extract_event_line
    wake = "You have been woken up by a alert trigger.\nTrading pair: X | Timeframe: 5m\n..."
    assert _extract_event_line(wake, "alert") is None
