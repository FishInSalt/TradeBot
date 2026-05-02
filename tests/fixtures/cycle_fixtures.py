"""In-memory builder for pydantic-ai message lists used by display.py tests.

Why an in-memory builder (instead of dumping binary fixtures to git)：
- 不持久化二进制 fixture 避免反序列化执行风险
- pydantic-ai message classes 不全支持 model_dump_json round-trip（私有字段）
- 测试需要 thinking 长度等参数化控制——builder 比静态 fixture 更灵活

Structure mirrors .working/verify_message_structure.py 实测 (2026-05-02)：
- 每 ModelResponse parts 顺序: [ThinkingPart, ToolCallPart...]（最终: [ThinkingPart, TextPart]）
- ToolReturnPart 在后续 ModelRequest 内，通过 tool_call_id 关联
- 跨 ModelResponse 时序 = LLM 生成时序
"""
from __future__ import annotations

import uuid

from pydantic_ai.messages import (
    ModelRequest, ModelResponse, TextPart, ThinkingPart,
    ToolCallPart, ToolReturnPart,
)


def build_cycle_messages(
    thinking_segments: list[str | None],
    tool_call_segments: list[list[tuple[str, dict, str]]],
    final_text: str,
) -> list[ModelRequest | ModelResponse]:
    """Build a list of pydantic-ai messages mimicking 1 cycle.

    Args:
        thinking_segments: per-ModelResponse thinking content（None=该 Response 无 ThinkingPart）
        tool_call_segments: per-ModelResponse list of (tool_name, args_dict, return_content)
        final_text: text in the final ModelResponse's TextPart

    Length contract: len(thinking_segments) == len(tool_call_segments) == N，
    其中 N = ModelResponse 数。最终 ModelResponse 强制有 TextPart（final_text）。
    """
    if len(thinking_segments) != len(tool_call_segments):
        raise ValueError("thinking_segments and tool_call_segments must have equal length")
    n = len(thinking_segments)
    if n == 0:
        raise ValueError("at least 1 segment required")

    msgs: list[ModelRequest | ModelResponse] = []

    for i in range(n):
        # Build ModelResponse parts: [ThinkingPart?, ToolCallPart..., TextPart? if last]
        parts: list = []
        if thinking_segments[i] is not None:
            parts.append(ThinkingPart(content=thinking_segments[i]))
        tool_calls_for_response: list[ToolCallPart] = []
        for tool_name, args_dict, _ret_content in tool_call_segments[i]:
            tcp = ToolCallPart(
                tool_name=tool_name,
                args=args_dict,
                tool_call_id=f"call_{uuid.uuid4().hex[:8]}",
            )
            parts.append(tcp)
            tool_calls_for_response.append(tcp)
        if i == n - 1:
            parts.append(TextPart(content=final_text))
        msgs.append(ModelResponse(parts=parts))

        # If this Response had tool calls, append a ModelRequest with matching ToolReturnPart
        if tool_calls_for_response:
            return_parts = []
            for tcp, (_tn, _args, ret_content) in zip(
                tool_calls_for_response, tool_call_segments[i]
            ):
                return_parts.append(ToolReturnPart(
                    tool_name=tcp.tool_name,
                    tool_call_id=tcp.tool_call_id,
                    content=ret_content,
                ))
            msgs.append(ModelRequest(parts=return_parts))

    return msgs
