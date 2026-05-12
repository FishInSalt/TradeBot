"""Tool-call metrics recorder — pydantic_ai capability for observation-period埋点.

See docs/superpowers/specs/2026-04-20-tool-call-metrics-design.md §3.1 for design.
"""
from __future__ import annotations

import json
import logging
import time
from contextvars import ContextVar
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from pydantic_ai import RunContext
from pydantic_ai.capabilities import (
    AbstractCapability,
    ValidatedToolArgs,
    WrapToolExecuteHandler,
)
from pydantic_ai.exceptions import (
    ApprovalRequired,
    CallDeferred,
    ModelRetry,
    SkipToolExecution,
    ToolRetryError,
)
from pydantic_ai.messages import ToolCallPart
from pydantic_ai.tools import ToolDefinition

from src.storage.database import get_session
from src.storage.models import ToolCall

if TYPE_CHECKING:
    # 避免 trader.py ↔ tool_call_recorder.py 循环 import
    # (create_trader_agent() 内部函数级懒加载本模块，见 trader.py)
    from src.agent.trader import TradingDeps

logger = logging.getLogger(__name__)

# pydantic_ai 控制流信号 — retry / approval / deferral，不是真错，也不是 ok。
# 直通不记 metrics 行，否则未来启用 approval / retry flow 时产生假阳性 error。
_CONTROL_FLOW_EXCEPTIONS = (
    ApprovalRequired,
    CallDeferred,
    ModelRetry,
    SkipToolExecution,
    ToolRetryError,
)

# R2-4 §4.2 — biz_error side-channel
# 工具内 note_biz_error("xxx") 上报；wrap_tool_execute 在 handler 返回后读
# LLM 看到的工具返回字符串不变（fact 透明，零行为改造）
_biz_error_type: ContextVar[str | None] = ContextVar(
    "tool_call_biz_error_type", default=None
)

BIZ_ERROR_TYPES: frozenset[str] = frozenset({
    "invalid_threshold_range",        # set_price_alert 阈值越界
    "invalid_alert_id_format",        # cancel_price_level_alert 协议错（非 8-char hex）
    "alert_not_found",                # update_price_level_alert 状态错（已触发 / 已被 close-fill 清理 / 未注册）
})


def note_biz_error(error_type: str) -> None:
    """工具内调用以标记本次 tool call 为业务失败。

    LLM 看到的返回字符串不变（fact 透明）；ToolCallRecorder.wrap_tool_execute
    在 handler 返回后读取此 ContextVar，写入 tool_calls.status='biz_error',
    error_type=<type>。

    拼错保护策略：fail-soft（运行期 logger.error + 跳过 ContextVar set）。
    drift guard 测试期 strict 检查（test_biz_error_types_drift_guard）。

    CAVEAT: 必须在工具协程主体内调用，不要在 asyncio.gather 子 task 内调
    （Python ContextVar 子 task 修改不会回流父 frame）。
    """
    if error_type not in BIZ_ERROR_TYPES:
        logger.error(
            "note_biz_error called with unknown type: %r — drift guard expected to catch this",
            error_type,
        )
        return
    _biz_error_type.set(error_type)


@dataclass
class ToolCallRecorder(AbstractCapability["TradingDeps"]):  # 字符串前向引用
    """从 ctx.deps.db_engine 读 engine; recorder 本身无字段。

    依赖 pydantic_ai 契约 (v1.78 已验证): capability 收到的 ctx.deps 即
    agent.run(deps=...) 传入的对象。集成测试隐式验证。
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
        start = time.monotonic()
        # R2-4 §4.2 — reset per-call (隔离嵌套 / 异步任务 / 跨调用泄漏)
        token = _biz_error_type.set(None)
        status, error_type = "ok", None
        skip_record = False
        try:
            result = await handler(args)
        except _CONTROL_FLOW_EXCEPTIONS:
            skip_record = True  # 控制流信号直通
            raise
        except Exception as e:
            status, error_type = "error", type(e).__name__
            raise
        else:
            # handler 成功返回 — 检查是否被 note_biz_error 标记
            biz = _biz_error_type.get()
            if biz is not None:
                status, error_type = "biz_error", biz
            return result
        finally:
            _biz_error_type.reset(token)
            if not skip_record:
                try:
                    duration_ms = int((time.monotonic() - start) * 1000)
                    if ctx.deps.cycle_id is None:
                        raise RuntimeError(
                            "cycle_id must be set on TradingDeps before tool call"
                        )
                    if ctx.deps.db_engine is None:
                        raise RuntimeError(
                            "db_engine must be set on TradingDeps"
                        )
                    # 序列化 args，strip reasoning（spec §T0-2 (b)）
                    # 用 pydantic-ai 内置 helper 处理 str|dict|None 三态 + INVALID_JSON_KEY 兜底
                    args_dict = dict(call.args_as_dict())   # shallow copy: pydantic-ai args_as_dict() returns self.args ref for dict inputs (messages.py:1660); avoid mutating live ToolCallPart
                    args_dict.pop("reasoning", None)   # strip 与 trade_actions.reasoning 重复存储
                    args_serialized = json.dumps(args_dict, ensure_ascii=False) if args_dict else None
                    if args_serialized and len(args_serialized) > 4000:
                        # char-level 截断，与 reasoning 一致；spec §4.4 明牌选择"保留 partial JSON 给分析师"
                        # 而非截断后置 None。99% 工具 args < 4000 chars，cap 仅做 outlier 防御信号。
                        # 消费方契约：读 args 时必须 try/except json.JSONDecodeError —— 截断的 outlier 行
                        # JSON 不完整，是预期而非 bug。需要严格 JSON 一致性的下游应在 4000 边界另存 partial=true 标记。
                        args_serialized = args_serialized[:4000]

                    insert_start = time.monotonic()
                    async with get_session(ctx.deps.db_engine) as session:
                        session.add(ToolCall(
                            session_id=ctx.deps.session_id,
                            cycle_id=ctx.deps.cycle_id,
                            tool_name=call.tool_name,
                            status=status,
                            duration_ms=duration_ms,
                            error_type=error_type,
                            args=args_serialized,            # ← 新增 (Iter 3 §G2)
                        ))
                        await session.commit()
                    insert_ms = (time.monotonic() - insert_start) * 1000
                    logger.debug(
                        "tool_call_insert_ms=%.1f tool=%s", insert_ms, call.tool_name
                    )
                except Exception as rec_err:
                    # CAVEAT: swallow protects `return result` (line 121) — Python finally
                    # 内未捕获异常会顶替 return → 破坏 LLM 透明度契约（recorder 是副作用）。
                    logger.error(
                        "tool_call_recorder failed for %s: %s",
                        call.tool_name, rec_err,
                    )
