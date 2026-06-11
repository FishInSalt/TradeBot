# iter-midcycle-event-injection Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** cycle 运行中触发的事件（fill / price_level_alert / percentage_alert）在 agent 下一次工具调用返回时注入事实块，注入即消费——echo 结构性归零、back-to-back 唤醒大幅下降。

**Architecture:** pydantic-ai `AbstractCapability.wrap_tool_execute` 在工具成功返回后弹空 scheduler 堆并把 §3 共享渲染器渲出的事件块追加到工具返回字符串；任何失败 requeue 回堆走兜底唤醒通道（降速不降级）；被丢弃的 run（retry / forensic 终态）回滚本 run 注入。取证落 `agent_cycles.injected_events` 列 + `v_alert_lifecycle` 双通道。

**Tech Stack:** pydantic-ai 1.78 capabilities / SQLAlchemy + alembic / SQLite JSON1（json_each）/ pytest + TestModel。

**Spec:** `docs/superpowers/specs/2026-06-11-iter-midcycle-event-injection-design.md`（代码锚点基于 worktree HEAD `85fa217`）。

**约定**（全任务适用）：

- 工作目录固定 `/Users/z/Z/TradeBot/.claude/worktrees/iter-midcycle-fill-awareness`（worktree，分支 `feature/iter-midcycle-fill-awareness`）。subagent 起手必须 `cd` 到该绝对路径并验证分支（per memory `subagent-worktree-cwd`）。
- 测试命令一律 `.venv/bin/python -m pytest`（worktree 自带 venv，Python 3.13）。
- 基线：2263 passed, 9 skipped。
- commit message 不写 Co-Authored 之外的 trailer；中文 message 风格与 `git log` 既有一致。

---

### Task 1: §3 事件渲染器提取 → `src/services/event_render.py`

行为保持搬移：wake prompt 输出 byte-identical；新增 `_format_event_breakdown` 提取；时间基准形参 `cycle_started_at` → `now`。

**Files:**
- Create: `src/services/event_render.py`
- Create: `tests/test_event_render.py`
- Modify: `src/cli/app.py`（删除被搬移函数 :100-150 / :374-384 / :417-477，改 `_wake_header_line` N>1 分支，加 import）
- Modify: `tests/test_wake_event_timestamp.py` / `tests/test_iter_alert_trigger_id_unknown_tool_render.py` / `tests/test_cycle_summary_injection.py`（被搬移函数 import 路径 `src.cli.app` → `src.services.event_render`，断言零改动）

- [ ] **Step 1.1: 写 `_format_event_breakdown` 失败测试**

新建 `tests/test_event_render.py`：

```python
"""§3 共享事件渲染器 — _format_event_breakdown 提取 + 模块归属。

被搬移函数（_format_relative_time / _format_event_age / _wake_time_suffix /
_format_price_level_alert_trigger / _render_event_block）的行为测试留在原文件
（test_wake_event_timestamp.py 等），仅改 import 路径——断言即 byte-identical 回归。
"""
from __future__ import annotations


def test_breakdown_single_fill():
    from src.services.event_render import _format_event_breakdown
    assert _format_event_breakdown([("conditional", None)]) == "1 fill"


def test_breakdown_plural_alerts():
    from src.services.event_render import _format_event_breakdown
    events = [("alert", None), ("alert", None)]
    assert _format_event_breakdown(events) == "2 alerts"


def test_breakdown_mixed_fill_first():
    """fill 在前——与堆优先级 conditional < alert 一致（spec §4）。"""
    from src.services.event_render import _format_event_breakdown
    events = [("conditional", None), ("alert", None), ("alert", None)]
    assert _format_event_breakdown(events) == "1 fill, 2 alerts"


def test_breakdown_unknown_types_fallback():
    """全未知类型 → 'N events' fallback（与 _wake_header_line 原行为一致）。"""
    from src.services.event_render import _format_event_breakdown
    events = [("mystery", None), ("mystery", None)]
    assert _format_event_breakdown(events) == "2 events"
```

- [ ] **Step 1.2: 跑测试确认失败**

Run: `.venv/bin/python -m pytest tests/test_event_render.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'src.services.event_render'`

- [ ] **Step 1.3: 创建 `src/services/event_render.py`**

从 `src/cli/app.py` **逐字搬移**以下函数（行号基于 HEAD `85fa217`）：`_format_relative_time`（:100-120）、`_format_event_age`（:123-135）、`_wake_time_suffix`（:138-150）、`_format_price_level_alert_trigger`（:374-384）、`_render_event_block`（:417-477）。仅两处改动：① `_render_event_block` 形参 `cycle_started_at` 改 `now`——函数体内 **3 处**实参同步改（`:466` / `:475` 两处 `_wake_time_suffix(..., cycle_started_at)` + `:470` 一处 `_format_price_level_alert_trigger(context, cycle_started_at)`）；② 新增 `_format_event_breakdown`。模块骨架：

```python
"""Shared event-block renderers — wake prompt 与 mid-cycle injection 双路径单源.

iter-midcycle-event-injection §3: 这些函数原住 src/cli/app.py（wake prompt 专用）；
注入路径（src/services/midcycle_injector.py）需要逐字同构的事件块——信号唯一权威
来源，fee/PnL/equiv-round-trip 计算只存在一份，注入块与 wake 块数字永不打架。

时间基准形参统一为中性名 `now`：wake 路径传 cycle_started_at，注入路径传注入时刻
（spec §3——避免把"注入时刻"塞进名为 cycle_started_at 的参数造成语义重载）。
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from src.integrations.exchange.base import PriceLevelAlertInfo


# （此处依序粘入 _format_relative_time / _format_event_age / _wake_time_suffix /
#   _format_price_level_alert_trigger 四个函数，docstring 与函数体逐字保留）


def _format_event_breakdown(events: list[tuple[str, Any]]) -> str:
    """Breakdown 拼接唯一权威来源（spec §3）：`1 fill` / `2 alerts` / `1 fill, 2 alerts`，
    fill 在前（匹配堆优先级 conditional < alert）；无已知类型 → `N events` fallback。

    自 _wake_header_line N>1 分支（app.py:405-414）提取；wake header 与 §4 注入
    header 共用，零漂移面。
    """
    n_fill = sum(1 for tt, _ in events if tt == "conditional")
    n_alert = sum(1 for tt, _ in events if tt == "alert")
    parts: list[str] = []
    if n_fill:
        parts.append(f"{n_fill} fill{'s' if n_fill > 1 else ''}")
    if n_alert:
        parts.append(f"{n_alert} alert{'s' if n_alert > 1 else ''}")
    return ", ".join(parts) if parts else f"{len(events)} events"


# （此处粘入 _render_event_block，形参 cycle_started_at → now，签名变为：
#   async def _render_event_block(deps, trigger_type: str, context, now: datetime) -> str
#   docstring 末尾追加一行：`now` is the rendering time anchor — wake path passes
#   cycle_started_at, injection path passes the injection moment (spec §3).）
```

- [ ] **Step 1.4: 改 `src/cli/app.py`**

① 删除五个被搬移函数定义；② import 区（:47 附近，与 `from src.services.cycle_capture import ...` 相邻）加：

```python
from src.services.event_render import (
    _format_event_breakdown,
    _format_relative_time,
    _render_event_block,
    _wake_time_suffix,
)
```

（app.py 自身只消费这四个：`_format_relative_time` 用于 `_render_recent_summaries`，`_wake_time_suffix` 用于 `_wake_header_line` scheduled 分支，`_render_event_block` 用于 wake prompt 装配 :630，`_format_event_breakdown` 见下。`_format_event_age` / `_format_price_level_alert_trigger` 仅渲染器内部用，不 import。）

③ `_wake_header_line`（:387）N>1 分支改为调用提取函数（行为保持）：

```python
    n = len(events)
    breakdown = _format_event_breakdown(events)
    return f"You have been woken up by {n} triggers ({breakdown}) since the last cycle"
```

④ 检查 `PriceLevelAlertInfo` 在 app.py 是否仍有其他引用：`grep -n PriceLevelAlertInfo src/cli/app.py`——若仅剩 :51 import 行则从该行删除（`FillEvent` 保留，`_create_fill_handler` 用）。

- [ ] **Step 1.5: 更新三个测试文件 import 路径 + 关键字调用点改名**

① `tests/test_wake_event_timestamp.py` / `tests/test_iter_alert_trigger_id_unknown_tool_render.py` / `tests/test_cycle_summary_injection.py` 中所有 `from src.cli.app import <被搬移函数>` 改为 `from src.services.event_render import <同名>`（用 grep 逐处确认；`_wake_header_line` 留在 app.py，其 import 不动）。

② `tests/test_wake_event_timestamp.py` 有 **4 处关键字实参**随形参改名同步改：`:225` / `:234` / `:247` / `:268` 的 `cycle_started_at=now` → `now=now`（不改会抛 `TypeError: unexpected keyword argument`）。`:168/:177/:186` 一带与 `test_iter_alert_trigger_id_unknown_tool_render.py` 的两处均为位置实参，不受影响。

断言一行不改。

- [ ] **Step 1.6: 跑测试**

Run: `.venv/bin/python -m pytest tests/test_event_render.py tests/test_wake_event_timestamp.py tests/test_iter_alert_trigger_id_unknown_tool_render.py tests/test_cycle_summary_injection.py -v`
Expected: 全 PASS（既有断言即 byte-identical 回归）

- [ ] **Step 1.7: 全量回归 + commit**

Run: `.venv/bin/python -m pytest -q`
Expected: 2263+4 passed（新增 4 条 breakdown 测试）, 9 skipped

```bash
git add src/services/event_render.py tests/test_event_render.py src/cli/app.py tests/test_wake_event_timestamp.py tests/test_iter_alert_trigger_id_unknown_tool_render.py tests/test_cycle_summary_injection.py
git commit -m "refactor(render): 事件渲染器提取到 src/services/event_render —— wake/注入双路径单源（spec §3）

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

### Task 2: §1 Scheduler `drain_pending_events()` / `requeue_events()`

**Files:**
- Modify: `src/scheduler/scheduler.py`（`trigger` 方法后插入两个同步方法）
- Modify: `tests/test_scheduler.py`（追加 5 个测试）

- [ ] **Step 2.1: 写失败测试**

追加到 `tests/test_scheduler.py` 末尾：

```python
# === iter-midcycle-event-injection §1: drain / requeue ===

async def test_drain_pending_events_priority_order():
    """全弹且按堆优先级序：conditional > alert（入堆序相反也成立）。"""
    from src.scheduler.scheduler import Scheduler

    scheduler = Scheduler(interval_seconds=10, callback=None)
    await scheduler.trigger("alert", context="a1")
    await scheduler.trigger("conditional", context="f1")
    await scheduler.trigger("alert", context="a2")

    events = scheduler.drain_pending_events()
    assert events == [("conditional", "f1"), ("alert", "a1"), ("alert", "a2")]
    assert scheduler._pending_events == []


async def test_drain_pending_events_empty():
    from src.scheduler.scheduler import Scheduler
    scheduler = Scheduler(interval_seconds=10, callback=None)
    assert scheduler.drain_pending_events() == []


async def test_drain_over_5_warns(caplog):
    """一次弹出 >5 警告（信号不丢弃）——阈值 = 略高于 sim #17 mid-cycle 批峰值 3。"""
    import logging
    from src.scheduler.scheduler import Scheduler

    scheduler = Scheduler(interval_seconds=10, callback=None)
    for i in range(6):
        await scheduler.trigger("alert", context=f"a{i}")
    with caplog.at_level(logging.WARNING, logger="src.scheduler.scheduler"):
        events = scheduler.drain_pending_events()
    assert len(events) == 6
    assert any("mid-cycle drain" in r.message for r in caplog.records)


async def test_drain_then_sleep_no_spurious_wake():
    """注入清堆后回主循环：_interruptible_sleep 正常睡满（spec §1 不碰 _wake_event）。

    trigger 留下的 _wake_event set 残留被 sleep 入口 clear() 吸收（clear 在 wait 之前）。
    """
    import asyncio
    import time
    from src.scheduler.scheduler import Scheduler

    scheduler = Scheduler(interval_seconds=10, callback=None)
    await scheduler.trigger("alert", context="a1")
    scheduler.drain_pending_events()

    start = time.monotonic()
    await scheduler._interruptible_sleep(0.3)
    assert time.monotonic() - start >= 0.25, "drain 后睡眠被虚假唤醒"


async def test_requeue_restores_same_batch_and_sets_wake():
    """drain → requeue → 再 drain 同批等价（同批相对序保持）+ _wake_event 置位。"""
    from src.scheduler.scheduler import Scheduler

    scheduler = Scheduler(interval_seconds=10, callback=None)
    await scheduler.trigger("conditional", context="f1")
    await scheduler.trigger("alert", context="a1")
    batch = scheduler.drain_pending_events()

    scheduler._wake_event.clear()
    scheduler.requeue_events(batch)
    assert scheduler._wake_event.is_set(), "requeue 须置位 _wake_event（睡眠中的主循环要能接手）"
    assert scheduler.drain_pending_events() == batch
```

- [ ] **Step 2.2: 跑测试确认失败**

Run: `.venv/bin/python -m pytest tests/test_scheduler.py -v -k "drain or requeue"`
Expected: FAIL — `AttributeError: 'Scheduler' object has no attribute 'drain_pending_events'`

- [ ] **Step 2.3: 实现两方法**

`src/scheduler/scheduler.py`，插在 `trigger`（:61-68）之后：

```python
    def drain_pending_events(self) -> list[tuple[str, Any]]:
        """Pop ALL pending events in heap priority order. Used by mid-cycle injection
        (spec 2026-06-11 iter-midcycle-event-injection §1).

        Sync by design: heap ops have no await point (same asyncio loop, no race
        surface), and the injector's failure path must requeue without spawning a
        coroutine. Does NOT touch _wake_event: after an injection drain the heap is
        empty, so _interruptible_sleep's pending check won't fire and clear()
        precedes wait() — a leftover set() never produces a spurious wake.

        The >5 WARNING is a pure observation signal (slightly above sim #17's
        observed mid-cycle batch peak of 3), not a tuning knob; distinct from the
        main-loop drain cap-20 window (historical wake-batch peak 4).
        """
        events: list[tuple[str, Any]] = []
        while self._pending_events:
            ev = heapq.heappop(self._pending_events)
            events.append((ev.trigger_type, ev.context))
        if len(events) > 5:
            logger.warning(
                "mid-cycle drain: %d events in one injection batch (types=%s)",
                len(events), _type_counts(events),
            )
        return events

    def requeue_events(self, events: list[tuple[str, Any]]) -> None:
        """Push events back onto the heap — injection-failure rollback handle
        (spec §1/§2). Delivery degrades to the wake fallback channel, never drops.

        Sequence numbers are re-assigned: same-batch relative order is preserved;
        cross-batch global FIFO is not guaranteed (heap consumption is by priority
        anyway). Sets _wake_event so a main loop already sleeping re-checks the heap.
        """
        for trigger_type, context in events:
            priority = _PRIORITY_MAP.get(trigger_type, 1)
            self._sequence_counter += 1
            heapq.heappush(
                self._pending_events,
                _TriggerEvent(priority, self._sequence_counter, trigger_type, context),
            )
        self._wake_event.set()
```

- [ ] **Step 2.4: 跑测试 + commit**

Run: `.venv/bin/python -m pytest tests/test_scheduler.py -v`
Expected: 全 PASS（既有 21 条 + 新 5 条）

```bash
git add src/scheduler/scheduler.py tests/test_scheduler.py
git commit -m "feat(scheduler): drain_pending_events / requeue_events 同步方法（spec §1）

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

### Task 3: TradingDeps 注入字段 + app.py 接线

**Files:**
- Modify: `src/agent/trader.py`（:4-5 imports + TradingDeps 字段）
- Modify: `src/cli/app.py`（:603 per-cycle 复位 + :1195 接线）
- Create: `tests/test_midcycle_injector.py`（先放 deps 默认值测试，Task 4 续写）

- [ ] **Step 3.1: 写失败测试**

新建 `tests/test_midcycle_injector.py`：

```python
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
```

- [ ] **Step 3.2: 跑测试确认失败**

Run: `.venv/bin/python -m pytest tests/test_midcycle_injector.py -v`
Expected: FAIL — `TypeError`/`AttributeError`（TradingDeps 无 `drain_pending_events_fn`）

- [ ] **Step 3.3: 改 `src/agent/trader.py`**

① :4-5 imports：

```python
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Literal
```

② TradingDeps 末尾（`cycle_id` 字段 :55 之后）追加：

```python
    # === iter-midcycle-event-injection（spec §1/§2/§6）===
    # 两 fn 须同时非 None 才启用注入（防"可弹不可回滚"半态）；None = 注入不可用，
    # capability 直通——单测/旧调用路径零侵入。app.py run() 接线（set_next_wake_fn 同模式）。
    drain_pending_events_fn: Callable[[], list[tuple[str, Any]]] | None = None
    requeue_events_fn: Callable[[list[tuple[str, Any]]], None] | None = None
    # 本 cycle 注入取证累积器（spec §6）；run_agent_cycle 设 cycle_id 处同步 clear()。
    injected_events_log: list[dict] = field(default_factory=list)
    # 注入 offset_ms 的时间基准；run_agent_cycle 与 cycle_id 一并设置。
    cycle_started_at: datetime | None = None
```

- [ ] **Step 3.4: 改 `src/cli/app.py` 两处**

① `run_agent_cycle` :603 处：

```python
    deps.cycle_id = cycle_id   # propagate to ToolCallRecorder via ctx.deps (§3.4 of spec)
    deps.cycle_started_at = cycle_started_at     # 注入 offset_ms 时间基准（spec §6）
    deps.injected_events_log.clear()             # per-cycle 复位（spec §6）
```

② `run()` 内 :1195 `deps.set_next_wake_fn = ...` 行之后：

```python
    # iter-midcycle-event-injection §1: 注入弹堆/回滚句柄（两者须同时接线）
    deps.drain_pending_events_fn = scheduler.drain_pending_events
    deps.requeue_events_fn = scheduler.requeue_events
```

- [ ] **Step 3.5: 跑测试 + commit**

Run: `.venv/bin/python -m pytest tests/test_midcycle_injector.py tests/test_tool_call_recorder.py -v`
Expected: 全 PASS

```bash
git add src/agent/trader.py src/cli/app.py tests/test_midcycle_injector.py
git commit -m "feat(deps): TradingDeps 注入字段（drain/requeue fn + 取证累积器）+ app 接线（spec §1/§6）

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

### Task 4: §2+§4 `MidCycleEventInjector` capability + 注册

**Files:**
- Create: `src/services/midcycle_injector.py`
- Modify: `src/agent/trader.py`（:96 懒加载 + :110 capabilities 注册）
- Modify: `tests/test_midcycle_injector.py`（追加 capability 测试）

- [ ] **Step 4.1: 写失败测试**

追加到 `tests/test_midcycle_injector.py`：

```python
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
```

- [ ] **Step 4.2: 跑测试确认失败**

Run: `.venv/bin/python -m pytest tests/test_midcycle_injector.py -v`
Expected: 新增项 FAIL — `ModuleNotFoundError: No module named 'src.services.midcycle_injector'`

- [ ] **Step 4.3: 创建 `src/services/midcycle_injector.py`**

```python
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
    deps: "TradingDeps", events: list[tuple[str, Any]], now: datetime,
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
        ctx: RunContext["TradingDeps"],
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

        deps.injected_events_log.extend(records)   # 记录先于交付（spec §2 步骤 4/5）
        return result + block
```

- [ ] **Step 4.4: 注册到 `src/agent/trader.py`**

① :96 懒加载处：

```python
    from src.services.midcycle_injector import MidCycleEventInjector
    from src.services.tool_call_recorder import ToolCallRecorder
```

② :110 注册（顺序即契约）：

```python
        # 顺序契约（spec §2）：combined.py reversed() 链式包裹 → 首注册在最外层。
        # Injector 必须最外层：注入发生在 Recorder 计时闭合之后，duration_ms 不含注入耗时。
        capabilities=[MidCycleEventInjector(), ToolCallRecorder()],
```

- [ ] **Step 4.5: 跑测试 + commit**

Run: `.venv/bin/python -m pytest tests/test_midcycle_injector.py tests/test_tool_call_recorder.py tests/test_tool_call_instrumentation.py -v`
Expected: 全 PASS（instrumentation e2e 验证双 capability 共存不破坏 recorder 链路）

```bash
git add src/services/midcycle_injector.py src/agent/trader.py tests/test_midcycle_injector.py
git commit -m "feat(injector): MidCycleEventInjector capability — 工具边界注入即消费（spec §2/§4）

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

### Task 5: §6 取证 — `injected_events` 列 + migration + 三写入点 + 被丢弃 run 回滚

**Files:**
- Modify: `src/storage/models.py`（AgentCycle :121 后加列）
- Create: `alembic/versions/<generated>_midcycle_injected_events.py`
- Modify: `src/cli/app.py`（回滚 helper + 3 个 AgentCycle 写入点）
- Create: `tests/test_midcycle_forensics.py`

- [ ] **Step 5.1: 写失败测试**

新建 `tests/test_midcycle_forensics.py`：

```python
"""§6 取证：injected_events 列三写入点 + §2 被丢弃 run ⇒ 注入回滚。"""
from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from sqlalchemy import select

from src.storage.models import AgentCycle


def _fake_record(raw=("conditional", "fill-sentinel")):
    return {"event": {"type": "fill"}, "raw": raw, "after_tool": "get_position", "offset_ms": 73000}


@pytest.mark.asyncio
async def test_injected_events_column_roundtrip(db_session):
    """新列可写可读 NULL / JSON 数组两态（spec §9 migration 验收）。"""
    db_session.add(AgentCycle(
        session_id="s-col", cycle_id="c1", triggered_by="scheduled",
        injected_events=json.dumps([{"event": {"type": "fill"}, "after_tool": "t", "offset_ms": 1}]),
    ))
    db_session.add(AgentCycle(session_id="s-col", cycle_id="c2", triggered_by="scheduled"))
    await db_session.commit()

    rows = (await db_session.execute(
        select(AgentCycle).where(AgentCycle.session_id == "s-col").order_by(AgentCycle.id)
    )).scalars().all()
    assert json.loads(rows[0].injected_events)[0]["after_tool"] == "t"
    assert rows[1].injected_events is None


def test_rollback_helper_requeues_and_clears():
    """_rollback_injected_events：requeue raw（同批序）+ 清空累积器。"""
    from src.cli.app import _rollback_injected_events
    from tests.test_midcycle_injector import make_deps

    requeued = []
    deps = make_deps()
    deps.requeue_events_fn = lambda evs: requeued.extend(evs)
    deps.injected_events_log.extend([
        _fake_record(("conditional", "f1")), _fake_record(("alert", "a1")),
    ])

    _rollback_injected_events(deps)
    assert requeued == [("conditional", "f1"), ("alert", "a1")]
    assert deps.injected_events_log == []


def test_rollback_helper_no_fn_just_clears():
    """requeue_events_fn 未接线（单测/旧路径）→ 只清空不炸。"""
    from src.cli.app import _rollback_injected_events
    from tests.test_midcycle_injector import make_deps

    deps = make_deps()
    deps.injected_events_log.append(_fake_record())
    _rollback_injected_events(deps)
    assert deps.injected_events_log == []


def _mock_agent_ok(deps_to_inject=None):
    """mock agent：run 时可选地向 deps 累积器塞注入记录（模拟 capability 行为）。"""
    from unittest.mock import MagicMock, AsyncMock
    mock_result = MagicMock()
    usage = MagicMock()
    usage.total_tokens = 100
    usage.details = {}
    usage.cache_read_tokens = 0
    usage.cache_write_tokens = 0
    usage.input_tokens = 50
    usage.output_tokens = 50
    mock_result.usage.return_value = usage
    mock_result.output = "decision text"
    mock_result.new_messages.return_value = []
    agent = MagicMock()
    agent.run = AsyncMock(return_value=mock_result)
    agent.model = MagicMock(model_name="test-model")
    return agent


@pytest.mark.asyncio
async def test_success_path_serializes_records_stripping_raw(deps_factory, db_engine, db_session):
    """成功路径：injected_events 落 accumulator 序列化，raw 字段剥离（spec §6）。"""
    from src.cli.app import TokenBudget, run_agent_cycle

    deps = deps_factory()
    agent = _mock_agent_ok()

    async def run_and_inject(*a, **kw):
        deps.injected_events_log.append(_fake_record())
        return agent._ok_result

    agent._ok_result = agent.run.return_value
    agent.run = AsyncMock(side_effect=run_and_inject)

    await run_agent_cycle(agent, deps, [("scheduled", None)], TokenBudget(daily_max=10**7), db_engine)

    row = (await db_session.execute(
        select(AgentCycle).order_by(AgentCycle.id.desc()).limit(1))).scalar_one()
    recs = json.loads(row.injected_events)
    assert recs[0]["after_tool"] == "get_position"
    assert "raw" not in recs[0]
    assert recs[0]["event"] == {"type": "fill"}


@pytest.mark.asyncio
async def test_usage_limit_rolls_back_and_nulls(deps_factory, db_engine, db_session):
    """usage_limit 终态：写库前 requeue + 清空 → injected_events 落 NULL（spec §2/§6）。"""
    from pydantic_ai.exceptions import UsageLimitExceeded
    from src.cli.app import TokenBudget, run_agent_cycle

    deps = deps_factory()
    requeued = []
    deps.requeue_events_fn = lambda evs: requeued.extend(evs)

    async def run_inject_then_blow(*a, **kw):
        deps.injected_events_log.append(_fake_record(("conditional", "f1")))
        raise UsageLimitExceeded("runaway")

    agent = _mock_agent_ok()
    agent.run = AsyncMock(side_effect=run_inject_then_blow)

    await run_agent_cycle(agent, deps, [("scheduled", None)], TokenBudget(daily_max=10**7), db_engine)

    row = (await db_session.execute(
        select(AgentCycle).order_by(AgentCycle.id.desc()).limit(1))).scalar_one()
    assert row.execution_status == "usage_limit_exceeded"
    assert row.injected_events is None
    assert requeued == [("conditional", "f1")]
    assert deps.injected_events_log == []


@pytest.mark.asyncio
async def test_transient_retry_rolls_back_attempt1_injections(deps_factory, db_engine, db_session):
    """retry 交互：attempt 1 注入后抛瞬时异常 → 重试前 requeue + 清空；
    存活 attempt（未再注入）→ 最终行 injected_events NULL（spec §2 被丢弃 run 规则）。"""
    from src.cli.app import TokenBudget, run_agent_cycle

    deps = deps_factory()
    requeued = []
    deps.requeue_events_fn = lambda evs: requeued.extend(evs)

    agent = _mock_agent_ok()
    ok_result = agent.run.return_value
    calls = {"n": 0}

    async def flaky(*a, **kw):
        calls["n"] += 1
        if calls["n"] == 1:
            deps.injected_events_log.append(_fake_record(("alert", "a1")))
            raise RuntimeError("transient")
        return ok_result

    agent.run = AsyncMock(side_effect=flaky)

    with patch("asyncio.sleep", new=AsyncMock()):
        await run_agent_cycle(agent, deps, [("scheduled", None)], TokenBudget(daily_max=10**7), db_engine)

    row = (await db_session.execute(
        select(AgentCycle).order_by(AgentCycle.id.desc()).limit(1))).scalar_one()
    assert row.execution_status == "ok"
    assert row.injected_events is None, "attempt 1 的注入已回滚，存活 attempt 未注入 → NULL"
    assert requeued == [("alert", "a1")], "重试前必须 requeue（事件经下一 attempt/兜底重新送达）"


@pytest.mark.asyncio
async def test_retry_exhausted_rolls_back_and_nulls(deps_factory, db_engine, db_session):
    """retry_exhausted 终态：3 attempt 各自回滚，forensic 行 injected_events NULL。"""
    from src.cli.app import TokenBudget, run_agent_cycle

    deps = deps_factory()
    requeued = []
    deps.requeue_events_fn = lambda evs: requeued.extend(evs)

    agent = _mock_agent_ok()

    async def always_fail(*a, **kw):
        deps.injected_events_log.append(_fake_record(("conditional", "f1")))
        raise RuntimeError("network down")

    agent.run = AsyncMock(side_effect=always_fail)

    with patch("asyncio.sleep", new=AsyncMock()):
        await run_agent_cycle(agent, deps, [("scheduled", None)], TokenBudget(daily_max=10**7), db_engine)

    row = (await db_session.execute(
        select(AgentCycle).order_by(AgentCycle.id.desc()).limit(1))).scalar_one()
    assert row.execution_status == "retry_exhausted"
    assert row.injected_events is None
    assert len(requeued) == 3, "每个被丢弃 attempt 的注入都回滚（3 attempt × 1 事件）"
```

- [ ] **Step 5.2: 跑测试确认失败**

Run: `.venv/bin/python -m pytest tests/test_midcycle_forensics.py -v`
Expected: FAIL — `AgentCycle` 无 `injected_events` 参数 / `_rollback_injected_events` 不存在

- [ ] **Step 5.3: 加 model 列 + migration**

① `src/storage/models.py` :121 `user_prompt_snapshot` 之后：

```python
    # iter-midcycle-event-injection §6: mid-cycle 注入事件取证（JSON 数组；NULL = 无注入，
    # 与 trigger_context 同形态）。元素 {"event": <单事件 capture>, "after_tool", "offset_ms"}；
    # 内存累积器中的 raw 回滚句柄落库时剥离。被丢弃 run（retry / forensic 终态）回滚后落 NULL。
    injected_events: Mapped[str | None] = mapped_column(Text, nullable=True)
```

② 生成 migration：

```bash
.venv/bin/alembic revision -m "midcycle injected_events"
```

填充生成文件（保留生成的 revision id；`down_revision` 指向当前 head `e70e70a8879d`）：

```python
"""iter-midcycle-event-injection: agent_cycles.injected_events nullable column

Mid-cycle 注入事件取证（JSON 数组 / NULL）。注：v_alert_lifecycle 的注入通道
SQL 只改 src/storage/views.py 单源（fresh DB 生效）——旧 DB 不重建 view，
与 #71 view 变更先例一致（历史数据本无注入行，旧 view 不失真）。

Revision ID: <generated>
Revises: e70e70a8879d
Create Date: <generated>
"""
from __future__ import annotations
from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa


revision: str = "<generated>"
down_revision: str | None = "e70e70a8879d"
branch_labels: Sequence[str] | None = None
depends_on: Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("agent_cycles", schema=None) as batch_op:
        batch_op.add_column(sa.Column("injected_events", sa.Text(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("agent_cycles", schema=None) as batch_op:
        batch_op.drop_column("injected_events")
```

- [ ] **Step 5.4: app.py 回滚 helper + 三写入点**

① helper（放 `run_agent_cycle` 定义之前）：

```python
def _rollback_injected_events(deps: TradingDeps) -> None:
    """被丢弃的 run ⇒ 注入回滚（spec §2）。

    retry 重试前、usage_limit / retry_exhausted 终态 forensic 写库前调用：被该 run
    消费的注入事件 requeue 回堆（经兜底通道重新送达——retry 场景通常被下一 attempt
    的首次工具调用重新注入），累积器清空 → 被丢弃 run 的 injected_events 落 NULL。
    不回滚则事件永远到不了任何存活决策（送达盲区换处藏身）。
    """
    if deps.injected_events_log and deps.requeue_events_fn is not None:
        deps.requeue_events_fn([rec["raw"] for rec in deps.injected_events_log])
    deps.injected_events_log.clear()
```

② `except UsageLimitExceeded`（:665）`logger.error` 之后、`async with get_session` 之前插入：

```python
            _rollback_injected_events(deps)   # 被丢弃 run ⇒ 注入回滚（spec §2）
```

该路径 AgentCycle(...) 写入参数加（`user_prompt_snapshot` 行后）：

```python
                    injected_events=None,   # 回滚后落 NULL（spec §2/§6）
```

③ `except Exception`（:711）块首（`if attempt < 2:` 之前）插入：

```python
            _rollback_injected_events(deps)   # 被丢弃 attempt ⇒ 注入回滚（spec §2，重试前 / 终态写库前）
```

retry_exhausted 路径 AgentCycle(...) 同样加 `injected_events=None,`。

④ 成功写入点（:828 AgentCycle）`user_prompt_snapshot` 行后加：

```python
                injected_events=json.dumps(
                    [{k: v for k, v in rec.items() if k != "raw"}
                     for rec in deps.injected_events_log]
                ) if deps.injected_events_log else None,   # raw 回滚句柄落库剥离（spec §6）
```

- [ ] **Step 5.5: 跑测试 + commit**

Run: `.venv/bin/python -m pytest tests/test_midcycle_forensics.py tests/test_run_agent_cycle_phase1.py tests/test_alembic_migration.py -v`
Expected: 全 PASS（migration 链测试自动覆盖新 revision）

```bash
git add src/storage/models.py alembic/versions/*midcycle* src/cli/app.py tests/test_midcycle_forensics.py
git commit -m "feat(forensics): agent_cycles.injected_events 列 + 被丢弃 run 注入回滚（spec §2/§6）

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

### Task 6: §7 `v_alert_lifecycle` 注入通道 + `delivery` 列

**Files:**
- Modify: `src/storage/views.py`（triggers CTE + 最终 SELECT）
- Modify: `tests/test_v_alert_lifecycle.py`（追加 3 个测试）

view SQL 单源只改 `views.py`（fresh DB 生效）；不补 view 重建 migration——与 `cb7d7db` #71 先例一致，理由已写进 Task 5 migration docstring。

- [ ] **Step 6.1: 写失败测试**

追加到 `tests/test_v_alert_lifecycle.py`：

```python
# === iter-midcycle-event-injection §7: injected 通道 + delivery 列 ===

@pytest.mark.asyncio
async def test_injected_channel_triggers_alert(db_session):
    """注入消费的 alert 经 injected_events 通道可见：triggered + delivery='injected'。"""
    db_session.add(TradeAction(
        session_id="test-lc-inj", cycle_id="cyc01",
        action="add_price_level_alert", alert_id="inj00001",
        symbol="BTC/USDT:USDT", price=61634.0, reasoning="below 61634",
    ))
    db_session.add(AgentCycle(
        session_id="test-lc-inj", cycle_id="cyc02", triggered_by="scheduled",
        injected_events=json.dumps([{
            "event": {
                "type": "price_level_alert", "alert_id": "inj00001",
                "symbol": "BTC/USDT:USDT", "current_price": 61630.5,
                "target_price": 61634.0, "direction": "below",
                "reasoning": "below 61634", "timestamp": 1765300000000,
            },
            "after_tool": "get_taker_flow", "offset_ms": 73000,
        }]),
        state_snapshot=json.dumps({"position": None}), decision="noted",
    ))
    await db_session.commit()

    row = (await db_session.execute(text(
        "SELECT final_status, triggered_at, triggered_price, delivery "
        "FROM v_alert_lifecycle WHERE alert_id='inj00001'"
    ))).mappings().one()
    assert row["final_status"] == "triggered"
    assert row["triggered_at"] is not None
    assert row["triggered_price"] == 61630.5
    assert row["delivery"] == "injected"


@pytest.mark.asyncio
async def test_wake_channel_delivery_label(db_session):
    """既有 wake 通道行为不回归 + delivery='wake' 标注。"""
    db_session.add(TradeAction(
        session_id="test-lc-wake", cycle_id="cyc01",
        action="add_price_level_alert", alert_id="wak00001",
        symbol="BTC/USDT:USDT", price=80000.0, reasoning="above 80000",
    ))
    db_session.add(AgentCycle(
        session_id="test-lc-wake", cycle_id="cyc02", triggered_by="alert",
        trigger_context=json.dumps([{
            "type": "price_level_alert", "alert_id": "wak00001",
            "current_price": 80050.0, "target_price": 80000.0, "direction": "above",
        }]),
        state_snapshot=json.dumps({"position": None}), decision="hold",
    ))
    await db_session.commit()

    row = (await db_session.execute(text(
        "SELECT final_status, delivery FROM v_alert_lifecycle WHERE alert_id='wak00001'"
    ))).mappings().one()
    assert row["final_status"] == "triggered"
    assert row["delivery"] == "wake"


@pytest.mark.asyncio
async def test_dual_channel_each_delivered_once(db_session):
    """双通道并存不漏：同 session 一 wake 一 injected，各自恰好一行。

    注：本测试覆盖的是"两个不同 alert 各走一通道"。"同一 alert 同时出现在
    trigger_context 与 injected_events"的去重边界 view **刻意不守护**——
    该互斥是注入语义（注入即消费）的运行期不变量，若真出现双行，本身就是
    上游 bug 的取证信号，UNION ALL 不去重恰好让它可见。"""
    db_session.add_all([
        TradeAction(
            session_id="test-lc-dual", cycle_id="cyc01",
            action="add_price_level_alert", alert_id="dualwake",
            symbol="BTC/USDT:USDT", price=80000.0, reasoning="above 80000",
        ),
        TradeAction(
            session_id="test-lc-dual", cycle_id="cyc01",
            action="add_price_level_alert", alert_id="dualinje",
            symbol="BTC/USDT:USDT", price=61634.0, reasoning="below 61634",
        ),
        AgentCycle(
            session_id="test-lc-dual", cycle_id="cyc02", triggered_by="alert",
            trigger_context=json.dumps([{
                "type": "price_level_alert", "alert_id": "dualwake",
                "current_price": 80050.0,
            }]),
            injected_events=json.dumps([{
                "event": {"type": "price_level_alert", "alert_id": "dualinje",
                          "current_price": 61630.5},
                "after_tool": "get_position", "offset_ms": 1000,
            }]),
            state_snapshot=json.dumps({"position": None}), decision="busy cycle",
        ),
    ])
    await db_session.commit()

    rows = (await db_session.execute(text(
        "SELECT alert_id, delivery FROM v_alert_lifecycle "
        "WHERE session_id='test-lc-dual' ORDER BY alert_id"
    ))).mappings().all()
    assert [(r["alert_id"], r["delivery"]) for r in rows] == [
        ("dualinje", "injected"), ("dualwake", "wake"),
    ]
```

- [ ] **Step 6.2: 跑测试确认失败**

Run: `.venv/bin/python -m pytest tests/test_v_alert_lifecycle.py -v -k "injected or delivery or dual"`
Expected: FAIL — `no such column: delivery`

- [ ] **Step 6.3: 改 `src/storage/views.py` triggers CTE**

`V_ALERT_LIFECYCLE_SQL` 的 triggers CTE（:106-128）改为双通道（现有 SELECT 加 `'wake' AS delivery`，UNION ALL 注入分支）：

```sql
triggers AS (
  -- spec 2026-06-08: trigger_context is now a JSON array (one element per drained event);
  -- unnest it. Legacy single-object rows are wrapped in json_array() first — bare
  -- json_each('{...}') on an object iterates BY KEY (one row per field), polluting the
  -- result. Drop the old `triggered_by='alert'` clause: a price-level alert batched with a
  -- fill has triggered_by='conditional', so that clause would silently drop it; filter
  -- per-element on '$.type' instead. ALL per-element reads come from json_each.value.
  --
  -- iter-midcycle-event-injection §7: two delivery channels, mutually exclusive per
  -- event (injection == consumption) → UNION ALL is dup-safe.
  --   'wake'     — alert consumed at cycle boundary (trigger_context element)
  --   'injected' — alert consumed mid-cycle at a tool boundary (injected_events
  --                element {"event": {...}, "after_tool", "offset_ms"} → '$.event.*');
  --                triggered_at uses cycle created_at, same granularity as wake.
  SELECT ac.session_id,
         json_extract(e.value, '$.alert_id') AS alert_id,
         ac.created_at AS triggered_at,
         CAST(json_extract(e.value, '$.current_price') AS REAL) AS triggered_price,
         'wake' AS delivery
  FROM agent_cycles ac,
       json_each(
         CASE WHEN json_type(ac.trigger_context) = 'array'
              THEN ac.trigger_context
              ELSE json_array(json(ac.trigger_context)) END
       ) e
  WHERE ac.trigger_context IS NOT NULL
    AND json_extract(e.value, '$.type') = 'price_level_alert'
    AND json_extract(e.value, '$.alert_id') IS NOT NULL
  -- ELSE branch assumes legacy rows are valid JSON (written by json.dumps); a manually
  -- inserted malformed string would raise at query time — not guarded (never happens in practice).
  UNION ALL
  SELECT ac.session_id,
         json_extract(e.value, '$.event.alert_id') AS alert_id,
         ac.created_at AS triggered_at,
         CAST(json_extract(e.value, '$.event.current_price') AS REAL) AS triggered_price,
         'injected' AS delivery
  FROM agent_cycles ac,
       json_each(ac.injected_events) e
  WHERE ac.injected_events IS NOT NULL
    AND json_extract(e.value, '$.event.type') = 'price_level_alert'
    AND json_extract(e.value, '$.event.alert_id') IS NOT NULL
),
```

最终 SELECT 的 `t.triggered_price,` 行后加：

```sql
  t.delivery,
```

（`cancel_attempts` CTE 等其余部分不动——spec §10 维持现状。）

- [ ] **Step 6.4: 跑测试 + commit**

Run: `.venv/bin/python -m pytest tests/test_v_alert_lifecycle.py tests/test_alert_lifecycle.py -v`
Expected: 全 PASS（既有 8 条 wake/legacy 兼容测试零回归 + 新 3 条）

```bash
git add src/storage/views.py tests/test_v_alert_lifecycle.py
git commit -m "feat(views): v_alert_lifecycle 注入送达通道 + delivery 列（spec §7）

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

### Task 7: §5 Persona 送达契约更新 + drift guard

**Files:**
- Modify: `src/agent/persona.py`（:122/:123/:124 通道中性化 + :126 bullet 末尾插入契约句）
- Modify: `tests/test_persona.py`（追加 drift guard；如有切片锚定断言失效则适配）

- [ ] **Step 7.1: 写失败测试**

追加到 `tests/test_persona.py`：

```python
def test_persona_carries_injection_delivery_contract():
    """§5 drift guard：persona 文本与 injector header 常量逐字一致（防两处漂移）。

    契约要素切片断言：① NEW EVENTS TRIGGERED 锚（≥2 处：wake bullet 契约句 +
    fill/alert response 通道中性化）；② 注入不 cancel one-shot wake 的边界句；
    ③ 末次工具调用后到达 → 正常唤醒的兜底分支。"""
    from src.agent.persona import generate_system_prompt
    from src.config import PersonaConfig
    from src.services.midcycle_injector import INJECTION_HEADER_PREFIX

    text = generate_system_prompt(PersonaConfig())
    assert text.count(INJECTION_HEADER_PREFIX) >= 2

    wake_bullet = [
        b for b in text.split("\n- **") if b.startswith("Wake interval control")
    ]
    assert len(wake_bullet) == 1
    bullet = wake_bullet[0]
    assert "delivered in your next tool result" in bullet
    assert "does **not** cancel the next-wake interval" in bullet
    assert "still arrives as a normal wake" in bullet
```

- [ ] **Step 7.2: 跑测试确认失败**

Run: `.venv/bin/python -m pytest tests/test_persona.py -v -k injection_delivery`
Expected: FAIL — `INJECTION_HEADER_PREFIX` 计数 0

- [ ] **Step 7.3: 改 `src/agent/persona.py` 四处**

①（:122 Open fill response，通道中性化）：

旧：
```
- **Open fill response**: When woken by a limit-order fill (conditional trigger) that opened a position, set your stop loss and take profit. (A synchronous market open does not trigger a wake — set SL/TP right after it, using the thesis you just formed.)
```
新：
```
- **Open fill response**: When a fill notification arrives for a limit order that opened a position (as a wake trigger or a NEW EVENTS TRIGGERED block), set your stop loss and take profit. (A synchronous market open does not produce a notification — set SL/TP right after it, using the thesis you just formed.)
```

②（:123 Close fill response）：

旧：
```
- **Close fill response**: When woken by a fill that closed a position via a stop-loss or take-profit trigger, review the trade outcome: what worked, what didn't, what you'd do differently. A manual market close returns its outcome synchronously — reflect in the same cycle.
```
新：
```
- **Close fill response**: When a fill notification arrives for a stop-loss or take-profit that closed a position (wake trigger or NEW EVENTS TRIGGERED block), review the trade outcome: what worked, what didn't, what you'd do differently. A manual market close returns its outcome synchronously — reflect in the same cycle.
```

③（:124 Alert response）：

旧：
```
- **Alert response**: When woken by a price alert, assess whether the price move changes your thesis. For a price level alert, evaluate whether the level held or broke and what that implies. For a volatility alert, determine if the move is the start of a trend or just noise before acting.
```
新：
```
- **Alert response**: When a price alert notification arrives (wake trigger or NEW EVENTS TRIGGERED block), assess whether the price move changes your thesis. For a price level alert, evaluate whether the level held or broke and what that implies. For a volatility alert, determine if the move is the start of a trend or just noise before acting.
```

④（:126 Wake interval control bullet——在 `set it **again** to keep a non-default cadence.` 之后、` Allowed range:` 之前插入，spec §5 锚点）：

```
 If an event fires while a cycle is already running, it is delivered in your next tool result under a `NEW EVENTS TRIGGERED` header and consumed there — no separate wake follows, and unlike an interrupting wake it does **not** cancel the next-wake interval you set. An event that fires after your last tool call of the cycle still arrives as a normal wake — cancelling the interval as above.
```

**措辞硬约束**：插入句必须用 `consumed`、禁用 `consumes`——既有 drift guard `test_wake_interval_control_states_one_shot_and_rearm` 断言 `"consumes" not in wake_bullet`（'cancels' not 'consumes' 是 load-bearing 语义，见该测试 docstring）；`"consumes" in "consumed"` 为 False 所以上文措辞安全，但任何改写不得引入 `consumes` 字面量。

（:121 "you will be notified when they fill" / tools_descriptions.py:15/:31 / tools_execution.py:155/:248 均不动——spec §5 审计表，通道中性仍真。）

- [ ] **Step 7.4: 跑 persona 全测试，适配可能失效的切片锚定**

Run: `.venv/bin/python -m pytest tests/test_persona.py -v`
Expected: 新测试 PASS；若既有 wake-bullet 切片断言（test_persona.py:280-303 `one-shot`/`cancels` drift guard）因 bullet 变长失效，仅调整切片提取方式、断言关键词不变（`one-shot` / `cancels` 仍必须在 bullet 内）。

- [ ] **Step 7.5: commit**

```bash
git add src/agent/persona.py tests/test_persona.py
git commit -m "feat(persona): mid-cycle 送达契约 — NEW EVENTS TRIGGERED 注入分支 + 通道中性化（spec §5）

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

### Task 8: §8 Session log 渲染 — full-keep

**Files:**
- Modify: `src/cli/display.py`（:576 一行）
- Modify: `tests/test_display_cycle.py`（追加 2 个测试）

- [ ] **Step 8.1: 写失败测试**

追加到 `tests/test_display_cycle.py`（仿该文件既有 `_render_tool_body` 测试风格；import 形式与文件内既有用法一致）：

```python
# === iter-midcycle-event-injection §8: NEW EVENTS TRIGGERED full-keep ===

def test_new_events_section_is_full_keep():
    """注入小节免 _clip_body 裁剪（事件行不折叠）。"""
    from src.cli.display import _is_full_keep_section
    assert _is_full_keep_section("NEW EVENTS TRIGGERED (1 fill, 1 alert)")
    assert _is_full_keep_section("NEW EVENTS TRIGGERED (2 alerts)")
    assert not _is_full_keep_section("Recent Closed Candles (30)")


def test_plain_tool_return_with_injection_renders_as_section():
    """无 section 标记的 plain 工具返回 + 注入块：原文本渲染不变、注入以独立小节追加
    （_parse_sections 归一化路径——防未来渲染管道改动引入模式分叉，spec §9）。"""
    from src.cli.display import _render_tool_body

    content = (
        "Position: short 59.67 contracts @ 61563.30"
        "\n\n=== NEW EVENTS TRIGGERED (1 fill) ===\n"
        "IMPORTANT EVENT: stop triggered — BTC/USDT:USDT 59.67 @ 61800.0,"
        " Fee: -36.88 USDT, PnL: -65.70 USDT (gross) — filled 2026-06-09 22:14 UTC (23s ago)"
    )
    out = _render_tool_body("get_position", content, head_args="get_position()")
    assert "Position: short 59.67 contracts" in out
    assert "=== NEW EVENTS TRIGGERED (1 fill) ===" in out
    assert "IMPORTANT EVENT: stop triggered" in out
```

- [ ] **Step 8.2: 跑测试确认失败**

Run: `.venv/bin/python -m pytest tests/test_display_cycle.py -v -k new_events`
Expected: `test_new_events_section_is_full_keep` FAIL（prefix 未注册）；第二个可能已 PASS（by-content dispatch 既有能力）——保留作回归锁

- [ ] **Step 8.3: 改 `src/cli/display.py` :576**

```python
_FULL_KEEP_SECTION_PREFIXES: tuple[str, ...] = ("Taker Flow", "NEW EVENTS TRIGGERED")
```

并把 :569-575 注释块末尾追加一行：

```python
# "NEW EVENTS TRIGGERED" — mid-cycle 注入事件块（iter-midcycle-event-injection §8）：
# 事件行 = forensic 主信号，折叠即失去复现价值；header 前缀与 midcycle_injector
# INJECTION_HEADER_PREFIX 逐字同源。
```

- [ ] **Step 8.4: 跑测试 + commit**

Run: `.venv/bin/python -m pytest tests/test_display_cycle.py -v`
Expected: 全 PASS

```bash
git add src/cli/display.py tests/test_display_cycle.py
git commit -m "feat(display): NEW EVENTS TRIGGERED 小节 full-keep 免裁剪（spec §8）

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

### Task 9: §9 集成测试（happy path + 反向断言）

retry 交互集成已在 Task 5（run_agent_cycle 层）覆盖；本任务补真实 `agent.run` 链路 happy path 与同步市价反向断言。

**Files:**
- Create: `tests/test_midcycle_injection_integration.py`

- [ ] **Step 9.1: 写集成测试**

```python
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
    # "执行中途入堆" 的时序由 unit 层覆盖）
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
                if "=== NEW EVENTS TRIGGERED (1 fill) ===" in str(p.content)]
    assert injected, "注入块必须进入 ToolReturnPart.content（LLM 可见通道）"
    assert "IMPORTANT EVENT: stop triggered" in str(injected[0].content)

    # ② 注入即消费：堆空 → cycle 结束无 back-to-back conditional cycle
    assert scheduler.drain_pending_events() == []

    # ③ injected_events 取证列
    row = (await db_session.execute(
        select(AgentCycle).order_by(AgentCycle.id.desc()).limit(1))).scalar_one()
    assert row.execution_status == "ok"
    recs = json.loads(row.injected_events)
    assert len(recs) == 1
    assert recs[0]["event"]["type"] == "fill"
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
```

- [ ] **Step 9.2: 跑测试**

Run: `.venv/bin/python -m pytest tests/test_midcycle_injection_integration.py -v`
Expected: 全 PASS。若 `close_position` 回执断言措辞不符，先 `grep -n "return" src/agent/tools_execution.py | sed -n '1,30p'` 核实实际回执前缀再调整断言字面量（断言意图不变：两次同步操作完成且 fill callback 零调用）。

- [ ] **Step 9.3: commit**

```bash
git add tests/test_midcycle_injection_integration.py
git commit -m "test(integration): mid-cycle 注入 happy path + 同步市价反向断言（spec §9）

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

### Task 10: 全量回归 + 收尾

- [ ] **Step 10.1: 全量测试**

Run: `.venv/bin/python -m pytest -q`
Expected: 2263 + ~30 新增 passed, 9 skipped, 0 failed

- [ ] **Step 10.2: lint**

Run: `.venv/bin/python -m ruff check src/ tests/`（若项目配置存在 ruff；否则跳过）
Expected: 无新增告警

- [ ] **Step 10.3: 验收清单核对（对 spec §9）**

逐项确认并在本文件勾选：

- [ ] 单测：scheduler drain/requeue（Task 2）
- [ ] 单测：capability 全失败语义 + 注册顺序锁（Task 4）
- [ ] 单测：渲染器提取 byte-identical 回归（Task 1——既有断言文件即回归）
- [ ] 单测：注入块格式（header / 排序 / 时间基准，Task 4）
- [ ] 单测：persona drift guard（Task 7）
- [ ] 单测：migration 两态 + view 双通道（Task 5/6）
- [ ] 单测：display full-keep + plain 归一化回归（Task 8）
- [ ] 集成：happy path / retry 交互（Task 5）/ 同步市价反向断言（Task 9）

- [ ] **Step 10.4: 勾选本计划全部 checkbox 后 commit 计划文件更新**

```bash
git add docs/superpowers/plans/2026-06-11-iter-midcycle-event-injection.md
git commit -m "docs(plan): iter-midcycle-event-injection 实施完成勾选

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

注：sim 验收信号（echo 归零 / 延迟中位 82s→<30s / 脏 decision 消失 / adoption forensic / token 增量 / wake-rearm 侧效应）属下次 sim run 的观察项，不在本 plan 执行范围（spec §9 验收信号段）。memory `design-fidelity-gaps` 更新（G11）是 merge 后 follow-up（spec §10）。
