"""Mid-cycle event injection — pydantic_ai capability (spec 2026-06-11).

cycle 运行中触发的事件（fill / price_level_alert / percentage_alert）入
scheduler 堆后，本 capability 在下一次工具成功返回时全弹（drain）并把 §3 共享
渲染器渲出的事件块追加在工具返回文本之后——注入即消费。任何注入路径失败整批
requeue 回堆，事件退化为兜底唤醒送达（送达保证降速不降级，spec §2）。

Header 常量 `NEW EVENTS TRIGGERED` 同时是 persona 送达契约（persona.py wake
bullet）与 narrative forensic 的 grep 锚点——drift guard 断言两处逐字一致。
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any

from pydantic_ai import RunContext
from pydantic_ai.capabilities import (
    AbstractCapability,
    ValidatedToolArgs,
    WrapToolExecuteHandler,
)
from pydantic_ai.messages import ToolCallPart
from pydantic_ai.tools import ToolDefinition

from src.services.cycle_capture import _capture_trigger_context
from src.services.event_render import _format_event_breakdown, _render_event_block

if TYPE_CHECKING:
    # 避免 trader.py ↔ midcycle_injector.py 循环 import（同 tool_call_recorder 模式）
    from src.agent.trader import TradingDeps

logger = logging.getLogger(__name__)

INJECTION_HEADER_PREFIX = "NEW EVENTS TRIGGERED"


async def _render_injection_block(
    deps: TradingDeps, events: list[tuple[str, Any]], now: datetime,
) -> str:
    """§4 注入块：`=== NEW EVENTS TRIGGERED ({breakdown}) ===` + 逐事件块。

    事件正文零新格式——逐条复用 §3 渲染器（与 wake prompt 块逐字同构）；渲染块
    自带的 `\\n\\n` 前缀归一为单行分隔；排序 = 堆优先级序（fill 在前，drain 已序）。
    相对时间基准 = 注入时刻 `now`（"23s ago" 指距此刻，非 cycle 起点）。
    """
    lines: list[str] = []
    for trigger_type, context in events:
        block = await _render_event_block(deps, trigger_type, context, now)
        if block:
            lines.append(block.lstrip("\n"))
    header = f"=== {INJECTION_HEADER_PREFIX} ({_format_event_breakdown(events)}) ==="
    return "\n\n" + header + "\n" + "\n".join(lines)


@dataclass
class MidCycleEventInjector(AbstractCapability["TradingDeps"]):
    """工具边界事件注入（spec §2）。无字段；状态全在 ctx.deps。

    注册序契约：capabilities=[MidCycleEventInjector(), ToolCallRecorder()] ——
    pydantic-ai combined.py 用 reversed() 链式包裹，首注册在最外层；注入发生在
    Recorder 计时闭合之后，tool_calls.duration_ms 不含注入耗时（工具本体时长语义
    保持）。测试锁定该顺序（test_registration_order_injector_outermost）。
    """

    async def wrap_tool_execute(
        self,
        ctx: RunContext[TradingDeps],
        *,
        call: ToolCallPart,
        tool_def: ToolDefinition,
        args: ValidatedToolArgs,
        handler: WrapToolExecuteHandler,
    ) -> Any:
        # handler 抛出的任何异常（真错或 ModelRetry 等控制流信号）从这里直接
        # 传播——不弹堆，事件留堆走兜底唤醒通道（spec §2 失败语义第一条）。
        result = await handler(args)

        deps = ctx.deps
        if (
            deps.drain_pending_events_fn is None
            or deps.requeue_events_fn is None       # 半态防御：可弹必可回滚
            or not isinstance(result, str)
        ):
            return result

        events = deps.drain_pending_events_fn()
        if not events:
            return result

        try:
            now = datetime.now(timezone.utc)
            block = await _render_injection_block(deps, events, now)
            offset_ms = (
                int((now - deps.cycle_started_at).total_seconds() * 1000)
                if deps.cycle_started_at is not None
                else None
            )
            # 先完整构建本批取证记录，再一次性 extend——构建期任何异常走 except
            # 整批回滚且累积器零残留（不变量：注入对存活 run 成立 ⇔ 有取证记录，
            # spec §6）。`raw` 是被丢弃 run 回滚的 requeue 句柄，落库时剥离。
            records = [
                {
                    "event": _capture_trigger_context(deps.cycle_id or "", tt, evt_ctx),
                    "raw": (tt, evt_ctx),
                    "after_tool": call.tool_name,
                    "offset_ms": offset_ms,
                }
                for tt, evt_ctx in events
            ]
        except Exception:
            # 渲染/记录失败 → 整批回滚，返回原始 result（绝不污染工具返回，
            # 与 ToolCallRecorder swallow 契约一致）。事件经兜底通道重新送达。
            logger.warning(
                "mid-cycle injection failed after %s; requeueing %d event(s)",
                call.tool_name, len(events), exc_info=True,
            )
            deps.requeue_events_fn(events)
            return result
        except BaseException:
            # CancelledError 等 BaseException：requeue 后再传播——drain 与 requeue 之间
            # 的取消窗口不得吞批（spec §2 never-drop 不变量须无条件成立，不依赖渲染器
            # 当前 await 拓扑；pydantic-ai 并行工具失败会 cancel 兄弟 task）。
            deps.requeue_events_fn(events)
            raise

        deps.injected_events_log.extend(records)   # 记录先于交付（spec §2 步骤 4/5）
        return result + block
