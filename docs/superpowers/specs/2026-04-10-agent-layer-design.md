# Agent 层改造 — 设计文档

## 背景

Phase 1a 核心基础设施和 SimulatedExchange 已完成（PR #1 已合并）。本轮迭代目标是**改造 Agent 层，完整模拟真实世界交易员的决策流程**，并通过 SimulatedExchange 进行端到端联调验证。

### 核心问题

现有 Agent 层存在三个主要问题：

1. **TradeRecord 设计不合理** — 采用两步写入的生命周期模式（open → close），与交易所数据职责重叠，且无法覆盖条件单自动触发（止损/止盈/强平）的场景
2. **交易日志缺失** — `get_trade_history` 只返回 MemoryEntry，agent 无法回顾自己的交易决策时间线
3. **决策流程不完整** — 订单成交后没有 agent 复盘环节，`open_position` 将开仓和设 SL/TP 捆绑在一次调用中

### 设计原则

沿用 SimulatedExchange 设计文档确立的职责边界：

| 职责 | 负责方 | 说明 |
|---|---|---|
| 实时状态（余额/持仓/行情/挂单） | 交易所 | Agent 通过 BaseExchange API 查询 |
| 订单处理、撮合、订单历史 | 交易所 | 记录所有订单的完整生命周期 |
| 交易决策和推理 | Agent | 通过 TradeAction 记录每次操作及理由 |
| 经验教训和规律认知 | Agent | 通过 MemoryEntry 记录长期记忆 |

**交易所管"发生了什么"（客观事实），Agent 管"我怎么想的"（主观决策）。**

## 交易员决策流程

### 真实世界的交易员工作循环

```
触发唤醒（定时看盘 / 订单成交 / 价格警报）
    ↓
信息收集
  ├── 发生了什么？（触发事件详情）
  ├── 当前行情（价格、技术指标）
  ├── 当前持仓（方向、数量、浮盈浮亏）
  ├── 账户状态（可用资金、保证金）
  ├── 挂单状态（未成交的条件单）
  ├── 交易日志（最近操作的决策时间线）
  └── 经验教训（历史中积累的认知）
    ↓
分析与决策
  ├── 技术面分析
  ├── 仓位评估
  ├── 风险评估
  └── 最终决策：开仓/平仓/调整SL-TP/观望
    ↓
执行
    ↓
记录（交易日志 + 经验反思）
    ↓
等待下一个触发
```

### 系统实现的完整决策循环

**关键设计：每次订单成交（无论是 agent 主动下单还是条件单自动触发）都会产生 FillEvent，唤醒 agent 进行新一轮思考。** 这保证了：

- agent 主动开仓后，会被唤醒确认成交，并基于**实际成交价**设定 SL/TP
- 条件单触发后，agent 会被唤醒复盘结果，决定下一步操作
- 所有成交事件走同一个流程：成交 → 唤醒 → 思考 → 决策

#### 示例：完整的开仓到平仓流程

**Cycle 1 — 定时触发，agent 决策开仓：**

```
[15:00] Scheduler 定时触发

Agent:
  1. get_market_data → BTC 60200, RSI 28, MA20 上穿 MA50
  2. get_account_balance → 可用 100 USDT
  3. get_position → 无持仓
  4. get_open_orders → 无挂单
  5. get_trade_journal → 回顾最近交易记录
  6. get_memories → 回忆相关经验

  分析 → 决策：RSI 超卖 + 金叉，做多

  7. open_position(side="long", position_pct=30, leverage=3)
     → 市价单成交 @ 60200 → FillEvent
     → TradeAction 自动记录
```

**Cycle 2 — 开仓成交触发，agent 设定风控：**

```
[15:00] FillEvent: 市价单成交

Agent:
  1. 确认成交详情：开多 0.0075 BTC @ 60200
  2. get_position → 确认持仓
  3. 基于实际成交价设定风控：
     SL = 60200 × 0.97 = 58394
     TP = 60200 × 1.06 = 63812

  4. set_stop_loss(price=58394)
  5. set_take_profit(price=63812)
     → TradeAction 记录

  决策：风控已设置，观望等待
```

**Cycle 3 — 定时触发，持仓中检查：**

```
[15:15] Scheduler 定时触发

Agent:
  1. get_position → 浮盈 +15 USDT
  2. get_open_orders → SL @ 58394, TP @ 63812
  3. get_market_data → 趋势健康

  决策：上移止损到成本价保本
  4. set_stop_loss(price=60200)
     → TradeAction 记录
```

**Cycle 4 — 止损触发，agent 复盘：**

```
[18:30] FillEvent: 止损单成交 @ 58394

  → TradeAction 自动写入（exchange 触发）

Agent:
  1. 确认：止损被打，仓位已清
  2. get_trade_journal → 回顾完整决策时间线
  3. 复盘分析

  4. save_memory("lesson", "RSI超卖不代表见底，需确认趋势", 0.8)

  决策：观望
```

## 数据模型

### 新增：TradeAction（替代 TradeRecord）

采用 **append-only 事件模型**，每次操作一条独立记录，不需要两步更新。

```python
class TradeAction(Base):
    """Agent 的交易操作日志 — 每次操作一条记录。"""

    __tablename__ = "trade_actions"

    id: Mapped[int] = mapped_column(primary_key=True)
    session_id: Mapped[str] = mapped_column(String(36), ForeignKey("sessions.id"), index=True)
    action: Mapped[str] = mapped_column(String(30))
    # agent 主动操作: open_position, close_position, set_stop_loss, set_take_profit, adjust_leverage
    # 系统自动记录: order_filled (条件单成交), order_expired (订单过期)
    order_id: Mapped[str | None] = mapped_column(String(36), nullable=True)  # 关联交易所订单
    symbol: Mapped[str] = mapped_column(String(50))
    side: Mapped[str | None] = mapped_column(String(10), nullable=True)      # long / short
    reasoning: Mapped[str | None] = mapped_column(Text, nullable=True)       # agent 的决策理由
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)
```

**设计要点：**

- **append-only** — 不修改已有记录，每次操作追加一条
- **order_id 关联** — 通过 order_id 可反查交易所获取成交价、手续费等客观数据
- **session_id 隔离** — 未来多 agent 共享同一交易所账户时，每个 agent 只看到自己的操作
- **reasoning 字段** — agent 主动操作时记录决策理由；系统自动记录时填写触发原因

### 废弃：TradeRecord

删除 `TradeRecord` 模型及所有相关的两步写入代码（`_record_trade_open`、`_update_trade_closed`）。

### 保留：DecisionLog

DecisionLog 与 TradeAction 职责不同：

| | DecisionLog | TradeAction |
|---|---|---|
| 粒度 | 每个 agent 决策周期一条 | 每次交易操作一条 |
| 内容 | 周期元信息（trigger_type, tokens_used, model_used） | 具体操作（开仓/平仓/设SL） |
| 关系 | 一个 cycle 可能产生 0~N 条 TradeAction | 每条 TradeAction 属于某个 cycle |

一个 agent 决策周期可能不产生任何交易操作（决定观望），也可能产生多条操作。两者记录不同维度的信息，保留各自独立。

## Tools 改造

### 感知类 Tools

| Tool | 变更 | 说明 |
|------|------|------|
| `get_market_data` | 不变 | 行情 + 技术指标 |
| `get_account_balance` | 不变 | 账户余额 |
| `get_position` | 不变 | 当前持仓 |
| `get_open_orders` | **新增** | 查看未成交的条件单（止损/止盈），调用 `exchange.fetch_open_orders()` |
| `get_trade_journal` | **新增** | 查看交易日志（TradeAction 时间线），通过 order_id 关联交易所数据补充成交价/手续费 |
| `get_memories` | **重命名** | 原 `get_trade_history` 改名，只返回 MemoryEntry（经验教训） |

#### get_open_orders

```python
async def get_open_orders(deps: TradingDeps) -> str:
    """查看当前未成交的挂单（止损/止盈等条件单）。"""
    orders = await deps.exchange.fetch_open_orders(deps.symbol)
    if not orders:
        return "No pending orders."
    lines = ["Pending Orders:"]
    for o in orders:
        lines.append(f"  {o.order_type.upper()} {o.side} {o.amount} @ {o.price:.2f} | ID: {o.id}")
    return "\n".join(lines)
```

#### get_trade_journal

```python
async def get_trade_journal(deps: TradingDeps, limit: int = 20) -> str:
    """查看交易日志 — agent 的操作决策时间线，包含成交详情。"""
    # 1. 从 DB 获取 TradeAction 记录
    actions = await _fetch_trade_actions(deps.db_engine, deps.session_id, limit)
    if not actions:
        return "No trade journal entries yet."

    # 2. 收集有 order_id 的记录，批量从交易所获取订单详情
    order_ids = [a.order_id for a in actions if a.order_id]
    order_details = {}
    for oid in order_ids:
        try:
            order = await deps.exchange.fetch_order(oid, deps.symbol)
            order_details[oid] = order
        except Exception:
            pass  # 订单查询失败不影响日志展示

    # 3. 格式化输出
    lines = ["=== Trade Journal ==="]
    for a in actions:
        ts = a.created_at.strftime("%m-%d %H:%M")
        line = f"[{ts}] {a.action}"
        if a.side:
            line += f" ({a.side})"

        # 补充交易所数据
        if a.order_id and a.order_id in order_details:
            od = order_details[a.order_id]
            if od.price:
                line += f" @ {od.price:.2f}"
            if od.fee:
                line += f", fee={od.fee:.4f}"
            line += f" [{od.status}]"

        if a.reasoning:
            line += f"\n  Reasoning: {a.reasoning}"
        lines.append(line)

    return "\n".join(lines)
```

### 执行类 Tools

| Tool | 变更 | 说明 |
|------|------|------|
| `open_position` | **简化** | 去掉 `stop_loss_price` 和 `take_profit_price` 参数，只下市价单 |
| `close_position` | **简化** | 去掉 TradeRecord 更新逻辑 |
| `set_stop_loss` | 不变（功能），改写入 TradeAction | |
| `set_take_profit` | 不变（功能），改写入 TradeAction | |
| `adjust_leverage` | 不变（功能），改写入 TradeAction | |

#### open_position 简化后

```python
async def open_position(
    deps: TradingDeps,
    side: str,
    position_pct: float,
    leverage: int,
) -> str:
    """Open a new position. side='long' or 'short'. position_pct=% of free balance."""
    balance = await deps.exchange.fetch_balance()
    ticker = await deps.market_data.get_ticker(deps.symbol)
    usdt_amount = balance.free_usdt * (position_pct / 100.0)
    raw_quantity = (usdt_amount * leverage) / ticker.last
    quantity = deps.exchange.amount_to_precision(deps.symbol, raw_quantity)
    if quantity <= 0:
        return f"Position too small: {raw_quantity:.8f} rounds to 0 after precision adjustment."

    reasoning = f"Open {side} {position_pct}% at ~{ticker.last:.2f}, {leverage}x leverage"
    approved = await _check_approval(
        deps, f"open_{side}", reasoning, position_pct, leverage
    )
    if not approved:
        return "Trade rejected by human approval."

    await deps.exchange.set_leverage(deps.symbol, leverage)
    order_side = "buy" if side == "long" else "sell"
    order = await deps.exchange.create_order(
        symbol=deps.symbol, side=order_side, order_type="market", amount=quantity
    )

    # 写入 TradeAction
    await _record_action(
        deps, action="open_position", order_id=order.id,
        side=side, reasoning=reasoning,
    )

    return (
        f"Position opened:\n"
        f"  Side: {side} | Quantity: {quantity:.6f} | Leverage: {leverage}x\n"
        f"  Entry: ~{ticker.last:.2f} | Order: {order.id} ({order.status})"
    )
```

### 记忆类 Tools

| Tool | 变更 |
|------|------|
| `save_memory` | 不变 |

## FillEvent 处理

### 核心原则

**所有订单成交都产生 FillEvent，所有 FillEvent 都唤醒 agent。** 无论是 agent 主动下的市价单，还是条件单自动触发，走同一条路径：成交 → FillEvent → 写入 TradeAction → 唤醒 agent。

### SimulatedExchange 改造

当前 SimulatedExchange 只在 `_process_tick()`（条件单/强平）中调用 `_fill_callback`，市价单成交不产生 FillEvent。需要改造 `create_order()` 方法，在市价单成交后也触发 FillEvent callback：

```python
async def create_order(self, symbol, side, order_type, amount, price=None):
    ...
    if order_type == "market":
        order, position_side = self._execute_market_order(symbol, side, amount)
        if self._db_engine:
            await self._persist_state(new_orders=[(order, position_side)])
        # 新增：市价单成交也触发 FillEvent
        if self._fill_callback:
            fill_event = FillEvent(
                order_id=order.id, symbol=symbol, side=order.side,
                position_side=position_side,
                trigger_reason="market",
                fill_price=order.price, amount=order.amount,
                fee=order.fee,
                timestamp=int(datetime.now(timezone.utc).timestamp() * 1000),
            )
            await self._fill_callback(fill_event)
        return order
    ...
```

**时序兼容：** 市价单的 FillEvent 在 `create_order()` 内部触发，此时 agent 仍在当前决策周期中。FillEvent handler 调用 `scheduler.trigger()` 仅设置 `_pending_trigger` 标志和 context，不会立即启动新周期。当前周期结束后，Scheduler 主循环检测到 pending trigger，才启动下一个周期。现有 Scheduler 设计已支持此流程，无需改造。

### FillEvent Handler

当前 `app.py` 中的 fill handler 是空实现（`pass`）。改造后：

```python
def _create_fill_handler(sched, engine, session_id, symbol):
    async def handle_fill(event: FillEvent):
        try:
            await _record_action_from_fill(engine, session_id, event)
        finally:
            await sched.trigger("conditional", context=event)
    return handle_fill
```

### TradeAction 去重

由于 agent 的执行 tool（如 `open_position`）和 FillEvent handler 都会写 TradeAction，市价单会产生两条记录。这两条记录承载不同信息，**不需要去重**：

- **Tool 写入的 TradeAction**：`action="open_position"`, `reasoning="RSI超卖+金叉"`（记录 agent 的决策理由）
- **FillEvent 写入的 TradeAction**：`action="order_filled"`, `reasoning="(exchange: market order filled @ 60200)"`（记录成交事实）

两条记录通过相同的 `order_id` 关联，共同构成完整的决策 → 执行时间线。在 `get_trade_journal` 展示时可以合并同 order_id 的记录。

```python
async def _record_action_from_fill(engine, session_id, event: FillEvent):
    """将 FillEvent 记录为 TradeAction。"""
    async with get_session(engine) as session:
        session.add(TradeAction(
            session_id=session_id,
            action="order_filled",
            order_id=event.order_id,
            symbol=event.symbol,
            side=event.position_side,
            reasoning=f"(exchange: {event.trigger_reason} order filled @ {event.fill_price:.2f})",
        ))
        await session.commit()
```

## MetricsService 改造

### 当前问题

`MetricsService.compute_from_trades()` 依赖 `TradeRecord` 的 `status="closed"` 记录计算指标。废弃 TradeRecord 后需要新的数据源。

### 改造方案

MetricsService 从交易所的 `fetch_closed_orders()` 获取已成交订单数据，结合 agent 的 TradeAction 日志（用于确定哪些订单属于当前 agent session）计算指标。

```python
class MetricsService:
    async def compute(self, exchange, symbol, engine, session_id, initial_balance):
        # 1. 获取当前 agent 的所有 order_id
        agent_order_ids = await _get_agent_order_ids(engine, session_id)

        # 2. 从交易所获取已成交订单
        closed_orders = await exchange.fetch_closed_orders(symbol)

        # 3. 过滤出属于当前 agent 的订单
        my_orders = [o for o in closed_orders if o.id in agent_order_ids]

        # 4. 配对开仓/平仓订单，计算 PnL
        trades = _pair_orders_to_trades(my_orders)

        # 5. 计算指标
        return _compute_metrics(trades, initial_balance)
```

**接口变更：** `compute_from_trades(trade_records)` → `compute(exchange, symbol, engine, session_id, initial_balance)`。MetricsService 变为异步，需要访问交易所和数据库。

## App 层改造

### run_agent_cycle

无重大改动。Prompt 构建方式保持不变（trigger_type + FillEvent context + memories）。TradeAction 的写入由各 tool 和 FillEvent handler 负责，不在 cycle 层面处理。

### 初始 Metrics 展示

将 `select(TradeRecord)` 替换为 `MetricsService.compute()` 调用。

### TradingDeps

无需新增字段。TradeAction 的读写通过已有的 `db_engine` 和 `session_id` 完成。

## 数据库迁移

### 新增表

- `trade_actions` — TradeAction 模型

### 废弃表

- `trade_records` — 删除 TradeRecord 模型

**迁移策略：** 由于系统处于早期开发阶段，不存在生产数据需要迁移，直接删除旧表、新增新表即可。通过 `init_db()` 的 `create_all()` 自动建表。

## 不在本轮范围

| 功能 | 原因 |
|------|------|
| 限价单支持 | 新的 order_type，独立功能扩展 |
| `cancel_order` tool | 依赖限价单场景，BaseExchange 尚无 cancel_order 方法 |
| 多 agent 协作 | 架构已预留（session_id 隔离），但编排逻辑是独立工程 |
| 新闻/消息面数据 | Phase 1b 范畴 |
| 语义化记忆检索 | 当前 relevance_score 方案够用 |
| Short-term memory 生命周期 | clear_short_term() 未使用，不影响核心流程 |
