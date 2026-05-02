"""Sanity tests for tests/fixtures/cycle_fixtures.build_cycle_messages.

Builder 构造 in-memory list[ModelRequest | ModelResponse] 给 display.py 集成
测试用。结构参数参考 .working/verify_message_structure.py 实测：
- 每 ModelResponse 1 ThinkingPart at parts[0]（先于 ToolCallPart）
- 跨 ModelResponse 时序 = LLM 生成时序
- 最终 ModelResponse 含 ThinkingPart + TextPart
"""
from __future__ import annotations

from pydantic_ai.messages import ModelRequest, ModelResponse, TextPart, ThinkingPart, ToolCallPart, ToolReturnPart


def test_build_cycle_messages_minimal_no_tools():
    from tests.fixtures.cycle_fixtures import build_cycle_messages
    msgs = build_cycle_messages(
        thinking_segments=["Quick reasoning."],
        tool_call_segments=[[]],   # 1 segment, 0 tool calls
        final_text="Final decision text.",
    )
    response_msgs = [m for m in msgs if isinstance(m, ModelResponse)]
    assert len(response_msgs) == 1, "thinking_segments=1 → 1 ModelResponse"
    parts = response_msgs[0].parts
    assert isinstance(parts[0], ThinkingPart)
    assert parts[0].content == "Quick reasoning."
    assert any(isinstance(p, TextPart) and p.content == "Final decision text." for p in parts)


def test_build_cycle_messages_multi_segment_with_tools():
    from tests.fixtures.cycle_fixtures import build_cycle_messages
    msgs = build_cycle_messages(
        thinking_segments=["Think 1", "Think 2", "Think 3 final"],
        tool_call_segments=[
            [("get_market_data", {}, "BTC $75,000")],
            [("get_position", {}, "Short 0.265 @ 75350"),
             ("get_open_orders", {}, "1 orders")],
            [],  # final response: no tool calls, only ThinkingPart + TextPart
        ],
        final_text="Hold short.",
    )
    response_msgs = [m for m in msgs if isinstance(m, ModelResponse)]
    assert len(response_msgs) == 3
    # ModelResponse[0]: ThinkingPart + 1 ToolCallPart
    parts0 = response_msgs[0].parts
    assert isinstance(parts0[0], ThinkingPart)
    assert sum(1 for p in parts0 if isinstance(p, ToolCallPart)) == 1
    # ModelResponse[1]: ThinkingPart + 2 ToolCallPart
    parts1 = response_msgs[1].parts
    assert isinstance(parts1[0], ThinkingPart)
    assert sum(1 for p in parts1 if isinstance(p, ToolCallPart)) == 2
    # ModelResponse[2]: ThinkingPart + TextPart (no tools)
    parts2 = response_msgs[2].parts
    assert isinstance(parts2[0], ThinkingPart)
    assert any(isinstance(p, TextPart) for p in parts2)
    # Tool returns wired into ModelRequest with matching tool_call_id
    request_msgs = [m for m in msgs if isinstance(m, ModelRequest)]
    return_parts = [p for m in request_msgs for p in m.parts if isinstance(p, ToolReturnPart)]
    assert len(return_parts) == 3, "3 tool calls → 3 returns"
    # Returns reference the same tool_call_id as the calls
    call_ids = {p.tool_call_id for m in response_msgs for p in m.parts if isinstance(p, ToolCallPart)}
    return_ids = {p.tool_call_id for p in return_parts}
    assert call_ids == return_ids


def test_build_cycle_messages_no_thinking():
    """Non-thinking model: thinking_segments=[None, None] → ModelResponse 无 ThinkingPart."""
    from tests.fixtures.cycle_fixtures import build_cycle_messages
    msgs = build_cycle_messages(
        thinking_segments=[None, None],
        tool_call_segments=[[("get_position", {}, "FLAT")], []],
        final_text="No action.",
    )
    response_msgs = [m for m in msgs if isinstance(m, ModelResponse)]
    assert all(
        not any(isinstance(p, ThinkingPart) for p in r.parts)
        for r in response_msgs
    ), "thinking_segments=None → 全无 ThinkingPart"
