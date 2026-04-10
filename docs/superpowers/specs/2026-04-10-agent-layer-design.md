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

**关键设计：所有订单成交统一走 FillEvent 异步流程。** 无论市价单还是条件单，成交后都产生 FillEvent 唤醒 agent 进入新一轮决策周期。

**设计理由（非对齐真实交易所行为，而是基于 agent 工作模式的设计选择）：**

1. **统一决策粒度** — 每个周期只做一个核心决策（开仓 OR 设风控），职责清晰。LLM agent 天然以"周期"为单位工作，每个周期独立推理
2. **降低裸仓风险** — 如果开仓和设 SL/TP 在同一周期，agent 可能因推理遗漏而忘记设风控。拆成独立周期后，FillEvent 周期会重新收集信息（包括看到裸仓），更不容易遗漏
3. **扩展性** — 未来新增限价单时天然适配（限价单可能立即成交也可能延后成交，统一走 FillEvent 无需区分）
4. **代码简单** — 一条 FillEvent 路径处理所有成交，TradeAction 记录逻辑集中

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
     → 返回 "Order submitted: long 0.0075, order: aaa"
     → TradeAction 记录：action="open_position", reasoning="RSI超卖+金叉"
     → 内部：市价单成交，FillEvent 产生（排队等当前周期结束）

  决策完成，等待下一周期处理成交事宜
```

**Cycle 2 — 成交触发，agent 确认并设风控：**

```
[15:00] FillEvent: 市价单成交 @ 60200

  → TradeAction 自动写入：action="order_filled"
  → Scheduler conditional 触发

Agent:
  1. Prompt 注入："市价单成交 — BTC 0.0075 @ 60200"
  2. get_position → 确认持仓：多 0.0075 BTC @ 60200, 3x
  3. get_open_orders → 无挂单（裸仓，需设风控）

  基于实际成交价 60200 计算风控价位：
     SL = 60200 × 0.97 = 58394
     TP = 60200 × 1.06 = 63812

  4. set_stop_loss(price=58394)
     → TradeAction 记录：action="set_stop_loss"
  5. set_take_profit(price=63812)
     → TradeAction 记录：action="set_take_profit"

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
[18:30] BTC 跌至 58394，止损单被交易所撮合

  → FillEvent 产生（条件单异步触发）
  → TradeAction 自动写入：action="order_filled", trigger_reason="stop"
  → Scheduler conditional 触发

Agent:
  1. 确认：止损被打，仓位已清
  2. get_trade_journal → 回顾完整决策时间线：
     "15:00 开多 — RSI超卖+金叉
      15:00 成交确认 @ 60200
      15:00 设 SL@58394 / TP@63812
      15:15 上移 SL 到 60200
      18:30 止损触发 @ 58394, 亏损 -13.5 USDT"
  3. 复盘分析

  4. save_memory("lesson", "RSI超卖不代表见底，需确认趋势", 0.8)

  决策：观望
```

## 变更影响范围

### 文件变更映射

| 文件 | 变更类型 | 说明 |
|------|---------|------|
| `src/storage/models.py` | 修改 | 新增 TradeAction 模型（含 trigger_reason、pnl 字段），删除 TradeRecord 模型 |
| `src/integrations/exchange/base.py` | 修改 | FillEvent 从 simulated.py 移入（新增 pnl 字段）；BaseExchange 新增 `cancel_order` 抽象方法 |
| `src/integrations/exchange/simulated.py` | 修改 | `create_order()` 市价单触发 FillEvent；实现 `cancel_order()`；FillEvent 改从 base.py 导入 |
| `src/integrations/exchange/okx.py` | 修改 | 实现 `cancel_order()`（包装 ccxt） |
| `src/agent/tools_execution.py` | 重写 | 删除 `_record_trade_open`/`_update_trade_closed`，新增 `_record_action`；简化 `open_position`/`close_position`；`set_stop_loss`/`set_take_profit` 增加自动取消旧单逻辑 |
| `src/agent/tools_perception.py` | 修改 | 新增 `get_open_orders`、`get_trade_journal`；重命名 `get_trade_history` → `get_memories` |
| `src/agent/trader.py` | 修改 | 注册新 tools，重命名旧 tool，更新 `open_position` 参数签名 |
| `src/agent/persona.py` | 修改 | System prompt 更新：事件驱动决策流程、去掉 SL/TP 捆绑指令、裸仓检测提示 |
| `src/services/metrics.py` | 重写 | 数据源从 TradeRecord 改为 TradeAction.pnl 聚合；接口变为异步 |
| `src/cli/app.py` | 修改 | FillEvent handler 写入 TradeAction（含 trigger_reason、pnl）+ 触发唤醒；初始 metrics 改用新接口 |
| `tests/` | 修改 | 相关测试适配新数据模型和接口 |

### 当前代码关键现状（供审查员参考）

**`tools_execution.py` 当前结构：**
- `_record_trade_open(deps, **kwargs)` — 创建 TradeRecord（status="open"）
- `_update_trade_closed(deps, symbol, side, pnl, exit_price)` — 查找匹配的 open 记录，更新为 closed
- `open_position()` — 下市价单 + 设 SL + 设 TP + 写 TradeRecord（一次性完成）
- `close_position()` — 遍历持仓逐个平仓 + 更新 TradeRecord
- `set_stop_loss/set_take_profit/adjust_leverage` — 纯交易所操作，无数据库写入

**`app.py` FillEvent handler 当前状态：**
```python
async def handle_fill(event: FillEvent):
    try:
        pass  # Agent layer recording — out of scope for this phase
    finally:
        await sched.trigger("conditional", context=event)
```

**`metrics.py` 当前接口：**
```python
class MetricsService:
    def compute_from_trades(self, trades: list[TradeRecord], current_position: str = "none") -> dict
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
    # 系统自动记录: order_filled (订单成交), order_expired (订单过期)
    order_id: Mapped[str | None] = mapped_column(String(36), nullable=True)  # 关联交易所订单
    symbol: Mapped[str] = mapped_column(String(50))
    side: Mapped[str | None] = mapped_column(String(10), nullable=True)      # long / short
    trigger_reason: Mapped[str | None] = mapped_column(String(20), nullable=True)  # market / stop / take_profit / liquidation
    pnl: Mapped[float | None] = mapped_column(Float, nullable=True)          # 平仓时的已实现盈亏
    reasoning: Mapped[str | None] = mapped_column(Text, nullable=True)       # agent 的决策理由
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)
```

**设计要点：**

- **append-only** — 不修改已有记录，每次操作追加一条
- **order_id 关联** — 通过 order_id 可反查交易所获取成交价、手续费等客观数据
- **session_id 隔离** — 未来多 agent 共享同一交易所账户时，每个 agent 只看到自己的操作
- **trigger_reason 字段** — 结构化的触发原因（market/stop/take_profit/liquidation），支持按触发类型统计查询
- **pnl 字段** — 仅在平仓类成交时填写，由交易所计算提供。MetricsService 直接聚合此字段，无需自行配对计算
- **reasoning 字段** — agent 主动操作时记录决策理由；系统自动记录时填写触发描述

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
    # 1. 从 DB 获取 TradeAction 记录（按 session_id 过滤，created_at DESC 排序，取最近 limit 条）
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
| `set_stop_loss` | **改造** | 先取消已有同类型挂单，再创建新单；写入 TradeAction |
| `set_take_profit` | **改造** | 先取消已有同类型挂单，再创建新单；写入 TradeAction |
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
        f"Order submitted:\n"
        f"  Side: {side} | Quantity: {quantity:.6f} | Leverage: {leverage}x\n"
        f"  Order: {order.id} | You will be notified when filled."
    )
```

#### _record_action 辅助函数

所有执行类 tools 共用的 TradeAction 写入函数：

```python
async def _record_action(deps: TradingDeps, action: str, order_id: str | None = None,
                          side: str | None = None, reasoning: str | None = None) -> None:
    """写入一条 TradeAction 记录。"""
    if deps.db_engine is None:
        return
    from src.storage.database import get_session
    from src.storage.models import TradeAction

    async with get_session(deps.db_engine) as session:
        session.add(TradeAction(
            session_id=deps.session_id,
            action=action,
            order_id=order_id,
            symbol=deps.symbol,
            side=side,
            reasoning=reasoning,
        ))
        await session.commit()
```

#### close_position 简化后

```python
async def close_position(deps: TradingDeps) -> str:
    """Close all open positions."""
    positions = await deps.exchange.fetch_positions(deps.symbol)
    if not positions:
        return "No positions to close."

    total_pnl = sum(p.unrealized_pnl for p in positions)
    reasoning = f"Close {len(positions)} position(s), total PnL: {total_pnl:.2f} USDT"
    approved = await _check_approval(deps, "close", reasoning, 0, 0)
    if not approved:
        return "Close rejected by human approval."

    results = []
    for p in positions:
        order_side = "sell" if p.side == "long" else "buy"
        order = await deps.exchange.create_order(
            symbol=deps.symbol, side=order_side, order_type="market", amount=p.contracts
        )
        # 写入 TradeAction（不再更新 TradeRecord）
        await _record_action(
            deps, action="close_position", order_id=order.id,
            side=p.side, reasoning=reasoning,
        )
        results.append(f"Closed {p.side} {p.contracts} | Order: {order.id}")

    return "Orders submitted:\n" + "\n".join(results) + "\nYou will be notified when filled."
```

**与当前代码的主要差异：**
- 删除 `_update_trade_closed()` 调用
- 删除 `ticker` 查询（不再需要记录 exit_price，由交易所提供）
- 返回 "Orders submitted" 而非 "Positions closed"（统一异步语义）

#### BaseExchange 新增 cancel_order

```python
# base.py
class BaseExchange(ABC):
    ...
    @abstractmethod
    async def cancel_order(self, order_id: str, symbol: str) -> None: ...
```

- **OKXExchange**：包装 `self._client.cancel_order(order_id, symbol)`
- **SimulatedExchange**：从 `_pending_orders` 移除 + 更新 SimOrder 状态为 "cancelled"

本轮仅作为内部方法使用（SL/TP 自动替换），不暴露为 agent tool。

#### set_stop_loss / set_take_profit 自动替换

新设计中 SL/TP 在独立周期设置，agent 更容易重复调用（如 Cycle 2 设 SL，Cycle 3 调整 SL）。为避免多个同类型条件单共存，设置前先取消已有同类型挂单：

```python
async def set_stop_loss(deps: TradingDeps, price: float) -> str:
    """Set stop loss on current position. Cancels existing stop order if any."""
    positions = await deps.exchange.fetch_positions(deps.symbol)
    if not positions:
        return "No open position to set stop loss on."

    # 取消已有的 stop 挂单
    open_orders = await deps.exchange.fetch_open_orders(deps.symbol)
    for o in open_orders:
        if o.order_type == "stop":
            await deps.exchange.cancel_order(o.id, deps.symbol)

    p = positions[0]
    side = "sell" if p.side == "long" else "buy"
    order = await deps.exchange.create_order(
        symbol=deps.symbol, side=side, order_type="stop", amount=p.contracts, price=price
    )

    await _record_action(
        deps, action="set_stop_loss", order_id=order.id,
        side=p.side, reasoning=f"Stop loss set at {price:.2f}",
    )
    return f"Stop loss set at {price:.2f} | Order: {order.id}"
```

`set_take_profit` 逻辑相同，将 `order_type` 过滤条件换为 `"take_profit"`。

### 记忆类 Tools

| Tool | 变更 |
|------|------|
| `save_memory` | 不变 |

## FillEvent 处理

### 核心原则

**所有订单成交统一走 FillEvent 流程。** 无论市价单还是条件单，成交后都通过 FillEvent 写入 TradeAction 并唤醒 agent。

### FillEvent 移至 base.py

FillEvent 当前定义在 `simulated.py` 内部。为了让 `app.py` 的 fill handler 和未来的 OKX WebSocket 监听都能使用，将 FillEvent 移至 `src/integrations/exchange/base.py`，并增加 `pnl` 字段：

```python
@dataclass
class FillEvent:
    order_id: str
    symbol: str
    side: str
    position_side: str
    trigger_reason: str    # market / stop / take_profit / liquidation
    fill_price: float
    amount: float
    fee: float
    pnl: float | None      # 新增：平仓时的已实现盈亏（开仓时为 None）
    timestamp: int
```

`pnl` 由交易所在平仓时计算提供（SimulatedExchange 的 `_close_position_core` 和 `_execute_fill` 已经算出了 pnl），开仓时为 None。

### SimulatedExchange 改造

当前 SimulatedExchange 只在 `_process_tick()`（条件单/强平）中调用 `_fill_callback`，市价单成交不产生 FillEvent。需要改造 `create_order()` 方法，在市价单成交后也触发 FillEvent callback：

```python
async def create_order(self, symbol, side, order_type, amount, price=None):
    ...
    async with self._lock:
        if order_type == "market":
            # 返回值需扩展为 (order, position_side, pnl)
            # 开仓时 pnl=None，平仓时 pnl 由 _close_position_core 计算
            order, position_side, pnl = self._execute_market_order(symbol, side, amount)
            if self._db_engine:
                await self._persist_state(new_orders=[(order, position_side)])
            # 新增：市价单成交也触发 FillEvent
            fill_event = FillEvent(
                order_id=order.id, symbol=symbol, side=order.side,
                position_side=position_side,
                trigger_reason="market",
                fill_price=order.price, amount=order.amount,
                fee=order.fee,
                pnl=pnl,
                timestamp=int(datetime.now(timezone.utc).timestamp() * 1000),
            )
            # 注意：callback 在 lock 外调用，与条件单行为一致
    # 在 lock 外触发回调
    if order_type == "market" and self._fill_callback:
        await self._fill_callback(fill_event)
    return order
    ...
```

**注意：** `_execute_market_order` 的返回值需从 `(order, position_side)` 扩展为 `(order, position_side, pnl)`。开仓路径（`_open_market_order`）返回 `pnl=None`；平仓路径（`_close_market_order`）从 `_close_position_core` 获取 pnl 并返回。`_execute_fill`（条件单）同理，已有 pnl 计算逻辑。

**时序兼容：** 市价单的 FillEvent 在 `create_order()` 内部产生，此时 agent 仍在当前决策周期中（tool 调用尚未返回）。FillEvent handler 调用 `scheduler.trigger()` 仅设置 `_pending_trigger` 标志和 context，不会立即启动新周期。当前周期结束后，Scheduler 主循环检测到 pending trigger，才启动下一个周期。现有 Scheduler 设计已支持此流程，无需改造。

**Scheduler 单 pending 限制：** 当前 Scheduler 的 `trigger()` 方法仅保留一个 pending context。如果同一周期内产生多个 FillEvent（如 `close_position` 遍历多个持仓），只有第一个的 context 会保留在 prompt 中，后续的 context 丢失（`_pending_context = None`）。

当前不影响：系统为单交易对设计（SimPosition 有 `UniqueConstraint("session_id", "symbol")`），每个 session 每个 symbol 最多一个持仓，因此 `close_position` 只产生一个 FillEvent。**所有 FillEvent 产生的 TradeAction 记录不受影响**（每个 FillEvent 独立写入 TradeAction），agent 可以通过 `get_trade_journal` 获取完整信息。

未来多交易对支持时需要将 Scheduler 改造为事件队列模式。

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
            trigger_reason=event.trigger_reason,
            pnl=event.pnl,
            reasoning=f"(exchange: {event.trigger_reason} order filled @ {event.fill_price:.2f})",
        ))
        await session.commit()
```

### TradeAction 记录策略

市价单会产生两条 TradeAction 记录，承载不同信息：

- **Tool 写入**：`action="open_position"`, `reasoning="RSI超卖+金叉"`（agent 的决策理由）
- **FillEvent 写入**：`action="order_filled"`, `reasoning="(exchange: market order filled @ 60200)"`（成交事实）

两条记录通过相同的 `order_id` 关联，共同构成完整的决策 → 执行时间线。`get_trade_journal` 展示时可合并同 order_id 的记录以提升可读性。

条件单只有 FillEvent 写入的一条记录（agent 未主动发起操作），但 agent 在 FillEvent 周期中的 `save_memory` 或后续操作会补充主观记录。

FillEvent handler 的职责明确：
1. 将成交事件写入 TradeAction（agent 下次查看 `get_trade_journal` 时可见）
2. 触发 Scheduler 的 conditional 唤醒（agent 被叫醒来处理成交后事宜）

## MetricsService 改造

### 当前问题

`MetricsService.compute_from_trades()` 依赖 `TradeRecord` 的 `status="closed"` 记录计算指标。废弃 TradeRecord 后需要新的数据源。

### 改造方案

TradeAction 的 `pnl` 字段直接携带了交易所计算的已实现盈亏（来自 FillEvent），MetricsService 只需聚合 TradeAction 中 `action="order_filled"` 且 `pnl IS NOT NULL` 的记录，无需自行配对开仓/平仓订单。

```python
class MetricsService:
    async def compute(self, engine, session_id, initial_balance):
        # 1. 查询所有有 pnl 的 order_filled 记录
        async with get_session(engine) as session:
            result = await session.execute(
                select(TradeAction)
                .where(TradeAction.session_id == session_id)
                .where(TradeAction.action == "order_filled")
                .where(TradeAction.pnl.isnot(None))
                .order_by(TradeAction.created_at)
            )
            fills = result.scalars().all()

        # 2. 直接从 pnl 字段计算指标
        pnls = [f.pnl for f in fills]
        return _compute_metrics(pnls, initial_balance)
```

**简化关键：** 交易所在撮合时已经计算了 PnL（SimulatedExchange 的 `_close_position_core` 返回 pnl），通过 FillEvent.pnl → TradeAction.pnl 传递到 agent 层。MetricsService 无需重新配对计算，避免了部分平仓、加仓均价等复杂场景。

**接口变更：** `compute_from_trades(trade_records)` → `compute(engine, session_id, initial_balance)`。MetricsService 变为异步，但不再需要访问交易所，只需查询 TradeAction 表。

## App 层改造

### run_agent_cycle

无重大改动。Prompt 构建方式保持不变（trigger_type + FillEvent context + memories）。TradeAction 的写入由各 tool 和 FillEvent handler 负责，不在 cycle 层面处理。

### 初始 Metrics 展示

将 `select(TradeRecord)` 替换为 `MetricsService.compute()` 调用。

### TradingDeps

无需新增字段。TradeAction 的读写通过已有的 `db_engine` 和 `session_id` 完成。

## System Prompt 改造

当前 system prompt（`persona.py`）的 "Decision Output Format" 要求 agent 在每次决策中同时输出 Stop Loss 和 Take Profit。新的统一异步流程将 SL/TP 拆到 FillEvent 触发的独立周期中，system prompt 需要相应更新。

### 需要更新的内容

1. **决策流程指引** — 告诉 agent 每个周期的职责：

```
## Decision Workflow

You operate in event-driven cycles. Each cycle is triggered by either a scheduled timer
or a fill event. Follow the appropriate workflow:

### On scheduled trigger (routine market check):
1. Gather information: market data, positions, open orders, trade journal, memories
2. Analyze and decide: open/close/adjust/skip
3. If opening a position: call open_position. SL/TP will be set after fill confirmation.

### On fill event (order was filled):
1. Review what happened (the fill details are in your prompt)
2. If a new position was opened: set stop loss and take profit based on the actual fill price
3. If a position was closed (SL/TP/manual): review the outcome, save lessons to memory
4. Decide if any further action is needed
```

2. **去掉 SL/TP 捆绑指令** — 删除 "Decision Output Format" 中要求每次同时输出 Stop Loss 和 Take Profit 的内容

3. **裸仓检测提示** — 提醒 agent 在 FillEvent 周期中检查是否有裸仓（持仓但无挂单保护）

## 数据库迁移

### 新增表

- `trade_actions` — TradeAction 模型

### 废弃表

- `trade_records` — 删除 TradeRecord 模型

**迁移策略：** 由于系统处于早期开发阶段，不存在生产数据需要迁移，直接删除旧表、新增新表即可。通过 `init_db()` 的 `create_all()` 自动建表。

## 端到端验证标准

使用 `config/settings_sim.yaml`（`exchange.name: simulated`）启动系统，验证以下场景：

### 验证场景

1. **完整开仓流程**
   - Scheduler 定时触发 → agent 收集信息 → 调用 `open_position` → TradeAction 写入
   - FillEvent 触发新周期 → agent 确认成交 → 调用 `set_stop_loss` + `set_take_profit` → TradeAction 写入
   - 验证：`trade_actions` 表有 4 条记录（open_position + order_filled + set_stop_loss + set_take_profit）

2. **条件单触发复盘**
   - 价格变动触发止损/止盈 → FillEvent → TradeAction 自动写入
   - agent 被唤醒 → 调用 `get_trade_journal` 看到完整时间线 → 调用 `save_memory` 记录教训
   - 验证：TradeAction 有 order_filled 记录，MemoryEntry 有新增条目

3. **交易日志完整性**
   - `get_trade_journal` 返回的时间线包含所有操作，且通过 order_id 关联了成交价/手续费
   - 验证：日志输出包含 action、reasoning、成交价、手续费

4. **Metrics 正确性**
   - 经过若干轮交易后，MetricsService 能从交易所订单数据计算出正确的收益率、胜率、最大回撤
   - 验证：指标数值与手动计算一致

5. **工具覆盖**
   - 所有感知 tools（`get_market_data`、`get_account_balance`、`get_position`、`get_open_orders`、`get_trade_journal`、`get_memories`）正常返回
   - 所有执行 tools（`open_position`、`close_position`、`set_stop_loss`、`set_take_profit`、`adjust_leverage`）正常执行并写入 TradeAction

## 不在本轮范围

| 功能 | 原因 |
|------|------|
| 限价单支持 | 新的 order_type，独立功能扩展 |
| `cancel_order` agent tool | `cancel_order` 已作为 BaseExchange 内部方法实现（SL/TP 自动替换），但不暴露为 agent tool。等限价单支持时再加 |
| 多 agent 协作 | 架构已预留（session_id 隔离），但编排逻辑是独立工程 |
| 新闻/消息面数据 | Phase 1b 范畴 |
| 语义化记忆检索 | 当前 relevance_score 方案够用 |
| Short-term memory 生命周期 | clear_short_term() 未使用，不影响核心流程 |
| Scheduler 事件队列 | 当前单 pending trigger 在单交易对下不影响，多交易对支持时再改造 |
