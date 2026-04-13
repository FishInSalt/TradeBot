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

### 设计目标

Agent 在模拟和实盘上的行为模式完全一致：
1. 开仓后都要等 fill 通知再设止损
2. 限价单在两种环境都可用
3. 所有 fill 走统一的异步回调路径

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
       ├─ 验证：余额足够（预检，非最终扣款）
       ├─ 冻结保证金：_free_usdt -= estimated_margin + fee
       │                _frozen_usdt += estimated_margin + fee
       ├─ 创建 _PendingOrder(order_type="market", ...)
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

市价单不再同步扣款，但需要防止超额下单（两笔市价单同时提交，余额只够一笔）：

```python
# create_order("market") 时：
estimated_price = ticker.ask if side == "buy" else ticker.bid
leverage = self._leverage.get(symbol, 1)
estimated_margin = (estimated_price * amount) / leverage
estimated_fee = estimated_price * amount * self._fee_rate
frozen = estimated_margin + estimated_fee

if self._free_usdt < frozen:
    raise ValueError(f"Insufficient balance: need {frozen:.2f}, have {self._free_usdt:.2f}")

self._free_usdt -= frozen
self._frozen_usdt += frozen
```

撮合时以实际成交价重新计算，差额退还或追扣：

```python
# _process_tick 撮合时：
actual_margin = (fill_price * amount) / leverage
actual_fee = fill_price * amount * self._fee_rate
actual_cost = actual_margin + actual_fee
diff = frozen - actual_cost

self._frozen_usdt -= frozen
self._used_usdt += actual_margin
self._free_usdt += diff  # 正数=退还，负数=追扣
```

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
def _execute_market_fill(self, order: _PendingOrder, ticker: Ticker) -> FillEvent:
    """撮合 pending 市价单。与 _execute_fill 类似但处理开仓/平仓两种情况。"""
    is_close = self._is_close_order(order)
    if is_close:
        return self._fill_market_close(order, ticker)
    else:
        return self._fill_market_open(order, ticker)
```

**开仓撮合** (`_fill_market_open`)：
- 用当前 tick 价格计算实际 margin/fee
- 解冻 `_frozen_usdt`，扣 `_used_usdt`，退差额到 `_free_usdt`
- 创建或合并 `_positions[symbol]`
- 返回 FillEvent(trigger_reason="market", pnl=None)

**平仓撮合** (`_fill_market_close`)：
- 用当前 tick 价格计算 PnL
- 调用 `_close_position_core()` 复用现有平仓逻辑
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
    """Check for pending market orders. Default: False (real exchanges handle this server-side)."""
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
- 限价单是**开仓**操作，不需要已有仓位

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
    # 冻结保证金（与市价单相同逻辑）
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
def _execute_limit_fill(self, order: _PendingOrder, ticker: Ticker) -> FillEvent:
    """限价单撮合。成交价 = 限价（price improvement 首版不考虑）。"""
    fill_price = order.trigger_price  # 限价单以限定价成交
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
        triggered.append(fill)
        filled_order_ids.append(order.id)

    # 1. 清算检查
    # ...（现有）

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
            triggered.append(fill)
            filled_order_ids.append(order.id)
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

        # 解冻保证金（市价单和限价单都有冻结）
        if order.frozen_margin > 0:
            self._frozen_usdt -= order.frozen_margin
            self._free_usdt += order.frozen_margin

        self._pending_orders = [o for o in self._pending_orders if o.id != order_id]
        # ... persist
```

### 新 Agent Tool: place_limit_order

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

    balance = await deps.exchange.fetch_balance()
    usdt_amount = balance.free_usdt * (position_pct / 100.0)
    raw_quantity = (usdt_amount * leverage) / price
    quantity = deps.exchange.amount_to_precision(deps.symbol, raw_quantity)
    if quantity <= 0:
        return f"Position too small: {raw_quantity:.8f} rounds to 0 after precision adjustment."

    action_desc = f"Limit {side} {position_pct}% at {price:.2f}, {leverage}x leverage"
    approved = await _check_approval(deps, f"limit_{side}", action_desc, position_pct, leverage)
    if not approved:
        return "Limit order rejected by human approval."

    await deps.exchange.set_leverage(deps.symbol, leverage)
    order_side = "buy" if side == "long" else "sell"
    order = await deps.exchange.create_order(
        symbol=deps.symbol, side=order_side, order_type="limit",
        amount=quantity, price=price,
    )

    await _record_action(
        deps, action="place_limit_order", order_id=order.id,
        side=side, price=price, reasoning=reasoning,
    )

    return f"Limit order placed: {side} {quantity:.6f} @ {price:.2f}, {leverage}x | ID: {order.id}"
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

`_restore_state` 恢复 pending 市价单/限价单时需读取这两个字段。

### _frozen_usdt 新增属性

SimExchange 新增内部状态：

```python
self._frozen_usdt: float = 0.0   # 市价单/限价单冻结的保证金
```

`fetch_balance` 中的语义：
- `free_usdt`：可用余额（已扣除冻结）
- `used_usdt`：已占用保证金（已成交仓位）
- `frozen_usdt`：挂单冻结（pending market/limit 订单）

注：`_frozen_usdt` 仅为内部账务追踪，不影响 Balance dataclass（Agent 看到的 `free_usdt` 已经扣除冻结）。

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

### test_simulated_exchange.py — 不受影响的测试

以下测试已经使用 `_process_tick` 或不涉及市价单，无需修改：

- `test_should_trigger_stop_long/short`（已有 tick 撮合）
- `test_should_trigger_take_profit_long/short`（已有 tick 撮合）
- `test_no_trigger_when_price_above_stop`（已有 tick 撮合）
- `test_liquidation_triggers_before_stop`（已有 tick 撮合）
- `test_liquidation_short`（已有 tick 撮合）
- `test_fill_event_carries_pnl_on_stop`（已有 tick 撮合）
- `test_force_liquidate_fill_event_has_pnl`（已有 tick 撮合）
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

---

## 全量文件变化汇总

| 文件 | 改动 |
|------|------|
| **SimExchange 引擎** | |
| `src/integrations/exchange/simulated.py` | 核心改造：`create_order` 市价单→pending；新增 `_frozen_usdt`；新增 limit 支持；`_process_tick` 新增市价/限价撮合；新增 `_execute_market_fill`/`_execute_limit_fill`/`_fill_market_open`/`_fill_market_close`；新增 `has_pending_market_order`；`cancel_order` 解冻保证金；`_PendingOrder` 扩展 `frozen_margin`/`leverage`；删除 `_pending_fills`/`drain_pending_fills`；`_restore_state`/`_persist_state` 适配新字段 |
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
| `tests/test_simulated_exchange.py` | 15 个测试插入 tick 撮合步骤；2 个测试重写语义；新增 14 个测试 |
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
