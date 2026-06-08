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


def test_extract_event_lines_scheduled_empty():
    from src.cli.display import _extract_event_lines
    assert _extract_event_lines("anything", "scheduled") == []


def test_extract_event_lines_single():
    from src.cli.display import _extract_event_lines
    wake = (
        "You have been woken up by a alert trigger.\n"
        "Trading pair: BTC/USDT:USDT | Timeframe: 15m\n"
        "Assess the situation and decide what to do.\n\n"
        "PRICE LEVEL: BTC/USDT:USDT reached 80050.00 (alert id=a1 above 80000.00 — r)"
        " — fired 2026-06-01 14:34 UTC (4 min ago)"
    )
    lines = _extract_event_lines(wake, "alert")
    assert len(lines) == 1
    assert lines[0].startswith("PRICE LEVEL: BTC/USDT:USDT reached 80050.00")


def test_extract_event_lines_price_level_verbatim():
    """price-level alert → 保 alert id + reasoning，空白 collapse。"""
    from src.cli.display import _extract_event_lines
    wake = (
        "You have been woken up by a alert trigger.\n"
        "Trading pair: BTC/USDT:USDT | Timeframe: 5m\n"
        "Assess the situation and decide what to do.\n\n"
        "PRICE LEVEL: BTC/USDT:USDT reached 73384.00 "
        "(alert id=934cfd above 73384.00 — MA20 reclaim: bounce)"
    )
    lines = _extract_event_lines(wake, "alert")
    assert len(lines) == 1
    assert lines[0].startswith("PRICE LEVEL:")
    assert "alert id=934cfd" in lines[0]
    assert "MA20 reclaim: bounce" in lines[0]
    assert "You have been woken up" not in lines[0]  # scaffold 已剥离


def test_extract_event_lines_conditional_fill():
    """conditional fill → 保 fee/PnL 段。"""
    from src.cli.display import _extract_event_lines
    wake = (
        "You have been woken up by a conditional trigger.\n"
        "Trading pair: BTC/USDT:USDT | Timeframe: 5m\n"
        "Assess the situation and decide what to do.\n\n"
        "IMPORTANT EVENT: take_profit triggered — BTC/USDT:USDT 0.265 @ 75350.0, "
        "Fee: -2.10 USDT, PnL: +12.40 USDT (gross) / +8.20 USDT (this fill, equiv-round-trip)"
    )
    lines = _extract_event_lines(wake, "conditional")
    assert len(lines) == 1
    assert lines[0].startswith("IMPORTANT EVENT: take_profit triggered")
    assert "PnL: +12.40 USDT (gross)" in lines[0]


def test_extract_event_lines_no_known_prefix_returns_empty():
    """alert 但无任何已知前缀（识别不到）→ []（不渲 Woke by）。"""
    from src.cli.display import _extract_event_lines
    wake = "You have been woken up by a alert trigger.\nTrading pair: X | Timeframe: 5m\n..."
    assert _extract_event_lines(wake, "alert") == []


def test_extract_event_lines_multi_splits_per_prefix():
    from src.cli.display import _extract_event_lines
    wake = (
        "You have been woken up by 2 triggers (1 fill, 1 alert) since the last cycle.\n"
        "Trading pair: BTC/USDT:USDT | Timeframe: 15m\n"
        "Assess the situation and decide what to do.\n\n"
        "IMPORTANT EVENT: tp triggered — BTC/USDT:USDT 1.0 @ 80000, Fee: -1.00 USDT"
        " — filled 2026-06-01 14:34 UTC (1 min ago)\n\n"
        "PRICE ALERT: BTC/USDT:USDT surged 1.5% in 15min (78000.00 → 79170.00)"
        " — fired 2026-06-01 14:34 UTC (2 min ago)"
    )
    lines = _extract_event_lines(wake, "conditional")
    assert len(lines) == 2
    assert lines[0].startswith("IMPORTANT EVENT: tp triggered")
    assert lines[1].startswith("PRICE ALERT: BTC/USDT:USDT surged")


def test_extract_event_lines_prefix_in_freetext_no_oversplit():
    from src.cli.display import _extract_event_lines
    # A price-level alert whose reasoning literally contains "PRICE ALERT" must stay ONE bullet.
    wake = (
        "You have been woken up by a alert trigger.\n"
        "Trading pair: BTC/USDT:USDT | Timeframe: 15m\n"
        "Assess the situation and decide what to do.\n\n"
        "PRICE LEVEL: BTC/USDT:USDT reached 80050.00 (alert id=a1 above 80000.00 "
        "— watch for PRICE ALERT confirmation) — fired 2026-06-01 14:34 UTC (4 min ago)"
    )
    lines = _extract_event_lines(wake, "alert")
    assert len(lines) == 1
    assert lines[0].startswith("PRICE LEVEL: BTC/USDT:USDT reached 80050.00")
    assert "watch for PRICE ALERT confirmation" in lines[0]


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
    仅锚定 5 个已知字段名才剥（PR #67 审查 follow-up）。
    """
    from src.cli.display import _strip_field_label
    # ① em-dash 分隔
    assert _strip_field_label("Stance — flat near MA20.") == "flat near MA20."
    # ④ 长字段名 + 内容含 colon（colon 在 em-dash 之后，不被误剥）
    assert (_strip_field_label("Thesis & invalidation — bearish; conviction: low")
            == "bearish; conviction: low")
    # 已知字段名 + colon 分隔写法 → 仍剥
    assert _strip_field_label("Stance: flat") == "flat"
    # 内容含 hyphen 不被吃（hyphen 不是分隔符）
    assert _strip_field_label("Stance — range 73,000-73,100") == "range 73,000-73,100"
    # 大小写漂移容忍
    assert _strip_field_label("stance — flat") == "flat"


def test_strip_field_label_never_eats_content_without_known_name():
    """锚定已知字段名 → 退化写法（无字段名）绝不吃内容（PR #67 审查 Important follow-up）。

    旧泛化正则 `^.{0,40}[—–:]` 会把内容早期的 colon / em-dash 当 label 静默吃掉前半句；
    锚定后这些都 fall-through → 原样返回。
    """
    from src.cli.display import _strip_field_label
    # 无字段名 + 内容早期含 colon（交易笔记常见：conviction: / target:）→ 不吞
    assert _strip_field_label("Flat, conviction: low") == "Flat, conviction: low"
    assert _strip_field_label("Long bias, target: 77,750") == "Long bias, target: 77,750"
    # 无字段名 + 内容早期含 em-dash → 不吞
    assert _strip_field_label("flat — watching for breakout") == "flat — watching for breakout"
    # 未知字段名 → fall-through（drift-guard：可见双标签而非静默丢失）
    assert _strip_field_label("Bias: short") == "Bias: short"
    # 无任何分隔符 → 原样返回
    raw = "flat near MA20 with no leading label or separator anywhere in here"
    assert _strip_field_label(raw) == raw


def _injected_block_asc() -> str:
    """模拟 app._render_recent_summaries 产出：ASC（最旧在前），3 条，含两块头变体。"""
    return (
        "\n\n"
        "[cycle 824e2233 · conditional · 2026-05-31 07:00 UTC (35 min ago) · 91 words]\n"
        "**(1) Stance** — flat; cascade compressing.\n"
        "**(4) Thesis & invalidation** — bearish; invalidation > 74,200.\n\n"
        "[cycle 47d5ef01 · usage_limit_exceeded · 2026-05-31 07:01 UTC (34 min ago)]\n"  # NULL 变体：无 · N words
        "⚠️ The previous cycle did not complete normally. Please verify state.\n\n"
        "[cycle 00f7abcd · alert · 2026-05-31 07:27 UTC (8 min ago) · 96 words]\n"
        "**(1) Stance** — flat; MA20 reclaim confirmed.\n"
        "**(4) Thesis & invalidation** — bearish macro intact."
    )


def test_parse_injected_summaries_reversed_newest_first():
    """源 ASC → 反转为 newest-first（00f7 最新在前，824e 最旧在后）。"""
    from src.cli.display import _parse_injected_summaries
    blocks = _parse_injected_summaries(_injected_block_asc())
    assert len(blocks) == 3
    ids = [b[0] for b in blocks]
    assert ids == ["00f7", "47d5", "824e"]  # newest-first，且 id8 → id4


def test_parse_injected_summaries_two_header_variants_ago():
    """两块头变体（有/无 · N words）均能取 id+ago（去括号）。"""
    from src.cli.display import _parse_injected_summaries
    blocks = _parse_injected_summaries(_injected_block_asc())
    by_id = {b[0]: b for b in blocks}
    assert by_id["00f7"][1] == "8 min ago"      # valid 变体（有 · 96 words）
    assert by_id["47d5"][1] == "34 min ago"     # NULL 变体（无 · N words）
    # body 切片正确（含字段标记 / forensic 系统文本）
    assert "MA20 reclaim confirmed" in by_id["00f7"][2]
    assert "did not complete normally" in by_id["47d5"][2]


def test_parse_injected_summaries_empty_no_blocks():
    from src.cli.display import _parse_injected_summaries
    assert _parse_injected_summaries("") == []
    assert _parse_injected_summaries("no block header here") == []


_FULL5 = (
    "**(1) Stance** — flat; MA20 reclaim confirmed.\n"
    "**(2) Active commitments** — alert above 73,384.\n"
    "**(3) This cycle delta** — updated alert.\n"
    "**(4) Thesis & invalidation** — bearish macro intact; invalidation > 74,200.\n"
    "**(5) Watch list** — 74,200 resistance."
)
_NO_WATCH4 = (
    "**(1) Stance** — flat; MA20 reclaim confirmed.\n"
    "**(2) Active commitments** — alert above 73,384.\n"
    "**(3) This cycle delta** — updated alert.\n"
    "**(4) Thesis & invalidation** — bearish macro intact; invalidation > 74,200."
)


def test_render_carried_block_newest_stance_and_thesis():
    """最近一条 → Stance + Thesis；(+N more) = 5 − 2 = 3。

    断言用精确整行（`in lines`），catch 审查发现的双标签：buggy 实现产出
    '      Stance — Stance — flat...' / '      Thesis — Thesis & invalidation — ...'
    与下面精确行不等 → 红。
    """
    from src.cli.display import _render_carried_block
    lines = _render_carried_block("00f7", "8 min ago", _FULL5, is_newest=True)
    assert "    00f7 · 8 min ago" in lines
    # 单标签、字段名已剥 —— 精确整行（双标签会变成另一行字符串 → 不在 lines 里）
    assert "      Stance — flat; MA20 reclaim confirmed." in lines
    assert "      Thesis — bearish macro intact; invalidation > 74,200." in lines
    # (+N more) 独占行
    assert "      (+3 more)" in lines


def test_render_carried_block_earlier_stance_only():
    """较早一条 → 仅 Stance，无 Thesis；(+N more) = 5 − 1 = 4。"""
    from src.cli.display import _render_carried_block
    lines = _render_carried_block("47d5", "34 min ago", _FULL5, is_newest=False)
    assert "      Stance — flat; MA20 reclaim confirmed." in lines
    assert not any(line.lstrip().startswith("Thesis —") for line in lines)
    assert "      (+4 more)" in lines


def test_render_carried_block_n_more_dynamic_no_watch():
    """缺 ⑤Watch（4 字段）→ N 动态减 1：newest 4−2=2，earlier 4−1=3。"""
    from src.cli.display import _render_carried_block
    newest = _render_carried_block("00f7", "8 min ago", _NO_WATCH4, is_newest=True)
    earlier = _render_carried_block("47d5", "34 min ago", _NO_WATCH4, is_newest=False)
    assert "      (+2 more)" in newest
    assert "      (+3 more)" in earlier


def test_render_carried_block_fallback_terse_no_labels():
    """terse / 无 ①④ → 整条兜底：无 Stance/Thesis 标签、无 (+N more)。"""
    from src.cli.display import _render_carried_block
    lines = _render_carried_block("824e", "35 min ago", "Done. Next wake in 30 min.", is_newest=True)
    text = "\n".join(lines)
    assert "Done. Next wake in 30 min." in text
    assert "Stance —" not in text
    assert "Thesis —" not in text
    assert "more)" not in text


def test_render_carried_block_newest_fallback_when_self_terse():
    """最近一条自身落兜底（无 ①④）→ 同样整条兜底（优先级 兜底 > 字段）。"""
    from src.cli.display import _render_carried_block
    lines = _render_carried_block("00f7", "8 min ago", "Holding. No change.", is_newest=True)
    text = "\n".join(lines)
    assert "Holding. No change." in text
    assert "Stance —" not in text and "more)" not in text


def _stats_not_first():
    """非首 cycle 的 SessionStats —— record 过一轮 → last_cycle_ended_at 非 None
    （Header 显示 '+N min from prev'，is_first_cycle=False）。"""
    from datetime import datetime, timezone
    from src.cli.session_state import SessionStats
    s = SessionStats()
    s.record_cycle(0, datetime(2026, 5, 31, 7, 30, 0, tzinfo=timezone.utc))
    return s


def _ctx(trigger_type, user_prompt_snapshot, messages=None, final_text="Hold.", stats=None):
    """构造一个带 user_prompt_snapshot 的 success-path ctx（messages 给最小非 None 值触发正常渲染路径）。

    stats=None → 默认 SessionStats()（首 cycle，last_cycle_ended_at=None →
    is_first_cycle=True）。传 _stats_not_first() 模拟非首 cycle。
    """
    from datetime import datetime, timezone, timedelta
    from src.cli.display import CycleRenderContext
    from src.cli.session_state import SessionStats
    started = datetime(2026, 5, 31, 7, 35, 0, tzinfo=timezone.utc)
    return CycleRenderContext(
        cycle_id="06e9abcd", trigger_type=trigger_type,
        trigger_context={"type": "scheduled_tick"}, state_snapshot=None,
        messages=messages, final_text=final_text, cycle_tokens=1000,
        stats=stats if stats is not None else SessionStats(), cache_hit_rate=90.0,
        cycle_started_at=started, cycle_ended_at=started + timedelta(seconds=3),
        forensic_reason=None, user_prompt_snapshot=user_prompt_snapshot,
    )


_ALERT_SNAPSHOT = (
    "You have been woken up by a alert trigger.\n"
    "Trading pair: BTC/USDT:USDT | Timeframe: 5m\n"
    "Assess the situation and decide what to do.\n\n"
    "PRICE LEVEL: BTC/USDT:USDT reached 73384.00 (alert id=934cfd above 73384.00 — MA20 reclaim)\n\n"
    "Your prior cycle summaries (most recent N=3, from this session):\n\n"
    "[cycle 824e2233 · conditional · 2026-05-31 07:00 UTC (35 min ago) · 91 words]\n"
    "**(1) Stance** — flat; cascade compressing.\n"
    "**(2) Active commitments** — none.\n"
    "**(3) This cycle delta** — closed short.\n"
    "**(4) Thesis & invalidation** — bearish; invalidation > 74,200.\n"
    "**(5) Watch list** — 73,000 support.\n\n"
    "[cycle 00f7abcd · alert · 2026-05-31 07:27 UTC (8 min ago) · 96 words]\n"
    "**(1) Stance** — flat; MA20 reclaim confirmed.\n"
    "**(2) Active commitments** — alert above 73,384.\n"
    "**(3) This cycle delta** — updated alert.\n"
    "**(4) Thesis & invalidation** — bearish macro intact; invalidation > 74,200.\n"
    "**(5) Watch list** — 74,200 resistance."
)


def test_render_context_section_present_between_header_and_reasoning():
    """Context 段在 Header 之后、第一段 Reasoning 之前。"""
    from src.cli.display import format_cycle_output
    from tests.fixtures.cycle_fixtures import build_cycle_messages
    msgs = build_cycle_messages(
        thinking_segments=["Assess."], tool_call_segments=[[]], final_text="Hold.",
    )
    out = format_cycle_output(_ctx("alert", _ALERT_SNAPSHOT, messages=msgs))
    ctx_idx = out.find("▾ Context (carried into this cycle)")
    header_idx = out.find("Cycle 06e9")
    reasoning_idx = out.find("▾ Reasoning")
    assert header_idx >= 0 and ctx_idx > header_idx, "Context 须在 Header 之后"
    assert reasoning_idx > ctx_idx, "Context 须在第一段 Reasoning 之前"
    # Woke by（alert 事件行）
    assert "Woke by — PRICE LEVEL:" in out
    assert "alert id=934cfd" in out
    # Carried thesis newest-first：00f7 在 824e 之前
    assert out.find("00f7 · 8 min ago") < out.find("824e · 35 min ago")
    # 最近一条有 Thesis，较早只有 Stance
    assert "Thesis — bearish macro intact" in out      # 00f7（newest）
    assert "(+3 more)" in out                            # newest 5−2
    assert "(+4 more)" in out                            # earlier 5−1
    # blocks 非空 → 渲实块，绝不出占位行（即便 stats 为首 cycle，if blocks 优先于 elif 占位）
    assert "none (first cycle" not in out


def test_render_context_scheduled_renders_woke_by_label():
    """scheduled → 渲 'Woke by — SCHEDULED'（镜像 Header Trigger SCHEDULED），再接 Carried thesis。

    行为变更（2026-06-07）：原省略 Woke by，现为跨 trigger 类型一致 + 自包含而渲类型标签。
    """
    from src.cli.display import format_cycle_output
    from tests.fixtures.cycle_fixtures import build_cycle_messages
    snap = _ALERT_SNAPSHOT.replace(
        "You have been woken up by a alert trigger.\n"
        "Trading pair: BTC/USDT:USDT | Timeframe: 5m\n"
        "Assess the situation and decide what to do.\n\n"
        "PRICE LEVEL: BTC/USDT:USDT reached 73384.00 (alert id=934cfd above 73384.00 — MA20 reclaim)\n\n",
        "You have been woken up by a scheduled trigger.\n"
        "Trading pair: BTC/USDT:USDT | Timeframe: 5m\n"
        "Assess the situation and decide what to do.\n\n",
    )
    msgs = build_cycle_messages(thinking_segments=["x."], tool_call_segments=[[]], final_text="Hold.")
    # 非首 cycle（有 prior summaries）→ Woke by + Carried thesis 实块
    out = format_cycle_output(_ctx("scheduled", snap, messages=msgs, stats=_stats_not_first()))
    assert "▾ Context (carried into this cycle)" in out
    assert "Woke by — SCHEDULED" in out
    assert "Carried thesis — last" in out
    assert "none (first cycle" not in out  # 有 prior → 不渲占位


def test_extract_scheduled_wake_suffix_present():
    """新 header 后缀（spec 2026-06-08）→ 抽出 ' — fired {UTC} ({age})'。"""
    from src.cli.display import _extract_scheduled_wake_suffix
    wake = (
        "You have been woken up by a scheduled trigger — fired 2026-06-01 14:38 UTC (just now).\n"
        "Trading pair: BTC/USDT:USDT | Timeframe: 5m\n"
        "Assess the situation and decide what to do."
    )
    assert _extract_scheduled_wake_suffix(wake) == " — fired 2026-06-01 14:38 UTC (just now)"


def test_extract_scheduled_wake_suffix_legacy_returns_empty():
    """无后缀的 legacy scheduled snapshot → ''（向后兼容，仍渲纯标签）。"""
    from src.cli.display import _extract_scheduled_wake_suffix
    wake = (
        "You have been woken up by a scheduled trigger.\n"
        "Trading pair: BTC/USDT:USDT | Timeframe: 5m\n"
        "Assess the situation and decide what to do."
    )
    assert _extract_scheduled_wake_suffix(wake) == ""


def test_render_context_scheduled_shows_wake_time_in_section():
    """scheduled Context 段自包含 cycle 唤醒时间（abs-UTC + age），不依赖 Header。"""
    from src.cli.display import format_cycle_output
    from tests.fixtures.cycle_fixtures import build_cycle_messages
    snap = (
        "You have been woken up by a scheduled trigger — fired 2026-06-01 14:38 UTC (just now).\n"
        "Trading pair: BTC/USDT:USDT | Timeframe: 5m\n"
        "Assess the situation and decide what to do."
    )
    msgs = build_cycle_messages(thinking_segments=["x."], tool_call_segments=[[]], final_text="Hold.")
    out = format_cycle_output(_ctx("scheduled", snap, messages=msgs))
    assert "Woke by — SCHEDULED — fired 2026-06-01 14:38 UTC (just now)" in out


def test_render_context_none_snapshot_omits_section():
    """user_prompt_snapshot=None → 整段省略（spec §5）。"""
    from src.cli.display import format_cycle_output
    from tests.fixtures.cycle_fixtures import build_cycle_messages
    msgs = build_cycle_messages(thinking_segments=["x."], tool_call_segments=[[]], final_text="Hold.")
    out = format_cycle_output(_ctx("scheduled", None, messages=msgs))
    assert "▾ Context" not in out


def test_render_context_scheduled_first_cycle_renders_woke_by_and_placeholder():
    """scheduled 首 cycle（无 prior）→ Context 段含 'Woke by — SCHEDULED' + 占位行。

    行为变更（2026-06-07）：原整段省略，现始终渲 Context 段、首 cycle 显式占位。
    """
    from src.cli.display import format_cycle_output
    from tests.fixtures.cycle_fixtures import build_cycle_messages
    snap = (
        "You have been woken up by a scheduled trigger.\n"
        "Trading pair: BTC/USDT:USDT | Timeframe: 5m\n"
        "Assess the situation and decide what to do."
    )
    msgs = build_cycle_messages(thinking_segments=["x."], tool_call_segments=[[]], final_text="Hold.")
    out = format_cycle_output(_ctx("scheduled", snap, messages=msgs))  # default stats = 首 cycle
    assert "▾ Context (carried into this cycle)" in out
    assert "Woke by — SCHEDULED" in out
    assert "Carried thesis — none (first cycle in this session)" in out


def test_render_context_alert_first_cycle_woke_by_and_placeholder():
    """conditional/alert 首 cycle（有 Woke by、无 prior）→ Woke by 事件行 + Carried thesis 占位。

    行为变更（2026-06-07）：原只渲 Woke by，现首 cycle 同样补占位行。
    """
    from src.cli.display import format_cycle_output
    from tests.fixtures.cycle_fixtures import build_cycle_messages
    snap = (
        "You have been woken up by a alert trigger.\n"
        "Trading pair: BTC/USDT:USDT | Timeframe: 5m\n"
        "Assess the situation and decide what to do.\n\n"
        "PRICE LEVEL: BTC/USDT:USDT reached 73384.00 (alert id=934cfd above 73384.00 — x)"
    )
    msgs = build_cycle_messages(thinking_segments=["x."], tool_call_segments=[[]], final_text="Hold.")
    out = format_cycle_output(_ctx("alert", snap, messages=msgs))  # default stats = 首 cycle
    assert "Woke by — PRICE LEVEL:" in out
    assert "Carried thesis — none (first cycle in this session)" in out


def test_render_context_multi_event_batch_bullets():
    """batch wake (2 events) → Context 段含 'Woke by — 2 events:' + 两条 bullet 行。"""
    from src.cli.display import format_cycle_output
    from tests.fixtures.cycle_fixtures import build_cycle_messages
    snap = (
        "You have been woken up by 2 triggers (1 fill, 1 alert) since the last cycle.\n"
        "Trading pair: BTC/USDT:USDT | Timeframe: 15m\n"
        "Assess the situation and decide what to do.\n\n"
        "IMPORTANT EVENT: tp triggered — BTC/USDT:USDT 1.0 @ 80000, Fee: -1.00 USDT"
        " — filled 2026-06-01 14:34 UTC (1 min ago)\n\n"
        "PRICE ALERT: BTC/USDT:USDT surged 1.5% in 15min (78000.00 → 79170.00)"
        " — fired 2026-06-01 14:34 UTC (2 min ago)"
    )
    msgs = build_cycle_messages(thinking_segments=["x."], tool_call_segments=[[]], final_text="Hold.")
    out = format_cycle_output(_ctx("conditional", snap, messages=msgs))
    assert "Woke by — 2 events:" in out
    assert "• IMPORTANT EVENT: tp triggered" in out
    assert "• PRICE ALERT: BTC/USDT:USDT surged" in out


def test_render_context_non_first_cycle_empty_blocks_no_false_placeholder():
    """非首 cycle 但 blocks 空（如后续 cycle 的 summary 构建失败）→ 不谎称 first cycle、不渲占位。

    占位行必须锚定权威信号 is_first_cycle（= stats.last_cycle_ended_at is None），
    不能用 'blocks 空' 推断首 cycle，否则会把 summary 构建失败的后续 cycle 误标为首 cycle。
    """
    from src.cli.display import format_cycle_output
    from tests.fixtures.cycle_fixtures import build_cycle_messages
    snap = (
        "You have been woken up by a scheduled trigger.\n"
        "Trading pair: BTC/USDT:USDT | Timeframe: 5m\n"
        "Assess the situation and decide what to do."
    )
    msgs = build_cycle_messages(thinking_segments=["x."], tool_call_segments=[[]], final_text="Hold.")
    out = format_cycle_output(_ctx("scheduled", snap, messages=msgs, stats=_stats_not_first()))
    assert "Woke by — SCHEDULED" in out       # 仍渲 trigger 标签（scheduled 一致性）
    assert "first cycle" not in out            # 不谎称首 cycle
    assert "Carried thesis — none" not in out  # 无占位行


# === Task 9: round-trip drift-guard + length caps + backward-compat ===


def test_roundtrip_render_recent_summaries_parses_correctly():
    """drift-guard：app._render_recent_summaries 真实产出（valid + forensic body）
    → _split_wake_prompt + _parse_injected_summaries + _extract_summary_fields 全链正确。
    格式漂移 → 本测试先红（spec §6 首条风险缓解）。
    """
    from datetime import datetime, timezone, timedelta
    from src.cli.app import _render_recent_summaries, CycleSummary
    from src.cli.display import (
        _SUMMARIES_MARKER, _parse_injected_summaries, _extract_summary_fields,
    )

    now = datetime(2026, 5, 31, 8, 0, 0, tzinfo=timezone.utc)
    summaries = [
        # 最旧：valid 5-field（ASC 源序 → 列表首）
        CycleSummary(
            id=1, cycle_id="824e2233aa", triggered_by="conditional",
            decision=(
                "**(1) Stance** — flat; cascade compressing.\n"
                "**(2) Active commitments** — none.\n"
                "**(3) This cycle delta** — closed short.\n"
                "**(4) Thesis & invalidation** — bearish; invalidation > 74,200.\n"
                "**(5) Watch list** — 73,000 support."
            ),
            execution_status="ok", created_at=now - timedelta(minutes=35),
        ),
        # 中間：forensic（decision=None → _render_empty_decision_body，NULL 块头变体）
        CycleSummary(
            id=2, cycle_id="47d5ef0199", triggered_by="scheduled",
            decision=None, execution_status="usage_limit_exceeded",
            created_at=now - timedelta(minutes=34),
        ),
        # 最新：valid 5-field（ASC 源序 → 列表尾）
        CycleSummary(
            id=3, cycle_id="00f7abcd55", triggered_by="alert",
            decision=(
                "**(1) Stance** — flat; MA20 reclaim confirmed.\n"
                "**(2) Active commitments** — alert above 73,384.\n"
                "**(3) This cycle delta** — updated alert.\n"
                "**(4) Thesis & invalidation** — bearish macro intact; invalidation > 74,200.\n"
                "**(5) Watch list** — 74,200 resistance."
            ),
            execution_status="ok", created_at=now - timedelta(minutes=8),
        ),
    ]
    block = _render_recent_summaries(summaries, now=now)
    assert block.startswith(_SUMMARIES_MARKER)  # 标记逐字一致 — 否则 _split_wake_prompt 失配

    # 模拟完整 snapshot 的后半（标记之后部分）
    summaries_half = block[len(_SUMMARIES_MARKER):]
    blocks = _parse_injected_summaries(summaries_half)

    # 切块 + 反转：3 条，newest-first
    assert [b[0] for b in blocks] == ["00f7", "47d5", "824e"]
    assert blocks[0][1] == "8 min ago"
    assert blocks[1][1] == "34 min ago"     # NULL 块头变体仍取到 ago
    # 字段提取
    f_new = _extract_summary_fields(blocks[0][2])   # 00f7 valid
    assert 1 in f_new and 4 in f_new and len(f_new) == 5
    f_forensic = _extract_summary_fields(blocks[1][2])  # 47d5 forensic body → 兜底
    assert f_forensic == {}


def test_thesis_cap_truncates_pathological_long():
    """最近一条 Thesis 超 _CONTEXT_THESIS_CAP → ASCII ' ... [+N chars]' 截断。"""
    from src.cli.display import _render_carried_block, _CONTEXT_THESIS_CAP
    long_thesis = "x" * (_CONTEXT_THESIS_CAP + 500)
    body = f"**(1) Stance** — flat.\n**(4) Thesis & invalidation** — {long_thesis}"
    text = "\n".join(_render_carried_block("00f7", "8 min ago", body, is_newest=True))
    assert "... [+" in text and "chars]" in text


def test_fallback_whole_block_cap():
    """兜底 whole-block 超 _CONTEXT_FALLBACK_CAP → 截断。"""
    from src.cli.display import _render_carried_block, _CONTEXT_FALLBACK_CAP
    body = "y" * (_CONTEXT_FALLBACK_CAP + 300)  # 无 (N) marker → 兜底
    text = "\n".join(_render_carried_block("824e", "35 min ago", body, is_newest=False))
    assert "... [+" in text


def test_markdown_stars_stripped_in_render():
    """log 不解释 markdown：字面 ** 被剥离。"""
    from src.cli.display import _render_carried_block
    body = "**(1) Stance** — **flat** near MA20.\n**(4) Thesis & invalidation** — bearish."
    text = "\n".join(_render_carried_block("00f7", "8 min ago", body, is_newest=True))
    assert "**" not in text
