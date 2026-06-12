"""§9 集成：真实 agent.run（TestModel）+ 真实 Scheduler + SimulatedExchange。

happy path：cycle 运行中事件入堆 → 下一工具返回含注入块 → 堆空（无 back-to-back
残留）→ injected_events 列有记录。反向断言：同步市价不产生自注入。
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from unittest.mock import AsyncMock

import pytest
from pydantic_ai import models
from pydantic_ai.messages import ModelRequest, ToolReturnPart
from sqlalchemy import select

from src.storage.models import AgentCycle

models.ALLOW_MODEL_REQUESTS = False


def _now_ms() -> int:
    return int(datetime.now(timezone.utc).timestamp() * 1000)


@pytest.mark.asyncio
async def test_midcycle_fill_injected_at_next_tool_boundary(deps_factory, db_engine, db_session):
    from pydantic_ai.models.test import TestModel
    from src.agent.trader import create_trader_agent
    from src.cli.app import TokenBudget, run_agent_cycle
    from src.config import PersonaConfig
    from src.integrations.exchange.base import FillEvent
    from src.scheduler.scheduler import Scheduler

    agent = create_trader_agent(model="test", persona_config=PersonaConfig())
    scheduler = Scheduler(interval_seconds=999, callback=AsyncMock())

    deps = deps_factory()
    deps.drain_pending_events_fn = scheduler.drain_pending_events
    deps.requeue_events_fn = scheduler.requeue_events

    # 部分平仓 fill（is_full_close=False）——渲染不 await get_contract_size，
    # 免去 sim exchange start() 期 contractSize 缓存 fixture 依赖。
    fill = FillEvent(
        order_id="o1", symbol="BTC/USDT:USDT", side="sell", position_side="short",
        trigger_reason="stop", fill_price=61800.0, amount=59.67, fee=36.88,
        pnl=-65.70, timestamp=_now_ms(), is_full_close=False, entry_price=None,
    )
    # 事件在 cycle 开始前已入堆 ≡ mid-cycle 触发后的堆状态（drain 时机等价；
    # "执行中途入堆" 的时序由 unit 层覆盖）。alert 先入、fill 后入——真 drain 的
    # 堆优先级序应让 fill 渲染在前（跨契约断言，Task 4 review M-1 闭环）。
    from src.integrations.exchange.base import PriceLevelAlertInfo
    alert = PriceLevelAlertInfo(
        alert_id="i9alert1", symbol="BTC/USDT:USDT", current_price=61630.50,
        target_price=61634.00, direction="below",
        reasoning="integration alert", timestamp=_now_ms(),
    )
    await scheduler.trigger("alert", alert)
    await scheduler.trigger("conditional", fill)

    result = await run_agent_cycle(
        agent, deps, [("scheduled", None)], TokenBudget(daily_max=10_000_000),
        db_engine, model=TestModel(call_tools=["get_position"]),
    )
    assert result is not None

    # ① 注入块出现在工具返回（ToolReturnPart.content）—— 存在性前提 gate（spec §2 框架交互 1）
    tool_returns = [
        p for m in result.new_messages() if isinstance(m, ModelRequest)
        for p in m.parts if isinstance(p, ToolReturnPart)
    ]
    injected = [p for p in tool_returns
                if "=== NEW EVENTS TRIGGERED (1 fill, 1 alert) ===" in str(p.content)]
    assert injected, "注入块必须进入 ToolReturnPart.content（LLM 可见通道）"
    content = str(injected[0].content)
    assert "IMPORTANT EVENT: stop triggered" in content
    # 跨契约：真 drain 堆优先级序 → fill 块先于 alert 块（入堆序相反）
    assert content.index("IMPORTANT EVENT:") < content.index("PRICE LEVEL ALERT:")

    # ② 注入即消费：堆空 → cycle 结束无 back-to-back conditional cycle
    assert scheduler.drain_pending_events() == []

    # ③ injected_events 取证列
    row = (await db_session.execute(
        select(AgentCycle).order_by(AgentCycle.id.desc()).limit(1))).scalar_one()
    assert row.execution_status == "ok"
    recs = json.loads(row.injected_events)
    assert len(recs) == 2
    assert recs[0]["event"]["type"] == "fill"
    assert recs[1]["event"]["type"] == "price_level_alert"
    assert recs[0]["after_tool"] == "get_position"
    assert recs[0]["offset_ms"] >= 0
    assert "raw" not in recs[0]


@pytest.mark.asyncio
async def test_sync_market_fill_no_self_injection(deps_factory):
    """反向断言（spec §9）：同步市价 open/close 不经 trigger()（simulated.py 仅
    matching-loop dispatch）——fill callback 零调用 ⇒ 堆零事件 ⇒ 无自注入。lock 防回归。"""
    from src.agent.tools_execution import close_position, open_position

    deps = deps_factory(initial_balance=1000.0)
    deps.cycle_id = "cyc-sync"

    fill_spy = AsyncMock()
    deps.exchange.on_fill(fill_spy)

    receipt = await open_position(deps, "long", 50.0, 3, reasoning="sync market open")
    assert receipt.startswith("Filled:"), f"同步市价未成交：\n{receipt}"
    receipt2 = await close_position(deps, reasoning="sync market close")
    assert "Filled" in receipt2 or "Closed" in receipt2

    assert fill_spy.await_count == 0, (
        "同步市价路径不得 dispatch fill 事件——若此断言红，说明 simulated.py 同步分支"
        "误接了 _dispatch_fill_event，市价单将产生自注入/自唤醒"
    )
