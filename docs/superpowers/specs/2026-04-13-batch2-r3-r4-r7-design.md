# Batch 2 设计文档：R3 / R4 / R7

> **状态**: 初稿
> **日期**: 2026-04-13
> **范围**: 百分比告警重设计 (R3) + 动态唤醒间隔 (R4) + 价位级别 Alert (R7)
> **依赖**: Batch 1 (R5/R1/R2) 已合并

---

## 项目背景

### TradeBot 是什么

TradeBot 是一个 AI 驱动的加密货币合约交易机器人。核心理念：LLM Agent 扮演交易员角色，自主分析市场、做出交易决策、管理仓位。

用户启动后，Agent 按固定间隔（如 15 分钟）被唤醒，或被价格波动/订单成交等事件打断唤醒。每次唤醒时，Agent 通过 tool 调用获取市场数据、查看持仓、回顾历史，然后决定操作（开仓/平仓/设止损/观望）。

### 技术栈

| 组件 | 技术 |
|------|------|
| 语言 | Python 3.12+, asyncio |
| LLM 框架 | pydantic-ai（Agent + tool 定义） |
| 交易所接口 | ccxt / ccxt.pro（REST + WebSocket） |
| 数据库 | SQLite + SQLAlchemy async（aiosqlite） |
| 终端 UI | Rich（表格、面板、彩色输出、交互 prompt） |
| 测试 | pytest + pytest-asyncio，248 个测试 |

### 项目阶段

- **Phase 1a** (已完成) — 最小 agent 循环：单模型、REST 轮询、定时唤醒、模拟交易所
- **Phase 1b** (已完成) — 事件驱动闭环：多模型选择、WebSocket fill 推送、价格告警、Scheduler 事件队列化
- **Batch 1** (已完成, PR #5 R1, PR #6 R2) — 基础设施改造：日志分离、CLI 配置向导、Session 管理
- **当前 (Batch 2)** — Agent 自主性增强（本文档）
- **Phase 2** (规划中) — 产品化：Web UI、多会话并行、多交易对

### 当前架构（Batch 1 完成后）

```
main.py → src/cli/app.py::run()
  ├── Phase 1: setup_system_logging()
  ├── Phase 2: init_db() + migration
  ├── Phase 3: select_or_create_session()   ← R2 新增
  ├── Phase 4: setup_session_logging()
  ├── Phase 5: build_services()             ← 构建 exchange, deps, agent, budget
  └── Phase 6: run_main_loop()              ← scheduler + event handlers
```

### 当前告警与调度数据流

```
Exchange (ticker WebSocket)
  │
  ├─→ PriceAlertService.check(price, ts)  ← 每个 tick 调用
  │     └─ 达到阈值 → AlertInfo
  │           └─→ _alert_callback(AlertInfo)
  │                 └─→ scheduler.trigger("alert", AlertInfo)
  │                       └─→ on_tick("alert", AlertInfo)
  │                             └─→ run_agent_cycle(prompt 含 alert 信息)
  │
  ├─→ 条件单撮合 → FillEvent
  │     └─→ _fill_callback(FillEvent)
  │           └─→ scheduler.trigger("conditional", FillEvent)
  │
  └─→ Scheduler._interruptible_sleep(固定间隔)
        └─ 超时 → on_tick("scheduled", None)
              └─→ run_agent_cycle(常规唤醒)
```

Agent 在 cycle 中可调用 `set_price_alert` tool 动态修改 PriceAlertService 的阈值和窗口参数。

### 本批需求

| 编号 | 需求 | 类别 | 核心问题 |
|------|------|------|---------|
| R3 | 百分比告警重设计 | Agent 自主性 | 冷却期丢弃持续波动，Agent 错失反应窗口 |
| R4 | 动态唤醒间隔 | Agent 自主性 | 固定轮询不符合交易员行为，浪费 token |
| R7 | 价位级别 Alert | Agent 自主性 | 无法设定具体价位提醒（支撑/阻力位） |

三者接口相对独立，可并行实施。实施顺序建议 R3 → R7 → R4（R3 改告警基础设施，R7 在其上新增，R4 独立于告警）。

---

## R3: 百分比告警重设计

### 目标

将"sliding window + 方向冷却"改为"触发后重置 window"。每次 alert 代表"从上次 alert 后又波动了 N%"。删除 cooldown 机制。

### 现状

`PriceAlertService.check()` 当前逻辑：
1. 维护 `_ticks: deque[(price, timestamp)]` sliding window
2. 驱逐超出 window 的旧 tick
3. 计算 window 内 max/min，与当前价比较得出涨跌幅
4. 达到阈值时，检查方向冷却（`_last_alert_ts`），冷却期内同方向不触发
5. 触发后记录方向冷却时间戳

**问题**：持续单边行情中（如 BTC 连续暴跌），第一次 alert 触发后进入冷却期，期间的后续波动被静默丢弃，Agent 错失反应窗口。

### 设计选型

触发后如何"重置"有两种方案：

| 方案 | 描述 | 分析 |
|------|------|------|
| A. 清空 window | 触发后 `_ticks.clear()`，从零积累 | 语义清晰：每次 alert = "从上次 alert 后又波动了 N%"。连续暴跌时每跌 5% 通知一次 |
| B. 重置基线价 | 保留 window 数据，但将当前价设为新参考点 | 实现更复杂，window 语义模糊（保留了旧数据但不用于计算） |

**选择方案 A**。理由：
- 语义最清晰，代码改动最小（一行 `_ticks.clear()`）
- 连续暴跌场景：100k → 95k (alert) → 90.25k (alert) → 85.7k (alert)，每次 5% 通知
- V 形反弹：跌 5% alert 后清空，反弹 5% 再 alert
- 高阈值 (5%) + 长窗口 (1h) 天然防止区间震荡中刷 alert，不需要冷却机制

### 新逻辑

```python
def check(self, price: float, timestamp: int) -> AlertInfo | None:
    self._ticks.append((price, timestamp))
    self._evict_old(timestamp)

    if len(self._ticks) < 2:
        return None

    prices = [p for p, _ in self._ticks]
    high = max(prices)
    low = min(prices)

    drop_pct = (price - high) / high * 100 if high > 0 else 0.0    # 负值
    rise_pct = (price - low) / low * 100 if low > 0 else 0.0       # 正值

    # 保留现有优先级：取绝对值更大的方向
    if abs(drop_pct) >= abs(rise_pct) and abs(drop_pct) >= self._threshold_pct:
        self._ticks.clear()                   # ← 重置
        return AlertInfo(symbol=..., change_pct=drop_pct, reference_price=high, ...)

    if rise_pct >= self._threshold_pct:
        self._ticks.clear()                   # ← 重置
        return AlertInfo(symbol=..., change_pct=rise_pct, reference_price=low, ...)

    return None
```

关键变化：
- 触发后 `_ticks.clear()`，下一个 tick 成为新基线
- 删除 `_last_alert_ts` 和所有冷却逻辑
- 删除 `_cooldown_ms` 内部状态

### 默认参数变更

| 参数 | 旧值 | 新值 |
|------|------|------|
| `window_minutes` | 5 | 60 |
| `threshold_pct` | 3.0 | 5.0 |
| `cooldown_minutes` | 15 | **删除** |

### 参数验证范围变更

`_validate_params` 同步更新：

| 参数 | 旧范围 | 新范围 |
|------|--------|--------|
| `window_minutes` | 1-60 | 1-240（默认 60，允许更长窗口） |
| `threshold_pct` | 0.5-50.0 | 0.5-50.0（不变） |
| `cooldown_minutes` | 1-120 | **删除** |

### 构造函数变更

```python
# 旧
PriceAlertService(symbol, window_minutes=5, threshold_pct=3.0, cooldown_minutes=15)

# 新
PriceAlertService(symbol, window_minutes=60, threshold_pct=5.0)
```

### update_params 变更

```python
# 旧
def update_params(self, threshold_pct, window_minutes, cooldown_minutes)

# 新
def update_params(self, threshold_pct, window_minutes)
```

### Cooldown 删除级联

cooldown 的删除涉及整个调用链，需同步修改：

| 文件 | 改动 |
|------|------|
| `src/services/price_alert.py` | 删 cooldown 参数/状态/检查，`check()` 加 `_ticks.clear()` |
| `src/config.py` | `AlertsConfig` 删 `cooldown_minutes`，改 window=60, threshold=5.0 |
| `src/cli/wizard.py` | Step 4 删 cooldown 提示；`_show_summary` 删 cooldown 显示；`WizardResult` 删 `alert_cooldown_min` |
| `src/agent/tools_execution.py` | `set_price_alert` 删 cooldown 参数；`window_minutes` 验证范围从 1-60 扩展到 1-240 |
| `src/agent/trader.py` | tool 注册删 cooldown 参数 |
| `src/integrations/exchange/base.py` | `update_alert_params` 删 cooldown |
| `src/integrations/exchange/simulated.py` | 同上 |
| `src/integrations/exchange/okx.py` | 同上 |
| `src/cli/session_manager.py` | `_create_session` alert_config JSON 删 cooldown；`_restore_session` 删 cooldown 读取 |
| `src/cli/app.py` | `build_services` 删 cooldown 传参；`build_services` 中 alert 显示信息删 cooldown |
| `src/storage/models.py` | 更新 `alert_config` 字段注释（删除 cooldown 描述） |
| `config/settings.yaml` | 删除 `cooldown_minutes`，更新 `window_minutes: 60`、`threshold_pct: 5.0` |
| `config/settings_sim.yaml` | 同步更新 alert 参数（该文件已标记 DEPRECATED 且不被读取，仅作为参考保持准确） |

### 旧 session 兼容

系统尚未投入生产使用，不存在需要迁移的旧数据。如有残留的旧 session，其 `alert_config` JSON 中的 `"cooldown"` 字段会被 `dict.get()` 安全忽略，无需 DB migration。

### 测试变更

- 删除：cooldown 相关测试（blocks same direction, allows opposite, expires）
- 新增：触发后 window 重置测试（触发→清空→重新积累→再触发）
- 更新：所有调用 `PriceAlertService()` 的地方删 cooldown 参数
- `tests/test_config.py`：删除 `AlertsConfig.cooldown_minutes` 相关断言
- `tests/test_tools.py`：删除 `test_set_price_alert_cooldown_out_of_range`；更新其他 `set_price_alert` 测试删 cooldown 参数；`window_minutes` 边界值从 70（旧范围 1-60 的越界值）改为 250（新范围 1-240 的越界值）
- `tests/test_price_alert.py`：`test_update_params_boundary_validation` 中 `window_minutes` 越界断言同步更新

---

## R4: 动态唤醒间隔

### 目标

Agent 每次 cycle 可通过 tool 设定下次唤醒时间，未调用时回到兜底间隔。模拟真实交易员根据市况调整看盘频率的行为。

### 现状

`Scheduler` 构造时接收固定 `interval_seconds`，`start()` 循环中 `_interruptible_sleep` 每次等待相同时长。Agent 无法控制唤醒节奏。

真实交易员会根据市况调整看盘频率：
- 有仓位 + 波动加剧 → 频繁查看（几分钟一次）
- 无仓位 + 市场平静 → 拉长间隔（半小时到一小时）
- 刚止损出场 → 中等间隔观望

当前固定轮询（如 15min）在市场平静时浪费 LLM token，在波动剧烈时又反应太慢。

注意：fill event 和 price alert 已能通过 `scheduler.trigger()` 打断 sleep 立即唤醒 Agent。动态间隔只影响 scheduled trigger（无事件时的定时轮询）。

### 设计选型

间隔生效方式有两种：

| 方案 | 描述 | 分析 |
|------|------|------|
| A. 一次性 | 只影响下一次唤醒，之后回到兜底 | 更安全：Agent 异常时自动回到默认。每次 cycle 都有机会重新判断 |
| B. 持续生效 | 设了 5min 后一直用 5min | Agent 某次设了极端值后，需要主动调回。异常时可能卡在极端值 |

**选择方案 A**。理由：Agent 不调用时自动回到用户配置的兜底间隔，不会卡在极端值。符合"每次 cycle 都是独立决策"的 Agent 行为模式。

间隔范围：

| 参数 | 值 | 理由 |
|------|-----|------|
| 最小值 | 1 min | 低于 1 分钟意义不大（LLM 调用+数据获取本身需要时间） |
| 最大值 | `min(max(4 × scheduler_interval_min, 60), 180)` min | 随用户配置缩放，硬顶 3 小时 |
| 兜底值 | wizard 配置的 `scheduler_interval_min` | 不引入额外参数，复用现有配置 |

### Scheduler 改造

```python
class Scheduler:
    def __init__(self, interval_seconds: float, callback):
        self._interval = interval_seconds    # 兜底间隔
        self._next_interval: float | None = None     # ← 新增：一次性覆盖

    def set_next_interval(self, seconds: float) -> None:
        """设置下一次 sleep 的间隔（一次性）。"""
        self._next_interval = seconds

    async def start(self):
        self._running = True
        await self._run_cycle("scheduled", None)
        while self._running:
            # 取一次性间隔，用完即重置
            interval = self._next_interval if self._next_interval is not None else self._interval
            self._next_interval = None
            await self._interruptible_sleep(interval)
            # ... drain events, run cycles ...
```

**Sleep 被打断时的行为**：`_next_interval` 在进入 sleep 前被消费并重置为 None。如果 sleep 在第 30s 被 event 打断，已消费的 interval 不会被恢复——打断意味着有新事件需要处理，Agent 在新 cycle 中有机会重新设定间隔。

### 新 Agent Tool

```python
set_next_wake(minutes: int, reasoning: str) -> str
```

- **参数类型 `int`**：选择整数而非浮点数——对 LLM tool calling 更友好（避免 1.5 vs 2 的无意义精度纠结），交易场景不需要亚分钟精度
- **范围**：1 min ~ `min(max(4 * scheduler_interval_min, 60), 180)` min
- 超范围自动 clamp，返回 clamp 后的实际值（避免误导 Agent）
- 一次性生效：只影响下一次唤醒，之后回到兜底间隔
- 未调用时 Scheduler 使用 `_interval`（wizard 配置的值）
- 调用 `_record_action` 记录到 TradeAction 表（与 `set_price_alert` 保持一致）

### 接入方式

`TradingDeps` 新增可选字段：

```python
@dataclass
class TradingDeps:
    # ... 现有字段 ...
    wake_min_minutes: int = 1
    wake_max_minutes: int = 60
    set_next_wake_fn: Callable[[int], None] | None = None
```

`app.py` `run()` 函数中，Phase 6 的 scheduler 创建（line ~339）之后、`scheduler.start()` 之前注入（注：`deps` 在 Phase 5 `build_services()` 中创建，此处修改其属性）：

```python
max_wake = min(max(4 * result.scheduler_interval_min, 60), 180)
deps.wake_min_minutes = 1
deps.wake_max_minutes = max_wake
deps.set_next_wake_fn = lambda minutes: scheduler.set_next_interval(minutes * 60)
```

Tool 实现（clamp + record 在 tool 层）：

```python
async def set_next_wake(deps: TradingDeps, minutes: int, reasoning: str) -> str:
    if deps.set_next_wake_fn is None:
        return "Dynamic wake not available"
    clamped = max(deps.wake_min_minutes, min(minutes, deps.wake_max_minutes))
    deps.set_next_wake_fn(clamped)
    await _record_action(deps, action="set_next_wake",
                         reasoning=f"interval={clamped}min | {reasoning}")
    if clamped != minutes:
        return f"Next wake set to {clamped} min (clamped from {minutes}). Reason: {reasoning}"
    return f"Next wake set to {clamped} min. Reason: {reasoning}"
```

### 系统 Prompt 变更

在 Agent 系统 prompt 中新增建议性引导（非强制）：

> "You can use `set_next_wake` to adjust how soon you want to check the market again. If you don't call it, the default interval applies."

### 文件变更

| 文件 | 改动 |
|------|------|
| `src/scheduler/scheduler.py` | 新增 `_next_interval` + `set_next_interval()` |
| `src/agent/trader.py` | 新增 `set_next_wake` tool 注册 |
| `src/agent/tools_execution.py` | 新增 `set_next_wake` 实现 |
| `src/agent/persona.py` | `generate_system_prompt` 新增 `set_next_wake` 建议性引导 |
| `src/cli/app.py` | Phase 6 注入 `deps.set_next_wake_fn` |

### 测试

- `test_scheduler.py` 新增：`set_next_interval` 一次性覆盖测试、用完重置测试、未设时用默认测试
- `test_tools_execution.py` 或新文件：`set_next_wake` tool 参数 clamp 测试

---

## R7: 价位级别 Alert

### 目标

Agent 通过 tool 在关键价位设定提醒（如"BTC 跌破 58000 通知我"），触发后一次性消耗。赋予 Agent 真实交易员在关键支撑/阻力位设价格提醒的能力。

### 现状

Agent 当前只能通过 `set_price_alert` tool 调整百分比波动告警的参数（阈值/窗口），无法设定"到某个具体价位提醒我"。真实交易员常用的操作是在关键技术位设提醒（如"BTC 跌破前低 58000 通知我"、"ETH 突破压力位 4200 通知我"），当前工具集不支持。

### 与 R3（百分比波动告警）的关系

| 维度 | R3 百分比告警 | R7 价位告警 |
|------|-------------|------------|
| 设定者 | 系统自动（用户配置参数） | Agent 主动设定 |
| 触发条件 | 时间窗口内波动超阈值 | 价格到达具体价位 |
| 持续性 | 持续监控，触发后重置继续 | 一次性，触发后消耗 |
| 定位 | 安全网：捕获 Agent 没预见的异常波动 | 策略工具：体现 Agent 的技术分析判断 |

两者互补，都通过 `scheduler.trigger("alert", context)` 唤醒 Agent，prompt 中根据 context 类型区分来源。

### 设计选型

**存储方式**：

| 方案 | 分析 |
|------|------|
| A. 仅内存 | 重启后丢失，Agent 下次 cycle 重新设定。符合交易员行为：价格提醒基于当时分析，有时效性 |
| B. 持久化到 DB | 重启后恢复。但旧的价位提醒可能已过时，强行恢复可能误导 Agent |

**选择方案 A**。理由：价位 alert 基于 Agent 当时的市场分析（支撑/阻力位判断），有时效性。重启后市场格局可能已变，Agent 应重新分析后设定新的关注价位，而非恢复旧的。

**回调路径**：

| 方案 | 分析 |
|------|------|
| A. 复用 `on_alert` | 与百分比 alert 走同一回调，简单，Agent 通过 prompt 内容区分来源 |
| B. 独立 `on_price_level_alert` | 分开注册，增加接口但分离更清晰 |

**选择方案 A**。理由：真实世界里交易员收到提醒时不区分渠道（都是手机震一下），区分来源是在看到提醒内容时。同一回调 + 不同 prompt 格式即可。

### 新 Dataclass

定义在 `src/integrations/exchange/base.py`，与 `FillEvent` 同级。注：这与百分比告警的 `AlertInfo`（定义在 `src/services/price_alert.py`）是不同的 dataclass：

```python
@dataclass
class PriceLevelAlertInfo:
    symbol: str
    target_price: float
    direction: str          # "above" / "below"
    current_price: float
    reasoning: str          # Agent 设定时的理由
    timestamp: int          # milliseconds
```

### 存储

仅内存，不持久化。Exchange 对象维护列表，进程重启后丢失。Agent 下次 cycle 可重新设定。

```python
# BaseExchange 新增
_price_level_alerts: list[dict]
# 每项: {"id": str, "price": float, "direction": str, "symbol": str, "reasoning": str}
```

### Exchange 层方法

当前 `BaseExchange` 无 `__init__`（纯抽象类）。新增 `__init__` 初始化 `_price_level_alerts`，子类 (`SimulatedExchange`, `OKXExchange`) 需加 `super().__init__()` 调用。这比在每个子类重复 state + 方法更 DRY。

```python
class BaseExchange:
    def __init__(self):
        self._price_level_alerts: list[dict] = []
        self._latest_price: float | None = None   # 最新 ticker 价格，用于即时触发警告

    def add_price_level_alert(self, price: float, direction: str,
                               symbol: str, reasoning: str) -> str:
        """添加价位 alert，返回 alert_id。上限 20 个，超出时返回错误提示。
        使用 self._latest_price 检查即时触发风险并在返回信息中警告。"""

    def remove_price_level_alert(self, alert_id: str) -> bool:
        """移除价位 alert，返回是否找到。"""

    def _check_price_levels(self, current_price: float,
                             timestamp: int) -> list[PriceLevelAlertInfo]:
        """检查触发的价位 alerts，触发的从列表移除（一次性消耗），返回触发列表。
        采用"收集+重建"模式避免迭代中修改列表："""
        triggered = []
        remaining = []
        for alert in self._price_level_alerts:
            if (alert["direction"] == "above" and current_price >= alert["price"]) or \
               (alert["direction"] == "below" and current_price <= alert["price"]):
                triggered.append(PriceLevelAlertInfo(...))
            else:
                remaining.append(alert)
        self._price_level_alerts = remaining
        return triggered
```

注意：
- `SimulatedExchange.__init__` 和 `OKXExchange.__init__` 需在开头加 `super().__init__()`
- `SimulatedExchange` 已有 `_latest_ticker` 存储（在 `_process_tick` 中），改为同时更新 `self._latest_price = ticker.last`
- `OKXExchange` 的 `_watch_ticker_loop` 当前不存储 ticker，需新增 `self._latest_price = ticker.last`
- `_latest_price` 用于 `add_price_level_alert` 的即时触发警告检查

### Tick 处理集成

**SimulatedExchange._process_tick**：

```python
async def _process_tick(self, ticker):
    alert_info = None
    level_alerts = []

    async with self._lock:
        # ... 现有：liquidation, conditional orders ...

        # 3. 百分比 alert (R3)
        if self._alert_service:
            alert_info = self._alert_service.check(ticker.last, ticker.timestamp)

        # 4. 价位 alert (R7) ← 新增，lock 内执行（修改 _price_level_alerts 列表）
        level_alerts = self._check_price_levels(ticker.last, ticker.timestamp)

    # Notify outside lock（回调可能触发 scheduler.trigger，不能持有 lock）
    if alert_info and self._alert_callback:
        await self._alert_callback(alert_info)
    for la in level_alerts:                    # ← 新增
        if self._alert_callback:
            await self._alert_callback(la)
```

**OKXExchange._watch_ticker_loop**：同样在 ticker 处理中新增 `_check_price_levels` 调用。同时更新 `self._latest_price`。

**OKX 并发安全说明**：OKXExchange 无 `_lock`（不同于 SimulatedExchange）。`_check_price_levels` 是同步操作（遍历列表、收集触发项、从列表移除），在 asyncio 单线程模型下不会被中断。方法返回触发项的快照列表后，后续 `await callback(la)` 期间即使 Agent 调用 `add_price_level_alert` 追加新项，也不影响已返回的快照。因此不需要 lock。

### 回调路径

复用现有 `on_alert` 回调。`AlertInfo` 和 `PriceLevelAlertInfo` 走同一条路径：

```
Exchange tick → _alert_callback(info) → handle_alert → scheduler.trigger("alert", context=info)
```

### Agent 唤醒 Prompt 区分

`run_agent_cycle` 中根据 context 类型生成不同 prompt：

```python
elif trigger_type == "alert" and context is not None:
    if isinstance(context, PriceLevelAlertInfo):
        prompt += (
            f"\n\nPRICE LEVEL: {context.symbol} reached {context.current_price:.2f} "
            f"(your alert: {context.direction} {context.target_price:.2f} "
            f"— {context.reasoning})"
        )
    else:  # AlertInfo
        direction = "dropped" if context.change_pct < 0 else "surged"
        prompt += (
            f"\n\nPRICE ALERT: {context.symbol} {direction} "
            f"{abs(context.change_pct):.1f}% in {context.window_minutes}min "
            f"({context.reference_price:.2f} → {context.current_price:.2f})"
        )
```

### 新 Agent Tool

```python
add_price_level_alert(price: float, direction: str, reasoning: str) -> str
```

- `direction`：`"above"` 或 `"below"`
- 调用 `deps.exchange.add_price_level_alert(...)`
- 返回确认信息含 alert_id
- Agent 可设定多个价位 alert（上限 20 个，超出时返回提示）

### 即时触发防护

Tool 不阻止可能立即触发的 alert（Agent 可能有意确认突破），但在返回信息中警告：

```python
if (direction == "above" and current_price >= price) or \
   (direction == "below" and current_price <= price):
    return f"Alert set (id={alert_id}), but WARNING: current price ({current_price}) " \
           f"already {'above' if direction == 'above' else 'below'} {price}, may trigger immediately"
```

`add_price_level_alert` 使用 `self._latest_price` 做检查。如果 `_latest_price is None`（尚未收到第一个 ticker），跳过即时触发警告，正常添加 alert。

### 首版不提供取消 tool

Exchange 层有 `remove_price_level_alert(alert_id)` 方法，但首版不暴露对应的 Agent tool。理由：
- 一次性消耗 + 仅内存，未触发的 alert 零成本
- Agent 无需管理 alert_id，市场分析变了直接设新 alert 即可
- 减少 Agent 工具集复杂度

后续如有需要可补充 `cancel_price_alert_level(alert_id)` tool。

### 同 tick 多 alert 行为

极端场景（闪崩同时击穿多个价位 + 触发百分比告警）可能在一个 tick 内产生 N+1 个 `scheduler.trigger("alert", ...)` 事件，每个独立触发一次 Agent cycle。

这是**预期行为**——每个 alert 代表独立的市场信号，Agent 应逐一处理。同 tick 的多个 alert 按顺序处理（Scheduler drain 逐个执行），后续 cycle 的 Agent 能看到前序 cycle 的操作结果（如已平仓），这是优势而非问题。

保护机制：
- Scheduler 安全阀：单次 drain 最多处理 10 个事件
- `TokenBudget`：日级 token 上限（默认 500k），预算耗尽时跳过 cycle

### 文件变更

| 文件 | 改动 |
|------|------|
| `src/integrations/exchange/base.py` | 新增 `__init__` + `PriceLevelAlertInfo` + 3 个方法；R3: `update_alert_params` 删 cooldown |
| `src/integrations/exchange/simulated.py` | `__init__` 加 `super().__init__()`；`_process_tick` 新增价位检查；R3: `update_alert_params` 删 cooldown |
| `src/integrations/exchange/okx.py` | `__init__` 加 `super().__init__()`；`_watch_ticker_loop` 新增价位检查；R3: `update_alert_params` 删 cooldown |
| `src/agent/tools_execution.py` | 新增 `add_price_level_alert` |
| `src/agent/trader.py` | tool 注册 |
| `src/agent/persona.py` | `generate_system_prompt` 新增 `add_price_level_alert` 建议性引导 |
| `src/cli/app.py` | `run_agent_cycle` prompt 分支 + `PriceLevelAlertInfo` import |

### 测试

新增 `tests/test_price_level_alert.py`：
- 触发 "above" 方向
- 触发 "below" 方向
- 未触发（价格未到）
- 一次性消耗（触发后列表中移除）
- 多个 alert 同时存在
- remove 方法

---

## 实施顺序

```
PR #1: R3 百分比告警重设计
  重写 PriceAlertService (check + update_params)
  cooldown 删除级联 (10 个文件)
  更新测试

PR #2: R7 价位级别 Alert
  新增 PriceLevelAlertInfo + Exchange 方法
  SimExchange/OKX tick 集成
  新增 agent tool + prompt 分支
  新增测试

PR #3: R4 动态唤醒间隔
  Scheduler 改造 (set_next_interval)
  新增 agent tool + TradingDeps 回调
  app.py 注入
  新增测试
```

每个 PR 可独立测试和合并。R3 先行因为它改了告警基础设施（PriceAlertService），R7 在其上新增。R4 与告警无关，顺序灵活。

---

## 全量文件变化汇总

### 新建文件

| 文件 | 需求 | 用途 |
|------|------|------|
| `tests/test_price_level_alert.py` | R7 | 价位 alert 测试 |

### 修改文件

| 文件 | 需求 | 改动 |
|------|------|------|
| `src/services/price_alert.py` | R3 | 重写 check()，删 cooldown |
| `src/config.py` | R3 | AlertsConfig 删 cooldown，改默认值 |
| `src/cli/wizard.py` | R3 | Step 4 删 cooldown 提示；`_show_summary` 删 cooldown 显示；WizardResult 删字段 |
| `src/cli/session_manager.py` | R3 | alert_config JSON 删 cooldown |
| `src/cli/app.py` | R3+R4+R7 | build_services 删 cooldown + alert 显示信息删 cooldown；run_agent_cycle prompt 分支；Phase 6 注入 wake setter |
| `src/agent/tools_execution.py` | R3+R4+R7 | set_price_alert 删 cooldown + window 范围扩展到 1-240；新增 set_next_wake；新增 add_price_level_alert |
| `src/agent/trader.py` | R3+R4+R7 | tool 注册更新 |
| `src/agent/persona.py` | R4+R7 | `generate_system_prompt` 新增 set_next_wake + add_price_level_alert 建议 |
| `src/integrations/exchange/base.py` | R3+R7 | 新增 `__init__`；update_alert_params 删 cooldown；新增 PriceLevelAlertInfo + 3 方法 |
| `src/integrations/exchange/simulated.py` | R3+R7 | `__init__` 加 `super().__init__()`；update_alert_params 删 cooldown；_process_tick 新增价位检查 + `_latest_price` 更新 |
| `src/integrations/exchange/okx.py` | R3+R7 | `__init__` 加 `super().__init__()`；update_alert_params 删 cooldown；_watch_ticker_loop 新增价位检查 + `_latest_price` 存储 |
| `src/scheduler/scheduler.py` | R4 | 新增 _next_interval + set_next_interval() |
| `config/settings.yaml` | R3 | 删 cooldown_minutes，更新 window=60, threshold=5.0 |
| `config/settings_sim.yaml` | R3 | 同步更新 alert 参数（DEPRECATED，仅参考） |

### 测试文件

| 文件 | 改动 |
|------|------|
| `tests/test_price_alert.py` | 重写：删 cooldown 测试，加 window-reset 测试 |
| `tests/test_price_level_alert.py` | 新建：R7 触发/消耗/方向测试 |
| `tests/test_scheduler.py` | 新增：set_next_interval 测试 |
| `tests/test_wizard.py` | 更新：删 cooldown 相关 mock/断言 |
| `tests/test_session_manager.py` | 更新：alert_config 不含 cooldown |
| `tests/test_trader_agent.py` | 更新：新增 tool 注册检查 |
| `tests/test_config.py` | 更新：删 AlertsConfig.cooldown_minutes 断言 |
| `tests/test_tools.py` | 更新：删 cooldown 测试；window_minutes 边界值 70→250 |

### 不改动

| 文件 | 原因 |
|------|------|
| `src/storage/models.py` | Session 表无新字段（仅更新 alert_config 注释） |
| `src/storage/database.py` | 无变化 |
| `src/cli/logging_config.py` | 无变化 |
| `src/cli/display.py` | 无变化 |
| `src/cli/approval.py` | 无变化 |
| `main.py` | 无新 CLI 参数 |
