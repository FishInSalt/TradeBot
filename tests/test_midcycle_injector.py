"""§2 MidCycleEventInjector capability + TradingDeps 注入字段单测。"""
from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock

import pytest


def make_deps(**overrides):
    """最小 TradingDeps（仿 test_tool_call_recorder.make_deps，注入字段可覆写）。"""
    from src.agent.trader import TradingDeps
    kwargs = dict(
        symbol="BTC/USDT:USDT",
        timeframe="15m",
        market_data=MagicMock(),
        exchange=MagicMock(),
        technical=MagicMock(),
        memory=AsyncMock(),
        session_id="sess-test",
        cycle_id="cyc-test",
    )
    kwargs.update(overrides)
    return TradingDeps(**kwargs)


def test_trading_deps_injection_fields_default_off():
    """新字段默认值 = 注入关闭：fn 双 None、累积器空、cycle_started_at None。"""
    deps = make_deps()
    assert deps.drain_pending_events_fn is None
    assert deps.requeue_events_fn is None
    assert deps.injected_events_log == []
    assert deps.cycle_started_at is None


def test_trading_deps_log_not_shared_between_instances():
    """default_factory 隔离：两实例不共享累积器 list。"""
    d1, d2 = make_deps(), make_deps()
    d1.injected_events_log.append({"x": 1})
    assert d2.injected_events_log == []


# === capability 行为 ===

def make_ctx(deps):
    ctx = MagicMock()
    ctx.deps = deps
    return ctx


def make_call(tool_name: str = "get_position"):
    call = MagicMock()
    call.tool_name = tool_name
    return call


def make_fill(ts_ms: int | None = None, **overrides):
    """部分平仓 FillEvent（pnl 非 None / is_full_close=False）——渲染走 gross 分支，
    不 await get_contract_size，单测无需 exchange fixture。"""
    from src.integrations.exchange.base import FillEvent
    if ts_ms is None:
        ts_ms = int(datetime.now(timezone.utc).timestamp() * 1000)
    kwargs = dict(
        order_id="o1", symbol="BTC/USDT:USDT", side="sell", position_side="short",
        trigger_reason="stop", fill_price=61800.0, amount=59.67, fee=36.88,
        pnl=-65.70, timestamp=ts_ms, is_full_close=False, entry_price=None,
    )
    kwargs.update(overrides)
    return FillEvent(**kwargs)


def make_alert(ts_ms: int | None = None):
    from src.integrations.exchange.base import PriceLevelAlertInfo
    if ts_ms is None:
        ts_ms = int(datetime.now(timezone.utc).timestamp() * 1000)
    return PriceLevelAlertInfo(
        alert_id="f3fd8021", symbol="BTC/USDT:USDT", current_price=61630.50,
        target_price=61634.00, direction="below",
        reasoning="22:00 1H bar low break revives breakdown thesis", timestamp=ts_ms,
    )


def wired_deps(events):
    """带 stub drain/requeue 的 deps：drain 首调返回 events、再调返回 []；requeue 录参。"""
    state = {"queue": list(events), "requeued": []}
    deps = make_deps(
        cycle_started_at=datetime.now(timezone.utc),
    )
    def drain():
        out, state["queue"] = state["queue"], []
        return out
    deps.drain_pending_events_fn = drain
    deps.requeue_events_fn = lambda evs: state["requeued"].extend(evs)
    return deps, state


async def test_injects_block_on_success():
    """成功返回 + 堆非空 → result 追加注入块；header breakdown / fill 在前 / 取证记录。"""
    from src.services.midcycle_injector import MidCycleEventInjector

    fill, alert = make_fill(), make_alert()
    deps, state = wired_deps([("conditional", fill), ("alert", alert)])

    async def handler(args):
        return "Position: short 59.67 contracts"

    result = await MidCycleEventInjector().wrap_tool_execute(
        make_ctx(deps), call=make_call("get_position"),
        tool_def=MagicMock(), args={}, handler=handler,
    )

    assert result.startswith("Position: short 59.67 contracts")
    assert "=== NEW EVENTS TRIGGERED (1 fill, 1 alert) ===" in result
    # 事件正文零新格式：与 wake 块同前缀；fill 行在 alert 行之前
    assert result.index("IMPORTANT EVENT:") < result.index("PRICE LEVEL ALERT:")
    # 相对时间基准 = 注入时刻（事件刚发生 → 秒级 age）
    assert "just now" in result or "sec ago" in result
    # 取证累积器：每事件一条，含 raw 回滚句柄
    assert len(deps.injected_events_log) == 2
    rec = deps.injected_events_log[0]
    assert rec["event"]["type"] == "fill"
    assert rec["raw"] == ("conditional", fill)
    assert rec["after_tool"] == "get_position"
    assert isinstance(rec["offset_ms"], int) and rec["offset_ms"] >= 0
    assert state["requeued"] == []


async def test_percentage_alert_injectable():
    """三类事件全注入（scope 演化③）：percentage_alert 渲 PRICE VOLATILITY ALERT。"""
    from src.services.midcycle_injector import MidCycleEventInjector
    from src.services.price_alert import AlertInfo

    pct = AlertInfo(
        symbol="BTC/USDT:USDT", current_price=60000.0, reference_price=61500.0,
        change_pct=-2.44, window_minutes=15,
        timestamp=int(datetime.now(timezone.utc).timestamp() * 1000),
    )
    deps, _ = wired_deps([("alert", pct)])

    async def handler(args):
        return "ok"

    result = await MidCycleEventInjector().wrap_tool_execute(
        make_ctx(deps), call=make_call(), tool_def=MagicMock(), args={}, handler=handler,
    )
    assert "=== NEW EVENTS TRIGGERED (1 alert) ===" in result
    assert "PRICE VOLATILITY ALERT:" in result
    assert deps.injected_events_log[0]["event"]["type"] == "percentage_alert"


async def test_fns_none_passthrough():
    """drain/requeue 任一 None → 直通不弹堆（spec §2 步骤 1）。"""
    from src.services.midcycle_injector import MidCycleEventInjector

    drain_called = []
    deps = make_deps()
    deps.drain_pending_events_fn = lambda: drain_called.append(1) or []
    deps.requeue_events_fn = None   # 半态 → 注入关闭

    async def handler(args):
        return "untouched"

    result = await MidCycleEventInjector().wrap_tool_execute(
        make_ctx(deps), call=make_call(), tool_def=MagicMock(), args={}, handler=handler,
    )
    assert result == "untouched"
    assert drain_called == [], "半态接线不得弹堆"


async def test_non_str_result_passthrough():
    from src.services.midcycle_injector import MidCycleEventInjector
    deps, state = wired_deps([("conditional", make_fill())])

    async def handler(args):
        return {"not": "a string"}

    result = await MidCycleEventInjector().wrap_tool_execute(
        make_ctx(deps), call=make_call(), tool_def=MagicMock(), args={}, handler=handler,
    )
    assert result == {"not": "a string"}
    assert len(state["queue"]) == 1, "非 str 返回不弹堆——事件留堆走兜底"


async def test_empty_heap_passthrough():
    from src.services.midcycle_injector import MidCycleEventInjector
    deps, _ = wired_deps([])

    async def handler(args):
        return "plain"

    result = await MidCycleEventInjector().wrap_tool_execute(
        make_ctx(deps), call=make_call(), tool_def=MagicMock(), args={}, handler=handler,
    )
    assert result == "plain"
    assert deps.injected_events_log == []


async def test_handler_exception_propagates_without_drain():
    """handler 抛异常 → 直通不弹堆（事件留堆走兜底唤醒，spec §2 失败语义）。"""
    from src.services.midcycle_injector import MidCycleEventInjector

    drain_called = []
    deps = make_deps()
    deps.drain_pending_events_fn = lambda: drain_called.append(1) or []
    deps.requeue_events_fn = lambda evs: None

    async def handler(args):
        raise ValueError("tool blew up")

    with pytest.raises(ValueError):
        await MidCycleEventInjector().wrap_tool_execute(
            make_ctx(deps), call=make_call(), tool_def=MagicMock(), args={}, handler=handler,
        )
    assert drain_called == []


async def test_control_flow_signal_propagates_without_drain():
    """ModelRetry 等控制流信号直通（与 ToolCallRecorder 同集合，spec §2）。"""
    from pydantic_ai.exceptions import ModelRetry
    from src.services.midcycle_injector import MidCycleEventInjector

    drain_called = []
    deps = make_deps()
    deps.drain_pending_events_fn = lambda: drain_called.append(1) or []
    deps.requeue_events_fn = lambda evs: None

    async def handler(args):
        raise ModelRetry("try different args")

    with pytest.raises(ModelRetry):
        await MidCycleEventInjector().wrap_tool_execute(
            make_ctx(deps), call=make_call(), tool_def=MagicMock(), args={}, handler=handler,
        )
    assert drain_called == []


async def test_render_failure_requeues_batch_and_returns_original():
    """渲染异常 → 整批 requeue + 返回原始 result + 累积器零残留（spec §2 步骤 3/4 失败）。

    用畸形 context（缺渲染所需属性的裸 object）触发渲染层 AttributeError。
    """
    from src.services.midcycle_injector import MidCycleEventInjector

    broken = object()   # 无 trigger_reason/symbol 等属性 → _render_event_block 抛
    deps, state = wired_deps([("conditional", broken)])

    async def handler(args):
        return "original result"

    result = await MidCycleEventInjector().wrap_tool_execute(
        make_ctx(deps), call=make_call(), tool_def=MagicMock(), args={}, handler=handler,
    )
    assert result == "original result", "失败时绝不污染工具返回"
    assert state["requeued"] == [("conditional", broken)], "整批回滚到堆"
    assert deps.injected_events_log == [], "失败批次不得留取证残留（不变量：注入成功 ⇔ 有记录）"


async def test_cancelled_between_drain_and_requeue_requeues_then_propagates():
    """BaseException（如 CancelledError）落在 drain 后：requeue 再传播——never-drop
    不变量无条件成立（quality review I-1：pydantic-ai 并行工具失败 cancel 兄弟 task，
    取消若吞批则事件永失且无兜底唤醒）。"""
    import asyncio
    from unittest.mock import patch

    from src.services.midcycle_injector import MidCycleEventInjector

    deps, state = wired_deps([("conditional", make_fill())])

    async def handler(args):
        return "result"

    with patch(
        "src.services.midcycle_injector._render_injection_block",
        side_effect=asyncio.CancelledError,
    ):
        with pytest.raises(asyncio.CancelledError):
            await MidCycleEventInjector().wrap_tool_execute(
                make_ctx(deps), call=make_call(), tool_def=MagicMock(), args={}, handler=handler,
            )

    assert len(state["requeued"]) == 1, "取消窗口内的批必须回滚到堆"
    assert deps.injected_events_log == [], "无取证残留"


def test_registration_order_injector_outermost():
    """注册序锁定（spec §2 框架交互 2）：[Injector, Recorder] → combined.py reversed()
    链式包裹下 Injector 在最外层，注入发生在 Recorder 计时闭合之后，duration_ms
    不含注入耗时。锚 pydantic-ai 1.78 私有属性 _root_capability——版本升级断裂即
    本测试要捕的回归信号。"""
    from src.agent.trader import create_trader_agent
    from src.config import PersonaConfig

    agent = create_trader_agent(model="test", persona_config=PersonaConfig())
    caps = agent._root_capability.capabilities
    assert [type(c).__name__ for c in caps] == ["MidCycleEventInjector", "ToolCallRecorder"]
