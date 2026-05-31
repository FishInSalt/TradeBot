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
