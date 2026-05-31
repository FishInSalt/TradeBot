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


def test_clean_field_strips_bold_and_collapses_whitespace():
    from src.cli.display import _clean_field
    raw = "Flat. MA20 reclaim **confirmed**\n  by 07:30 close;   bearish bias tempered."
    cleaned = _clean_field(raw)
    assert "**" not in cleaned
    assert "\n" not in cleaned
    assert "  " not in cleaned  # 多空格已 collapse
    assert cleaned == "Flat. MA20 reclaim confirmed by 07:30 close; bearish bias tempered."


@pytest.mark.parametrize("marker", [
    "**(1) Stance** —",      # **(N) Field
    "(1) **Stance** —",      # (N) **Field
    "(1) Stance —",          # (N) Field
    "### (1) Stance —",      # ### (N) Field (markdown heading)
])
def test_extract_summary_fields_four_marker_styles(marker):
    """4 种 cosmetic 写法均能定位 ①④。"""
    from src.cli.display import _extract_summary_fields
    body = (
        f"{marker} Flat near MA20.\n"
        "**(2) Active commitments** — alert above 73,384.\n"
        "**(3) This cycle delta** — updated alert.\n"
        "**(4) Thesis & invalidation** — bearish macro; invalidation > 74,200.\n"
        "**(5) Watch list** — 74,200 resistance."
    )
    fields = _extract_summary_fields(body)
    assert 1 in fields and 4 in fields
    assert "Flat near MA20" in fields[1]
    assert "bearish macro" in fields[4]
    assert len(fields) == 5


def test_extract_summary_fields_terse_returns_empty():
    """terse 一句话（无 (N) marker）→ {}（caller 走整条兜底）。"""
    from src.cli.display import _extract_summary_fields
    assert _extract_summary_fields("Done. Next wake in 30 min.") == {}


def test_extract_summary_fields_degraded_only_1_and_4():
    """退化：仅 ①④ 在（缺 ②③⑤）→ ④ 以 block 末兜底定界。"""
    from src.cli.display import _extract_summary_fields
    body = "(1) Stance — flat.\n(4) Thesis — bearish; invalidation > 74,200."
    fields = _extract_summary_fields(body)
    assert set(fields) == {1, 4}
    assert "bearish; invalidation > 74,200" in fields[4]


def test_strip_field_label_removes_name_header():
    """剥 '<FieldName> — ' header（_extract_summary_fields 保留了字段名，render 前须剥）。

    覆盖审查发现的双标签根因：fields[1]='Stance — ...'，若不剥则 render 出 'Stance — Stance — ...'。
    """
    from src.cli.display import _strip_field_label
    # ① em-dash 分隔
    assert _strip_field_label("Stance — flat near MA20.") == "flat near MA20."
    # ④ 长字段名 + 内容含 colon（colon 在 em-dash 之后，不被误剥）
    assert (_strip_field_label("Thesis & invalidation — bearish; conviction: low")
            == "bearish; conviction: low")
    # colon 分隔写法
    assert _strip_field_label("Stance: flat") == "flat"
    # 内容含 hyphen 不被吃（hyphen 不在分隔符类）
    assert _strip_field_label("Stance — range 73,000-73,100") == "range 73,000-73,100"
    # 无 name—sep 前缀（≤40 内无分隔符）→ 原样返回（降级，不吃内容）
    raw = "flat near MA20 with no leading label or separator anywhere in here"
    assert _strip_field_label(raw) == raw
