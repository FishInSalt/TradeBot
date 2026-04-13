# R6 设计文档：SimulatedExchange 与真实交易所行为对齐

> **状态**: 初稿
> **日期**: 2026-04-13
> **范围**: 市价单异步化 + 限价单支持 + 重复下单防护 + fill 路径统一
> **依赖**: Batch 2 (R3/R4/R7) 已合并

---

## 项目背景

### TradeBot 是什么

TradeBot 是一个 AI 驱动的加密货币合约交易机器人。核心理念：LLM Agent 扮演交易员角色，自主分析市场、做出交易决策、管理仓位。

用户启动后，Agent 按动态间隔被唤醒（R4），或被价格波动/订单成交等事件打断唤醒。每次唤醒时，Agent 通过 tool 调用获取市场数据、查看持仓、回顾历史，然后决定操作（开仓/平仓/设止损/观望）。

### 技术栈

| 组件 | 技术 |
|------|------|
| 语言 | Python 3.12+, asyncio |
| LLM 框架 | pydantic-ai（Agent + tool 定义） |
| 交易所接口 | ccxt / ccxt.pro（REST + WebSocket） |
| 数据库 | SQLite + SQLAlchemy async（aiosqlite） |
| 终端 UI | Rich（表格、面板、彩色输出、交互 prompt） |
| 测试 | pytest + pytest-asyncio |

### 项目阶段

- **Phase 1a** (已完成) — 最小 agent 循环：单模型、REST 轮询、定时唤醒、模拟交易所
- **Phase 1b** (已完成) — 事件驱动闭环：多模型选择、WebSocket fill 推送、价格告警、Scheduler 事件队列化
- **Batch 1** (已完成) — 基础设施改造：日志分离 (R5)、CLI 配置向导 (R1)、Session 管理 (R2)
- **Batch 2** (已完成) — Agent 自主性增强：百分比告警重设计 (R3)、动态唤醒 (R4)、价位级别 Alert (R7)
- **当前 (R6)** — 引擎对齐（本文档）
- **Phase 2** (规划中) — 产品化：Web UI、多会话并行、多交易对

### 当前架构

```
main.py → src/cli/app.py::run()
  ├── Phase 1: setup_system_logging()
  ├── Phase 2: init_db() + migration
  ├── Phase 3: select_or_create_session()
  ├── Phase 4: setup_session_logging()
  ├── Phase 5: build_services()             ← 构建 exchange, deps, agent, budget
  └── Phase 6: run_main_loop()              ← scheduler + event handlers
```

### 当前交易所数据流

```
Exchange (ccxt.pro WebSocket)
  │
  ├─→ PriceAlertService.check(price, ts)    ← 每个 tick 调用 (R3)
  │     └─ 达到阈值 → AlertInfo → scheduler.trigger("alert")
  │
  ├─→ _check_price_levels(price, ts)        ← 每个 tick 调用 (R7)
  │     └─ 到达价位 → PriceLevelAlertInfo → scheduler.trigger("alert")
  │
  ├─→ 条件单撮合 → FillEvent
  │     └─→ _fill_callback → scheduler.trigger("conditional")
  │
  ├─→ 市价单 fill → _pending_fills 队列      ← ⚠️ 仅 SimExchange 的同步路径
  │     └─→ on_tick finally 中 drain → handle_fill → scheduler.trigger("conditional")
  │
  └─→ Scheduler._interruptible_sleep(动态间隔, R4)
        └─ 超时 → on_tick("scheduled", None) → run_agent_cycle
```

---

## 核心问题

SimulatedExchange 与 OKXExchange 存在两个关键行为差异，违背了"模拟应与真实完全一致"的设计原则：

### 问题 1: 市价单时序不一致

| 维度 | SimExchange (当前) | OKXExchange |
|------|-------------------|-------------|
| 市价单执行 | `create_order("market")` 同步执行：立即更新仓位/余额 | REST 提交后返回，实际成交通过 WebSocket 异步通知 |
| Agent 行为 | 同一 cycle 内：开仓 → 立即看到仓位 → 设止损 ✓ | 开仓 → 仓位还没更新 → 设止损失败，需等 fill callback 下一 cycle 再设 |
| Fill 通知 | 同步产生 FillEvent → 存入 `_pending_fills` → on_tick finally 中 drain | WebSocket `watch_orders` → `_fill_callback` 立即调用 |

**后果**：Agent 在模拟中学到的策略模式（开仓后立即设止损）在实盘上会失败。

### 问题 2: 限价单缺失

| 维度 | SimExchange (当前) | OKXExchange |
|------|-------------------|-------------|
| 订单类型 | `market` / `stop` / `take_profit` | ccxt 透传，支持 `limit` 及其他 |
| Agent 工具 | `open_position` (市价) / `set_stop_loss` / `set_take_profit` | 同左（受限于 Agent tool 定义） |

**后果**：真实交易员常用限价单在关键价位建仓（如"在 58000 挂买单"），Agent 无法使用这一基础能力。

### 为什么现在做

R6 是 Phase 2（产品化）的前置条件。如果模拟和实盘行为不一致，用户在模拟中验证通过的策略上线后可能直接失败（如开仓后立即设止损在实盘上不生效），造成资金风险。在产品化之前修复引擎对齐是最低成本的时机——后续改动的测试兼容负担只会更大。

### 设计目标

| # | 目标 | 可验收标准 |
|---|------|-----------|
| G1 | 市价单时序对齐 | SimExchange 市价单异步成交：`create_order("market")` 返回 `status="open"`，仓位在下一个 tick 撮合后才可见，fill 通过 `_fill_callback` 通知 |
| G2 | Fill 路径统一 | 删除 `drain_pending_fills` 机制，所有 fill（市价/条件/清算）走 `_fill_callback` → `scheduler.trigger("conditional")` 同一路径 |
| G3 | 限价单支持 | SimExchange 和 OKX 均支持 `create_order("limit")`，Agent 有 `place_limit_order` 工具 |
| G4 | 重复下单防护 | pending 市价单未成交时，Agent 再次调用 `open_position`/`close_position` 被拒绝并收到提示 |
| G5 | 现有测试全部通过 | 适配后的测试套件 `pytest` 全绿 |

### 非目标（首版不做）

| 项目 | 理由 |
|------|------|
| 部分成交 (partial fill) | SimExchange 和 Agent tool 层均假设全量成交，与 stop/take_profit 一致。引入 partial fill 需要订单状态机和仓位合并逻辑的大幅改造，ROI 不高 |
| 限价平仓单 | `take_profit` 条件单已覆盖"到目标价平仓"场景。限价平仓的差异（锁定价格但可能不完全成交）在全量成交简化下无实质区别。后续可扩展 |
| 市价单失败/拒绝模拟 | 真实交易所可能拒绝订单（余额不足、风控限制等），SimExchange 当前在 `create_order` 阶段做余额预检，不模拟提交后被拒的场景 |
| 网络延迟模拟 | 撮合延迟已通过"下一个 tick"自然产生，不额外模拟网络抖动或超时 |
| OKXExchange 侧改动 | OKX 已是异步行为，`create_order` 已透传 ccxt（含 limit）。本次改动集中在 SimExchange 侧 |

### 关键设计决策汇总

| # | 决策 | 选择 | 核心理由 |
|---|------|------|---------|
| D1 | 市价单异步化方案 | 延迟状态更新（非延迟通知） | 语义一致：`create_order` = 提交，`_process_tick` = 撮合。避免"状态已变但 Agent 不知道"的半异步矛盾 |
| D2 | 开仓/平仓冻结逻辑 | 区分处理：开仓冻结 margin+fee，平仓仅冻结 fee | 全仓开仓后 `free_usdt ≈ 0`，统一冻结 margin+fee 会导致平仓死局 |
| D3 | 重复下单防护层 | Tool 层拒绝（非 Exchange 层异常） | Tool 返回提示比异常更友好，Agent 可理解原因；OKX 不阻止重复下单，Exchange 层不应添加 OKX 不存在的限制 |
| D4 | 限价单首版范围 | 仅开仓 | `take_profit` 已覆盖限价平仓主要场景；减少首版复杂度 |
| D5 | 限价单杠杆 | 有仓位时强制匹配仓位杠杆，无仓位时 Agent 指定 | 与 `_open_market_order` 的 leverage mismatch 检查一致 |
| D6 | 限价单反向仓位 | 引擎层拒绝 | SimExchange 不支持双向持仓（one-way mode），与 OKX 设置一致 |
| D7 | _cancel_orphaned_orders | 用 `_is_close_order_static`（基于订单字段）判断方向，保留开仓单，清理平仓孤儿单并解冻 | 调用时仓位已被删除，`_is_close_order`（基于 `_positions`）会误判；开仓单不应因止损/清算而被误删 |
| D8 | drain_pending_fills | 删除 | 市价单异步化后所有 fill 走 `_fill_callback`，`_pending_fills` 队列不再需要 |
| D9 | 开仓冻结缓冲 | frozen *= 1.002 + 撮合时 clamp 兜底 | 全仓+价格不利可能导致 `_free_usdt < 0`，0.2% 缓冲覆盖正常波动，极端场景用追加保证金语义兜底 |
| D10 | 限价单撮合时反向仓位检查 | `_execute_limit_fill` 返回 None 取消订单并解冻 | 创建时无冲突，但 pending 期间 market 单先成交建了反向仓位，填充时必须二次检查 |
| D11 | cancel_order 拒绝市价单 | 市价单不可取消 | 与真实交易所一致——市价单已进入撮合队列，下个 tick 必然成交 |
| D12 | 限价单填充时杠杆校验 | `_execute_limit_fill` 检查 pos.leverage != order.leverage 则取消 | pending 期间 set_leverage 或其他订单可能改变杠杆，必须在填充时二次检查 |
| D13 | place_limit_order 不检查 pending market | 允许 market + limit 共存 | 市价即时入场 + 限价低位加仓是有效策略；方向冲突由填充时二次检查兜底 |
| D14 | _fill_market_open/close 防御性检查 | 与 `_execute_limit_fill` 对称：反向仓位/杠杆不一致/仓位不存在均返回 None | 引擎层应自洽，不依赖外部工具层或撮合顺序的保证 |
| D15 | 平仓填充使用 pnl_cap=True | 与清算一致 | 异步化多一个 tick 窗口，价格可能穿过清算价，需要同一安全阀防止 free_usdt 断言失败 |

---

## 改动 1: 市价单异步化

### 现状

`SimulatedExchange.create_order("market")` 当前流程（`simulated.py:154-178`）：

```
create_order("market", side, amount)
  └─ async with self._lock:
       ├─ _execute_market_order()          ← 同步计算：更新 _positions, _free_usdt, _used_usdt
       ├─ _persist_state(new_orders=...)   ← 写入 DB（Order status="closed"）
       ├─ _pending_fills.append(FillEvent) ← 队列化 fill 通知
       └─ return Order(status="closed")    ← Agent 看到"已成交"
```

Agent 调用 `open_position` tool 后，`fetch_positions()` 立即看到仓位 → 同一 cycle 调用 `set_stop_loss` 成功。

### 设计选型

| 方案 | 描述 | 分析 |
|------|------|------|
| A. 延迟状态更新 | `create_order` 只记录 pending，下一个 tick 撮合更新状态 | 完全对齐 OKX：Agent 提交后看不到仓位，等 fill callback。语义清晰，改动集中在 SimExchange |
| B. 同步执行 + 延迟通知 | 仍同步更新状态，但不返回成交信息，fill 延迟通知 | 半异步：状态已变但 Agent 不知道。`fetch_positions` 能看到仓位但 tool 返回"等待成交"，行为矛盾 |

**选择方案 A**。理由：
- 语义一致：`create_order` 只是"提交订单"，状态更新在撮合时发生
- Agent 行为完全对齐 OKX：开仓后 `fetch_positions` 返回空，必须等 fill callback
- 成交价使用撮合时的 tick 价格，而非提交时的价格，更贴近真实市场
- 实际延迟很小（ccxt.pro ticker 通常每秒推送），不影响体验

### 新流程

```
create_order("market", side, amount)
  └─ async with self._lock:
       ├─ 判断开仓/平仓：is_close = _is_close_order(symbol, side)
       ├─ 确定 position_side：
       │    开仓: "long" if side == "buy" else "short"
       │    平仓: pos.side（当前仓位方向，此时仓位必然存在）
       ├─ 冻结保证金（开仓: margin+fee*1.002; 平仓: 仅 fee）
       ├─ 创建 _PendingOrder(order_type="market",
       │      position_side=position_side,
       │      frozen_margin=frozen, leverage=leverage)
       ├─ _persist_state()
       └─ return Order(status="open")     ← Agent 看到"已提交"

_process_tick(ticker)
  └─ async with self._lock:
       ├─ 0. 撮合 pending 市价单（新增步骤）
       │    ├─ 以当前 tick 价格执行 _execute_market_fill()
       │    ├─ 更新 _positions, _free_usdt, _used_usdt, _frozen_usdt
       │    └─ 生成 FillEvent
       ├─ 1. 清算检查（现有）
       ├─ 2. 条件单/限价单撮合（现有 + 新增 limit）
       ├─ 3. 百分比告警（现有, R3）
       └─ 4. 价位告警（现有, R7）

  └─ Notify outside lock:
       ├─ 市价单 fill → _fill_callback()    ← 与条件单走同一路径
       ├─ 条件单 fill → _fill_callback()
       └─ alerts → _alert_callback()
```

### 余额冻结机制

市价单不再同步扣款，但需要防止超额下单。**开仓和平仓的冻结逻辑不同**：

#### 开仓市价单冻结

开仓需要新保证金，从 `_free_usdt` 冻结 estimated_margin + fee：

```python
# create_order("market") — 开仓：
estimated_price = ticker.ask if side == "buy" else ticker.bid
leverage = self._leverage.get(symbol, 1)
estimated_margin = (estimated_price * amount) / leverage
estimated_fee = estimated_price * amount * self._fee_rate
frozen = (estimated_margin + estimated_fee) * 1.002  # 0.2% 缓冲，防止撮合时价格不利导致 _free_usdt 为负

if self._free_usdt < frozen:
    raise ValueError(f"Insufficient balance: need {frozen:.2f}, have {self._free_usdt:.2f}")

self._free_usdt -= frozen
self._frozen_usdt += frozen
```

**为什么加 0.2% 缓冲**：市价单在下一个 tick 撮合，成交价可能高于提交时的 ask（价格不利方向移动）。如果 Agent 使用 100% free_usdt 开仓且不加缓冲，实际 cost > frozen 会导致 `_free_usdt` 变为负数。0.2% 缓冲覆盖了 tick 间的正常价格波动（BTC 秒级波动通常远小于 0.2%）。极端行情下仍可能穿透缓冲，`_fill_market_open` 中需做兜底 clamp：

```python
# _fill_market_open 中：
diff = order.frozen_margin - actual_cost
self._frozen_usdt -= order.frozen_margin
self._used_usdt += actual_margin
self._free_usdt += diff
# 兜底：极端情况下 free 可能微量为负，clamp 到 0
if self._free_usdt < 0:
    shortfall = -self._free_usdt
    self._free_usdt = 0.0
    self._used_usdt += shortfall  # 追加保证金语义
```

开仓撮合时以实际成交价重新计算，差额退还或追扣：

```python
# _process_tick 开仓撮合时：
actual_margin = (fill_price * amount) / leverage
actual_fee = fill_price * amount * self._fee_rate
actual_cost = actual_margin + actual_fee
diff = frozen - actual_cost

self._frozen_usdt -= frozen
self._used_usdt += actual_margin
self._free_usdt += diff  # 正数=退还，负数=追扣
```

#### 平仓市价单冻结

平仓不需要新保证金（保证金已在 `_used_usdt` 中），仅冻结预估手续费。若 `_free_usdt` 不足以覆盖手续费，允许从平仓收回的保证金中扣除（即冻结 0，fee 在撮合时从释放的保证金中扣）：

```python
# create_order("market") — 平仓：
estimated_price = ticker.bid if pos.side == "long" else ticker.ask
estimated_fee = estimated_price * amount * self._fee_rate
frozen = min(estimated_fee, self._free_usdt)  # 尽量冻结 fee，不足则冻结可用余额

self._free_usdt -= frozen
self._frozen_usdt += frozen
```

平仓撮合时，fee 从释放的保证金中扣除：

```python
# _process_tick 平仓撮合时：
pnl, fee, released_margin = self._close_position_core(symbol, pos.side, amount, fill_price)
# close_position_core 已处理 _used_usdt 和 _free_usdt 的释放
# 仅需解冻 frozen 部分
self._frozen_usdt -= frozen
self._free_usdt += frozen  # 退还冻结的 fee 预估（实际 fee 已在 _close_position_core 中扣除）
```

#### 为什么区分开仓/平仓

全仓开仓后 `_free_usdt ≈ 0`、`_used_usdt ≈ 全部资金`。如果平仓也冻结 margin + fee，会因 `_free_usdt` 不足被拒绝，Agent 陷入无法平仓的死局。真实交易所中平仓不需要追加保证金，SimExchange 应保持一致。

#### 判断开仓/平仓

需要两个版本的判断方法：

**动态版本**（`create_order` 时使用，依赖当前仓位状态）：

```python
def _is_close_order(self, symbol: str, side: str) -> bool:
    """判断这笔市价单是否为平仓。create_order 时调用，此时仓位状态可用。"""
    pos = self._positions.get(symbol)
    return (
        (pos is not None and pos.side == "long" and side == "sell") or
        (pos is not None and pos.side == "short" and side == "buy")
    )
```

**静态版本**（`_cancel_orphaned_orders` 和其他仓位已删除场景使用，依赖订单自身字段）：

```python
@staticmethod
def _is_close_order_static(o: _PendingOrder) -> bool:
    """判断 pending order 是否为平仓方向。不依赖当前仓位状态。"""
    return (
        (o.position_side == "long" and o.side == "sell") or
        (o.position_side == "short" and o.side == "buy")
    )
```

`_cancel_orphaned_orders` 和 `_execute_market_fill` 的路由必须使用静态版本——因为仓位可能已在同一 tick 内被清算/止损删除。

### _PendingOrder 扩展

当前 `_PendingOrder` 为条件单设计，字段 `trigger_price` 对市价单无意义。扩展以支持市价单和限价单：

```python
@dataclass
class _PendingOrder:
    id: str
    symbol: str
    side: str
    position_side: str
    order_type: str          # "market" | "limit" | "stop" | "take_profit"
    amount: float
    trigger_price: float | None   # 改为 Optional：market 单为 None
    frozen_margin: float = 0.0    # 新增：市价/限价单冻结的保证金+手续费
    leverage: int = 1             # 新增：下单时的杠杆（撮合时需要）
```

### 撮合方法

新增 `_execute_market_fill()` 方法，处理 pending 市价单的撮合：

```python
def _execute_market_fill(self, order: _PendingOrder, ticker: Ticker) -> FillEvent | None:
    """撮合 pending 市价单。返回 None 表示因冲突取消（解冻保证金）。
    使用静态方向判断（仓位可能已在同一 tick 内变化）。"""
    if self._is_close_order_static(order):
        return self._fill_market_close(order, ticker)
    else:
        return self._fill_market_open(order, ticker)
```

**开仓撮合** (`_fill_market_open`) — 返回 `FillEvent | None`：
- 防御性检查（与 `_execute_limit_fill` 对称）：
  - 反向仓位：已有仓位且方向不同 → 取消并解冻，返回 None
  - 杠杆不一致：已有仓位且杠杆不同 → 取消并解冻，返回 None
- 用当前 tick 价格计算实际 margin/fee
- 解冻 `_frozen_usdt`，扣 `_used_usdt`，退差额到 `_free_usdt`（含 clamp 兜底）
- 创建或合并 `_positions[symbol]`
- 同步 `self._leverage[symbol] = order.leverage`（与 `_restore_state` 保持一致）
- 返回 FillEvent(trigger_reason="market", pnl=None)

**平仓撮合** (`_fill_market_close`) — 返回 `FillEvent | None`：
- 检查仓位是否存在（防御性：若仓位已在同一 tick 被清算删除，取消并解冻，返回 None）
- `actual_amount = min(order.amount, pos.contracts)` — 防御性 clamping，与现有 `_close_market_order` 一致
- 用当前 tick 价格计算 PnL（调用 `_close_position_core(pnl_cap=True)` — 使用 pnl_cap 防止极端价格间隙导致 `free_usdt` 触发 RuntimeError 断言。异步化多了一个 tick 的时间窗口，价格可能穿过清算价，与清算使用同一安全阀）
- 解冻 `_frozen_usdt`（退全额，因为平仓不占用新保证金）
- 返回 FillEvent(trigger_reason="market", pnl=实际PnL)

### _process_tick 新增步骤

在 `_process_tick` 的 lock 内，**清算检查之前**新增市价单撮合步骤：

```python
async def _process_tick(self, ticker: Ticker) -> None:
    self._latest_ticker = ticker
    self._latest_price = ticker.last

    triggered: list[FillEvent] = []
    filled_order_ids: list[str] = []
    new_orders: list[tuple[Order, str]] = []
    alert_info = None
    level_alerts = []

    async with self._lock:
        # 0. 撮合 pending 市价单（新增）
        market_orders = [o for o in self._pending_orders if o.order_type == "market"]
        for order in market_orders:
            fill = self._execute_market_fill(order, ticker)
            triggered.append(fill)
            filled_order_ids.append(order.id)
            new_orders.append((Order(
                id=order.id, symbol=order.symbol,
                side=order.side, order_type="market",
                amount=fill.amount, price=fill.fill_price,
                status="closed", fee=fill.fee,
            ), fill.position_side))

        # 1. 清算检查（现有）
        # ...

        # 2. 条件单 + 限价单撮合（现有 + 新增）
        # ...
```

**为什么市价单在清算检查之前？** 真实交易所中市价单提交后会立即排入撮合队列，优先级高于条件单检查。先撮合市价单、更新仓位，再检查清算和条件单，符合真实顺序。

### _cancel_orphaned_orders 改造

当前逻辑（`simulated.py:342-347`）删除"symbol 无仓位"的所有 pending orders：

```python
def _cancel_orphaned_orders(self) -> None:
    self._pending_orders = [
        o for o in self._pending_orders
        if o.symbol in self._positions
    ]
```

**问题**：market/limit 开仓单在仓位建立**之前**存在。如果止损触发导致仓位关闭，会误删 pending market buy / pending limit buy，且冻结保证金不会被解冻——余额泄露。

同理，如果仓位被清算，pending market close 也会成为孤儿单，需要解冻处理。

**改造后**：

```python
def _cancel_orphaned_orders(self) -> None:
    """Remove pending orders that lost their raison d'être.
    - stop/take_profit: 仓位不存在则删（平仓单失去目标）
    - market/limit 开仓方向: 保留（开仓单不依赖现有仓位）
    - market close (平仓方向): 仓位不存在则删（仓位已被清算/止损平掉）

    注意：
    - 必须使用 _is_close_order_static 判断方向，因为调用时仓位可能已被删除。
    - 限价开仓单在清算后存活是预期行为——与真实交易所一致（限价单是独立的交易
      意图，不因清算自动取消）。Agent 在清算 FillEvent 回调唤醒后可通过
      fetch_open_orders 看到仍存活的限价单并决定是否取消。
    """
    remaining = []
    for o in self._pending_orders:
        if o.order_type in ("stop", "take_profit"):
            if o.symbol in self._positions:
                remaining.append(o)
            # else: 条件单无冻结，直接丢弃
        elif o.order_type == "market" and self._is_close_order_static(o):
            if o.symbol in self._positions:
                remaining.append(o)
            else:
                # 平仓市价单的目标仓位已消失（被清算），解冻保证金
                if o.frozen_margin > 0:
                    self._frozen_usdt -= o.frozen_margin
                    self._free_usdt += o.frozen_margin
        else:
            # market open / limit: 保留
            remaining.append(o)
    self._pending_orders = remaining
```

### create_order 返回值变更

| 字段 | 旧值 | 新值 |
|------|------|------|
| `status` | `"closed"` | `"open"` |
| `price` | 成交价 | `None`（未撮合，无成交价） |
| `fee` | 实际手续费 | `None`（未撮合，无手续费） |

Agent tool (`open_position`) 返回信息变更：
- 旧：`"Order submitted: long 0.01 @ ~65000.00, 10x | ID: xxx"`
- 新：`"Order submitted: long 0.01 ~65000, 10x | ID: xxx\nYou will be notified when filled."`

注：`open_position` 的返回信息已经包含 "You will be notified when filled"（`tools_execution.py:83-84`），无需修改文案。但 OKX 的 `create_order` 返回的 `price` 可能是 `None`（未成交时），现有代码已通过 `~{ticker.last:.2f}` 使用 ticker 价格而非 order.price，因此 tool 层不受影响。

### 删除 drain_pending_fills

市价单异步化后，所有 fill 统一通过 `_fill_callback` 通知，`_pending_fills` 和 `drain_pending_fills` 不再需要。

**删除清单：**

| 文件 | 改动 |
|------|------|
| `src/integrations/exchange/base.py:147-149` | 删除 `drain_pending_fills()` 默认实现 |
| `src/integrations/exchange/simulated.py:67` | 删除 `_pending_fills` 属性 |
| `src/integrations/exchange/simulated.py:170-177` | 删除 `create_order` 中的 `_pending_fills.append(...)` |
| `src/integrations/exchange/simulated.py:577-580` | 删除 `drain_pending_fills()` 方法 |
| `src/cli/app.py:336-342` | 删除 `on_tick` finally 块中的 `drain_pending_fills` 逻辑 |

**OKXExchange 不受影响**：OKX 从未使用 `_pending_fills`，继承了 base 的空实现。删除 base 方法后 OKX 无需改动。

---

## 改动 2: 重复下单防护

### 问题场景

市价单异步化后，fill 在下一个 tick 才产生。如果 Agent 在 fill 之前被再次唤醒，会看到"无仓位"或"仓位仍在"，导致重复下单：

**场景 A: 重复开仓**
```
cycle 1: Agent 调用 open_position → pending market buy → 返回"已提交"
[fill 尚未触发]
cycle 2: Agent 被 scheduled 唤醒 → fetch_positions 返回空 → 再次 open_position → 重复开仓
```

**场景 B: 重复平仓**
```
cycle 1: Agent 调用 close_position → pending market sell → 返回"已提交"
[fill 尚未触发]
cycle 2: Agent 被 scheduled 唤醒 → fetch_positions 仍有仓位 → 再次 close_position → 重复平仓
```

### 设计

在 SimExchange 层新增查询方法，Agent tool 层做防护检查：

**SimExchange 新增方法**：

```python
def has_pending_market_order(self, symbol: str, side: str | None = None) -> bool:
    """检查是否有未撮合的市价单。side 可选过滤。"""
    for o in self._pending_orders:
        if o.order_type == "market" and o.symbol == symbol:
            if side is None or o.side == side:
                return True
    return False
```

**BaseExchange 新增方法**（默认返回 False，OKX 无需 override）：

```python
def has_pending_market_order(self, symbol: str, side: str | None = None) -> bool:
    """Check for pending market orders. Default: False.

    Real exchanges (OKX) don't track pending state client-side — orders are
    submitted and confirmed asynchronously via WebSocket. This method exists
    for SimExchange where we deliberately prevent duplicate orders from
    LLM agents that can't visually confirm "order in progress" state.
    """
    return False
```

**Agent tool 层防护**：

`open_position`（`tools_execution.py`）新增检查：
```python
# 在 _check_approval 之前
if deps.exchange.has_pending_market_order(deps.symbol):
    return "A market order is already pending. Wait for fill confirmation before opening another position."
```

`close_position`（`tools_execution.py`）新增检查：
```python
# 在 fetch_positions 之后
order_side = "sell" if positions[0].side == "long" else "buy"
if deps.exchange.has_pending_market_order(deps.symbol, side=order_side):
    return "A close order is already pending. Wait for fill confirmation."
```

**OKX 行为对比**：OKX 服务端不阻止重复下单（可以连续发多笔市价单），但这通常是用户错误。SimExchange 的防护比 OKX 更严格，这是刻意的——防止 LLM Agent 的重复下单比防止人类交易员更重要，因为 Agent 无法从 UI 判断"订单正在处理中"。

### 设计选型：防护在哪一层？

| 方案 | 描述 | 分析 |
|------|------|------|
| A. Exchange 层拒绝 | `create_order` 检查 pending，有则抛异常 | 简单粗暴，但 OKX 不这样做，且 close 场景需要区分方向 |
| B. Tool 层拒绝 | tool 函数检查后返回提示信息 | 更灵活，Agent 收到提示可调整策略，不会引发异常中断 |

**选择方案 B**。理由：
- Tool 返回提示信息比抛异常更友好，Agent 可以理解原因
- 防护逻辑与业务场景强相关（开仓 vs 平仓检查不同），放在 tool 层更自然
- `has_pending_market_order` 在 Exchange 层暴露查询能力，tool 层决定策略

---

## 改动 3: 限价单支持

### 设计

限价单行为与条件单（stop/take_profit）类似：提交后 pending，价格到达时撮合。区别在于：
- 条件单是**平仓**操作，必须有已有仓位
- 限价单是**开仓**操作（首版），不需要已有仓位

**首版限制：限价单仅用于开仓。** 真实交易中限价单也可用于平仓（如"在 70000 挂卖单止盈"），但当前 `take_profit` 条件单已覆盖大部分限价平仓场景（价格到达 → 市价成交）。两者语义差异（take_profit 保证成交但可能滑点 vs limit sell 锁定价格但可能不完全成交）在首版全量成交的简化下无实质区别。后续可扩展 limit 支持平仓方向。

### 杠杆约束

限价单的杠杆必须与现有仓位一致（若有仓位）。这与 `_open_market_order` 中的 leverage mismatch 检查（`simulated.py:219-224`）保持一致：

- **无仓位时**：使用当前 `_leverage[symbol]` 设置
- **有仓位时**：强制使用仓位杠杆，忽略 `_leverage[symbol]`。若 Agent 先 `set_leverage(20)` 再下限价单，但当前有 10x 仓位，限价单以 10x 执行

`place_limit_order` tool 接受 `leverage` 参数，行为分两种情况：
- **有仓位时**：忽略参数，强制使用仓位杠杆（与 `_open_market_order` 的 leverage mismatch 检查一致）
- **无仓位时**：调用 `set_leverage(leverage)` 设置杠杆后再下单（与 `open_position` 行为一致）

```python
# place_limit_order tool:
positions = await deps.exchange.fetch_positions(deps.symbol)
if positions:
    actual_leverage = positions[0].leverage  # 强制匹配，忽略参数
else:
    await deps.exchange.set_leverage(deps.symbol, leverage)
    actual_leverage = leverage
```

### 反向仓位校验

引擎层必须拒绝与现有仓位方向相反的限价开仓单。例如已有 long 仓位时，`create_order("limit", side="sell")` 应被拒绝——SimExchange 不支持双向持仓（与 OKX 的 one-way mode 一致）：

```python
# create_order("limit") 中新增校验：
pos = self._positions.get(symbol)
position_side = "long" if side == "buy" else "short"
if pos is not None and pos.side != position_side:
    raise ValueError(
        f"Cannot open {position_side} limit order: existing {pos.side} position. "
        f"Close position first."
    )
```

### SimExchange create_order 扩展

```python
if order_type not in ("market", "limit", "stop", "take_profit"):
    raise ValueError(f"Unknown order_type: {order_type}")
```

限价单处理：

```python
if order_type == "limit":
    if price is None:
        raise ValueError("price is required for limit orders")
    # 冻结保证金（无需 * 1.002 缓冲——限价单以指定价格成交，无价格偏差）
    leverage = self._leverage.get(symbol, 1)
    margin = (price * amount) / leverage
    fee = price * amount * self._fee_rate
    frozen = margin + fee
    if self._free_usdt < frozen:
        raise ValueError(f"Insufficient balance: need {frozen:.2f}, have {self._free_usdt:.2f}")
    self._free_usdt -= frozen
    self._frozen_usdt += frozen

    position_side = "long" if side == "buy" else "short"
    order_id = str(uuid.uuid4())
    self._pending_orders.append(_PendingOrder(
        id=order_id, symbol=symbol, side=side,
        position_side=position_side, order_type="limit",
        amount=amount, trigger_price=price,
        frozen_margin=frozen, leverage=leverage,
    ))
    if self._db_engine:
        await self._persist_state()
    return Order(id=order_id, symbol=symbol, side=side,
                 order_type="limit", amount=amount, price=price, status="open")
```

### 限价单撮合条件

在 `_should_trigger` 中新增 limit 判断：

```python
def _should_trigger(self, order: _PendingOrder, ticker: Ticker) -> bool:
    if order.order_type == "limit":
        # Buy limit: 卖方出价 ≤ 限定价 → 可以买入
        if order.side == "buy":
            return ticker.ask <= order.trigger_price
        # Sell limit: 买方出价 ≥ 限定价 → 可以卖出
        else:
            return ticker.bid >= order.trigger_price
    elif order.order_type == "stop":
        # ... 现有逻辑
```

### 限价单撮合执行

限价单成交时的处理与市价单开仓撮合类似（`_fill_market_open`），但成交价使用限价而非市场价：

```python
def _execute_limit_fill(self, order: _PendingOrder, ticker: Ticker) -> FillEvent | None:
    """限价单撮合。成交价 = 限价（price improvement 首版不考虑）。
    返回 None 表示因反向仓位冲突而取消（解冻保证金）。"""
    # 撮合时二次检查（创建时可能无仓位/杠杆不同，但 pending 期间状态可能已变）
    pos = self._positions.get(order.symbol)
    position_side = "long" if order.side == "buy" else "short"

    # 检查 1: 反向仓位冲突
    if pos is not None and pos.side != position_side:
        logger.warning(
            f"Limit order {order.id} cancelled: conflicts with existing {pos.side} position"
        )
        self._frozen_usdt -= order.frozen_margin
        self._free_usdt += order.frozen_margin
        return None

    # 检查 2: 杠杆一致性（与 _open_market_order 的 leverage mismatch 检查对齐）
    # 覆盖场景：pending 期间 set_leverage 被调用或其他订单先以不同杠杆成交
    if pos is not None and pos.leverage != order.leverage:
        logger.warning(
            f"Limit order {order.id} cancelled: leverage mismatch "
            f"(order={order.leverage}x, position={pos.leverage}x)"
        )
        self._frozen_usdt -= order.frozen_margin
        self._free_usdt += order.frozen_margin
        return None

    fill_price = order.trigger_price  # 限价单以限定价成交（无需缓冲，价格确定）
    leverage = order.leverage
    actual_margin = (fill_price * order.amount) / leverage
    actual_fee = fill_price * order.amount * self._fee_rate
    actual_cost = actual_margin + actual_fee

    # 解冻 → 占用
    self._frozen_usdt -= order.frozen_margin
    self._used_usdt += actual_margin
    self._free_usdt += (order.frozen_margin - actual_cost)

    # 创建/合并仓位
    position_side = "long" if order.side == "buy" else "short"
    pos = self._positions.get(order.symbol)
    if pos is not None and pos.side == position_side:
        # 合并仓位
        new_contracts = pos.contracts + order.amount
        new_entry = (pos.entry_price * pos.contracts + fill_price * order.amount) / new_contracts
        pos.contracts = new_contracts
        pos.entry_price = new_entry
    else:
        self._positions[order.symbol] = _Position(
            side=position_side, contracts=order.amount,
            entry_price=fill_price, leverage=leverage,
        )
    self._leverage[order.symbol] = leverage  # 与 _fill_market_open / _restore_state 保持对称

    return FillEvent(
        order_id=order.id, symbol=order.symbol, side=order.side,
        position_side=position_side, trigger_reason="limit",
        fill_price=fill_price, amount=order.amount, fee=actual_fee,
        pnl=None,  # 开仓无 PnL
        timestamp=int(datetime.now(timezone.utc).timestamp() * 1000),
    )
```

### _process_tick 中的撮合顺序

```python
async with self._lock:
    # 0. 撮合 pending 市价单
    market_orders = [o for o in self._pending_orders if o.order_type == "market"]
    for order in market_orders:
        fill = self._execute_market_fill(order, ticker)
        filled_order_ids.append(order.id)
        if fill is None:
            # 因冲突取消（仓位已消失/反向/杠杆不一致），已解冻
            continue
        triggered.append(fill)
        new_orders.append((Order(..., status="closed"), fill.position_side))

    # 1. 清算检查
    # ...（现有，不变）

    # 2. 条件单 + 限价单撮合
    non_market = [o for o in self._pending_orders
                  if o.order_type != "market" and o.id not in filled_order_ids]
    for order in non_market:
        if self._should_trigger(order, ticker):
            if order.order_type in ("stop", "take_profit"):
                if not self._has_position(order.symbol):
                    continue
                fill = self._execute_fill(order, ticker)
            elif order.order_type == "limit":
                fill = self._execute_limit_fill(order, ticker)
                if fill is None:
                    # 因冲突被取消（反向仓位/杠杆不一致），仅需从 pending 中移除
                    filled_order_ids.append(order.id)
                    continue
            triggered.append(fill)
            filled_order_ids.append(order.id)

    # 3. 统一清理（所有 fill 类型共用，与现有逻辑结构一致）
    if triggered or filled_order_ids:
        for fid in filled_order_ids:
            self._remove_order_by_id(fid)
        self._cancel_orphaned_orders()  # 使用 _is_close_order_static
        if self._db_engine:
            await self._persist_state(
                new_orders=new_orders,
                filled_order_ids=filled_order_ids,
                fill_events=triggered,
            )

    # 4. 百分比告警 (R3)
    if self._alert_service:
        alert_info = self._alert_service.check(ticker.last, ticker.timestamp)

    # 5. 价位告警 (R7)
    level_alerts = self._check_price_levels(ticker.last, ticker.timestamp)
```

### 限价单取消

限价单应支持取消（Agent 可能改变策略），复用现有的 `cancel_order` 方法。取消时需解冻保证金：

```python
async def cancel_order(self, order_id: str, symbol: str | None = None) -> None:
    async with self._lock:
        order = None
        for o in self._pending_orders:
            if o.id == order_id:
                order = o
                break
        if order is None:
            raise ValueError(f"Order {order_id} not found")

        # 真实交易所不允许取消市价单（已进入撮合队列，下个 tick 必然成交）
        if order.order_type == "market":
            raise ValueError("Cannot cancel market orders")

        # 解冻保证金（限价单有冻结）
        if order.frozen_margin > 0:
            self._frozen_usdt -= order.frozen_margin
            self._free_usdt += order.frozen_margin

        self._pending_orders = [o for o in self._pending_orders if o.id != order_id]
        # ... persist
```

### 新 Agent Tool: place_limit_order

**注意：`place_limit_order` 不检查 `has_pending_market_order`。** 这是刻意的设计取舍——market + limit 组合是有效的交易策略（市价立即入场 + 限价低位加仓）。如果 market 和 limit 方向冲突，`_execute_limit_fill` 的撮合时二次检查会自动取消冲突的限价单并解冻保证金。

```python
async def place_limit_order(
    deps: TradingDeps,
    side: str,
    price: float,
    position_pct: float,
    leverage: int,
    reasoning: str,
) -> str:
    """Place a limit order at a specific price."""
    if side not in ("long", "short"):
        return "side must be 'long' or 'short'"

    # 杠杆：有仓位时强制与仓位一致，无仓位时使用 Agent 指定的值
    positions = await deps.exchange.fetch_positions(deps.symbol)
    if positions:
        actual_leverage = positions[0].leverage  # 强制匹配，忽略 leverage 参数
    else:
        await deps.exchange.set_leverage(deps.symbol, leverage)
        actual_leverage = leverage

    balance = await deps.exchange.fetch_balance()
    usdt_amount = balance.free_usdt * (position_pct / 100.0)
    raw_quantity = (usdt_amount * actual_leverage) / price
    quantity = deps.exchange.amount_to_precision(deps.symbol, raw_quantity)
    if quantity <= 0:
        return f"Position too small: {raw_quantity:.8f} rounds to 0 after precision adjustment."

    action_desc = f"Limit {side} {position_pct}% at {price:.2f}, {actual_leverage}x leverage"
    approved = await _check_approval(deps, f"limit_{side}", action_desc, position_pct, actual_leverage)
    if not approved:
        return "Limit order rejected by human approval."

    order_side = "buy" if side == "long" else "sell"
    order = await deps.exchange.create_order(
        symbol=deps.symbol, side=order_side, order_type="limit",
        amount=quantity, price=price,
    )

    await _record_action(
        deps, action="place_limit_order", order_id=order.id,
        side=side, price=price, reasoning=reasoning,
    )

    return f"Limit order placed: {side} {quantity:.6f} @ {price:.2f}, {actual_leverage}x | ID: {order.id}"
```

### OKX 侧

OKX 的 `create_order` 已经通过 ccxt 透传任意 order_type（`okx.py:333-344`），无需修改。ccxt 会将 `"limit"` 正确映射为 OKX 的限价单 API。

---

## 改动 4: 条件单与 pending 市价单的交互

### 问题：无仓位 + pending 开仓单时设止损

市价单异步化后，Agent 开仓后立即设止损会失败（仓位还没建立）。这是**预期行为**——与 OKX 一致。

但需要明确拒绝，不排队：

**当前 `_create_conditional_order`（`simulated.py:318-340`）已有检查**：
```python
pos = self._positions.get(symbol)
if pos is None:
    raise ValueError("Cannot create conditional order without a position")
```

**无需修改**——Agent 调用 `set_stop_loss` → `fetch_positions` 返回空 → 返回 "No open position to set stop loss on."（`tools_execution.py:118-119`）。Agent 应在收到 fill callback 唤醒后的下一个 cycle 中设止损。

### 持久化：pending 市价单的 DB 表达

当前 `SimOrder` 表已有 `status` 字段，pending 市价单使用 `status="open"` 存储。`_restore_state` 恢复时将 `status="open"` 的记录加载为 `_PendingOrder`。

需要扩展 `SimOrder` 存储 `frozen_margin` 和 `leverage`：

```python
# models.py - SimOrder 扩展
class SimOrder(Base):
    # ... 现有字段 ...
    frozen_margin: Mapped[float] = mapped_column(Float, default=0.0)      # 新增
    leverage: Mapped[int] = mapped_column(Integer, default=1)              # 新增
```

`_restore_state` 恢复 pending 市价单/限价单时需读取这两个字段：

```python
# _restore_state 更新（line 618-628）：
for o in result.scalars().all():
    self._pending_orders.append(_PendingOrder(
        id=o.order_id, symbol=o.symbol, side=o.side,
        position_side=o.position_side, order_type=o.order_type,
        amount=o.amount, trigger_price=o.trigger_price,
        frozen_margin=o.frozen_margin,   # 新增
        leverage=o.leverage,             # 新增
    ))
```

`_persist_state` 中 upsert pending orders（line 730-741）需写入新字段：

```python
# 步骤 3d: upsert pending orders
stmt = sqlite_insert(SimOrder).values(
    # ... 现有字段 ...
    frozen_margin=pending.frozen_margin,  # 新增
    leverage=pending.leverage,            # 新增
)
```

`_persist_state` 中 upsert balance（line 650-664）需写入 `frozen_usdt`：

```python
stmt = sqlite_insert(SimBalance).values(
    session_id=self._session_id,
    free_usdt=self._free_usdt,
    used_usdt=self._used_usdt,
    frozen_usdt=self._frozen_usdt,  # 新增
    updated_at=now,
)
```

`_restore_state` 恢复 balance 时需读取 `frozen_usdt`：

```python
if bal:
    self._free_usdt = bal.free_usdt
    self._used_usdt = bal.used_usdt
    self._frozen_usdt = bal.frozen_usdt  # 新增
```

### _frozen_usdt 新增属性

SimExchange 新增内部状态：

```python
# __init__ 中：
self._frozen_usdt: float = 0.0   # 市价单/限价单冻结的保证金
```

**`_init_state`（`simulated.py:584-589`）也必须重置 `_frozen_usdt`**，否则新 session 可能继承旧值：

```python
async def _init_state(self, initial_balance: float) -> None:
    self._free_usdt = initial_balance
    self._used_usdt = 0.0
    self._frozen_usdt = 0.0    # 新增
    self._positions = {}
    self._pending_orders = []
    self._leverage = {}
```

`fetch_balance` 中的语义：
- `free_usdt`：可用余额（已扣除冻结）
- `used_usdt`：已占用保证金（已成交仓位）
- `frozen_usdt`：挂单冻结（pending market/limit 订单），内部状态

**`fetch_balance` 的 `total_usdt` 必须包含 `_frozen_usdt`**，否则资产总额凭空缩水：

```python
# 当前（错误，引入 frozen 后）:
total_usdt = self._free_usdt + self._used_usdt + unrealized

# 修正后:
total_usdt = self._free_usdt + self._used_usdt + self._frozen_usdt + unrealized
```

Balance dataclass 本身不新增字段（Agent 不需要知道冻结细节），`_frozen_usdt` 仅影响 `total_usdt` 的计算。

持久化：`_frozen_usdt` 需存入 `SimBalance` 表。

```python
# models.py - SimBalance 扩展
class SimBalance(Base):
    # ... 现有字段 ...
    frozen_usdt: Mapped[float] = mapped_column(Float, default=0.0)   # 新增
```

---

## 测试影响

### test_simulated_exchange.py — 需适配的测试

以下测试调用 `create_order("market")` 后**立即检查仓位/余额状态**，异步化后需要在 `create_order` 和状态检查之间插入一次 `_process_tick` 撮合：

| 测试名 | 改法 |
|--------|------|
| `test_market_buy_opens_long` | 在 `create_order` 后调用 `_process_tick(ticker)` 再检查仓位/余额 |
| `test_market_sell_opens_short` | 同上 |
| `test_market_close_long` | 开仓 → tick → 平仓 → tick → 检查 |
| `test_market_close_clamps_amount` | 同上 |
| `test_add_to_position` | 第一笔 → tick → 第二笔 → tick → 检查 |
| `test_add_position_leverage_mismatch` | 第一笔 → tick → 改杠杆 → 第二笔应失败 |
| `test_set_leverage_rejects_with_position` | 开仓 → tick → 改杠杆应失败 |
| `test_partial_close_position` | 开仓 → tick → 部分平仓 → tick → 检查 |
| `test_stop_order_creation` | 开仓 → tick → 设止损 |
| `test_stop_order_without_price` | 开仓 → tick → 设止损（缺 price） |
| `test_conditional_order_forces_full_amount` | 开仓 → tick → 设止损检查 amount |
| `test_cancel_order` | 开仓 → tick → 设止损 → cancel |
| `test_market_close_fill_event_has_pnl` | 开仓 → tick → 平仓 → tick → 检查 fill |
| `test_persist_and_restore` | 开仓 → tick → persist → restore → 检查 |
| `test_fetch_closed_orders_from_db` | 开仓 → tick → 检查 DB |

### test_simulated_exchange.py — 需修改语义的测试

| 测试名 | 改法 |
|--------|------|
| `test_market_order_queues_fill_event` | 删除或重写：不再有 `_pending_fills`。改为验证 `_process_tick` 后 `_fill_callback` 被调用 |
| `test_market_order_unknown_type` | 现在 "limit" 不再是 unknown type。改为测试真正的 unknown type（如 "foobar"） |

### test_simulated_exchange.py — 需插入 tick 的"条件单/清算"测试

以下测试虽然包含 `_process_tick` 调用，但模式是 `market → stop/take_profit → tick`，异步化后 `stop`/`take_profit` 创建时仓位不存在（市价单还在 pending），条件单创建会失败。需要在 market 和 conditional 之间插入一个 tick 先撮合市价单：

| 测试名 | 改法 |
|--------|------|
| `test_should_trigger_stop_long` | market buy → **tick（撮合开仓）** → set stop → tick（触发止损） |
| `test_should_trigger_stop_short` | 同上（sell 方向） |
| `test_should_trigger_take_profit_long` | market buy → **tick** → set take_profit → tick（触发止盈） |
| `test_should_trigger_take_profit_short` | 同上（sell 方向） |
| `test_no_trigger_when_price_above_stop` | market buy → **tick** → set stop → tick（不触发） |
| `test_liquidation_triggers_before_stop` | market buy → **tick** → set stop → tick（清算优先于止损） |
| `test_fill_event_carries_pnl_on_stop` | market buy → **tick** → set stop → tick（检查 fill PnL） |

以下清算测试有额外问题：异步化后市价单在 tick 时撮合，入场价变为 tick 价格。需要拆分为两个 tick（正常价格 tick 撮合开仓 + 极端价格 tick 触发清算）：

| 测试名 | 改法 |
|--------|------|
| `test_liquidation_short` | market sell → **tick@正常价（撮合开仓）** → tick@极端价（触发清算） |
| `test_force_liquidate_fill_event_has_pnl` | 同上；另外删除 `drain_pending_fills()` 调用，改为检查 `_fill_callback` |

### test_simulated_exchange.py — 不受影响的测试

以下测试不涉及市价单，无需修改：

- 所有 `test_fetch_balance_*`（不涉及市价单）
- 所有 `test_set_leverage_*`（除 `rejects_with_position`）
- 所有 `test_amount_to_precision*`
- 所有 alert 相关测试（不涉及市价单）
- `test_cancel_nonexistent_order`（不涉及市价单）
- `test_market_order_insufficient_balance`（余额验证仍在 create_order 时执行）
- `test_market_order_wrong_symbol`（符号验证不变）
- `test_market_order_invalid_amount`（amount 验证不变）
- `test_stop_order_without_position`（仍在 create_order 时拒绝）

### test_exchange.py

| 测试名 | 改法 |
|--------|------|
| `test_base_exchange_drain_pending_fills` | 删除 |

### tests/test_tools.py

Agent tool 测试使用 **mock exchange**（`AsyncMock`），不走 SimExchange 真实逻辑。**不受影响**。

### 新增测试

| 测试 | 描述 |
|------|------|
| `test_market_order_returns_open_status` | `create_order("market")` 返回 `status="open"`, `price=None` |
| `test_market_order_fills_on_next_tick` | 提交后无仓位，tick 后有仓位，fill_callback 被调用 |
| `test_market_close_fills_on_next_tick` | 平仓提交后仓位仍在，tick 后仓位消失 |
| `test_market_order_frozen_balance` | 提交后 `_free_usdt` 减少冻结量，tick 撮合后转为 `_used_usdt` |
| `test_frozen_balance_diff_refund` | 提交时 ask=100，撮合时 ask=99，差额退还 |
| `test_limit_order_creation` | `create_order("limit", price=X)` 返回 `status="open"`，冻结保证金 |
| `test_limit_order_fills_when_price_reached` | 买入限价 ask 到达时撮合 |
| `test_limit_order_not_filled_above_price` | 买入限价 ask 高于限价时不撮合 |
| `test_limit_order_cancel_unfreezes` | 取消限价单退还冻结保证金 |
| `test_duplicate_open_rejected` | pending 开仓时再次 open_position 被拒绝 |
| `test_duplicate_close_rejected` | pending 平仓时再次 close_position 被拒绝 |
| `test_has_pending_market_order` | `has_pending_market_order` 查询逻辑 |
| `test_pending_market_order_persisted_and_restored` | 市价单 pending 状态跨重启恢复 |
| `test_limit_order_persisted_and_restored` | 限价单 pending 状态跨重启恢复 |
| `test_close_market_order_minimal_freeze` | 全仓开仓后平仓不因余额不足被拒绝（平仓仅冻结 fee） |
| `test_orphan_cleanup_preserves_market_open` | 止损平仓后，pending market/limit 开仓单不被误删 |
| `test_orphan_cleanup_removes_market_close` | 清算后，pending market close 被清理且保证金解冻 |
| `test_orphan_cleanup_unfreezes_margin` | 孤儿单清理时冻结保证金正确退还 |
| `test_fetch_balance_total_includes_frozen` | `total_usdt = free + used + frozen + unrealized` |
| `test_limit_order_reverse_position_rejected` | 有 long 仓位时，limit sell 被拒绝 |
| `test_limit_order_leverage_matches_position` | 有仓位时限价单强制使用仓位杠杆 |
| `test_limit_fill_cancelled_on_reverse_position` | 限价买单填充时发现已有 short 仓位 → 取消并解冻 |
| `test_cancel_market_order_rejected` | `cancel_order` 拒绝取消市价单 |
| `test_frozen_buffer_covers_price_movement` | 冻结含 0.2% 缓冲，撮合后 free_usdt ≥ 0 |
| `test_frozen_extreme_clamp` | 极端价格不利时 free_usdt clamp 到 0，差额追加到 used_usdt |
| `test_limit_fill_cancelled_on_leverage_mismatch` | 限价单填充时仓位杠杆不一致 → 取消并解冻 |
| `test_fill_market_close_position_gone` | 平仓市价单填充时仓位已被清算 → 取消并解冻 |
| `test_fill_market_close_clamps_amount` | 平仓填充 amount 大于仓位 contracts → clamped |

---

## 全量文件变化汇总

| 文件 | 改动 |
|------|------|
| **SimExchange 引擎** | |
| `src/integrations/exchange/simulated.py` | 核心改造：`create_order` 市价单→pending（区分开仓/平仓冻结逻辑）；新增 `_frozen_usdt`；新增 limit 支持（含反向仓位校验）；`_process_tick` 新增市价/限价撮合；新增 `_execute_market_fill`/`_execute_limit_fill`/`_fill_market_open`/`_fill_market_close`/`_is_close_order`；新增 `has_pending_market_order`；`_cancel_orphaned_orders` 改造（保留 market/limit 开仓单，解冻孤儿单冻结保证金）；`cancel_order` 解冻保证金；`_PendingOrder` 扩展 `frozen_margin`/`leverage`；`fetch_balance` total 包含 frozen；删除 `_pending_fills`/`drain_pending_fills`；`_restore_state`/`_persist_state` 适配新字段 |
| **Exchange 基类** | |
| `src/integrations/exchange/base.py` | 删除 `drain_pending_fills`；新增 `has_pending_market_order` 默认实现 |
| **OKX** | |
| `src/integrations/exchange/okx.py` | 无改动（`create_order` 已透传 ccxt，limit 自动支持） |
| **Agent Tools** | |
| `src/agent/tools_execution.py` | `open_position` 新增 pending 检查；`close_position` 新增 pending 检查；新增 `place_limit_order` |
| `src/agent/trader.py` | 注册 `place_limit_order` tool |
| `src/agent/persona.py` | 系统 prompt 新增限价单引导 |
| **App** | |
| `src/cli/app.py` | 删除 `on_tick` finally 中的 `drain_pending_fills` 逻辑 |
| **Storage** | |
| `src/storage/models.py` | `SimOrder` 新增 `frozen_margin`/`leverage`；`SimBalance` 新增 `frozen_usdt` |
| **Tests** | |
| `tests/test_simulated_exchange.py` | 24 个测试插入 tick 撮合步骤（含 2 个清算测试拆分双 tick）；2 个测试重写语义；新增 30 个测试 |
| `tests/test_exchange.py` | 删除 `test_base_exchange_drain_pending_fills` |

---

## 实施顺序

```
PR #1: 市价单异步化 + drain_pending_fills 清理
  SimExchange: _PendingOrder 扩展, _frozen_usdt, create_order 改 pending
  SimExchange: _process_tick 新增市价单撮合
  SimExchange: 删除 _pending_fills / drain_pending_fills
  BaseExchange: 删除 drain_pending_fills
  app.py: 删除 on_tick finally drain 逻辑
  models.py: SimOrder/SimBalance 新增字段
  适配全部受影响测试 + 新增市价单异步测试

PR #2: 重复下单防护
  BaseExchange: 新增 has_pending_market_order
  SimExchange: 实现 has_pending_market_order
  tools_execution.py: open_position / close_position 防护
  新增防护测试

PR #3: 限价单
  SimExchange: create_order 支持 "limit"
  SimExchange: _should_trigger 新增 limit 判断
  SimExchange: _execute_limit_fill
  SimExchange: cancel_order 解冻
  tools_execution.py: 新增 place_limit_order
  trader.py: 注册 tool
  persona.py: prompt 引导
  新增限价单测试
```

PR #1 改动最大但最核心（不完成则 PR #2/#3 无意义）。PR #2 和 PR #3 可并行开发。

---

## 风险与迁移

### 旧 session 兼容

系统尚未投入生产使用，不存在需要迁移的用户数据。DB schema 变更（`SimOrder` 新增 `frozen_margin`/`leverage`，`SimBalance` 新增 `frozen_usdt`）通过 SQLAlchemy 的 `default=0.0` / `default=1` 处理，旧记录读取时回退到默认值，无需 DB migration。

如有残留的旧 session 恢复：
- `_restore_state` 读取 `frozen_margin=0.0`、`leverage=1`——旧的 pending 条件单（stop/take_profit）本来就没有冻结保证金，行为不变
- `_restore_state` 读取 `frozen_usdt=0.0`——旧 session 没有 pending 市价单/限价单，余额计算不受影响

### Agent prompt 兼容

市价单行为变化对 Agent 的影响：
- Agent 系统 prompt 中已有 "You will be notified when filled" 引导
- `open_position` 返回值已包含 "You will be notified when filled"
- 主要风险：Agent 可能仍尝试在同一 cycle 设止损 → tool 返回 "No open position"，Agent 需学习等待 fill callback

这是**预期行为变化**，不需要特殊处理——Agent 在 fill callback 唤醒后的下一个 cycle 会看到仓位并设止损。

### 测试回归风险

本次改动涉及 26 个测试的模式变更（24 个插入 tick 撮合步骤 + 2 个语义重写）。风险点：
- 测试适配不完整导致误判"通过"（如忘记在某个 assert 前插入 tick）
- 新增的 `_frozen_usdt` 账务逻辑在边界场景（如连续开仓+平仓+限价单交叉）可能出现精度累积误差

缓解措施：PR #1 必须在所有现有测试通过后才能合并；新增测试覆盖 frozen 账务的完整生命周期。
