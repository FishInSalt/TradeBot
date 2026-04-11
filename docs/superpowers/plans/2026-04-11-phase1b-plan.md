# Phase 1b Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 补完单 agent 事件驱动闭环 — 多模型支持、OKX WebSocket fill 推送、价格异动警报。

**Architecture:** 前置改造 Scheduler 事件队列 → 模块一多模型支持 → 模块二 OKX WebSocket → 模块三价格异动警报。三个模块技术独立但按此顺序实现。

**Tech Stack:** Python 3.12+, pydantic-ai, ccxt / ccxt.pro, asyncio

**Design spec:** `docs/superpowers/specs/2026-04-11-phase1b-design.md`

---

### Task 1: Scheduler 事件队列化改造

**概述:** 将 Scheduler 中的 `_pending_trigger` + `_pending_context` 单标志位替换为 `deque[_TriggerEvent]` 队列，解决 trigger_type 丢失和多事件 context 覆盖问题。这是 price alert 集成的前置条件。

**Files:**
- Modify: `src/scheduler/scheduler.py`
- Modify: `tests/test_scheduler.py`

- [ ] **Step 1: 编写失败测试**

在 `tests/test_scheduler.py` 末尾追加以下测试：

```python
# tests/test_scheduler.py — 末尾追加

async def test_scheduler_preserves_trigger_type():
    """trigger_type 应保留原始值（不被硬编码为 'conditional'）。"""
    from src.scheduler.scheduler import Scheduler

    fired = []

    async def callback(trigger_type: str, context):
        fired.append((trigger_type, context))

    scheduler = Scheduler(interval_seconds=10, callback=callback)
    task = asyncio.create_task(scheduler.start())
    await asyncio.sleep(0.05)

    await scheduler.trigger("alert", context="price_drop")
    await asyncio.sleep(0.15)

    scheduler.stop()
    await task
    alert_events = [(t, c) for t, c in fired if t == "alert"]
    assert len(alert_events) == 1
    assert alert_events[0] == ("alert", "price_drop")


async def test_scheduler_fifo_order():
    """多个事件应按 FIFO 顺序处理，每个事件保留各自的 trigger_type 和 context。"""
    from src.scheduler.scheduler import Scheduler

    fired = []

    async def callback(trigger_type: str, context):
        fired.append((trigger_type, context))

    scheduler = Scheduler(interval_seconds=10, callback=callback)
    task = asyncio.create_task(scheduler.start())
    await asyncio.sleep(0.05)

    # 在 scheduler sleep 期间快速入队三个事件
    await scheduler.trigger("conditional", context="fill_1")
    await scheduler.trigger("alert", context="price_drop")
    await scheduler.trigger("conditional", context="fill_2")
    await asyncio.sleep(0.3)

    scheduler.stop()
    await task

    # 排除首次 scheduled 触发
    non_scheduled = [(t, c) for t, c in fired if t != "scheduled"]
    assert len(non_scheduled) == 3
    assert non_scheduled[0] == ("conditional", "fill_1")
    assert non_scheduled[1] == ("alert", "price_drop")
    assert non_scheduled[2] == ("conditional", "fill_2")


async def test_scheduler_context_not_lost_on_multiple_triggers():
    """多个 trigger 的 context 不应互相覆盖。"""
    from src.scheduler.scheduler import Scheduler

    fired = []

    async def callback(trigger_type: str, context):
        fired.append((trigger_type, context))
        await asyncio.sleep(0.05)  # 模拟 cycle 耗时

    scheduler = Scheduler(interval_seconds=10, callback=callback)
    task = asyncio.create_task(scheduler.start())
    await asyncio.sleep(0.05)

    await scheduler.trigger("conditional", context="event_A")
    await scheduler.trigger("conditional", context="event_B")
    await asyncio.sleep(0.3)

    scheduler.stop()
    await task

    contexts = [c for t, c in fired if t == "conditional"]
    assert "event_A" in contexts
    assert "event_B" in contexts


async def test_scheduler_safety_valve_max_drain():
    """单次 drain 最多处理 10 个事件，防止无限循环。"""
    from src.scheduler.scheduler import Scheduler

    fired = []

    async def callback(trigger_type: str, context):
        fired.append((trigger_type, context))

    scheduler = Scheduler(interval_seconds=10, callback=callback)
    task = asyncio.create_task(scheduler.start())
    await asyncio.sleep(0.05)

    # 入队 15 个事件
    for i in range(15):
        await scheduler.trigger("conditional", context=f"event_{i}")
    await asyncio.sleep(0.3)

    scheduler.stop()
    await task

    # 首次 drain 最多处理 10 个，剩余 5 个在下一次 sleep 后处理
    conditional_events = [(t, c) for t, c in fired if t == "conditional"]
    assert len(conditional_events) == 15  # 所有事件最终都应被处理


async def test_scheduler_event_preempts_scheduled():
    """有 pending 事件时不执行 scheduled cycle（互斥）。"""
    from src.scheduler.scheduler import Scheduler

    fired = []

    async def callback(trigger_type: str, context):
        fired.append((trigger_type, context))

    scheduler = Scheduler(interval_seconds=0.1, callback=callback)
    task = asyncio.create_task(scheduler.start())
    await asyncio.sleep(0.05)

    # 在第一次 sleep 期间触发事件，应取代 scheduled
    await scheduler.trigger("conditional", context="urgent")
    await asyncio.sleep(0.15)

    scheduler.stop()
    await task

    # 第一次是 scheduled（初始启动），第二次应是 conditional（不是 scheduled）
    assert fired[0] == ("scheduled", None)
    assert fired[1] == ("conditional", "urgent")
```

- [ ] **Step 2: 运行测试确认失败**

Run: `cd /Users/z/Z/TradeBot && python -m pytest tests/test_scheduler.py::test_scheduler_preserves_trigger_type tests/test_scheduler.py::test_scheduler_fifo_order tests/test_scheduler.py::test_scheduler_context_not_lost_on_multiple_triggers tests/test_scheduler.py::test_scheduler_safety_valve_max_drain tests/test_scheduler.py::test_scheduler_event_preempts_scheduled -v`

Expected: FAIL — 当前 Scheduler 硬编码 `"conditional"`，trigger_type 不保留；多事件 context 互相覆盖。

- [ ] **Step 3: 实现 Scheduler 事件队列化**

将 `src/scheduler/scheduler.py` 完整替换为以下内容。注意：原代码第 51-54 行的二次检查逻辑（`if self._pending_trigger: ... await self._run_cycle("conditional", None)`）在新 deque 方案中被自然取代，不再需要：

```python
# src/scheduler/scheduler.py
from __future__ import annotations

import asyncio
import logging
from collections import deque
from dataclasses import dataclass
from typing import Any, Awaitable, Callable

logger = logging.getLogger(__name__)


@dataclass
class _TriggerEvent:
    trigger_type: str
    context: Any | None


class Scheduler:
    def __init__(
        self,
        interval_seconds: float,
        callback: Callable[[str, Any | None], Awaitable[None]],
    ):
        self._interval = interval_seconds
        self._callback = callback
        self._running = False
        self._cycle_running = False
        self._pending_events: deque[_TriggerEvent] = deque()
        self._wake_event = asyncio.Event()

    async def trigger(self, trigger_type: str, context: Any | None = None) -> None:
        self._pending_events.append(_TriggerEvent(trigger_type, context))
        self._wake_event.set()

    async def start(self) -> None:
        self._running = True
        logger.info(f"Scheduler started (interval={self._interval}s)")

        await self._run_cycle("scheduled", None)

        while self._running:
            await self._interruptible_sleep(self._interval)
            if not self._running:
                break

            if self._pending_events:
                # 安全阀：单次最多 drain 10 个事件，防止 cycle 内产生的新事件导致无限循环
                for _ in range(min(len(self._pending_events), 10)):
                    if not self._pending_events:
                        break
                    event = self._pending_events.popleft()
                    await self._run_cycle(event.trigger_type, event.context)
            else:
                await self._run_cycle("scheduled", None)

    def stop(self) -> None:
        self._running = False
        self._wake_event.set()
        logger.info("Scheduler stopped")

    async def _run_cycle(self, trigger_type: str, context: Any | None) -> None:
        self._cycle_running = True
        try:
            await self._callback(trigger_type, context)
        except Exception:
            logger.exception("Agent cycle failed")
        finally:
            self._cycle_running = False

    async def _interruptible_sleep(self, duration: float) -> None:
        if self._pending_events:
            return
        self._wake_event.clear()
        try:
            await asyncio.wait_for(self._wake_event.wait(), timeout=duration)
        except asyncio.TimeoutError:
            pass
```

- [ ] **Step 4: 更新旧测试适配新行为**

`test_scheduler_trigger_merges_multiple_events` 行为将变化（不再合并，而是 FIFO 保留所有事件），需要更新该测试以匹配新行为：

```python
# tests/test_scheduler.py — 替换 test_scheduler_trigger_merges_multiple_events

async def test_scheduler_trigger_merges_multiple_events():
    """多个 trigger 事件应按 FIFO 顺序全部处理（不再合并）。"""
    from src.scheduler.scheduler import Scheduler

    fired = []

    async def callback(trigger_type: str, context):
        fired.append((trigger_type, context))
        await asyncio.sleep(0.05)

    scheduler = Scheduler(interval_seconds=10, callback=callback)
    task = asyncio.create_task(scheduler.start())
    await asyncio.sleep(0.05)

    await scheduler.trigger("conditional", context="event1")
    await scheduler.trigger("conditional", context="event2")
    await asyncio.sleep(0.3)

    scheduler.stop()
    await task
    conditional_fired = [(t, ctx) for t, ctx in fired if t == "conditional"]
    assert len(conditional_fired) == 2
    contexts = [ctx for _, ctx in conditional_fired]
    assert contexts == ["event1", "event2"]
```

- [ ] **Step 5: 运行全部 Scheduler 测试**

Run: `cd /Users/z/Z/TradeBot && python -m pytest tests/test_scheduler.py -v`

Expected: PASS — 所有新旧测试通过。

- [ ] **Step 6: 提交**

Run: `cd /Users/z/Z/TradeBot && git add src/scheduler/scheduler.py tests/test_scheduler.py && git commit -m "refactor: replace Scheduler pending trigger with deque event queue"`

---

### Task 2: BaseExchange 接口扩展

**概述:** 在 `BaseExchange` 中添加 `start()`、`on_fill()`、`on_alert()`、`set_alert_service()`、`update_alert_params()` 默认空实现，统一两种 Exchange 的接口。

**Files:**
- Modify: `src/integrations/exchange/base.py`
- Test: `tests/test_exchange.py`

- [ ] **Step 1: 编写失败测试**

在 `tests/test_exchange.py` 末尾追加：

```python
# tests/test_exchange.py — 末尾追加

import asyncio
from unittest.mock import AsyncMock


async def test_base_exchange_start_default_noop():
    """BaseExchange.start() 默认空实现不应抛异常。"""
    from src.integrations.exchange.base import BaseExchange

    class DummyExchange(BaseExchange):
        async def fetch_ticker(self, symbol): ...
        async def fetch_ohlcv(self, symbol, timeframe, limit=100): ...
        async def create_order(self, symbol, side, order_type, amount, price=None): ...
        async def fetch_balance(self): ...
        async def fetch_positions(self, symbol): ...
        async def set_leverage(self, symbol, leverage): ...
        def amount_to_precision(self, symbol, amount): ...
        async def close(self): ...
        async def fetch_order(self, order_id, symbol=None): ...
        async def fetch_open_orders(self, symbol): ...
        async def fetch_closed_orders(self, symbol, limit=20): ...
        async def cancel_order(self, order_id, symbol): ...

    ex = DummyExchange()
    await ex.start()  # 不应抛异常


def test_base_exchange_on_fill_default_noop():
    """BaseExchange.on_fill() 默认空实现不应抛异常。"""
    from src.integrations.exchange.base import BaseExchange

    class DummyExchange(BaseExchange):
        async def fetch_ticker(self, symbol): ...
        async def fetch_ohlcv(self, symbol, timeframe, limit=100): ...
        async def create_order(self, symbol, side, order_type, amount, price=None): ...
        async def fetch_balance(self): ...
        async def fetch_positions(self, symbol): ...
        async def set_leverage(self, symbol, leverage): ...
        def amount_to_precision(self, symbol, amount): ...
        async def close(self): ...
        async def fetch_order(self, order_id, symbol=None): ...
        async def fetch_open_orders(self, symbol): ...
        async def fetch_closed_orders(self, symbol, limit=20): ...
        async def cancel_order(self, order_id, symbol): ...

    ex = DummyExchange()
    callback = AsyncMock()
    ex.on_fill(callback)  # 不应抛异常


def test_base_exchange_on_alert_default_noop():
    """BaseExchange.on_alert() 默认空实现不应抛异常。"""
    from src.integrations.exchange.base import BaseExchange

    class DummyExchange(BaseExchange):
        async def fetch_ticker(self, symbol): ...
        async def fetch_ohlcv(self, symbol, timeframe, limit=100): ...
        async def create_order(self, symbol, side, order_type, amount, price=None): ...
        async def fetch_balance(self): ...
        async def fetch_positions(self, symbol): ...
        async def set_leverage(self, symbol, leverage): ...
        def amount_to_precision(self, symbol, amount): ...
        async def close(self): ...
        async def fetch_order(self, order_id, symbol=None): ...
        async def fetch_open_orders(self, symbol): ...
        async def fetch_closed_orders(self, symbol, limit=20): ...
        async def cancel_order(self, order_id, symbol): ...

    ex = DummyExchange()
    callback = AsyncMock()
    ex.on_alert(callback)  # 不应抛异常


def test_base_exchange_set_alert_service_default_noop():
    """BaseExchange.set_alert_service() 默认空实现不应抛异常。"""
    from src.integrations.exchange.base import BaseExchange

    class DummyExchange(BaseExchange):
        async def fetch_ticker(self, symbol): ...
        async def fetch_ohlcv(self, symbol, timeframe, limit=100): ...
        async def create_order(self, symbol, side, order_type, amount, price=None): ...
        async def fetch_balance(self): ...
        async def fetch_positions(self, symbol): ...
        async def set_leverage(self, symbol, leverage): ...
        def amount_to_precision(self, symbol, amount): ...
        async def close(self): ...
        async def fetch_order(self, order_id, symbol=None): ...
        async def fetch_open_orders(self, symbol): ...
        async def fetch_closed_orders(self, symbol, limit=20): ...
        async def cancel_order(self, order_id, symbol): ...

    ex = DummyExchange()
    ex.set_alert_service(object())  # 不应抛异常


def test_base_exchange_update_alert_params_default_noop():
    """BaseExchange.update_alert_params() 默认空实现不应抛异常。"""
    from src.integrations.exchange.base import BaseExchange

    class DummyExchange(BaseExchange):
        async def fetch_ticker(self, symbol): ...
        async def fetch_ohlcv(self, symbol, timeframe, limit=100): ...
        async def create_order(self, symbol, side, order_type, amount, price=None): ...
        async def fetch_balance(self): ...
        async def fetch_positions(self, symbol): ...
        async def set_leverage(self, symbol, leverage): ...
        def amount_to_precision(self, symbol, amount): ...
        async def close(self): ...
        async def fetch_order(self, order_id, symbol=None): ...
        async def fetch_open_orders(self, symbol): ...
        async def fetch_closed_orders(self, symbol, limit=20): ...
        async def cancel_order(self, order_id, symbol): ...

    ex = DummyExchange()
    ex.update_alert_params(3.0, 5, 15)  # 不应抛异常
```

- [ ] **Step 2: 运行测试确认失败**

Run: `cd /Users/z/Z/TradeBot && python -m pytest tests/test_exchange.py::test_base_exchange_start_default_noop tests/test_exchange.py::test_base_exchange_on_fill_default_noop tests/test_exchange.py::test_base_exchange_on_alert_default_noop tests/test_exchange.py::test_base_exchange_set_alert_service_default_noop tests/test_exchange.py::test_base_exchange_update_alert_params_default_noop -v`

Expected: FAIL — `BaseExchange` 没有 `start()`、`on_fill()`、`on_alert()`、`set_alert_service()`、`update_alert_params()` 方法。

- [ ] **Step 3: 在 BaseExchange 中添加默认空实现**

在 `src/integrations/exchange/base.py` 的 `BaseExchange` 类中，在 `drain_pending_fills` 方法之前添加：

```python
# src/integrations/exchange/base.py — BaseExchange 类内，cancel_order 方法之后、drain_pending_fills 之前添加

    async def start(self) -> None:
        """启动 WebSocket 等后台任务。默认空实现。"""
        pass

    def on_fill(self, callback: Callable[['FillEvent'], Awaitable[None]]) -> None:
        """注册 fill 回调。默认空实现。"""
        pass

    def on_alert(self, callback: Callable[[Any], Awaitable[None]]) -> None:
        """注册价格异动回调。默认空实现。"""
        pass

    def set_alert_service(self, service: Any) -> None:
        """注入 PriceAlertService。默认空实现。"""
        pass

    def update_alert_params(self, threshold_pct: float, window_minutes: int, cooldown_minutes: int) -> None:
        """更新价格预警参数。默认空实现。"""
        pass
```

同时在文件顶部的 import 中添加所需类型：

```python
# src/integrations/exchange/base.py — 修改 import
from __future__ import annotations
from abc import ABC, abstractmethod
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any
```

- [ ] **Step 4: 运行全部 exchange 测试**

Run: `cd /Users/z/Z/TradeBot && python -m pytest tests/test_exchange.py -v`

Expected: PASS — 所有新旧测试通过。

- [ ] **Step 5: 提交**

Run: `cd /Users/z/Z/TradeBot && git add src/integrations/exchange/base.py tests/test_exchange.py && git commit -m "feat: add start/on_fill/on_alert/set_alert_service/update_alert_params to BaseExchange"`

---

### Task 3: Config 更新 — AlertsConfig + Settings.models Optional

**概述:** 在 `config.py` 中新增 `AlertsConfig`，在 `Settings` 中添加 `alerts` 字段；将 `Settings.models` 改为 `Optional`（Phase 1b 用 ModelManager 替代，sub-agent 阶段再启用 routing）。更新 `settings.yaml` 和 `settings_sim.yaml` 添加 alerts 配置段。

**Files:**
- Modify: `src/config.py`
- Modify: `config/settings.yaml`
- Modify: `config/settings_sim.yaml`
- Modify: `tests/test_config.py`

- [ ] **Step 1: 编写失败测试**

在 `tests/test_config.py` 末尾追加：

```python
# tests/test_config.py — 末尾追加

def test_alerts_config_defaults():
    """AlertsConfig 应有合理默认值。"""
    from src.config import AlertsConfig
    config = AlertsConfig()
    assert config.enabled is True
    assert config.window_minutes == 5
    assert config.threshold_pct == 3.0
    assert config.cooldown_minutes == 15


def test_settings_with_alerts(tmp_path: Path):
    """Settings 应能加载 alerts 配置段。"""
    settings_file = tmp_path / "settings.yaml"
    settings_file.write_text("""
exchange:
  name: okx
alerts:
  enabled: true
  window_minutes: 10
  threshold_pct: 5.0
  cooldown_minutes: 30
""")
    from src.config import load_settings
    settings = load_settings(settings_file, env_overrides={})
    assert settings.alerts.enabled is True
    assert settings.alerts.window_minutes == 10
    assert settings.alerts.threshold_pct == 5.0
    assert settings.alerts.cooldown_minutes == 30


def test_settings_without_alerts(tmp_path: Path):
    """不提供 alerts 配置段时应使用默认值。"""
    settings_file = tmp_path / "settings.yaml"
    settings_file.write_text("""
exchange:
  name: okx
""")
    from src.config import load_settings
    settings = load_settings(settings_file, env_overrides={})
    assert settings.alerts.enabled is True
    assert settings.alerts.window_minutes == 5


def test_settings_alerts_disabled(tmp_path: Path):
    """alerts.enabled=false 应正确加载。"""
    settings_file = tmp_path / "settings.yaml"
    settings_file.write_text("""
exchange:
  name: okx
alerts:
  enabled: false
""")
    from src.config import load_settings
    settings = load_settings(settings_file, env_overrides={})
    assert settings.alerts.enabled is False


def test_settings_models_optional(tmp_path: Path):
    """settings.yaml 中不提供 models 配置段时 Settings.models 应为 None。"""
    settings_file = tmp_path / "settings.yaml"
    settings_file.write_text("""
exchange:
  name: okx
""")
    from src.config import load_settings
    settings = load_settings(settings_file, env_overrides={})
    assert settings.models is None


def test_settings_models_still_works(tmp_path: Path):
    """settings.yaml 中提供 models 配置段时应正常加载（向后兼容）。"""
    settings_file = tmp_path / "settings.yaml"
    settings_file.write_text("""
exchange:
  name: okx
models:
  default: anthropic:claude-sonnet-4-20250514
  strong: anthropic:claude-opus-4-6
  weak: anthropic:claude-haiku-4-5-20251001
  routing:
    market_analysis: strong
    trade_decision: strong
    news_summary: weak
    review: weak
""")
    from src.config import load_settings
    settings = load_settings(settings_file, env_overrides={})
    assert settings.models is not None
    assert settings.models.strong == "anthropic:claude-opus-4-6"
```

- [ ] **Step 2: 运行测试确认失败**

Run: `cd /Users/z/Z/TradeBot && python -m pytest tests/test_config.py::test_alerts_config_defaults tests/test_config.py::test_settings_with_alerts tests/test_config.py::test_settings_without_alerts tests/test_config.py::test_settings_alerts_disabled tests/test_config.py::test_settings_models_optional tests/test_config.py::test_settings_models_still_works -v`

Expected: FAIL — `AlertsConfig` 不存在，`Settings.models` 不是 Optional。

- [ ] **Step 3: 实现 Config 更新**

修改 `src/config.py`：

```python
# src/config.py — 在 ApprovalConfig 之后、Settings 之前添加 AlertsConfig

class AlertsConfig(BaseModel):
    enabled: bool = True
    window_minutes: int = 5
    threshold_pct: float = 3.0
    cooldown_minutes: int = 15
```

修改 `Settings` 类中的 `models` 字段和添加 `alerts` 字段：

```python
# src/config.py — Settings 类修改

class Settings(BaseModel):
    exchange: ExchangeConfig = ExchangeConfig()
    trading: TradingConfig = TradingConfig()
    models: ModelsConfig | None = None
    scheduler: SchedulerConfig = SchedulerConfig()
    llm_budget: LLMBudgetConfig = LLMBudgetConfig()
    database: DatabaseConfig = DatabaseConfig()
    approval: ApprovalConfig = ApprovalConfig()
    alerts: AlertsConfig = AlertsConfig()
```

修改 `config/settings.yaml`，在末尾添加 alerts 配置段：

```yaml
# config/settings.yaml — 末尾追加

# === Price Alert Configuration ===
alerts:
  enabled: true                  # If true, agent is woken on significant price moves
  window_minutes: 5              # Sliding window size for volatility detection
  threshold_pct: 3.0             # Minimum % change to trigger alert
  cooldown_minutes: 15           # Cooldown per direction after alert fires
```

修改 `config/settings_sim.yaml`，在末尾添加同样的 alerts 配置段：

```yaml
# config/settings_sim.yaml — 末尾追加

# === Price Alert Configuration ===
alerts:
  enabled: true                  # If true, agent is woken on significant price moves
  window_minutes: 5              # Sliding window size for volatility detection
  threshold_pct: 3.0             # Minimum % change to trigger alert
  cooldown_minutes: 15           # Cooldown per direction after alert fires
```

- [ ] **Step 4: 运行全部 config 测试**

Run: `cd /Users/z/Z/TradeBot && python -m pytest tests/test_config.py -v`

Expected: PASS — 所有新旧测试通过。注意 `test_load_settings` 使用了包含 `models` 段的 YAML，应继续通过（向后兼容）。

同时运行已有 `conftest.py` 依赖测试确保不破坏：

Run: `cd /Users/z/Z/TradeBot && python -m pytest tests/test_llm_router.py -v`

Expected: PASS

- [ ] **Step 5: 提交**

Run: `cd /Users/z/Z/TradeBot && git add src/config.py config/settings.yaml config/settings_sim.yaml tests/test_config.py && git commit -m "feat: add AlertsConfig, make Settings.models optional for Phase 1b"`

---

### Task 4: ModelManager — 模型配置管理

**概述:** 新建 `src/services/model_manager.py`，实现 `ModelConfig` dataclass、`models.json` 读写（chmod 0o600）、`create_model()` 构造 pydantic-ai Model 对象、`test_connectivity()` API 连通性测试。

**Files:**
- Create: `src/services/model_manager.py`
- Create: `tests/test_model_manager.py`

- [ ] **Step 1: 编写失败测试**

创建 `tests/test_model_manager.py`：

```python
# tests/test_model_manager.py

import json
import os
import pytest
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch


def test_model_config_dataclass():
    """ModelConfig 应正确存储所有字段。"""
    from src.services.model_manager import ModelConfig
    config = ModelConfig(
        id="claude-opus",
        provider="anthropic",
        model="claude-opus-4-6",
        api_key="sk-ant-test",
        base_url=None,
    )
    assert config.id == "claude-opus"
    assert config.provider == "anthropic"
    assert config.model == "claude-opus-4-6"
    assert config.api_key == "sk-ant-test"
    assert config.base_url is None


def test_model_config_with_base_url():
    """ModelConfig 应支持 base_url 字段（OpenRouter 等场景）。"""
    from src.services.model_manager import ModelConfig
    config = ModelConfig(
        id="deepseek-chat",
        provider="openai",
        model="deepseek/deepseek-chat",
        api_key="sk-or-test",
        base_url="https://openrouter.ai/api/v1",
    )
    assert config.base_url == "https://openrouter.ai/api/v1"


def test_load_models_empty(tmp_path: Path):
    """models.json 不存在时应返回空列表。"""
    from src.services.model_manager import ModelManager
    manager = ModelManager(config_path=tmp_path / "models.json")
    models = manager.load_models()
    assert models == []


def test_save_and_load_models(tmp_path: Path):
    """保存后加载应返回相同数据。"""
    from src.services.model_manager import ModelManager, ModelConfig
    manager = ModelManager(config_path=tmp_path / "models.json")

    configs = [
        ModelConfig(id="claude-opus", provider="anthropic", model="claude-opus-4-6",
                    api_key="sk-ant-test", base_url=None),
        ModelConfig(id="deepseek", provider="openai", model="deepseek/deepseek-chat",
                    api_key="sk-or-test", base_url="https://openrouter.ai/api/v1"),
    ]
    manager.save_models(configs)

    loaded = manager.load_models()
    assert len(loaded) == 2
    assert loaded[0].id == "claude-opus"
    assert loaded[0].api_key == "sk-ant-test"
    assert loaded[1].id == "deepseek"
    assert loaded[1].base_url == "https://openrouter.ai/api/v1"


def test_save_models_file_permissions(tmp_path: Path):
    """models.json 应设置 0o600 权限。"""
    from src.services.model_manager import ModelManager, ModelConfig
    manager = ModelManager(config_path=tmp_path / "models.json")

    configs = [
        ModelConfig(id="test", provider="anthropic", model="test-model",
                    api_key="sk-test", base_url=None),
    ]
    manager.save_models(configs)

    file_path = tmp_path / "models.json"
    mode = oct(os.stat(file_path).st_mode & 0o777)
    assert mode == "0o600"


def test_create_model_anthropic():
    """create_model 应为 anthropic provider 返回 AnthropicModel。"""
    from src.services.model_manager import ModelManager, ModelConfig
    from pydantic_ai.models.anthropic import AnthropicModel

    manager = ModelManager(config_path=Path("/dev/null"))
    config = ModelConfig(id="claude", provider="anthropic", model="claude-opus-4-6",
                         api_key="sk-ant-test", base_url=None)
    model = manager.create_model(config)
    assert isinstance(model, AnthropicModel)


def test_create_model_openai():
    """create_model 应为 openai provider 返回 OpenAIModel。"""
    from src.services.model_manager import ModelManager, ModelConfig
    from pydantic_ai.models.openai import OpenAIModel

    manager = ModelManager(config_path=Path("/dev/null"))
    config = ModelConfig(id="gpt4", provider="openai", model="gpt-4o",
                         api_key="sk-test", base_url=None)
    model = manager.create_model(config)
    assert isinstance(model, OpenAIModel)


def test_create_model_openai_with_base_url():
    """create_model 应为 openai provider 传入 base_url。"""
    from src.services.model_manager import ModelManager, ModelConfig
    from pydantic_ai.models.openai import OpenAIModel

    manager = ModelManager(config_path=Path("/dev/null"))
    config = ModelConfig(id="deepseek", provider="openai", model="deepseek/deepseek-chat",
                         api_key="sk-or-test", base_url="https://openrouter.ai/api/v1")
    model = manager.create_model(config)
    assert isinstance(model, OpenAIModel)


def test_create_model_google():
    """create_model 应为 google-gla provider 返回 GoogleModel。"""
    from src.services.model_manager import ModelManager, ModelConfig
    from pydantic_ai.models.google import GoogleModel

    manager = ModelManager(config_path=Path("/dev/null"))
    config = ModelConfig(id="gemini", provider="google-gla", model="gemini-2.0-flash",
                         api_key="test-key", base_url=None)
    model = manager.create_model(config)
    assert isinstance(model, GoogleModel)


def test_create_model_groq():
    """create_model 应为 groq provider 返回 GroqModel。"""
    from src.services.model_manager import ModelManager, ModelConfig
    from pydantic_ai.models.groq import GroqModel

    manager = ModelManager(config_path=Path("/dev/null"))
    config = ModelConfig(id="llama", provider="groq", model="llama-3.3-70b-versatile",
                         api_key="gsk-test", base_url=None)
    model = manager.create_model(config)
    assert isinstance(model, GroqModel)


def test_create_model_unsupported_provider():
    """不支持的 provider 应抛出 ValueError。"""
    from src.services.model_manager import ModelManager, ModelConfig

    manager = ModelManager(config_path=Path("/dev/null"))
    config = ModelConfig(id="bad", provider="unsupported", model="test",
                         api_key="test", base_url=None)
    with pytest.raises(ValueError, match="Unsupported provider"):
        manager.create_model(config)


def test_get_model_by_id(tmp_path: Path):
    """get_model_by_id 应从已加载列表中按 id 查找。"""
    from src.services.model_manager import ModelManager, ModelConfig
    manager = ModelManager(config_path=tmp_path / "models.json")

    configs = [
        ModelConfig(id="claude-opus", provider="anthropic", model="claude-opus-4-6",
                    api_key="sk-ant-test", base_url=None),
        ModelConfig(id="deepseek", provider="openai", model="deepseek/deepseek-chat",
                    api_key="sk-or-test", base_url=None),
    ]
    manager.save_models(configs)
    loaded = manager.load_models()

    found = manager.get_model_by_id("deepseek", loaded)
    assert found is not None
    assert found.id == "deepseek"


def test_get_model_by_id_not_found(tmp_path: Path):
    """不存在的 id 应返回 None。"""
    from src.services.model_manager import ModelManager, ModelConfig
    manager = ModelManager(config_path=tmp_path / "models.json")
    found = manager.get_model_by_id("nonexistent", [])
    assert found is None


async def test_test_connectivity_mock():
    """test_connectivity 应调用 agent.run 并返回成功/失败。"""
    from src.services.model_manager import ModelManager
    from unittest.mock import patch, AsyncMock, MagicMock

    manager = ModelManager(config_path=Path("/dev/null"))

    mock_agent_cls = MagicMock()
    mock_agent_instance = MagicMock()
    mock_agent_instance.run = AsyncMock(return_value=MagicMock(output="hi"))
    mock_agent_cls.return_value = mock_agent_instance

    with patch("src.services.model_manager.Agent", mock_agent_cls):
        success, error = await manager.test_connectivity(MagicMock())
        assert success is True
        assert error is None


async def test_test_connectivity_failure():
    """test_connectivity 失败时应返回错误信息。"""
    from src.services.model_manager import ModelManager
    from unittest.mock import patch, AsyncMock, MagicMock

    manager = ModelManager(config_path=Path("/dev/null"))

    mock_agent_cls = MagicMock()
    mock_agent_instance = MagicMock()
    mock_agent_instance.run = AsyncMock(side_effect=Exception("auth failed"))
    mock_agent_cls.return_value = mock_agent_instance

    with patch("src.services.model_manager.Agent", mock_agent_cls):
        success, error = await manager.test_connectivity(MagicMock())
        assert success is False
        assert "auth failed" in error
```

- [ ] **Step 2: 运行测试确认失败**

Run: `cd /Users/z/Z/TradeBot && python -m pytest tests/test_model_manager.py -v`

Expected: FAIL — `src.services.model_manager` 模块不存在。

- [ ] **Step 3: 实现 ModelManager**

创建 `src/services/model_manager.py`：

```python
# src/services/model_manager.py
from __future__ import annotations

import asyncio
import json
import logging
import os
from dataclasses import dataclass, asdict
from pathlib import Path
from typing import Any

from pydantic_ai import Agent
from pydantic_ai.models.anthropic import AnthropicModel
from pydantic_ai.models.google import GoogleModel
from pydantic_ai.models.groq import GroqModel
from pydantic_ai.models.openai import OpenAIModel

logger = logging.getLogger(__name__)

_PROVIDER_MAP: dict[str, type] = {
    "anthropic": AnthropicModel,
    "openai": OpenAIModel,
    "google-gla": GoogleModel,
    "groq": GroqModel,
}


@dataclass
class ModelConfig:
    id: str
    provider: str
    model: str
    api_key: str
    base_url: str | None


class ModelManager:
    def __init__(self, config_path: Path = Path("config/models.json")):
        self._config_path = config_path

    def load_models(self) -> list[ModelConfig]:
        """从 models.json 加载模型配置列表。文件不存在时返回空列表。"""
        if not self._config_path.exists():
            return []
        with open(self._config_path) as f:
            data = json.load(f)
        return [
            ModelConfig(
                id=item["id"],
                provider=item["provider"],
                model=item["model"],
                api_key=item["api_key"],
                base_url=item.get("base_url"),
            )
            for item in data
        ]

    def save_models(self, configs: list[ModelConfig]) -> None:
        """保存模型配置列表到 models.json，设置 0o600 权限。"""
        self._config_path.parent.mkdir(parents=True, exist_ok=True)
        data = [asdict(c) for c in configs]
        with open(self._config_path, "w") as f:
            json.dump(data, f, indent=2)
        os.chmod(self._config_path, 0o600)

    def create_model(self, config: ModelConfig) -> Any:
        """根据 ModelConfig 构造 pydantic-ai Model 对象。"""
        model_cls = _PROVIDER_MAP.get(config.provider)
        if model_cls is None:
            raise ValueError(f"Unsupported provider: {config.provider}")

        kwargs: dict[str, Any] = {"api_key": config.api_key}
        if config.base_url:
            kwargs["base_url"] = config.base_url
        return model_cls(config.model, **kwargs)

    def get_model_by_id(self, model_id: str, models: list[ModelConfig]) -> ModelConfig | None:
        """按 id 查找模型配置。"""
        for m in models:
            if m.id == model_id:
                return m
        return None

    async def test_connectivity(self, model: Any, timeout: float = 10.0) -> tuple[bool, str | None]:
        """测试模型 API 连通性。返回 (success, error_message)。"""
        try:
            agent = Agent(model, output_type=str)
            await asyncio.wait_for(
                agent.run("Say 'ok' and nothing else."),
                timeout=timeout,
            )
            return True, None
        except asyncio.TimeoutError:
            return False, "Connection timed out"
        except Exception as e:
            return False, str(e)
```

- [ ] **Step 4: 运行测试**

Run: `cd /Users/z/Z/TradeBot && python -m pytest tests/test_model_manager.py -v`

Expected: PASS — 所有测试通过。

- [ ] **Step 5: 提交**

Run: `cd /Users/z/Z/TradeBot && git add src/services/model_manager.py tests/test_model_manager.py && git commit -m "feat: add ModelManager for multi-model support (models.json CRUD + pydantic-ai model construction)"`

---

### Task 5: App 启动模型集成

**概述:** 替换 `app.py` 中的 `llm_router.resolve()` 调用为 ModelManager 的启动交互流程。添加 `--model` CLI 参数支持无人值守启动。`run_agent_cycle` 传入 `model=model_obj` 覆盖 agent 默认模型。

**Files:**
- Modify: `src/cli/app.py`
- Modify: `.gitignore`

- [ ] **Step 1: 更新 `.gitignore`**

在 `.gitignore` 中添加 `config/models.json`：

```
# .gitignore — 末尾追加
config/models.json
```

- [ ] **Step 2: 修改 `run_agent_cycle` 接受 model 参数**

在 `src/cli/app.py` 中修改 `run_agent_cycle` 函数签名，添加 `model` 参数并传入 `agent.run()`：

```python
# src/cli/app.py — 修改 run_agent_cycle 签名和 agent.run 调用

async def run_agent_cycle(
    agent,
    deps: TradingDeps,
    trigger_type: str,
    budget: TokenBudget,
    engine,
    context=None,
    model=None,
):
    if budget.exhausted:
        logger.warning("Daily LLM token budget exhausted, skipping cycle")
        return None

    cycle_id = str(uuid.uuid4())[:8]
    prompt = (
        f"You have been woken up by a {trigger_type} trigger.\n"
        f"Trading pair: {deps.symbol} | Timeframe: {deps.timeframe}\n"
        "Analyze the current market, check your positions, and decide what to do.\n"
        "Use your tools to gather data before making a decision."
    )
    if trigger_type == "conditional" and context is not None:
        msg = (
            f"\n\nIMPORTANT EVENT: {context.trigger_reason} triggered "
            f"— {context.symbol} {context.amount} @ {context.fill_price}"
        )
        if context.pnl is not None:
            msg += f", PnL: {context.pnl:.2f} USDT"
        prompt += msg
    elif trigger_type == "alert" and context is not None:
        direction = "dropped" if context.change_pct < 0 else "surged"
        prompt += (
            f"\n\nPRICE ALERT: {context.symbol} {direction} {abs(context.change_pct):.1f}% "
            f"in {context.window_minutes}min ({context.reference_price:.2f} → {context.current_price:.2f})"
        )

    memory_context = await deps.memory.format_for_prompt()
    if memory_context != "No relevant memories.":
        prompt += f"\n\nYour memories:\n{memory_context}"

    # LLM call with exponential backoff retry
    result = None
    run_kwargs = {"deps": deps}
    if model is not None:
        run_kwargs["model"] = model

    for attempt in range(3):
        try:
            result = await agent.run(prompt, **run_kwargs)
            break
        except Exception as e:
            if attempt < 2:
                delay = 2 ** attempt
                logger.warning(f"LLM call attempt {attempt + 1}/3 failed: {e}, retrying in {delay}s")
                await asyncio.sleep(delay)
            else:
                logger.error(f"LLM call failed after 3 attempts: {e}")
                return None

    tokens = result.usage().total_tokens if result.usage() else 0
    budget.record(tokens)

    async with get_session(engine) as session:
        session.add(
            DecisionLog(
                session_id=deps.session_id,
                cycle_id=cycle_id,
                trigger_type=trigger_type,
                decision="completed",
                reasoning=result.output[:500],
                model_used=str(model) if model else str(agent.model),
                tokens_used=tokens,
            )
        )
        await session.commit()

    logger.info(f"Cycle {cycle_id}: {tokens} tokens ({budget.remaining} remaining)")
    console.print(f"\n[bold cyan]Agent:[/]\n{result.output}\n")
    return result
```

- [ ] **Step 3: 修改 `run()` 函数集成 ModelManager**

修改 `src/cli/app.py` 的 `run()` 函数：

```python
# src/cli/app.py — 修改 run() 函数

async def run(
    settings_path: Path = Path("config/settings.yaml"),
    trader_path: Path = Path("config/trader.yaml"),
    model_id: str | None = None,
):
    logging.basicConfig(
        level=logging.INFO,
        format="%(message)s",
        handlers=[RichHandler(console=console, rich_tracebacks=True)],
    )

    console.print("[bold green]TradeBot Phase 1b — Starting...[/]\n")

    settings = load_settings(settings_path)
    trader_config = load_trader_config(trader_path)

    console.print(f"Symbol: {settings.trading.symbol} | Timeframe: {settings.trading.timeframe}")
    console.print(f"Approval: {'ON' if settings.approval.enabled else 'OFF'}")
    console.print(
        f"Persona: {trader_config.persona.risk_tolerance} / {trader_config.persona.trading_style}\n"
    )

    # --- Model selection via ModelManager ---
    from src.services.model_manager import ModelManager

    project_root = settings_path.resolve().parent.parent
    model_manager = ModelManager(config_path=project_root / "config" / "models.json")
    existing_models = model_manager.load_models()

    selected_model = None  # pydantic-ai Model object
    selected_config = None

    if model_id:
        # CLI --model 参数：直接查找
        selected_config = model_manager.get_model_by_id(model_id, existing_models)
        if selected_config is None:
            console.print(f"[red]Model '{model_id}' not found in models.json[/]")
            return
        selected_model = model_manager.create_model(selected_config)
        console.print(f"Model: {selected_config.id} ({selected_config.provider}:{selected_config.model})")
    elif existing_models:
        # 交互模式：列出已有模型让用户选择
        console.print("[bold]Available models:[/]")
        for i, m in enumerate(existing_models):
            console.print(f"  {i + 1}. {m.id} ({m.provider}:{m.model})")
        console.print(f"  {len(existing_models) + 1}. Add new model")

        choice = input(f"\nSelect model [1-{len(existing_models) + 1}]: ").strip()
        try:
            idx = int(choice) - 1
        except ValueError:
            idx = 0  # 默认选第一个

        if 0 <= idx < len(existing_models):
            selected_config = existing_models[idx]
            selected_model = model_manager.create_model(selected_config)
        else:
            # 添加新模型
            selected_config, selected_model = await _interactive_add_model(
                model_manager, existing_models
            )
    else:
        # 无已配置模型：引导用户添加
        console.print("[yellow]No models configured. Let's add one.[/]\n")
        selected_config, selected_model = await _interactive_add_model(
            model_manager, existing_models
        )

    if selected_model is None:
        console.print("[red]No model selected. Exiting.[/]")
        return

    # 测试 API 连通性
    console.print(f"\nTesting API connectivity for {selected_config.id}...")
    success, error = await model_manager.test_connectivity(selected_model)
    if success:
        console.print("[green]API connection OK[/]")
    else:
        console.print(f"[red]API connection failed: {error}[/]")
        skip = input("Skip test and continue anyway? [y/N]: ").strip().lower()
        if skip != "y":
            return

    # 连通性测试通过后，保存新添加的模型到 models.json
    if selected_config not in existing_models:
        existing_models.append(selected_config)
        model_manager.save_models(existing_models)
        console.print(f"[green]Model '{selected_config.id}' saved to models.json[/]")

    console.print(f"Model: {selected_config.id} ({selected_config.provider}:{selected_config.model})\n")

    # --- Database ---
    data_dir = project_root / "data"
    data_dir.mkdir(exist_ok=True)
    db_url = settings.database.url
    if db_url.startswith("sqlite") and ":///" in db_url and not db_url.startswith("sqlite+aiosqlite:////"):
        relative_path = db_url.split(":///", 1)[1]
        absolute_path = project_root / relative_path
        db_url = f"sqlite+aiosqlite:///{absolute_path}"
    engine = await init_db(db_url)

    # Get or create default session
    async with get_session(engine) as db_sess:
        stmt = select(Session).where(Session.name == "default")
        result = await db_sess.execute(stmt)
        trading_session = result.scalar_one_or_none()
        if trading_session is None:
            trading_session = Session(
                name="default",
                symbol=settings.trading.symbol,
                persona_config=json.dumps(trader_config.persona.model_dump()),
                model_config=json.dumps({"id": selected_config.id, "provider": selected_config.provider, "model": selected_config.model}),
                initial_balance=settings.trading.initial_balance_usdt,
                status="active",
            )
            db_sess.add(trading_session)
            await db_sess.commit()
            await db_sess.refresh(trading_session)
            logger.info(f"Created session: {trading_session.id}")
        else:
            logger.info(f"Resumed session: {trading_session.id}")
    session_id = trading_session.id

    if settings.exchange.name == "simulated":
        from src.integrations.exchange.simulated import SimulatedExchange
        exchange = SimulatedExchange(
            config=settings.exchange,
            db_engine=engine,
            session_id=session_id,
            symbol=settings.trading.symbol,
        )
        console.print("Exchange: simulated (local matching)")
    else:
        # 注意：Task 5 的 app.py 保持 OKXExchange 旧构造函数调用（3 参数），
        # Task 8 Step 5 将统一更新为 4 参数（添加 symbol）。
        exchange = OKXExchange(
            api_key=settings.exchange.api_key,
            secret=settings.exchange.secret,
            password=settings.exchange.password,
        )
        console.print(f"Exchange: {settings.exchange.name} (REAL account)")
    market_data = MarketDataService(exchange)
    technical = TechnicalAnalysisService()
    memory = MemoryService(engine, session_id=session_id)
    metrics_service = MetricsService(initial_balance=trading_session.initial_balance)
    budget = TokenBudget(daily_max=settings.llm_budget.daily_max_tokens)
    approval_gate = ApprovalGate(
        enabled=settings.approval.enabled,
        timeout_seconds=settings.approval.timeout_seconds,
    )

    # create_trader_agent with placeholder model (overridden at runtime)
    agent = create_trader_agent(model="placeholder", persona_config=trader_config.persona)

    deps = TradingDeps(
        symbol=settings.trading.symbol,
        timeframe=settings.trading.timeframe,
        market_data=market_data,
        exchange=exchange,
        technical=technical,
        memory=memory,
        session_id=session_id,
        db_engine=engine,
        approval_gate=approval_gate,
        approval_enabled=settings.approval.enabled,
    )

    # Graceful shutdown
    shutdown_event = asyncio.Event()

    def _signal_handler():
        console.print("\n[yellow]Shutting down gracefully...[/]")
        shutdown_event.set()

    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, _signal_handler)

    # NOTE: fill handler, alert handler, and exchange.start() are NOT modified here.
    # Existing Phase 1a code (on_tick, fill handler, scheduler, exchange.start) remains as-is.
    # Task 11 will unify fill/alert handler registration for both exchange types.

    # ... (rest of run() unchanged from Phase 1a: fill handler, scheduler,
    #  exchange.start, metrics display, scheduler loop, shutdown — see existing app.py)

    # 在 on_tick 中传入 model（关键修改）
    async def on_tick(trigger_type: str, context=None):
        if shutdown_event.is_set():
            return
        try:
            await run_agent_cycle(agent, deps, trigger_type, budget, engine, context, model=selected_model)
        except Exception:
            logger.exception("Agent cycle failed")


async def _interactive_add_model(model_manager, existing_models):
    """交互式添加新模型。返回 (ModelConfig, pydantic-ai Model) 或 (None, None)。"""
    from src.services.model_manager import ModelConfig

    console.print("Supported providers: anthropic, openai, google-gla, groq")
    provider = input("Provider: ").strip()
    model_name = input("Model name (e.g. claude-opus-4-6, gpt-4o): ").strip()
    api_key = input("API key: ").strip()
    base_url_input = input("Base URL (press Enter for default): ").strip()
    base_url = base_url_input if base_url_input else None
    model_id = input("Friendly ID (e.g. claude-opus, gpt4o): ").strip()

    if not all([provider, model_name, api_key, model_id]):
        console.print("[red]All fields except base_url are required.[/]")
        return None, None

    config = ModelConfig(
        id=model_id,
        provider=provider,
        model=model_name,
        api_key=api_key,
        base_url=base_url,
    )

    try:
        model = model_manager.create_model(config)
    except ValueError as e:
        console.print(f"[red]{e}[/]")
        return None, None

    return config, model
```

Remove these 3 lines from `src/cli/app.py`（保留 `llm_router.py` 文件本身不删除）：

1. Top-level import: `from src.services.llm_router import LLMRouter` (currently around line 22)
2. Instance creation: `llm_router = LLMRouter(settings.models)` (currently around line 225)
3. Model resolution: `model = llm_router.resolve("trade_decision")` (currently around line 234)

- [ ] **Step 4: 运行全部测试确保无回归**

Run: `cd /Users/z/Z/TradeBot && python -m pytest tests/ -v --ignore=tests/test_cli.py`

Expected: PASS — 所有测试通过（`test_cli.py` 可能需要适配新签名，暂时忽略）。

- [ ] **Step 5: 移除 settings.yaml 和 settings_sim.yaml 中的 `models:` 段**

模型配置已迁移到 `config/models.json`（由 ModelManager 管理），YAML 中的 `models:` 段不再使用，需要移除。

从 `config/settings.yaml` 中删除以下内容（第 17-27 行）：

```yaml
# 删除以下内容
# === LLM Model Configuration ===
models:
  default: anthropic:claude-sonnet-4-20250514   # Fallback model for unspecified tasks
  strong: anthropic:claude-opus-4-6             # High-capability model (complex analysis)
  weak: anthropic:claude-haiku-4-5-20251001     # Fast/cheap model (simple tasks)
  routing:                     # Which model tier to use for each task type
    market_analysis: strong    # Market data analysis and interpretation
    trade_decision: strong     # Final trading decision (buy/sell/hold)
    news_summary: weak         # News article summarization (Phase 1b)
    review: weak               # Post-trade review and memory updates (Phase 1b)
```

从 `config/settings_sim.yaml` 中删除以下内容（第 15-25 行）：

```yaml
# 删除以下内容
# === LLM Model Configuration ===
models:
  default: anthropic:claude-sonnet-4-20250514   # Fallback model for unspecified tasks
  strong: anthropic:claude-opus-4-6             # High-capability model (complex analysis)
  weak: anthropic:claude-haiku-4-5-20251001     # Fast/cheap model (simple tasks)
  routing:                     # Which model tier to use for each task type
    market_analysis: strong    # Market data analysis and interpretation
    trade_decision: strong     # Final trading decision (buy/sell/hold)
    news_summary: weak         # News article summarization (Phase 1b)
    review: weak               # Post-trade review and memory updates (Phase 1b)
```

- [ ] **Step 6: 修改 `main.py` 解析 `--model` CLI 参数**

修改 `main.py` 为：

```python
# main.py

import argparse
import asyncio

from src.cli.app import run

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="TradeBot")
    parser.add_argument("--model", type=str, default=None, help="Model ID from models.json (skip interactive selection)")
    args = parser.parse_args()
    asyncio.run(run(model_id=args.model))
```

- [ ] **Step 7: 提交**

Run: `cd /Users/z/Z/TradeBot && git add src/cli/app.py .gitignore config/settings.yaml config/settings_sim.yaml main.py && git commit -m "feat: integrate ModelManager into app startup (model selection + connectivity test only, fill/alert/start handled by Task 11)"`

---

### Task 6: PriceAlertService — 价格异动检测服务

**概述:** 新建 `src/services/price_alert.py`，实现 sliding window 价格监控、阈值检测、方向冷却、运行时参数更新。

**Files:**
- Create: `src/services/price_alert.py`
- Create: `tests/test_price_alert.py`

- [ ] **Step 1: 编写失败测试**

创建 `tests/test_price_alert.py`：

```python
# tests/test_price_alert.py

import pytest


def test_alert_info_fields():
    """AlertInfo 应包含所有必需字段。"""
    from src.services.price_alert import AlertInfo
    info = AlertInfo(
        symbol="BTC/USDT:USDT",
        current_price=59000.0,
        reference_price=61000.0,
        change_pct=-3.28,
        window_minutes=5,
        timestamp=1712534400000,
    )
    assert info.symbol == "BTC/USDT:USDT"
    assert info.change_pct < 0
    assert info.reference_price == 61000.0


def test_no_alert_below_threshold():
    """价格变化未达阈值时不应触发警报。"""
    from src.services.price_alert import PriceAlertService
    service = PriceAlertService(
        symbol="BTC/USDT:USDT",
        window_minutes=5,
        threshold_pct=3.0,
        cooldown_minutes=15,
    )
    base_ts = 1_000_000_000_000  # ms
    # 喂入初始价格
    service.check(60000.0, base_ts)
    # 小幅下跌 1%
    result = service.check(59400.0, base_ts + 60_000)
    assert result is None


def test_alert_triggers_on_drop():
    """价格下跌超过阈值应触发 alert（change_pct 为负）。"""
    from src.services.price_alert import PriceAlertService
    service = PriceAlertService(
        symbol="BTC/USDT:USDT",
        window_minutes=5,
        threshold_pct=3.0,
        cooldown_minutes=15,
    )
    base_ts = 1_000_000_000_000
    service.check(60000.0, base_ts)
    # 下跌 3.5%
    result = service.check(57900.0, base_ts + 60_000)
    assert result is not None
    assert result.change_pct < -3.0
    assert result.reference_price == 60000.0  # window high
    assert result.current_price == 57900.0
    assert result.symbol == "BTC/USDT:USDT"


def test_alert_triggers_on_surge():
    """价格上涨超过阈值应触发 alert（change_pct 为正）。"""
    from src.services.price_alert import PriceAlertService
    service = PriceAlertService(
        symbol="BTC/USDT:USDT",
        window_minutes=5,
        threshold_pct=3.0,
        cooldown_minutes=15,
    )
    base_ts = 1_000_000_000_000
    service.check(60000.0, base_ts)
    # 上涨 3.5%
    result = service.check(62100.0, base_ts + 60_000)
    assert result is not None
    assert result.change_pct > 3.0
    assert result.reference_price == 60000.0  # window low
    assert result.current_price == 62100.0


def test_cooldown_blocks_same_direction():
    """同方向触发后在冷却期内不应重复触发。"""
    from src.services.price_alert import PriceAlertService
    service = PriceAlertService(
        symbol="BTC/USDT:USDT",
        window_minutes=5,
        threshold_pct=3.0,
        cooldown_minutes=15,
    )
    base_ts = 1_000_000_000_000
    service.check(60000.0, base_ts)
    # 首次下跌触发
    result1 = service.check(57900.0, base_ts + 60_000)
    assert result1 is not None

    # 继续下跌，但在冷却期内
    result2 = service.check(57000.0, base_ts + 120_000)
    assert result2 is None


def test_cooldown_allows_opposite_direction():
    """V 形反弹：下跌触发后，反方向上涨超阈值仍应触发。"""
    from src.services.price_alert import PriceAlertService
    service = PriceAlertService(
        symbol="BTC/USDT:USDT",
        window_minutes=5,
        threshold_pct=3.0,
        cooldown_minutes=15,
    )
    base_ts = 1_000_000_000_000
    service.check(60000.0, base_ts)
    # 下跌触发
    result1 = service.check(57900.0, base_ts + 60_000)
    assert result1 is not None
    assert result1.change_pct < 0

    # 快速反弹 — 从 window low (57900) 上涨超过 3%
    result2 = service.check(59700.0, base_ts + 120_000)
    # 59700 vs low(57900) = +3.1%，应触发
    assert result2 is not None
    assert result2.change_pct > 0


def test_cooldown_expires():
    """冷却期过后同方向应再次触发。"""
    from src.services.price_alert import PriceAlertService
    service = PriceAlertService(
        symbol="BTC/USDT:USDT",
        window_minutes=5,
        threshold_pct=3.0,
        cooldown_minutes=1,  # 1 分钟冷却
    )
    base_ts = 1_000_000_000_000
    service.check(60000.0, base_ts)
    # 首次下跌触发
    result1 = service.check(57900.0, base_ts + 30_000)
    assert result1 is not None

    # 冷却期过后（>1 分钟），再次喂入高价后下跌
    new_base = base_ts + 120_000  # 2 分钟后
    service.check(60000.0, new_base)
    result2 = service.check(57900.0, new_base + 30_000)
    assert result2 is not None


def test_window_eviction():
    """窗口外的旧数据应被淘汰，不影响当前计算。"""
    from src.services.price_alert import PriceAlertService
    service = PriceAlertService(
        symbol="BTC/USDT:USDT",
        window_minutes=1,  # 1 分钟窗口
        threshold_pct=3.0,
        cooldown_minutes=1,  # 最小冷却（tick 间隔 2min > 1min 冷却，测试不受影响）
    )
    base_ts = 1_000_000_000_000
    # 喂入高价
    service.check(60000.0, base_ts)
    # 2 分钟后喂入低价 — 高价已被淘汰出窗口
    result = service.check(58000.0, base_ts + 120_000)
    # 窗口内只有 58000 一个点，high == low == 58000，变化为 0
    assert result is None


def test_update_params():
    """update_params 应更新内部参数。"""
    from src.services.price_alert import PriceAlertService
    service = PriceAlertService(
        symbol="BTC/USDT:USDT",
        window_minutes=5,
        threshold_pct=3.0,
        cooldown_minutes=15,
    )
    # 初始状态：3% 阈值
    base_ts = 1_000_000_000_000
    service.check(60000.0, base_ts)
    result = service.check(59000.0, base_ts + 60_000)  # ~1.7% 下跌
    assert result is None

    # 降低阈值到 1%
    service.update_params(threshold_pct=1.0, window_minutes=5, cooldown_minutes=15)
    result = service.check(58800.0, base_ts + 120_000)  # 从 60000 到 58800 = 2%
    assert result is not None


def test_update_params_boundary_validation():
    """update_params 超出边界时应抛 ValueError。"""
    from src.services.price_alert import PriceAlertService
    service = PriceAlertService(
        symbol="BTC/USDT:USDT",
        window_minutes=5,
        threshold_pct=3.0,
        cooldown_minutes=15,
    )
    with pytest.raises(ValueError):
        service.update_params(threshold_pct=0.1, window_minutes=5, cooldown_minutes=15)  # < 0.5
    with pytest.raises(ValueError):
        service.update_params(threshold_pct=3.0, window_minutes=0, cooldown_minutes=15)  # < 1
    with pytest.raises(ValueError):
        service.update_params(threshold_pct=3.0, window_minutes=5, cooldown_minutes=0)  # < 1
    with pytest.raises(ValueError):
        service.update_params(threshold_pct=3.0, window_minutes=5, cooldown_minutes=200)  # > 120
    with pytest.raises(ValueError):
        service.update_params(threshold_pct=55.0, window_minutes=5, cooldown_minutes=15)  # > 50
    with pytest.raises(ValueError):
        service.update_params(threshold_pct=3.0, window_minutes=70, cooldown_minutes=15)  # > 60
```

- [ ] **Step 2: 运行测试确认失败**

Run: `cd /Users/z/Z/TradeBot && python -m pytest tests/test_price_alert.py -v`

Expected: FAIL — `src.services.price_alert` 模块不存在。

- [ ] **Step 3: 实现 PriceAlertService**

创建 `src/services/price_alert.py`：

```python
# src/services/price_alert.py
from __future__ import annotations

from collections import deque
from dataclasses import dataclass


@dataclass
class AlertInfo:
    symbol: str
    current_price: float
    reference_price: float
    change_pct: float
    window_minutes: int
    timestamp: int


class PriceAlertService:
    @staticmethod
    def _validate_params(threshold_pct: float, window_minutes: int, cooldown_minutes: int) -> None:
        if not (0.5 <= threshold_pct <= 50.0):
            raise ValueError(f"threshold_pct must be 0.5-50.0, got {threshold_pct}")
        if not (1 <= window_minutes <= 60):
            raise ValueError(f"window_minutes must be 1-60, got {window_minutes}")
        if not (1 <= cooldown_minutes <= 120):
            raise ValueError(f"cooldown_minutes must be 1-120, got {cooldown_minutes}")

    def __init__(
        self,
        symbol: str,
        window_minutes: int,
        threshold_pct: float,
        cooldown_minutes: int,
    ):
        self._validate_params(threshold_pct, window_minutes, cooldown_minutes)
        self._symbol = symbol
        self._window_ms = window_minutes * 60 * 1000
        self._window_minutes = window_minutes
        self._threshold_pct = threshold_pct
        self._cooldown_ms = cooldown_minutes * 60 * 1000
        self._ticks: deque[tuple[float, int]] = deque()  # (price, timestamp_ms)
        self._last_alert_ts: dict[str, int] = {}  # "drop" / "surge" → timestamp_ms

    def check(self, price: float, timestamp: int) -> AlertInfo | None:
        """喂入 tick 价格，返回 AlertInfo 或 None。"""
        # 1. 追加当前 tick，淘汰窗口外的旧数据
        self._ticks.append((price, timestamp))
        cutoff = timestamp - self._window_ms
        while self._ticks and self._ticks[0][1] < cutoff:
            self._ticks.popleft()

        if len(self._ticks) < 2:
            return None

        # 2. 计算窗口内 high/low
        high = max(p for p, _ in self._ticks)
        low = min(p for p, _ in self._ticks)

        # 3. 计算偏离
        drop_pct = (price - high) / high * 100 if high > 0 else 0.0
        rise_pct = (price - low) / low * 100 if low > 0 else 0.0

        # 4. 取绝对值更大的方向
        if abs(drop_pct) >= abs(rise_pct) and abs(drop_pct) >= self._threshold_pct:
            direction = "drop"
            change_pct = drop_pct
            reference_price = high
        elif abs(rise_pct) >= self._threshold_pct:
            direction = "surge"
            change_pct = rise_pct
            reference_price = low
        else:
            return None

        # 5. 冷却检查
        last_ts = self._last_alert_ts.get(direction, 0)
        if timestamp - last_ts < self._cooldown_ms:
            return None

        # 6. 触发
        self._last_alert_ts[direction] = timestamp
        return AlertInfo(
            symbol=self._symbol,
            current_price=price,
            reference_price=reference_price,
            change_pct=change_pct,
            window_minutes=self._window_minutes,
            timestamp=timestamp,
        )

    def update_params(
        self,
        threshold_pct: float,
        window_minutes: int,
        cooldown_minutes: int,
    ) -> None:
        """运行时更新参数。超出边界抛 ValueError。"""
        self._validate_params(threshold_pct, window_minutes, cooldown_minutes)

        self._threshold_pct = threshold_pct
        self._window_ms = window_minutes * 60 * 1000
        self._window_minutes = window_minutes
        self._cooldown_ms = cooldown_minutes * 60 * 1000
```

- [ ] **Step 4: 运行测试**

Run: `cd /Users/z/Z/TradeBot && python -m pytest tests/test_price_alert.py -v`

Expected: PASS — 所有测试通过。

- [ ] **Step 5: 提交**

Run: `cd /Users/z/Z/TradeBot && git add src/services/price_alert.py tests/test_price_alert.py && git commit -m "feat: add PriceAlertService with sliding window, threshold detection, and directional cooldown"`

---

### Task 7: set_price_alert tool — Agent 运行时调整价格预警

**概述:** 在 `tools_execution.py` 中添加 `set_price_alert` 工具函数，在 `trader.py` 中注册为 agent tool。

**Files:**
- Modify: `src/agent/tools_execution.py`
- Modify: `src/agent/trader.py`
- Modify: `tests/test_tools.py`

- [ ] **Step 1: 编写失败测试**

在 `tests/test_tools.py` 末尾追加：

```python
# tests/test_tools.py — 末尾追加

async def test_set_price_alert_valid(deps):
    """set_price_alert 参数合法时应调用 exchange.update_alert_params。"""
    from src.agent.tools_execution import set_price_alert
    deps.exchange.update_alert_params = MagicMock()
    result = await set_price_alert(deps, 2.0, 5, 10, reasoning="high volatility")
    assert "updated" in result.lower() or "set" in result.lower()
    deps.exchange.update_alert_params.assert_called_once_with(2.0, 5, 10)


async def test_set_price_alert_threshold_too_low(deps):
    """threshold_pct < 0.5 时应返回错误，不调用 update。"""
    from src.agent.tools_execution import set_price_alert
    deps.exchange.update_alert_params = MagicMock()
    result = await set_price_alert(deps, 0.1, 5, 10, reasoning="test")
    assert "error" in result.lower() or "invalid" in result.lower() or "must be" in result.lower()
    deps.exchange.update_alert_params.assert_not_called()


async def test_set_price_alert_threshold_too_high(deps):
    """threshold_pct > 50 时应返回错误。"""
    from src.agent.tools_execution import set_price_alert
    deps.exchange.update_alert_params = MagicMock()
    result = await set_price_alert(deps, 55.0, 5, 10, reasoning="test")
    assert "error" in result.lower() or "invalid" in result.lower() or "must be" in result.lower()
    deps.exchange.update_alert_params.assert_not_called()


async def test_set_price_alert_window_out_of_range(deps):
    """window_minutes 超出 1-60 范围时应返回错误。"""
    from src.agent.tools_execution import set_price_alert
    deps.exchange.update_alert_params = MagicMock()
    result = await set_price_alert(deps, 3.0, 0, 10, reasoning="test")
    assert "error" in result.lower() or "invalid" in result.lower() or "must be" in result.lower()
    deps.exchange.update_alert_params.assert_not_called()


async def test_set_price_alert_cooldown_out_of_range(deps):
    """cooldown_minutes 超出 1-120 范围时应返回错误。"""
    from src.agent.tools_execution import set_price_alert
    deps.exchange.update_alert_params = MagicMock()
    result = await set_price_alert(deps, 3.0, 5, 200, reasoning="test")
    assert "error" in result.lower() or "invalid" in result.lower() or "must be" in result.lower()
    deps.exchange.update_alert_params.assert_not_called()
```

- [ ] **Step 2: 运行测试确认失败**

Run: `cd /Users/z/Z/TradeBot && python -m pytest tests/test_tools.py::test_set_price_alert_valid tests/test_tools.py::test_set_price_alert_threshold_too_low tests/test_tools.py::test_set_price_alert_threshold_too_high tests/test_tools.py::test_set_price_alert_window_out_of_range tests/test_tools.py::test_set_price_alert_cooldown_out_of_range -v`

Expected: FAIL — `set_price_alert` 函数不存在。

- [ ] **Step 3: 实现 set_price_alert tool**

在 `src/agent/tools_execution.py` 末尾追加：

```python
# src/agent/tools_execution.py — 末尾追加

async def set_price_alert(
    deps: TradingDeps,
    threshold_pct: float,
    window_minutes: int,
    cooldown_minutes: int,
    reasoning: str,
) -> str:
    """Adjust price alert parameters. threshold_pct: 0.5-50%, window_minutes: 1-60, cooldown_minutes: 1-120."""
    # 参数边界验证
    if not (0.5 <= threshold_pct <= 50.0):
        return f"Invalid threshold_pct: must be 0.5-50.0, got {threshold_pct}"
    if not (1 <= window_minutes <= 60):
        return f"Invalid window_minutes: must be 1-60, got {window_minutes}"
    if not (1 <= cooldown_minutes <= 120):
        return f"Invalid cooldown_minutes: must be 1-120, got {cooldown_minutes}"

    deps.exchange.update_alert_params(threshold_pct, window_minutes, cooldown_minutes)

    await _record_action(
        deps, action="set_price_alert",
        reasoning=f"threshold={threshold_pct}%, window={window_minutes}min, cooldown={cooldown_minutes}min | {reasoning}",
    )

    return (
        f"Price alert updated: threshold={threshold_pct}%, "
        f"window={window_minutes}min, cooldown={cooldown_minutes}min"
    )
```

在 `src/agent/trader.py` 的 `create_trader_agent` 函数中，在 Memory Tools 部分之前添加 `set_price_alert` tool 注册：

```python
    # src/agent/trader.py — 在 "# === Memory Tools ===" 之前添加

    @agent.tool
    async def set_price_alert(
        ctx: RunContext[TradingDeps],
        threshold_pct: float,
        window_minutes: int,
        cooldown_minutes: int,
        reasoning: str,
    ) -> str:
        """Adjust price alert parameters. threshold_pct: 0.5-50%, window_minutes: 1-60, cooldown_minutes: 1-120. Always provide reasoning."""
        from src.agent.tools_execution import set_price_alert as _impl

        return await _impl(ctx.deps, threshold_pct, window_minutes, cooldown_minutes, reasoning=reasoning)
```

- [ ] **Step 4: 运行全部 tools 测试**

Run: `cd /Users/z/Z/TradeBot && python -m pytest tests/test_tools.py -v`

Expected: PASS — 所有新旧测试通过。

- [ ] **Step 5: 提交**

Run: `cd /Users/z/Z/TradeBot && git add src/agent/tools_execution.py src/agent/trader.py tests/test_tools.py && git commit -m "feat: add set_price_alert agent tool with parameter boundary validation"`

---

### Task 8: OKXExchange WebSocket — Fill 推送

**概述:** 在 `OKXExchange` 中新增 `ccxt.pro` WebSocket 客户端，实现 `start()`、`on_fill()`、`_watch_orders_loop()`、`_parse_fill_event()`。构造函数新增 `symbol` 参数。`close()` 改为 try/finally 双客户端关闭。

**Files:**
- Modify: `src/integrations/exchange/okx.py`
- Create: `tests/test_okx_websocket.py`

- [ ] **Step 1: 编写失败测试**

创建 `tests/test_okx_websocket.py`：

```python
# tests/test_okx_websocket.py

import asyncio
import pytest
from unittest.mock import AsyncMock, MagicMock, patch, PropertyMock


def test_okx_constructor_accepts_symbol():
    """OKXExchange 构造函数应接受 symbol 参数。"""
    with patch("ccxt.async_support.okx") as mock_okx:
        mock_okx.return_value = MagicMock()
        from src.integrations.exchange.okx import OKXExchange
        exchange = OKXExchange(
            api_key="test", secret="test", password="test",
            symbol="BTC/USDT:USDT",
        )
        assert exchange._symbol == "BTC/USDT:USDT"


def test_okx_on_fill_registers_callback():
    """on_fill 应注册回调函数。"""
    with patch("ccxt.async_support.okx") as mock_okx:
        mock_okx.return_value = MagicMock()
        from src.integrations.exchange.okx import OKXExchange
        exchange = OKXExchange(
            api_key="test", secret="test", password="test",
            symbol="BTC/USDT:USDT",
        )
        callback = AsyncMock()
        exchange.on_fill(callback)
        assert exchange._fill_callback is callback


async def test_parse_fill_event_stop_loss():
    """_parse_fill_event 应正确解析止损成交数据。"""
    with patch("ccxt.async_support.okx") as mock_okx:
        mock_okx.return_value = MagicMock()
        from src.integrations.exchange.okx import OKXExchange
        exchange = OKXExchange(
            api_key="test", secret="test", password="test",
            symbol="BTC/USDT:USDT",
        )
        order_data = {
            "id": "order-123",
            "symbol": "BTC/USDT:USDT",
            "side": "sell",
            "type": "stop",
            "status": "closed",
            "average": 59000.0,
            "price": 59000.0,
            "filled": 0.01,
            "fee": {"cost": 0.295, "currency": "USDT"},
            "timestamp": 1712534400000,
            "info": {
                "posSide": "long",
                "pnl": "-12.50",
            },
        }
        fill = await exchange._parse_fill_event(order_data)
        assert fill.order_id == "order-123"
        assert fill.symbol == "BTC/USDT:USDT"
        assert fill.side == "sell"
        assert fill.position_side == "long"
        assert fill.trigger_reason == "stop"
        assert fill.fill_price == 59000.0
        assert fill.amount == 0.01
        assert fill.fee == 0.295
        assert fill.pnl == -12.50
        assert fill.timestamp == 1712534400000


async def test_parse_fill_event_take_profit():
    """_parse_fill_event 应正确解析止盈成交。"""
    with patch("ccxt.async_support.okx") as mock_okx:
        mock_okx.return_value = MagicMock()
        from src.integrations.exchange.okx import OKXExchange
        exchange = OKXExchange(
            api_key="test", secret="test", password="test",
            symbol="BTC/USDT:USDT",
        )
        order_data = {
            "id": "order-456",
            "symbol": "BTC/USDT:USDT",
            "side": "sell",
            "type": "take_profit",
            "status": "closed",
            "average": 65000.0,
            "price": 65000.0,
            "filled": 0.01,
            "fee": {"cost": 0.325, "currency": "USDT"},
            "timestamp": 1712534500000,
            "info": {
                "posSide": "long",
                "pnl": "25.00",
            },
        }
        fill = await exchange._parse_fill_event(order_data)
        assert fill.position_side == "long"
        assert fill.trigger_reason == "take_profit"
        assert fill.pnl == 25.00


async def test_parse_fill_event_infer_position_side():
    """当 info.posSide 缺失时，应根据 side + type 推断 position_side。"""
    with patch("ccxt.async_support.okx") as mock_okx:
        mock_okx.return_value = MagicMock()
        from src.integrations.exchange.okx import OKXExchange
        exchange = OKXExchange(
            api_key="test", secret="test", password="test",
            symbol="BTC/USDT:USDT",
        )
        # sell + stop → long（多头止损）
        order_data = {
            "id": "order-789",
            "symbol": "BTC/USDT:USDT",
            "side": "sell",
            "type": "stop",
            "status": "closed",
            "average": 58000.0,
            "price": 58000.0,
            "filled": 0.01,
            "fee": {"cost": 0.29, "currency": "USDT"},
            "timestamp": 1712534600000,
            "info": {},  # 无 posSide
        }
        fill = await exchange._parse_fill_event(order_data)
        assert fill.position_side == "long"

        # buy + stop → short（空头止损）
        order_data["side"] = "buy"
        order_data["id"] = "order-790"
        fill2 = await exchange._parse_fill_event(order_data)
        assert fill2.position_side == "short"


async def test_parse_fill_event_pnl_missing():
    """当 info.pnl 缺失且 REST 补查也无 pnl 时，pnl 应为 None。"""
    with patch("ccxt.async_support.okx") as mock_okx:
        mock_okx.return_value = MagicMock()
        from src.integrations.exchange.okx import OKXExchange
        exchange = OKXExchange(
            api_key="test", secret="test", password="test",
            symbol="BTC/USDT:USDT",
        )
        # 显式 mock REST fetch_order — 返回的 info 中也无 pnl
        exchange._client.fetch_order = AsyncMock(return_value={"info": {}})
        order_data = {
            "id": "order-no-pnl",
            "symbol": "BTC/USDT:USDT",
            "side": "sell",
            "type": "stop",
            "status": "closed",
            "average": 58000.0,
            "price": 58000.0,
            "filled": 0.01,
            "fee": {"cost": 0.29, "currency": "USDT"},
            "timestamp": 1712534600000,
            "info": {"posSide": "long"},  # 无 pnl
        }
        fill = await exchange._parse_fill_event(order_data)
        assert fill.pnl is None
        exchange._client.fetch_order.assert_called_once()  # 确认触发了 REST 补查


async def test_parse_fill_event_pnl_rest_fallback():
    """当 info.pnl 缺失时，应通过 REST fetch_order 补查 pnl。"""
    with patch("ccxt.async_support.okx") as mock_okx:
        mock_okx.return_value = MagicMock()
        from src.integrations.exchange.okx import OKXExchange
        exchange = OKXExchange(
            api_key="test", secret="test", password="test",
            symbol="BTC/USDT:USDT",
        )
        order_data = {
            "id": "order-rest-pnl",
            "symbol": "BTC/USDT:USDT",
            "side": "sell",
            "type": "stop",
            "status": "closed",
            "average": 58000.0,
            "price": 58000.0,
            "filled": 0.01,
            "fee": {"cost": 0.29, "currency": "USDT"},
            "timestamp": 1712534600000,
            "info": {"posSide": "long"},  # 无 pnl
        }
        # Mock REST fetch_order 返回带 pnl 的数据
        exchange._client.fetch_order = AsyncMock(return_value={
            "info": {"pnl": "-3.50"},
        })
        fill = await exchange._parse_fill_event(order_data)
        assert fill.pnl == -3.5
        exchange._client.fetch_order.assert_called_once_with("order-rest-pnl", "BTC/USDT:USDT")


async def test_parse_fill_event_pnl_rest_fallback_timeout():
    """REST fetch_order 超时时 pnl 应为 None。"""
    with patch("ccxt.async_support.okx") as mock_okx:
        mock_okx.return_value = MagicMock()
        from src.integrations.exchange.okx import OKXExchange
        exchange = OKXExchange(
            api_key="test", secret="test", password="test",
            symbol="BTC/USDT:USDT",
        )
        exchange._pnl_fetch_timeout = 0.1  # 缩短超时，避免测试等 5 秒
        order_data = {
            "id": "order-timeout",
            "symbol": "BTC/USDT:USDT",
            "side": "sell",
            "type": "stop",
            "status": "closed",
            "average": 58000.0,
            "price": 58000.0,
            "filled": 0.01,
            "fee": {"cost": 0.29, "currency": "USDT"},
            "timestamp": 1712534600000,
            "info": {"posSide": "long"},  # 无 pnl
        }
        # Mock REST fetch_order 慢响应
        async def slow_fetch(*args):
            await asyncio.sleep(10)
        exchange._client.fetch_order = slow_fetch
        fill = await exchange._parse_fill_event(order_data)
        assert fill.pnl is None


async def test_parse_fill_event_unknown_order_type():
    """未知 order_type 应设 trigger_reason 为 'unknown'。"""
    with patch("ccxt.async_support.okx") as mock_okx:
        mock_okx.return_value = MagicMock()
        from src.integrations.exchange.okx import OKXExchange
        exchange = OKXExchange(
            api_key="test", secret="test", password="test",
            symbol="BTC/USDT:USDT",
        )
        order_data = {
            "id": "order-unknown",
            "symbol": "BTC/USDT:USDT",
            "side": "sell",
            "type": "trailingStop",
            "status": "closed",
            "average": 58000.0,
            "price": 58000.0,
            "filled": 0.01,
            "fee": {"cost": 0.29, "currency": "USDT"},
            "timestamp": 1712534600000,
            "info": {"posSide": "long"},
        }
        fill = await exchange._parse_fill_event(order_data)
        assert fill.trigger_reason == "unknown"


async def test_watch_orders_loop_calls_callback():
    """_watch_orders_loop 应在收到 closed 订单时调用 fill callback。"""
    with patch("ccxt.async_support.okx") as mock_okx:
        mock_okx.return_value = MagicMock()
        from src.integrations.exchange.okx import OKXExchange
        exchange = OKXExchange(
            api_key="test", secret="test", password="test",
            symbol="BTC/USDT:USDT",
        )

        callback = AsyncMock()
        exchange.on_fill(callback)
        exchange._running = True

        order_data = {
            "id": "order-ws",
            "symbol": "BTC/USDT:USDT",
            "side": "sell",
            "type": "stop",
            "status": "closed",
            "average": 59000.0,
            "price": 59000.0,
            "filled": 0.01,
            "fee": {"cost": 0.295, "currency": "USDT"},
            "timestamp": 1712534400000,
            "info": {"posSide": "long", "pnl": "-5.00"},
        }

        mock_ws = AsyncMock()
        call_count = 0

        async def mock_watch_orders(symbol):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return [order_data]
            # 第二次调用后停止循环
            exchange._running = False
            return []

        mock_ws.watch_orders = mock_watch_orders
        exchange._ws_client = mock_ws

        await exchange._watch_orders_loop()

        callback.assert_called_once()
        fill_event = callback.call_args[0][0]
        assert fill_event.order_id == "order-ws"
        assert fill_event.pnl == -5.00


async def test_watch_orders_loop_skips_open_orders():
    """_watch_orders_loop 应忽略 status != 'closed' 的订单。"""
    with patch("ccxt.async_support.okx") as mock_okx:
        mock_okx.return_value = MagicMock()
        from src.integrations.exchange.okx import OKXExchange
        exchange = OKXExchange(
            api_key="test", secret="test", password="test",
            symbol="BTC/USDT:USDT",
        )

        callback = AsyncMock()
        exchange.on_fill(callback)
        exchange._running = True

        open_order = {
            "id": "order-open",
            "symbol": "BTC/USDT:USDT",
            "side": "sell",
            "type": "stop",
            "status": "open",
            "average": None,
            "price": 59000.0,
            "filled": 0,
            "fee": {"cost": 0, "currency": "USDT"},
            "timestamp": 1712534400000,
            "info": {"posSide": "long"},
        }

        mock_ws = AsyncMock()
        call_count = 0

        async def mock_watch_orders(symbol):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return [open_order]
            exchange._running = False
            return []

        mock_ws.watch_orders = mock_watch_orders
        exchange._ws_client = mock_ws

        await exchange._watch_orders_loop()
        callback.assert_not_called()


async def test_watch_orders_loop_logs_partial_fill():
    """_watch_orders_loop 应对 partial fill（filled > 0 且 status != closed）记录 warning。"""
    with patch("ccxt.async_support.okx") as mock_okx:
        mock_okx.return_value = MagicMock()
        from src.integrations.exchange.okx import OKXExchange
        import logging
        exchange = OKXExchange(
            api_key="test", secret="test", password="test",
            symbol="BTC/USDT:USDT",
        )

        callback = AsyncMock()
        exchange.on_fill(callback)
        exchange._running = True

        partial_order = {
            "id": "order-partial",
            "symbol": "BTC/USDT:USDT",
            "side": "sell",
            "type": "stop",
            "status": "open",
            "average": 59000.0,
            "price": 59000.0,
            "filled": 0.005,  # partial fill
            "fee": {"cost": 0.15, "currency": "USDT"},
            "timestamp": 1712534400000,
            "info": {"posSide": "long"},
        }

        mock_ws = AsyncMock()
        call_count = 0

        async def mock_watch_orders(symbol):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return [partial_order]
            exchange._running = False
            return []

        mock_ws.watch_orders = mock_watch_orders
        exchange._ws_client = mock_ws

        with patch("src.integrations.exchange.okx.logger") as mock_logger:
            await exchange._watch_orders_loop()
            mock_logger.warning.assert_called()  # 应有 warning 日志


async def test_watch_orders_loop_error_recovery():
    """_watch_orders_loop 应在异常后重试并重置 error_count。"""
    with patch("ccxt.async_support.okx") as mock_okx:
        mock_okx.return_value = MagicMock()
        from src.integrations.exchange.okx import OKXExchange
        exchange = OKXExchange(
            api_key="test", secret="test", password="test",
            symbol="BTC/USDT:USDT",
        )

        exchange._running = True
        mock_ws = AsyncMock()
        call_count = 0

        async def mock_watch_orders(symbol):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                raise ConnectionError("network error")
            exchange._running = False
            return []

        mock_ws.watch_orders = mock_watch_orders
        exchange._ws_client = mock_ws

        with patch("asyncio.sleep", new_callable=AsyncMock):
            await exchange._watch_orders_loop()
        # 不应崩溃，成功退出
        assert call_count == 2
```

- [ ] **Step 2: 运行测试确认失败**

Run: `cd /Users/z/Z/TradeBot && python -m pytest tests/test_okx_websocket.py -v`

Expected: FAIL — `OKXExchange` 构造函数不接受 `symbol`，缺少 `_parse_fill_event` 等方法。

- [ ] **Step 3: 实现 OKXExchange WebSocket**

修改 `src/integrations/exchange/okx.py`，完整替换为：

```python
# src/integrations/exchange/okx.py
from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable
from functools import wraps
from typing import Any, ParamSpec, TypeVar

import ccxt.async_support as ccxt

from src.integrations.exchange.base import (
    Balance,
    BaseExchange,
    Candle,
    FillEvent,
    Order,
    Position,
    Ticker,
)

logger = logging.getLogger(__name__)

P = ParamSpec("P")
R = TypeVar("R")

# order_type → trigger_reason 映射
_TRIGGER_REASON_MAP = {
    "stop": "stop",
    "stop_market": "stop",
    "take_profit": "take_profit",
    "take_profit_market": "take_profit",
    "market": "market",
}

# (side, order_type) → position_side 推断表
_POSITION_SIDE_INFER = {
    ("sell", "stop"): "long",
    ("buy", "stop"): "short",
    ("sell", "stop_market"): "long",
    ("buy", "stop_market"): "short",
    ("sell", "take_profit"): "long",
    ("buy", "take_profit"): "short",
    ("sell", "take_profit_market"): "long",
    ("buy", "take_profit_market"): "short",
}


def _retry(max_retries: int = 3, base_delay: float = 1.0):
    """Exponential backoff retry decorator for async exchange methods."""

    def decorator(func: Callable[P, Awaitable[R]]) -> Callable[P, Awaitable[R]]:
        @wraps(func)
        async def wrapper(*args: P.args, **kwargs: P.kwargs) -> R:
            last_error: Exception | None = None
            for attempt in range(max_retries):
                try:
                    return await func(*args, **kwargs)
                except (
                    ccxt.NetworkError,
                    ccxt.ExchangeNotAvailable,
                    asyncio.TimeoutError,
                ) as e:
                    last_error = e
                    delay = base_delay * (2 ** attempt)
                    logger.warning(
                        f"{func.__name__} attempt {attempt + 1}/{max_retries} "
                        f"failed: {e}, retrying in {delay}s"
                    )
                    await asyncio.sleep(delay)
            raise last_error  # type: ignore[misc]

        return wrapper  # type: ignore[return-value]

    return decorator


class OKXExchange(BaseExchange):
    def __init__(self, api_key: str, secret: str, password: str, symbol: str):
        self._client = ccxt.okx(
            {
                "apiKey": api_key,
                "secret": secret,
                "password": password,
                "options": {"defaultType": "swap"},
                "timeout": 30000,
            }
        )
        self._symbol = symbol
        self._fill_callback: Callable[[FillEvent], Awaitable[None]] | None = None
        self._alert_callback: Callable[[Any], Awaitable[None]] | None = None
        self._alert_service: Any | None = None
        self._running = False
        self._ws_client: Any | None = None
        self._ws_connected = False
        self._pnl_fetch_timeout: float = 5.0  # 秒，可在测试中覆盖
        logger.info("OKX exchange initialized (real account)")

    # --- Fill / Alert callback registration ---

    def on_fill(self, callback: Callable[[FillEvent], Awaitable[None]]) -> None:
        self._fill_callback = callback

    def on_alert(self, callback: Callable[[Any], Awaitable[None]]) -> None:
        self._alert_callback = callback

    def set_alert_service(self, service: Any) -> None:
        self._alert_service = service

    def update_alert_params(self, threshold_pct: float, window_minutes: int, cooldown_minutes: int) -> None:
        if self._alert_service:
            self._alert_service.update_params(threshold_pct, window_minutes, cooldown_minutes)

    # --- WebSocket lifecycle ---

    async def start(self) -> None:
        """启动 WebSocket 监听循环。失败时降级为 REST-only 模式。"""
        try:
            import ccxt.pro as ccxtpro
            self._ws_client = ccxtpro.okx({
                "apiKey": self._client.apiKey,
                "secret": self._client.secret,
                "password": self._client.password,
                "options": {"defaultType": "swap"},
            })
            self._running = True
            self._ws_connected = True
            self._orders_task = asyncio.create_task(self._watch_orders_loop())
            if self._alert_service:
                self._ticker_task = asyncio.create_task(self._watch_ticker_loop())
            logger.info("OKX WebSocket started (watch_orders + watch_ticker)")
        except Exception:
            self._ws_connected = False
            logger.error("WebSocket connection failed, running in REST-only mode", exc_info=True)
            from rich.console import Console
            Console().print("[yellow]⚠ WebSocket connection failed, running in REST-only mode[/]")

    # --- watch_orders loop ---

    async def _watch_orders_loop(self) -> None:
        error_count = 0
        while self._running:
            try:
                orders = await self._ws_client.watch_orders(self._symbol)
                error_count = 0
                for order_data in orders:
                    status = order_data.get("status")
                    filled = order_data.get("filled", 0) or 0

                    if status == "closed":
                        fill_event = await self._parse_fill_event(order_data)
                        if self._fill_callback:
                            await self._fill_callback(fill_event)
                    elif filled > 0 and status != "closed":
                        logger.warning(
                            "Partial fill detected: order %s filled=%s status=%s (not processing)",
                            order_data.get("id"), filled, status,
                        )
            except asyncio.CancelledError:
                break
            except Exception:
                error_count += 1
                delay = min(5 * (2 ** (error_count - 1)), 60)
                logger.error("watch_orders error (retry in %ds)", delay, exc_info=True)
                await asyncio.sleep(delay)

    # --- watch_ticker loop ---

    async def _watch_ticker_loop(self) -> None:
        error_count = 0
        while self._running:
            try:
                raw = await self._ws_client.watch_ticker(self._symbol)
                error_count = 0
                ticker = Ticker(
                    symbol=raw["symbol"],
                    last=float(raw["last"]),
                    bid=float(raw["bid"]),
                    ask=float(raw["ask"]),
                    high=float(raw["high"]),
                    low=float(raw["low"]),
                    base_volume=float(raw["baseVolume"]),
                    timestamp=raw["timestamp"],
                )
                if self._alert_service:
                    alert = self._alert_service.check(ticker.last, ticker.timestamp)
                    if alert and self._alert_callback:
                        await self._alert_callback(alert)
            except asyncio.CancelledError:
                break
            except Exception:
                error_count += 1
                delay = min(5 * (2 ** (error_count - 1)), 60)
                logger.warning("watch_ticker error (retry in %ds)", delay, exc_info=True)
                await asyncio.sleep(delay)

    # --- FillEvent 解析 ---

    async def _parse_fill_event(self, order_data: dict) -> FillEvent:
        """从 watch_orders 返回的 order 数据构造 FillEvent。"""
        order_id = order_data["id"]
        symbol = order_data["symbol"]
        side = order_data["side"]
        order_type = order_data.get("type", "")
        info = order_data.get("info", {})

        # position_side
        pos_side_raw = info.get("posSide")
        if pos_side_raw and pos_side_raw not in ("", "net"):
            position_side = pos_side_raw
        else:
            position_side = _POSITION_SIDE_INFER.get((side, order_type), side)

        # trigger_reason
        trigger_reason = _TRIGGER_REASON_MAP.get(order_type, "unknown")

        # fill_price
        fill_price = order_data.get("average") or order_data.get("price") or 0.0
        fill_price = float(fill_price)

        # amount
        amount = float(order_data.get("filled", 0) or 0)

        # fee
        fee_info = order_data.get("fee", {})
        fee = float(fee_info.get("cost", 0) or 0) if fee_info else 0.0

        # pnl — 优先取 info.pnl，缺失则 REST 补查（超时 5s，失败则 None）
        pnl_raw = info.get("pnl")
        pnl: float | None = None
        if pnl_raw is not None and pnl_raw != "":
            try:
                pnl = float(pnl_raw)
            except (ValueError, TypeError):
                pnl = None
        if pnl is None:
            try:
                fetched = await asyncio.wait_for(
                    self._client.fetch_order(order_id, symbol),
                    timeout=self._pnl_fetch_timeout,
                )
                pnl_fetched = fetched.get("info", {}).get("pnl")
                if pnl_fetched is not None:
                    pnl = float(pnl_fetched)
            except Exception:
                logger.warning("pnl fetch failed for order %s, setting pnl=None", order_id)

        # timestamp
        timestamp = order_data.get("timestamp", 0) or 0

        return FillEvent(
            order_id=order_id,
            symbol=symbol,
            side=side,
            position_side=position_side,
            trigger_reason=trigger_reason,
            fill_price=fill_price,
            amount=amount,
            fee=fee,
            pnl=pnl,
            timestamp=timestamp,
        )

    # --- REST interface (unchanged) ---

    @_retry()
    async def fetch_ticker(self, symbol: str) -> Ticker:  # type: ignore[override]
        data = await self._client.fetch_ticker(symbol)
        return Ticker(
            symbol=data["symbol"],  # type: ignore[arg-type]
            last=float(data["last"]),  # type: ignore[arg-type]
            bid=float(data["bid"]),  # type: ignore[arg-type]
            ask=float(data["ask"]),  # type: ignore[arg-type]
            high=float(data["high"]),  # type: ignore[arg-type]
            low=float(data["low"]),  # type: ignore[arg-type]
            base_volume=float(data["baseVolume"]),  # type: ignore[arg-type]
            timestamp=data["timestamp"],  # type: ignore[arg-type]
        )

    @_retry()
    async def fetch_ohlcv(  # type: ignore[override]
        self, symbol: str, timeframe: str, limit: int = 100
    ) -> list[Candle]:
        data = await self._client.fetch_ohlcv(symbol, timeframe, limit=limit)
        return [
            Candle(
                timestamp=int(r[0]),
                open=float(r[1]),
                high=float(r[2]),
                low=float(r[3]),
                close=float(r[4]),
                volume=float(r[5]),
            )
            for r in data
        ]

    def _parse_fee(self, data: dict) -> float | None:
        fee_info = data.get("fee")
        if fee_info and fee_info.get("cost") is not None:
            return float(fee_info["cost"])
        return None

    def _parse_order(self, data: dict) -> Order:
        return Order(
            id=data["id"],  # type: ignore[arg-type]
            symbol=data["symbol"],  # type: ignore[arg-type]
            side=data["side"],  # type: ignore[arg-type]
            order_type=data["type"],  # type: ignore[arg-type]
            amount=float(data["amount"]),  # type: ignore[arg-type]
            price=float(data["price"]) if data.get("price") else None,  # type: ignore[arg-type]
            status=data["status"],  # type: ignore[arg-type]
            fee=self._parse_fee(data),
        )

    @_retry()
    async def create_order(  # type: ignore[override]
        self,
        symbol: str,
        side: str,
        order_type: str,
        amount: float,
        price: float | None = None,
    ) -> Order:
        data = await self._client.create_order(
            symbol, order_type, side, amount, price  # type: ignore[arg-type]
        )
        return self._parse_order(data)

    @_retry()
    async def fetch_order(  # type: ignore[override]
        self, order_id: str, symbol: str | None = None
    ) -> Order:
        data = await self._client.fetch_order(order_id, symbol)
        return self._parse_order(data)

    @_retry()
    async def fetch_open_orders(self, symbol: str) -> list[Order]:  # type: ignore[override]
        raw = await self._client.fetch_open_orders(symbol)
        return [self._parse_order(d) for d in raw]

    @_retry()
    async def fetch_closed_orders(  # type: ignore[override]
        self, symbol: str, limit: int = 20
    ) -> list[Order]:
        raw = await self._client.fetch_orders(
            symbol, limit=limit, params={"state": "filled"}
        )
        return [self._parse_order(d) for d in raw]

    @_retry()
    async def fetch_balance(self) -> Balance:  # type: ignore[override]
        data = await self._client.fetch_balance()
        return Balance(
            total_usdt=float(data["total"].get("USDT", 0)),
            free_usdt=float(data["free"].get("USDT", 0)),
            used_usdt=float(data["used"].get("USDT", 0)),
        )

    @_retry()
    async def fetch_positions(self, symbol: str) -> list[Position]:  # type: ignore[override]
        data = await self._client.fetch_positions([symbol])
        return [
            Position(
                symbol=p["symbol"],  # type: ignore[arg-type]
                side=p["side"],  # type: ignore[arg-type]
                contracts=float(p["contracts"]),  # type: ignore[arg-type]
                entry_price=float(p["entryPrice"]),  # type: ignore[arg-type]
                unrealized_pnl=float(p["unrealizedPnl"]),  # type: ignore[arg-type]
                leverage=int(p["leverage"]),  # type: ignore[arg-type]
                liquidation_price=(
                    float(p["liquidationPrice"])  # type: ignore[arg-type]
                    if p.get("liquidationPrice")
                    else None
                ),
            )
            for p in data
            if float(p["contracts"]) > 0  # type: ignore[arg-type]
        ]

    @_retry()
    async def set_leverage(self, symbol: str, leverage: int) -> None:  # type: ignore[override]
        await self._client.set_leverage(leverage, symbol)

    def amount_to_precision(self, symbol: str, amount: float) -> float:
        return float(self._client.amount_to_precision(symbol, amount))  # type: ignore[arg-type]

    @_retry()
    async def cancel_order(self, order_id: str, symbol: str) -> None:  # type: ignore[override]
        await self._client.cancel_order(order_id, symbol)

    async def close(self) -> None:
        self._running = False
        # 取消后台任务
        for attr in ("_orders_task", "_ticker_task"):
            task = getattr(self, attr, None)
            if task is not None:
                task.cancel()
                try:
                    await task
                except asyncio.CancelledError:
                    pass
        # 关闭 REST + WebSocket 客户端（try/finally 确保两个都尝试关闭）
        try:
            await self._client.close()
        finally:
            if self._ws_client:
                await self._ws_client.close()
```

- [ ] **Step 4: 运行测试**

Run: `cd /Users/z/Z/TradeBot && python -m pytest tests/test_okx_websocket.py -v`

Expected: PASS — 所有测试通过。

同时确保现有 OKX 测试无回归：

Run: `cd /Users/z/Z/TradeBot && python -m pytest tests/test_exchange.py -v`

Expected: PASS

- [ ] **Step 5: 更新 app.py 中 OKXExchange 构造调用**

Task 8 将 OKXExchange 构造函数改为 `symbol: str` 必填参数，需要同步更新 `src/cli/app.py` 中的调用：

```python
# src/cli/app.py — 找到 OKXExchange 构造调用（大约在 run() 函数中）
# 旧:
exchange = OKXExchange(
    api_key=settings.exchange.api_key,
    secret=settings.exchange.secret,
    password=settings.exchange.password,
)
# 新:
exchange = OKXExchange(
    api_key=settings.exchange.api_key,
    secret=settings.exchange.secret,
    password=settings.exchange.password,
    symbol=settings.trading.symbol,
)
```

- [ ] **Step 6: 提交**

Run: `cd /Users/z/Z/TradeBot && git add src/integrations/exchange/okx.py src/cli/app.py tests/test_okx_websocket.py && git commit -m "feat: add OKX WebSocket fill push via ccxt.pro watch_orders loop"`

---

### Task 9: OKXExchange 价格警报集成

**概述:** 在 `OKXExchange` 中添加 `_watch_ticker_loop` 集成 `PriceAlertService`（已在 Task 8 中一并实现）。本 Task 专注于 OKX ticker loop 与 PriceAlertService 的集成测试。

**Files:**
- Modify: `tests/test_okx_websocket.py`

- [ ] **Step 1: 编写测试**

在 `tests/test_okx_websocket.py` 末尾追加：

```python
# tests/test_okx_websocket.py — 末尾追加

async def test_okx_set_alert_service():
    """set_alert_service 应注入 PriceAlertService。"""
    with patch("ccxt.async_support.okx") as mock_okx:
        mock_okx.return_value = MagicMock()
        from src.integrations.exchange.okx import OKXExchange
        exchange = OKXExchange(
            api_key="test", secret="test", password="test",
            symbol="BTC/USDT:USDT",
        )
        mock_service = MagicMock()
        exchange.set_alert_service(mock_service)
        assert exchange._alert_service is mock_service


async def test_okx_update_alert_params_delegates():
    """update_alert_params 应委托给 PriceAlertService.update_params。"""
    with patch("ccxt.async_support.okx") as mock_okx:
        mock_okx.return_value = MagicMock()
        from src.integrations.exchange.okx import OKXExchange
        exchange = OKXExchange(
            api_key="test", secret="test", password="test",
            symbol="BTC/USDT:USDT",
        )
        mock_service = MagicMock()
        exchange.set_alert_service(mock_service)
        exchange.update_alert_params(2.0, 10, 30)
        mock_service.update_params.assert_called_once_with(2.0, 10, 30)


async def test_watch_ticker_loop_triggers_alert():
    """_watch_ticker_loop 应在 PriceAlertService 返回 AlertInfo 时调用 alert callback。"""
    with patch("ccxt.async_support.okx") as mock_okx:
        mock_okx.return_value = MagicMock()
        from src.integrations.exchange.okx import OKXExchange
        from src.services.price_alert import AlertInfo
        exchange = OKXExchange(
            api_key="test", secret="test", password="test",
            symbol="BTC/USDT:USDT",
        )

        alert_callback = AsyncMock()
        exchange.on_alert(alert_callback)

        mock_alert = AlertInfo(
            symbol="BTC/USDT:USDT",
            current_price=57900.0,
            reference_price=60000.0,
            change_pct=-3.5,
            window_minutes=5,
            timestamp=1712534400000,
        )
        mock_service = MagicMock()
        mock_service.check.return_value = mock_alert
        exchange.set_alert_service(mock_service)

        exchange._running = True
        mock_ws = AsyncMock()
        call_count = 0

        async def mock_watch_ticker(symbol):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return {
                    "symbol": "BTC/USDT:USDT",
                    "last": "57900",
                    "bid": "57899",
                    "ask": "57901",
                    "high": "60000",
                    "low": "57800",
                    "baseVolume": "12345",
                    "timestamp": 1712534400000,
                }
            exchange._running = False
            return {
                "symbol": "BTC/USDT:USDT",
                "last": "57900",
                "bid": "57899",
                "ask": "57901",
                "high": "60000",
                "low": "57800",
                "baseVolume": "12345",
                "timestamp": 1712534401000,
            }

        mock_ws.watch_ticker = mock_watch_ticker
        exchange._ws_client = mock_ws

        await exchange._watch_ticker_loop()

        alert_callback.assert_called_once()
        alert_info = alert_callback.call_args[0][0]
        assert alert_info.change_pct == -3.5


async def test_watch_ticker_loop_no_alert_when_service_returns_none():
    """当 PriceAlertService.check 返回 None 时不应调用 alert callback。"""
    with patch("ccxt.async_support.okx") as mock_okx:
        mock_okx.return_value = MagicMock()
        from src.integrations.exchange.okx import OKXExchange
        exchange = OKXExchange(
            api_key="test", secret="test", password="test",
            symbol="BTC/USDT:USDT",
        )

        alert_callback = AsyncMock()
        exchange.on_alert(alert_callback)

        mock_service = MagicMock()
        mock_service.check.return_value = None
        exchange.set_alert_service(mock_service)

        exchange._running = True
        mock_ws = AsyncMock()
        call_count = 0

        async def mock_watch_ticker(symbol):
            nonlocal call_count
            call_count += 1
            if call_count >= 2:
                exchange._running = False
            return {
                "symbol": "BTC/USDT:USDT",
                "last": "60000",
                "bid": "59999",
                "ask": "60001",
                "high": "60500",
                "low": "59500",
                "baseVolume": "12345",
                "timestamp": 1712534400000 + call_count * 1000,
            }

        mock_ws.watch_ticker = mock_watch_ticker
        exchange._ws_client = mock_ws

        await exchange._watch_ticker_loop()
        alert_callback.assert_not_called()
```

- [ ] **Step 2: 运行测试**

Run: `cd /Users/z/Z/TradeBot && python -m pytest tests/test_okx_websocket.py -v`

Expected: PASS — 所有测试通过（实现已在 Task 8 中完成）。

- [ ] **Step 3: 提交**

Run: `cd /Users/z/Z/TradeBot && git add tests/test_okx_websocket.py && git commit -m "test: add OKX WebSocket ticker loop + PriceAlertService integration tests"`

---

### Task 10: SimulatedExchange 价格警报集成

**概述:** 在 `SimulatedExchange` 中添加 `on_alert()`、`set_alert_service()`、`update_alert_params()`，在 `_process_tick` 中集成 `PriceAlertService.check()`，alert callback 在锁外执行。

**Files:**
- Modify: `src/integrations/exchange/simulated.py`
- Modify: `tests/test_simulated_exchange.py`

- [ ] **Step 1: 编写失败测试**

在 `tests/test_simulated_exchange.py` 末尾追加：

```python
# tests/test_simulated_exchange.py — 末尾追加

async def test_simulated_exchange_alert_service_integration():
    """SimulatedExchange 应在 _process_tick 中调用 PriceAlertService.check。"""
    from src.integrations.exchange.simulated import SimulatedExchange
    from src.integrations.exchange.base import Ticker
    from src.services.price_alert import AlertInfo
    from unittest.mock import MagicMock, AsyncMock
    from src.config import ExchangeConfig

    config = ExchangeConfig(
        name="simulated", fee_rate=0.0005,
        precision={"BTC/USDT:USDT": 3},
    )
    exchange = SimulatedExchange(
        config=config, db_engine=None, session_id="test",
        symbol="BTC/USDT:USDT",
    )
    exchange._free_usdt = 1000.0
    exchange._running = True

    # 注入 mock PriceAlertService
    mock_alert = AlertInfo(
        symbol="BTC/USDT:USDT",
        current_price=57900.0,
        reference_price=60000.0,
        change_pct=-3.5,
        window_minutes=5,
        timestamp=1712534400000,
    )
    mock_service = MagicMock()
    mock_service.check.return_value = mock_alert
    exchange.set_alert_service(mock_service)

    # 注册 alert callback
    alert_callback = AsyncMock()
    exchange.on_alert(alert_callback)

    # 模拟 tick
    ticker = Ticker(
        symbol="BTC/USDT:USDT", last=57900.0,
        bid=57899.0, ask=57901.0,
        high=60000.0, low=57800.0,
        base_volume=12345.0, timestamp=1712534400000,
    )
    await exchange._process_tick(ticker)

    # PriceAlertService.check 应被调用
    mock_service.check.assert_called_once_with(57900.0, 1712534400000)
    # alert callback 应被调用
    alert_callback.assert_called_once()
    alert_info = alert_callback.call_args[0][0]
    assert alert_info.change_pct == -3.5


async def test_simulated_exchange_no_alert_when_service_returns_none():
    """PriceAlertService.check 返回 None 时不应调用 alert callback。"""
    from src.integrations.exchange.simulated import SimulatedExchange
    from src.integrations.exchange.base import Ticker
    from unittest.mock import MagicMock, AsyncMock
    from src.config import ExchangeConfig

    config = ExchangeConfig(
        name="simulated", fee_rate=0.0005,
        precision={"BTC/USDT:USDT": 3},
    )
    exchange = SimulatedExchange(
        config=config, db_engine=None, session_id="test",
        symbol="BTC/USDT:USDT",
    )
    exchange._free_usdt = 1000.0
    exchange._running = True

    mock_service = MagicMock()
    mock_service.check.return_value = None
    exchange.set_alert_service(mock_service)

    alert_callback = AsyncMock()
    exchange.on_alert(alert_callback)

    ticker = Ticker(
        symbol="BTC/USDT:USDT", last=60000.0,
        bid=59999.0, ask=60001.0,
        high=60500.0, low=59500.0,
        base_volume=12345.0, timestamp=1712534400000,
    )
    await exchange._process_tick(ticker)

    mock_service.check.assert_called_once()
    alert_callback.assert_not_called()


async def test_simulated_exchange_update_alert_params():
    """update_alert_params 应委托给内部 PriceAlertService。"""
    from src.integrations.exchange.simulated import SimulatedExchange
    from unittest.mock import MagicMock
    from src.config import ExchangeConfig

    config = ExchangeConfig(
        name="simulated", fee_rate=0.0005,
        precision={"BTC/USDT:USDT": 3},
    )
    exchange = SimulatedExchange(
        config=config, db_engine=None, session_id="test",
        symbol="BTC/USDT:USDT",
    )

    mock_service = MagicMock()
    exchange.set_alert_service(mock_service)
    exchange.update_alert_params(2.0, 10, 30)
    mock_service.update_params.assert_called_once_with(2.0, 10, 30)


async def test_simulated_exchange_alert_callback_outside_lock():
    """alert callback 应在锁外执行（与 fill callback 同模式）。"""
    from src.integrations.exchange.simulated import SimulatedExchange
    from src.integrations.exchange.base import Ticker
    from src.services.price_alert import AlertInfo
    from unittest.mock import MagicMock, AsyncMock
    from src.config import ExchangeConfig

    config = ExchangeConfig(
        name="simulated", fee_rate=0.0005,
        precision={"BTC/USDT:USDT": 3},
    )
    exchange = SimulatedExchange(
        config=config, db_engine=None, session_id="test",
        symbol="BTC/USDT:USDT",
    )
    exchange._free_usdt = 1000.0
    exchange._running = True

    mock_alert = AlertInfo(
        symbol="BTC/USDT:USDT",
        current_price=57900.0,
        reference_price=60000.0,
        change_pct=-3.5,
        window_minutes=5,
        timestamp=1712534400000,
    )
    mock_service = MagicMock()
    mock_service.check.return_value = mock_alert
    exchange.set_alert_service(mock_service)

    lock_held_during_callback = False

    async def alert_callback(info):
        nonlocal lock_held_during_callback
        lock_held_during_callback = exchange._lock.locked()

    exchange.on_alert(alert_callback)

    ticker = Ticker(
        symbol="BTC/USDT:USDT", last=57900.0,
        bid=57899.0, ask=57901.0,
        high=60000.0, low=57800.0,
        base_volume=12345.0, timestamp=1712534400000,
    )
    await exchange._process_tick(ticker)

    # alert callback 应在锁外执行
    assert lock_held_during_callback is False
```

- [ ] **Step 2: 运行测试确认失败**

Run: `cd /Users/z/Z/TradeBot && python -m pytest tests/test_simulated_exchange.py::test_simulated_exchange_alert_service_integration tests/test_simulated_exchange.py::test_simulated_exchange_no_alert_when_service_returns_none tests/test_simulated_exchange.py::test_simulated_exchange_update_alert_params tests/test_simulated_exchange.py::test_simulated_exchange_alert_callback_outside_lock -v`

Expected: FAIL — `SimulatedExchange` 没有 `set_alert_service()`、`on_alert()`、`update_alert_params()` 方法，`_process_tick` 没有 alert 集成。

- [ ] **Step 3: 实现 SimulatedExchange 价格警报集成**

修改 `src/integrations/exchange/simulated.py`：

在 `__init__` 方法中添加 alert 相关属性（在 `self._error_count = 0` 之后）：

```python
        self._alert_callback: Callable[[Any], Awaitable[None]] | None = None
        self._alert_service: Any | None = None
```

同时在文件顶部的 import 中确保有 `Any`：

```python
from collections.abc import Awaitable, Callable
from typing import Any
```

在类中添加三个方法（在 `on_fill` 方法之后、`drain_pending_fills` 之前）：

```python
    def on_alert(self, callback: Callable[[Any], Awaitable[None]]) -> None:
        self._alert_callback = callback

    def set_alert_service(self, service: Any) -> None:
        self._alert_service = service

    def update_alert_params(self, threshold_pct: float, window_minutes: int, cooldown_minutes: int) -> None:
        if self._alert_service:
            self._alert_service.update_params(threshold_pct, window_minutes, cooldown_minutes)
```

修改 `_process_tick` 方法，在锁外（`for fill in triggered:` 循环之后）添加 alert 检测和回调：

```python
    async def _process_tick(self, ticker: Ticker) -> None:
        """Process a single tick -- check liquidations, conditional orders, and price alerts."""
        self._latest_ticker = ticker

        triggered: list[FillEvent] = []
        filled_order_ids: list[str] = []
        new_orders: list[tuple[Order, str]] = []
        alert_info = None

        async with self._lock:
            # 1. Liquidation check (must be before conditional orders)
            for symbol, pos in list(self._positions.items()):
                liq = self._calc_liquidation_price(pos)
                if pos.side == "long" and ticker.bid <= liq:
                    fill = self._force_liquidate(pos, symbol, ticker.bid)
                    triggered.append(fill)
                    new_orders.append((Order(
                        id=fill.order_id, symbol=symbol,
                        side="sell", order_type="liquidation",
                        amount=fill.amount, price=fill.fill_price,
                        status="closed", fee=fill.fee,
                    ), fill.position_side))
                elif pos.side == "short" and ticker.ask >= liq:
                    fill = self._force_liquidate(pos, symbol, ticker.ask)
                    triggered.append(fill)
                    new_orders.append((Order(
                        id=fill.order_id, symbol=symbol,
                        side="buy", order_type="liquidation",
                        amount=fill.amount, price=fill.fill_price,
                        status="closed", fee=fill.fee,
                    ), fill.position_side))

            # 2. Conditional order check
            for order in list(self._pending_orders):
                if self._should_trigger(order, ticker):
                    if not self._has_position(order.symbol):
                        continue
                    fill = self._execute_fill(order, ticker)
                    triggered.append(fill)
                    filled_order_ids.append(order.id)

            if triggered:
                for fill in triggered:
                    self._remove_order_by_id(fill.order_id)
                self._cancel_orphaned_orders()
                if self._db_engine:
                    await self._persist_state(
                        new_orders=new_orders,
                        filled_order_ids=filled_order_ids,
                        fill_events=triggered,
                    )

            # 3. Price alert check (inside lock for reading ticker, result used outside)
            if self._alert_service:
                alert_info = self._alert_service.check(ticker.last, ticker.timestamp)

        # Notify outside lock
        for fill in triggered:
            if self._fill_callback:
                await self._fill_callback(fill)

        # Alert callback outside lock
        if alert_info and self._alert_callback:
            await self._alert_callback(alert_info)
```

- [ ] **Step 4: 运行全部 simulated exchange 测试**

Run: `cd /Users/z/Z/TradeBot && python -m pytest tests/test_simulated_exchange.py -v`

Expected: PASS — 所有新旧测试通过。

- [ ] **Step 5: 提交**

Run: `cd /Users/z/Z/TradeBot && git add src/integrations/exchange/simulated.py tests/test_simulated_exchange.py && git commit -m "feat: integrate PriceAlertService into SimulatedExchange _process_tick"`

---

### Task 11: App 集成 — Fill + Alert handler 统一注册

**概述:** 在 `app.py` 中扩展 fill handler 注册到 OKX 模式，添加 alert handler 注册、alert 启动交互、`exchange.start()` 对 OKX 的调用。

**Files:**
- Modify: `src/cli/app.py`

- [ ] **Step 1: 修改 `run()` 函数集成 fill/alert handler 和 exchange.start()**

在 `src/cli/app.py` 的 `run()` 函数中，替换 fill handler 注册和 exchange.start() 部分为：

```python
    # src/cli/app.py — 替换 fill handler 注册 + alert handler + exchange.start 部分

    # --- Price alert setup ---
    from src.services.price_alert import PriceAlertService

    alert_service = None
    if settings.alerts.enabled:
        console.print("\n[bold]Price alert settings:[/]")
        try:
            window_input = input(f"  Window (minutes) [{settings.alerts.window_minutes}]: ").strip()
            threshold_input = input(f"  Threshold (%) [{settings.alerts.threshold_pct}]: ").strip()
            cooldown_input = input(f"  Cooldown (minutes) [{settings.alerts.cooldown_minutes}]: ").strip()
            window = int(window_input) if window_input else settings.alerts.window_minutes
            threshold = float(threshold_input) if threshold_input else settings.alerts.threshold_pct
            cooldown = int(cooldown_input) if cooldown_input else settings.alerts.cooldown_minutes
            alert_service = PriceAlertService(
                symbol=settings.trading.symbol,
                window_minutes=window,
                threshold_pct=threshold,
                cooldown_minutes=cooldown,
            )
            console.print(f"  Price alerts: ON (threshold={threshold}%, window={window}min, cooldown={cooldown}min)")
        except (ValueError, TypeError) as e:
            console.print(f"[yellow]Invalid alert settings ({e}), using defaults[/]")
            alert_service = PriceAlertService(
                symbol=settings.trading.symbol,
                window_minutes=settings.alerts.window_minutes,
                threshold_pct=settings.alerts.threshold_pct,
                cooldown_minutes=settings.alerts.cooldown_minutes,
            )
            console.print(
                f"  Price alerts: ON (threshold={settings.alerts.threshold_pct}%, "
                f"window={settings.alerts.window_minutes}min, cooldown={settings.alerts.cooldown_minutes}min)"
            )
        exchange.set_alert_service(alert_service)
    else:
        console.print("Price alerts: OFF")

    handle_fill = None

    async def on_tick(trigger_type: str, context=None):
        if shutdown_event.is_set():
            return
        try:
            await run_agent_cycle(agent, deps, trigger_type, budget, engine, context, model=selected_model)
        except Exception:
            logger.exception("Agent cycle failed")
        finally:
            # drain_pending_fills 仅用于 simulated exchange 的市价单
            if handle_fill is not None:
                for fill in exchange.drain_pending_fills():
                    try:
                        await handle_fill(fill)
                    except Exception:
                        logger.exception("Fill handler failed for order %s", fill.order_id)

    interval = settings.scheduler.interval_minutes * 60
    scheduler = Scheduler(interval_seconds=interval, callback=on_tick)

    # --- Fill handler registration (unified for both exchange types) ---
    def _create_fill_handler(sched, eng, sid):
        async def handle_fill(event: FillEvent):
            try:
                await _record_action_from_fill(eng, sid, event)
            except Exception:
                logger.warning("Failed to record fill event", exc_info=True)
            finally:
                await sched.trigger("conditional", context=event)
        return handle_fill

    handle_fill = _create_fill_handler(scheduler, engine, session_id)
    exchange.on_fill(handle_fill)

    # --- Alert handler registration ---
    if settings.alerts.enabled:
        async def handle_alert(alert_info):
            await scheduler.trigger("alert", context=alert_info)

        exchange.on_alert(handle_alert)

    # --- Start exchange (both simulated and OKX) ---
    await exchange.start()  # SimulatedExchange: 恢复状态 + 撮合循环; OKXExchange: WebSocket loops

    # Show initial metrics (after start so simulated mode has restored state)
    positions = await exchange.fetch_positions(settings.trading.symbol)
    pos_str = f"{positions[0].side} {positions[0].contracts}" if positions else "none"
    metrics = await metrics_service.compute(engine, session_id, current_position=pos_str)
    display_metrics(metrics)

    console.print(
        f"\n[bold]Scheduler: every {settings.scheduler.interval_minutes} min[/]"
    )
    console.print(f"[bold]LLM Budget: {settings.llm_budget.daily_max_tokens:,} tokens/day[/]")
    console.print("[dim]Press Ctrl+C to stop[/]\n")

    scheduler_task = asyncio.create_task(scheduler.start())
    await shutdown_event.wait()

    scheduler.stop()
    await scheduler_task
    await exchange.close()
    console.print("[green]TradeBot stopped.[/]")
```

- [ ] **Step 2: 运行全部测试确保无回归**

Run: `cd /Users/z/Z/TradeBot && python -m pytest tests/ -v`

Expected: PASS — 所有测试通过。

- [ ] **Step 3: 提交**

Run: `cd /Users/z/Z/TradeBot && git add src/cli/app.py && git commit -m "feat: unify fill/alert handler registration for both exchanges, add alert startup interaction"`

---

### Task 12: 全量测试 + 冒烟检查

**概述:** 运行全部测试，检查残留引用，验证端到端启动流程无报错。

**Files:**
- 无新建/修改（仅验证）

- [ ] **Step 1: 运行全部测试**

Run: `cd /Users/z/Z/TradeBot && python -m pytest tests/ -v`

Expected: PASS — 全部测试通过。

- [ ] **Step 2: 检查 stale 引用**

Run: `cd /Users/z/Z/TradeBot && grep -rn "llm_router.resolve" src/ --include="*.py"`

Expected: 无输出（所有 `llm_router.resolve` 调用已被 ModelManager 替代）。

Run: `cd /Users/z/Z/TradeBot && grep -rn "_pending_trigger" src/ --include="*.py"`

Expected: 无输出（`_pending_trigger` 已被 `_pending_events` 替代）。

Run: `cd /Users/z/Z/TradeBot && grep -rn "_pending_context" src/ --include="*.py"`

Expected: 无输出（`_pending_context` 已被 `_pending_events` 替代）。

- [ ] **Step 3: 验证 import 链无循环**

Run: `cd /Users/z/Z/TradeBot && python -c "from src.services.model_manager import ModelManager; print('ModelManager OK')"`

Expected: 输出 `ModelManager OK`

Run: `cd /Users/z/Z/TradeBot && python -c "from src.services.price_alert import PriceAlertService; print('PriceAlertService OK')"`

Expected: 输出 `PriceAlertService OK`

Run: `cd /Users/z/Z/TradeBot && python -c "from src.integrations.exchange.okx import OKXExchange; print('OKXExchange OK')"`

Expected: 输出 `OKXExchange OK`

- [ ] **Step 4: 验证配置文件**

Run: `cd /Users/z/Z/TradeBot && python -c "
from src.config import load_settings
from pathlib import Path
s = load_settings(Path('config/settings.yaml'))
print(f'alerts.enabled={s.alerts.enabled}')
print(f'alerts.threshold_pct={s.alerts.threshold_pct}')
print(f'models={s.models}')
"`

Expected:
```
alerts.enabled=True
alerts.threshold_pct=3.0
models=None
```

（`models=None` 因为 settings.yaml 中的 models 段将在 Task 5 后被移除或注释）

- [ ] **Step 5: 检查 .gitignore**

Run: `cd /Users/z/Z/TradeBot && grep "models.json" .gitignore`

Expected: `config/models.json`

- [ ] **Step 6: 最终提交（如有修复）**

如果上述检查发现问题并修复，提交修复：

Run: `cd /Users/z/Z/TradeBot && git add -A && git commit -m "fix: resolve Phase 1b integration issues found in smoke test"`

如果没有问题，此步骤跳过。
