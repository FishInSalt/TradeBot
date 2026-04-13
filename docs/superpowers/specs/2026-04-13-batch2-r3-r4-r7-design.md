# Batch 2 设计文档：R3 / R4 / R7

> **状态**: 初稿
> **日期**: 2026-04-13
> **范围**: 百分比告警重设计 (R3) + 动态唤醒间隔 (R4) + 价位级别 Alert (R7)
> **依赖**: Batch 1 (R5/R1/R2) 已合并

---

## 项目背景

参见 `docs/superpowers/specs/2026-04-12-batch1-r5-r1-r2-design.md` 的项目背景章节。

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

### 本批需求

| 编号 | 需求 | 类别 |
|------|------|------|
| R3 | 百分比告警重设计（触发后重置） | Agent 自主性 |
| R4 | 动态唤醒间隔（Agent 控制看盘节奏） | Agent 自主性 |
| R7 | 价位级别 Alert（Agent 设定关注价位） | Agent 自主性 |

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

**问题**：持续单边行情中，第一次 alert 后冷却期内的后续波动被丢弃。

### 新逻辑

```python
def check(self, price: float, timestamp: int) -> AlertInfo | None:
    self._ticks.append((price, timestamp))
    self._evict_old(timestamp)

    if len(self._ticks) < 2:
        return None

    prices = [p for p, _ in self._ticks]
    high, low = max(prices), min(prices)

    drop_pct = (price - high) / high * 100    # 负值
    rise_pct = (price - low) / low * 100      # 正值

    if abs(drop_pct) >= self._threshold_pct:
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
| `src/cli/wizard.py` | Step 4 删 cooldown 提示；`WizardResult` 删 `alert_cooldown_min` |
| `src/agent/tools_execution.py` | `set_price_alert` 删 cooldown 参数 |
| `src/agent/trader.py` | tool 注册删 cooldown 参数 |
| `src/integrations/exchange/base.py` | `update_alert_params` 删 cooldown |
| `src/integrations/exchange/simulated.py` | 同上 |
| `src/integrations/exchange/okx.py` | 同上 |
| `src/cli/session_manager.py` | `_create_session` alert_config JSON 删 cooldown；`_restore_session` 删 cooldown 读取 |
| `src/cli/app.py` | `build_services` 删 cooldown 传参 |

### 测试变更

- 删除：cooldown 相关测试（blocks same direction, allows opposite, expires）
- 新增：触发后 window 重置测试（触发→清空→重新积累→再触发）
- 更新：所有调用 `PriceAlertService()` 的地方删 cooldown 参数

---

## R4: 动态唤醒间隔

### 目标

Agent 每次 cycle 可通过 tool 设定下次唤醒时间，未调用时回到兜底间隔。

### 现状

`Scheduler` 使用固定 `interval_seconds`，`_interruptible_sleep` 每次等待相同时长。Agent 无法控制唤醒节奏。

### Scheduler 改造

```python
class Scheduler:
    def __init__(self, interval_seconds: float, callback):
        self._interval_seconds = interval_seconds    # 兜底间隔
        self._next_interval: float | None = None     # ← 新增：一次性覆盖

    def set_next_interval(self, seconds: float) -> None:
        """设置下一次 sleep 的间隔（一次性）。"""
        self._next_interval = seconds

    async def start(self):
        self._running = True
        await self._run_cycle("scheduled", None)
        while self._running:
            # 取一次性间隔，用完即重置
            interval = self._next_interval or self._interval_seconds
            self._next_interval = None
            await self._interruptible_sleep(interval)
            # ... drain events, run cycles ...
```

### 新 Agent Tool

```python
set_next_wake(minutes: int, reasoning: str) -> str
```

- **范围**：1 min ~ `min(max(4 * scheduler_interval_min, 60), 180)` min
- 超范围自动 clamp，不报错
- 一次性生效：只影响下一次唤醒，之后回到兜底间隔
- 未调用时 Scheduler 使用 `_interval_seconds`（wizard 配置的值）

### 接入方式

`TradingDeps` 新增可选回调字段：

```python
@dataclass
class TradingDeps:
    # ... 现有字段 ...
    set_next_wake_fn: Callable[[int], None] | None = None
```

`app.py` Phase 6 中，Scheduler 创建后注入：

```python
def _make_wake_setter(scheduler, min_min, max_min):
    def setter(minutes):
        clamped = max(min_min, min(minutes, max_min))
        scheduler.set_next_interval(clamped * 60)
    return setter

max_wake = min(max(4 * result.scheduler_interval_min, 60), 180)
deps.set_next_wake_fn = _make_wake_setter(scheduler, 1, max_wake)
```

Tool 实现：

```python
async def set_next_wake(deps: TradingDeps, minutes: int, reasoning: str) -> str:
    if deps.set_next_wake_fn is None:
        return "Dynamic wake not available"
    deps.set_next_wake_fn(minutes)
    return f"Next wake set to {minutes} min. Reason: {reasoning}"
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
| `src/agent/trader.py` | 系统 prompt 新增建议 |
| `src/cli/app.py` | Phase 6 注入 `deps.set_next_wake_fn` |

### 测试

- `test_scheduler.py` 新增：`set_next_interval` 一次性覆盖测试、用完重置测试、未设时用默认测试
- `test_tools_execution.py` 或新文件：`set_next_wake` tool 参数 clamp 测试

---

## R7: 价位级别 Alert

### 目标

Agent 通过 tool 在关键价位设定提醒（如"BTC 跌破 58000 通知我"），触发后一次性消耗。

### 新 Dataclass

定义在 `src/integrations/exchange/base.py`，与 `FillEvent`、`AlertInfo` 同级：

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

```python
class BaseExchange:
    def add_price_level_alert(self, price: float, direction: str,
                               symbol: str, reasoning: str) -> str:
        """添加价位 alert，返回 alert_id。"""

    def remove_price_level_alert(self, alert_id: str) -> bool:
        """移除价位 alert，返回是否找到。"""

    def _check_price_levels(self, current_price: float,
                             timestamp: int) -> list[PriceLevelAlertInfo]:
        """检查触发的价位 alerts，触发的从列表移除（一次性消耗），返回触发列表。"""
```

### Tick 处理集成

**SimulatedExchange._process_tick**：

```python
async def _process_tick(self, ticker):
    # ... 现有：liquidation, conditional orders ...

    # 3. 百分比 alert (R3)
    if self._alert_service:
        alert_info = self._alert_service.check(ticker.last, ticker.timestamp)

    # 4. 价位 alert (R7) ← 新增
    level_alerts = self._check_price_levels(ticker.last, ticker.timestamp)

    # Notify outside lock
    if alert_info and self._alert_callback:
        await self._alert_callback(alert_info)
    for la in level_alerts:                    # ← 新增
        if self._alert_callback:
            await self._alert_callback(la)
```

**OKXExchange._watch_ticker_loop**：同样在 ticker 处理中新增 `_check_price_levels` 调用。

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
set_price_alert_level(price: float, direction: str, reasoning: str) -> str
```

- `direction`：`"above"` 或 `"below"`
- 调用 `deps.exchange.add_price_level_alert(...)`
- 返回确认信息含 alert_id
- Agent 可设定多个价位 alert

### 文件变更

| 文件 | 改动 |
|------|------|
| `src/integrations/exchange/base.py` | 新增 `PriceLevelAlertInfo`、3 个方法 |
| `src/integrations/exchange/simulated.py` | `_process_tick` 新增价位检查 |
| `src/integrations/exchange/okx.py` | `_watch_ticker_loop` 新增价位检查 |
| `src/agent/tools_execution.py` | 新增 `set_price_alert_level` |
| `src/agent/trader.py` | tool 注册 + prompt 建议 |
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
| `src/cli/wizard.py` | R3 | Step 4 删 cooldown 提示，WizardResult 删字段 |
| `src/cli/session_manager.py` | R3 | alert_config JSON 删 cooldown |
| `src/cli/app.py` | R3+R4+R7 | build_services 删 cooldown；run_agent_cycle prompt 分支；Phase 6 注入 wake setter |
| `src/agent/tools_execution.py` | R3+R4+R7 | set_price_alert 删 cooldown；新增 set_next_wake；新增 set_price_alert_level |
| `src/agent/trader.py` | R3+R4+R7 | tool 注册更新；系统 prompt 新增建议 |
| `src/integrations/exchange/base.py` | R3+R7 | update_alert_params 删 cooldown；新增 PriceLevelAlertInfo + 3 方法 |
| `src/integrations/exchange/simulated.py` | R3+R7 | update_alert_params 删 cooldown；_process_tick 新增价位检查 |
| `src/integrations/exchange/okx.py` | R3+R7 | 同上 |
| `src/scheduler/scheduler.py` | R4 | 新增 _next_interval + set_next_interval() |

### 测试文件

| 文件 | 改动 |
|------|------|
| `tests/test_price_alert.py` | 重写：删 cooldown 测试，加 window-reset 测试 |
| `tests/test_price_level_alert.py` | 新建：R7 触发/消耗/方向测试 |
| `tests/test_scheduler.py` | 新增：set_next_interval 测试 |
| `tests/test_wizard.py` | 更新：删 cooldown 相关 mock/断言 |
| `tests/test_session_manager.py` | 更新：alert_config 不含 cooldown |
| `tests/test_trader_agent.py` | 更新：新增 tool 注册检查 |

### 不改动

| 文件 | 原因 |
|------|------|
| `src/storage/models.py` | Session 表无新字段 |
| `src/storage/database.py` | 无变化 |
| `src/cli/logging_config.py` | 无变化 |
| `src/cli/display.py` | 无变化 |
| `src/cli/approval.py` | 无变化 |
| `main.py` | 无新 CLI 参数 |
