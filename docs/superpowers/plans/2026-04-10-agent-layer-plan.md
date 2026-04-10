# Agent 层改造 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 改造 Agent 层，用 append-only 的 TradeAction 替代 TradeRecord，实现统一异步 FillEvent 流程，完整模拟交易员决策循环。

**Architecture:** 所有订单（市价/条件）统一走"下单 → 等待状态变化 → FillEvent 异步通知"流程。TradeAction 记录 agent 决策（reasoning），FillEvent handler 记录成交事实（pnl）。市价单 FillEvent 在 SimulatedExchange 中入队延迟处理，保证时序正确。

**Tech Stack:** Python 3.12+, asyncio, SQLAlchemy ORM, pydantic-ai, pytest + pytest-asyncio

**Design spec:** `docs/superpowers/specs/2026-04-10-agent-layer-design.md`

---

### Task 1: Foundation — FillEvent 迁移 + BaseExchange 接口扩展

**Files:**
- Modify: `src/integrations/exchange/base.py`
- Test: `tests/test_exchange.py`

- [ ] **Step 1: Write failing tests for FillEvent in base.py and new BaseExchange methods**

```python
# tests/test_exchange.py — 在文件末尾追加

def test_fill_event_from_base():
    """FillEvent should be importable from base.py with pnl field."""
    from src.integrations.exchange.base import FillEvent
    event = FillEvent(
        order_id="o1", symbol="BTC/USDT:USDT", side="buy",
        position_side="long", trigger_reason="market",
        fill_price=60200.0, amount=0.001, fee=0.03,
        pnl=None, timestamp=1712534400000,
    )
    assert event.pnl is None

    event_with_pnl = FillEvent(
        order_id="o2", symbol="BTC/USDT:USDT", side="sell",
        position_side="long", trigger_reason="stop",
        fill_price=58394.0, amount=0.001, fee=0.03,
        pnl=-1.35, timestamp=1712534401000,
    )
    assert event_with_pnl.pnl == -1.35


def test_base_exchange_drain_pending_fills():
    """BaseExchange.drain_pending_fills default returns empty list."""
    from src.integrations.exchange.base import BaseExchange
    # Create a concrete subclass just to test the default method
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
    assert ex.drain_pending_fills() == []
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_exchange.py::test_fill_event_from_base tests/test_exchange.py::test_base_exchange_drain_pending_fills -v`
Expected: FAIL — FillEvent not in base.py, cancel_order not in BaseExchange

- [ ] **Step 3: Implement FillEvent in base.py + extend BaseExchange**

在 `src/integrations/exchange/base.py` 末尾（`BaseExchange` 类之后）添加 `FillEvent`，在 `BaseExchange` 类中添加 `cancel_order` 和 `drain_pending_fills`：

```python
# src/integrations/exchange/base.py — 在 BaseExchange 类内追加两个方法

    @abstractmethod
    async def cancel_order(self, order_id: str, symbol: str) -> None: ...

    def drain_pending_fills(self) -> list['FillEvent']:
        """Return and clear queued FillEvents. Default: empty (OKX etc. need not override)."""
        return []


# 在文件末尾添加 FillEvent dataclass
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
    pnl: float | None      # 已实现盈亏（开仓时 None）
    timestamp: int
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_exchange.py::test_fill_event_from_base tests/test_exchange.py::test_base_exchange_drain_pending_fills -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/integrations/exchange/base.py tests/test_exchange.py
git commit -m "feat: add FillEvent to base.py, extend BaseExchange with cancel_order and drain_pending_fills"
```

---

### Task 2: TradeAction 模型 + 删除 TradeRecord

**Files:**
- Modify: `src/storage/models.py`
- Modify: `tests/test_storage.py`
- Modify: `tests/test_metrics.py` (暂时修复导入错误)

- [ ] **Step 1: Write failing test for TradeAction model**

```python
# tests/test_storage.py — 替换 test_create_trade_record，新增 test_create_trade_action

async def test_create_trade_action(db_session):
    from src.storage.models import TradeAction
    action = TradeAction(
        session_id="test-session",
        action="open_position",
        order_id="uuid-order-1",
        symbol="BTC/USDT:USDT",
        side="long",
        trigger_reason=None,
        price=None,
        pnl=None,
        reasoning="RSI oversold + golden cross",
    )
    db_session.add(action)
    await db_session.commit()
    await db_session.refresh(action)
    assert action.id is not None
    assert action.reasoning == "RSI oversold + golden cross"
    assert action.created_at is not None


async def test_create_trade_action_with_pnl(db_session):
    from src.storage.models import TradeAction
    action = TradeAction(
        session_id="test-session",
        action="order_filled",
        order_id="uuid-order-2",
        symbol="BTC/USDT:USDT",
        side="long",
        trigger_reason="stop",
        price=None,
        pnl=-1.35,
        reasoning="(exchange: stop order filled @ 60200)",
    )
    db_session.add(action)
    await db_session.commit()
    await db_session.refresh(action)
    assert action.pnl == -1.35
    assert action.trigger_reason == "stop"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_storage.py::test_create_trade_action -v`
Expected: FAIL — TradeAction not defined

- [ ] **Step 3: Add TradeAction model and remove TradeRecord from models.py**

在 `src/storage/models.py` 中：
1. 删除整个 `TradeRecord` 类（行 38-58）
2. 在 `DecisionLog` 类之前添加 `TradeAction`：

```python
class TradeAction(Base):
    """Agent 的交易操作日志 — append-only 事件模型。"""

    __tablename__ = "trade_actions"

    id: Mapped[int] = mapped_column(primary_key=True)
    session_id: Mapped[str] = mapped_column(String(36), ForeignKey("sessions.id"), index=True)
    action: Mapped[str] = mapped_column(String(30))
    order_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    symbol: Mapped[str] = mapped_column(String(50))
    side: Mapped[str | None] = mapped_column(String(10), nullable=True)
    trigger_reason: Mapped[str | None] = mapped_column(String(20), nullable=True)
    price: Mapped[float | None] = mapped_column(Float, nullable=True)
    pnl: Mapped[float | None] = mapped_column(Float, nullable=True)
    reasoning: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)
```

3. 在 `tests/test_storage.py` 中删除 `test_create_trade_record`
4. 在 `tests/test_metrics.py` 中暂时注释掉所有测试（`@pytest.mark.skip(reason="MetricsService rewrite pending")`），因为它导入 `TradeRecord`

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_storage.py -v`
Expected: PASS（新增 2 个 TradeAction 测试通过，旧 TradeRecord 测试已删除）

- [ ] **Step 5: Verify all imports are clean**

Run: `pytest tests/ -v --ignore=tests/test_cli.py`
Expected: 检查是否有其他文件因 TradeRecord 删除而报错。如有，修复导入。

注意：`src/services/metrics.py`、`src/agent/tools_execution.py`、`src/cli/app.py` 仍引用 TradeRecord，后续 task 会修复。此 step 只需确保 test 文件通过。

- [ ] **Step 6: Commit**

```bash
git add src/storage/models.py tests/test_storage.py tests/test_metrics.py
git commit -m "feat: add TradeAction model, remove TradeRecord"
```

---

### Task 3: SimulatedExchange 改造 — cancel_order + pnl 传递 + FillEvent 入队

**Files:**
- Modify: `src/integrations/exchange/simulated.py`
- Modify: `tests/test_simulated_exchange.py`

- [ ] **Step 1: Update SimulatedExchange imports — FillEvent from base.py**

在 `src/integrations/exchange/simulated.py` 中：
1. 从 `base.py` 导入 `FillEvent`（不再在 simulated.py 中定义）
2. 删除 simulated.py 中的 `FillEvent` dataclass 定义（行 24-33）
3. 在 imports 中添加：`from src.integrations.exchange.base import FillEvent`（与现有 base imports 合并）

更新 `tests/test_simulated_exchange.py` 第 3 行：
```python
# 旧: from src.integrations.exchange.simulated import FillEvent
# 新:
from src.integrations.exchange.base import FillEvent
```

Run: `pytest tests/test_simulated_exchange.py -v -x` — 验证现有测试不受影响。

- [ ] **Step 2: Write failing tests for cancel_order**

```python
# tests/test_simulated_exchange.py — 追加

async def test_cancel_order():
    ex = _make_exchange(initial_balance=100.0)
    await ex.set_leverage("BTC/USDT:USDT", 3)
    # Open a position first
    await ex.create_order("BTC/USDT:USDT", "buy", "market", 0.001)
    # Set a stop loss
    sl_order = await ex.create_order("BTC/USDT:USDT", "sell", "stop", 0.001, price=90000.0)
    assert len(await ex.fetch_open_orders("BTC/USDT:USDT")) == 1

    # Cancel it
    await ex.cancel_order(sl_order.id, "BTC/USDT:USDT")
    assert len(await ex.fetch_open_orders("BTC/USDT:USDT")) == 0


async def test_cancel_nonexistent_order():
    ex = _make_exchange()
    with pytest.raises(ValueError, match="not found"):
        await ex.cancel_order("nonexistent-id", "BTC/USDT:USDT")
```

- [ ] **Step 3: Implement cancel_order**

```python
# src/integrations/exchange/simulated.py — SimulatedExchange 类内添加

    async def cancel_order(self, order_id: str, symbol: str) -> None:
        self._validate_symbol(symbol)
        async with self._lock:
            found = any(o.id == order_id for o in self._pending_orders)
            if not found:
                raise ValueError(f"Order not found: {order_id}")
            self._remove_order_by_id(order_id)
            if self._db_engine:
                from sqlalchemy import update
                from src.storage.database import get_session
                from src.storage.models import SimOrder
                async with get_session(self._db_engine) as session:
                    await session.execute(
                        update(SimOrder)
                        .where(SimOrder.order_id == order_id)
                        .values(status="cancelled")
                    )
                    await session.commit()
        logger.info(f"Order cancelled: {order_id}")
```

- [ ] **Step 4: Run cancel_order tests**

Run: `pytest tests/test_simulated_exchange.py::test_cancel_order tests/test_simulated_exchange.py::test_cancel_nonexistent_order -v`
Expected: PASS

- [ ] **Step 5: Write failing tests for pnl propagation in FillEvent**

```python
# tests/test_simulated_exchange.py — 追加

async def test_fill_event_carries_pnl_on_stop():
    """When a stop order triggers, FillEvent should include pnl."""
    ex = _make_exchange(initial_balance=100.0)
    fills = []
    ex.on_fill(lambda event: fills.append(event) or asyncio.sleep(0))

    await ex.set_leverage("BTC/USDT:USDT", 3)
    await ex.create_order("BTC/USDT:USDT", "buy", "market", 0.001)
    await ex.create_order("BTC/USDT:USDT", "sell", "stop", 0.001, price=90000.0)

    # Simulate price drop to trigger stop
    drop_ticker = Ticker("BTC/USDT:USDT", 89000.0, 89000.0, 89010.0,
                         96000.0, 88000.0, 1000.0, 1712534500000)
    await ex._process_tick(drop_ticker)

    assert len(fills) == 1
    assert fills[0].trigger_reason == "stop"
    assert fills[0].pnl is not None
    assert fills[0].pnl < 0  # loss


async def test_market_order_queues_fill_event():
    """Market order should queue FillEvent, not call callback immediately."""
    ex = _make_exchange(initial_balance=100.0)
    callback_calls = []
    ex.on_fill(lambda event: callback_calls.append(event) or asyncio.sleep(0))

    await ex.set_leverage("BTC/USDT:USDT", 3)
    await ex.create_order("BTC/USDT:USDT", "buy", "market", 0.001)

    # Callback should NOT have been called
    assert len(callback_calls) == 0

    # But pending fills should have one entry
    fills = ex.drain_pending_fills()
    assert len(fills) == 1
    assert fills[0].trigger_reason == "market"
    assert fills[0].pnl is None  # opening, no pnl

    # Second drain should be empty
    assert len(ex.drain_pending_fills()) == 0


async def test_market_close_fill_event_has_pnl():
    """Market close should produce FillEvent with pnl in pending queue."""
    ex = _make_exchange(initial_balance=100.0)
    await ex.set_leverage("BTC/USDT:USDT", 3)
    await ex.create_order("BTC/USDT:USDT", "buy", "market", 0.001)
    ex.drain_pending_fills()  # clear open fill

    # Close position
    await ex.create_order("BTC/USDT:USDT", "sell", "market", 0.001)
    fills = ex.drain_pending_fills()
    assert len(fills) == 1
    assert fills[0].trigger_reason == "market"
    assert fills[0].pnl is not None  # close has pnl
```

- [ ] **Step 6: Run pnl/queue tests to verify they fail**

Run: `pytest tests/test_simulated_exchange.py::test_fill_event_carries_pnl_on_stop tests/test_simulated_exchange.py::test_market_order_queues_fill_event tests/test_simulated_exchange.py::test_market_close_fill_event_has_pnl -v`
Expected: FAIL

- [ ] **Step 7: Implement pnl propagation + FillEvent queuing**

在 `src/integrations/exchange/simulated.py` 中：

1. 在 `__init__` 中添加 `self._pending_fills: list[FillEvent] = []`

2. 修改 `_open_market_order` 返回值为 `(order, position_side, None)` — 末尾的 `return order, position_side` 改为 `return order, position_side, None`

3. 修改 `_close_market_order` 返回值为 `(order, position_side, pnl)` — 已有 `pnl, fee, _ = self._close_position_core(...)`，末尾 `return order, position_side` 改为 `return order, position_side, pnl`

4. 修改 `_execute_market_order`：`order, position_side = ...` 改为 `order, position_side, pnl = ...`，并 `return order, position_side, pnl`

5. 修改 `create_order` 中 market 分支：

```python
if order_type == "market":
    order, position_side, pnl = self._execute_market_order(symbol, side, amount)
    if self._db_engine:
        await self._persist_state(new_orders=[(order, position_side)])
    self._pending_fills.append(FillEvent(
        order_id=order.id, symbol=symbol, side=order.side,
        position_side=position_side,
        trigger_reason="market",
        fill_price=order.price, amount=order.amount,
        fee=order.fee, pnl=pnl,
        timestamp=int(datetime.now(timezone.utc).timestamp() * 1000),
    ))
    return order
```

6. 修改 `_execute_fill` 中 FillEvent 构造——添加 `pnl=pnl`（变量已存在于该方法中）

7. 修改 `_force_liquidate` 中 FillEvent 构造——添加 `pnl=pnl`（变量已存在）

8. 添加 `drain_pending_fills` 方法（覆写 BaseExchange 默认实现）：

```python
def drain_pending_fills(self) -> list[FillEvent]:
    fills = self._pending_fills.copy()
    self._pending_fills.clear()
    return fills
```

- [ ] **Step 8: Run all SimulatedExchange tests**

Run: `pytest tests/test_simulated_exchange.py -v`
Expected: ALL PASS

- [ ] **Step 9: Commit**

```bash
git add src/integrations/exchange/simulated.py tests/test_simulated_exchange.py
git commit -m "feat: SimulatedExchange cancel_order, pnl in FillEvent, market fill queuing"
```

---

### Task 4: OKXExchange — cancel_order

**Files:**
- Modify: `src/integrations/exchange/okx.py`
- Modify: `tests/test_exchange.py`

- [ ] **Step 1: Write failing test**

```python
# tests/test_exchange.py — 追加

async def test_okx_cancel_order(monkeypatch):
    from src.integrations.exchange.okx import OKXExchange
    from unittest.mock import AsyncMock, MagicMock
    import ccxt.async_support as ccxt

    mock_client = MagicMock(spec=ccxt.okx)
    mock_client.cancel_order = AsyncMock(return_value={"id": "o1", "status": "cancelled"})

    exchange = OKXExchange.__new__(OKXExchange)
    exchange._client = mock_client

    await exchange.cancel_order("o1", "BTC/USDT:USDT")
    mock_client.cancel_order.assert_called_once_with("o1", "BTC/USDT:USDT")
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_exchange.py::test_okx_cancel_order -v`
Expected: FAIL — cancel_order not defined on OKXExchange

- [ ] **Step 3: Implement cancel_order on OKXExchange**

```python
# src/integrations/exchange/okx.py — 在 close() 方法之前追加

    @_retry()
    async def cancel_order(self, order_id: str, symbol: str) -> None:  # type: ignore[override]
        await self._client.cancel_order(order_id, symbol)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_exchange.py::test_okx_cancel_order -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/integrations/exchange/okx.py tests/test_exchange.py
git commit -m "feat: OKXExchange cancel_order implementation"
```

---

### Task 5: Approval Gate 简化

**Files:**
- Modify: `src/cli/approval.py`
- Modify: `tests/test_approval.py`

- [ ] **Step 1: Update tests to remove stop_loss/take_profit**

```python
# tests/test_approval.py — 完整替换

def test_format_decision():
    from src.cli.approval import format_decision_for_approval

    text = format_decision_for_approval(
        action="open_long",
        reasoning="Bullish trend",
        position_pct=20.0,
        leverage=3,
    )
    assert "LONG" in text.upper()
    assert "20" in text
    assert "3" in text


def test_auto_approve_when_disabled():
    from src.cli.approval import ApprovalGate

    gate = ApprovalGate(enabled=False, timeout_seconds=300)
    result = gate.check_sync("open_long", "Bullish", 20.0, 3)
    assert result is True


def test_approval_accepted(monkeypatch):
    from src.cli.approval import ApprovalGate

    monkeypatch.setattr("builtins.input", lambda _: "y")
    gate = ApprovalGate(enabled=True, timeout_seconds=300)
    result = gate.check_sync("open_long", "Bullish trend", 20.0, 3)
    assert result is True


def test_approval_rejected(monkeypatch):
    from src.cli.approval import ApprovalGate

    monkeypatch.setattr("builtins.input", lambda _: "n")
    gate = ApprovalGate(enabled=True, timeout_seconds=300)
    result = gate.check_sync("open_long", "Weak signal", 20.0, 3)
    assert result is False
```

- [ ] **Step 2: Update approval.py — remove stop_loss/take_profit params**

从 `format_decision_for_approval`、`check_sync`、`check` 三个方法中移除 `stop_loss` 和 `take_profit` 参数。删除 `format_decision_for_approval` 中的 stop_loss/take_profit 显示逻辑。

- [ ] **Step 3: Run tests**

Run: `pytest tests/test_approval.py -v`
Expected: PASS

- [ ] **Step 4: Commit**

```bash
git add src/cli/approval.py tests/test_approval.py
git commit -m "refactor: remove stop_loss/take_profit from approval gate"
```

---

### Task 6: 执行类 Tools 重写

**Files:**
- Modify: `src/agent/tools_execution.py`
- Modify: `tests/test_tools.py`

- [ ] **Step 1: Rewrite tests/test_tools.py for execution tools**

```python
# tests/test_tools.py — 替换执行类 tool 的测试（从 test_open_position 开始）

async def test_open_position(deps):
    from src.agent.tools_execution import open_position
    result = await open_position(deps, "long", 20.0, 3, reasoning="RSI oversold")
    assert "submitted" in result.lower()
    assert "o1" in result
    deps.exchange.set_leverage.assert_called_once()


async def test_open_position_too_small(deps):
    from src.agent.tools_execution import open_position
    deps.exchange.amount_to_precision = MagicMock(return_value=0.0)
    result = await open_position(deps, "long", 0.001, 1, reasoning="test")
    assert "too small" in result.lower()


async def test_close_position(deps):
    from src.agent.tools_execution import close_position
    result = await close_position(deps, reasoning="MACD death cross")
    assert "submitted" in result.lower()


async def test_close_position_no_positions(deps):
    from src.agent.tools_execution import close_position
    deps.exchange.fetch_positions.return_value = []
    result = await close_position(deps, reasoning="test")
    assert "no positions" in result.lower()


async def test_set_stop_loss_cancels_existing(deps):
    from src.agent.tools_execution import set_stop_loss
    deps.exchange.fetch_open_orders.return_value = [
        Order("old-sl", "BTC/USDT:USDT", "sell", "stop", 0.01, 60000.0, "open"),
    ]
    deps.exchange.cancel_order = AsyncMock()
    result = await set_stop_loss(deps, 63000.0, reasoning="trailing stop")
    assert "63000" in result
    deps.exchange.cancel_order.assert_called_once_with("old-sl", "BTC/USDT:USDT")


async def test_set_take_profit(deps):
    from src.agent.tools_execution import set_take_profit
    deps.exchange.fetch_open_orders.return_value = []
    deps.exchange.cancel_order = AsyncMock()
    result = await set_take_profit(deps, 68000.0, reasoning="target reached")
    assert "68000" in result


async def test_adjust_leverage(deps):
    from src.agent.tools_execution import adjust_leverage
    result = await adjust_leverage(deps, 5, reasoning="reducing risk")
    assert "5" in result
```

同时更新 `MockDeps` 添加 `fetch_open_orders` mock：
```python
# 在 deps fixture 中追加
d.exchange.fetch_open_orders.return_value = []
d.exchange.cancel_order = AsyncMock()
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_tools.py::test_open_position -v`
Expected: FAIL — open_position signature changed

- [ ] **Step 3: Rewrite src/agent/tools_execution.py**

完整替换文件内容，实现设计文档中的所有执行类 tools（`_record_action`、`_check_approval` 简化、`open_position`、`close_position`、`set_stop_loss`、`set_take_profit`、`adjust_leverage`）。

关键变更：
- 删除 `_record_trade_open` 和 `_update_trade_closed`
- 新增 `_record_action` (容错，try/except)
- 所有 tool 增加 `reasoning: str` 参数
- `open_position` 去掉 SL/TP 参数，返回 "Order submitted"
- `close_position` 去掉 TradeRecord 逻辑，返回 "Orders submitted"
- `set_stop_loss`/`set_take_profit` 先取消同类型旧单
- `_check_approval` 去掉 stop_loss/take_profit 参数

- [ ] **Step 4: Run all tool tests**

Run: `pytest tests/test_tools.py -v`
Expected: ALL PASS

- [ ] **Step 5: Commit**

```bash
git add src/agent/tools_execution.py tests/test_tools.py
git commit -m "feat: rewrite execution tools with reasoning, TradeAction, auto-cancel SL/TP"
```

---

### Task 7: 感知类 Tools — get_open_orders, get_trade_journal, rename get_trade_history

**Files:**
- Modify: `src/agent/tools_perception.py`
- Modify: `tests/test_tools.py`

- [ ] **Step 1: Write failing tests for new perception tools**

```python
# tests/test_tools.py — 追加

async def test_get_open_orders(deps):
    from src.agent.tools_perception import get_open_orders
    deps.exchange.fetch_open_orders.return_value = [
        Order("sl1", "BTC/USDT:USDT", "sell", "stop", 0.01, 63000.0, "open"),
    ]
    result = await get_open_orders(deps)
    assert "STOP" in result
    assert "63000" in result


async def test_get_open_orders_empty(deps):
    from src.agent.tools_perception import get_open_orders
    deps.exchange.fetch_open_orders.return_value = []
    result = await get_open_orders(deps)
    assert "no pending" in result.lower()


async def test_get_memories(deps):
    from src.agent.tools_perception import get_memories
    result = await get_memories(deps)
    assert "No memories" in result


async def test_get_trade_journal_empty(deps):
    from src.agent.tools_perception import get_trade_journal
    result = await get_trade_journal(deps)
    assert "no trade journal" in result.lower()
```

更新旧的 `test_get_trade_history` 测试为 `test_get_memories`。

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_tools.py::test_get_open_orders tests/test_tools.py::test_get_memories -v`
Expected: FAIL

- [ ] **Step 3: Implement new perception tools**

在 `src/agent/tools_perception.py` 中：

1. 新增 `get_open_orders(deps)` — 调用 `exchange.fetch_open_orders()`
2. 新增 `get_trade_journal(deps, limit=20)` — 查询 TradeAction + 关联交易所订单详情
3. 将 `get_trade_history` 重命名为 `get_memories` — 功能不变，只返回 MemoryEntry

```python
async def get_open_orders(deps: TradingDeps) -> str:
    """Get pending conditional orders (stop loss, take profit)."""
    orders = await deps.exchange.fetch_open_orders(deps.symbol)
    if not orders:
        return "No pending orders."
    lines = ["Pending Orders:"]
    for o in orders:
        lines.append(f"  {o.order_type.upper()} {o.side} {o.amount} @ {o.price:.2f} | ID: {o.id}")
    return "\n".join(lines)


async def get_trade_journal(deps: TradingDeps, limit: int = 20) -> str:
    """Get trade journal — agent's decision timeline with fill details."""
    if deps.db_engine is None:
        return "No trade journal entries yet."
    from sqlalchemy import select, desc
    from src.storage.database import get_session
    from src.storage.models import TradeAction

    async with get_session(deps.db_engine) as session:
        result = await session.execute(
            select(TradeAction)
            .where(TradeAction.session_id == deps.session_id)
            .order_by(desc(TradeAction.created_at))
            .limit(limit)
        )
        actions = list(result.scalars().all())

    if not actions:
        return "No trade journal entries yet."

    order_details = {}
    order_ids = list({a.order_id for a in actions if a.order_id})
    for oid in order_ids:
        try:
            order = await deps.exchange.fetch_order(oid, deps.symbol)
            order_details[oid] = order
        except Exception:
            logger.warning("Failed to fetch order %s", oid, exc_info=True)

    lines = ["=== Trade Journal ==="]
    for a in reversed(actions):  # chronological order
        ts = a.created_at.strftime("%m-%d %H:%M")
        line = f"[{ts}] {a.action}"
        if a.side:
            line += f" ({a.side})"
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


async def get_memories(deps: TradingDeps) -> str:
    """Get long-term memories (lessons, patterns, trade reviews)."""
    return await deps.memory.format_for_prompt()
```

- [ ] **Step 4: Run all tool tests**

Run: `pytest tests/test_tools.py -v`
Expected: ALL PASS

- [ ] **Step 5: Commit**

```bash
git add src/agent/tools_perception.py tests/test_tools.py
git commit -m "feat: add get_open_orders, get_trade_journal; rename get_trade_history to get_memories"
```

---

### Task 8: Agent 注册更新 (trader.py)

**Files:**
- Modify: `src/agent/trader.py`
- Modify: `tests/test_trader_agent.py`

- [ ] **Step 1: Update test for new tool set**

```python
# tests/test_trader_agent.py — 替换 test_trader_agent_has_all_tools

def test_trader_agent_has_all_tools():
    from src.agent.trader import create_trader_agent
    from src.config import PersonaConfig

    agent = create_trader_agent(model="test", persona_config=PersonaConfig())
    tool_names = set(agent._function_toolset.tools)
    # 感知类
    assert "get_market_data" in tool_names
    assert "get_position" in tool_names
    assert "get_account_balance" in tool_names
    assert "get_open_orders" in tool_names
    assert "get_trade_journal" in tool_names
    assert "get_memories" in tool_names
    # 执行类
    assert "open_position" in tool_names
    assert "close_position" in tool_names
    assert "set_stop_loss" in tool_names
    assert "set_take_profit" in tool_names
    assert "adjust_leverage" in tool_names
    # 记忆类
    assert "save_memory" in tool_names
    # 旧名称不存在
    assert "get_trade_history" not in tool_names
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_trader_agent.py::test_trader_agent_has_all_tools -v`
Expected: FAIL — get_open_orders/get_trade_journal/get_memories not registered

- [ ] **Step 3: Update trader.py — register new tools, update signatures**

在 `src/agent/trader.py` 中：
1. 删除 `get_trade_history` tool 注册
2. 添加 `get_open_orders`、`get_trade_journal`、`get_memories` 注册
3. 更新 `open_position` 签名（添加 `reasoning: str`，去掉 SL/TP）
4. 更新 `close_position` 签名（添加 `reasoning: str`）
5. 更新 `set_stop_loss`/`set_take_profit` 签名（添加 `reasoning: str`）
6. 更新 `adjust_leverage` 签名（添加 `reasoning: str`）

- [ ] **Step 4: Run all trader agent tests**

Run: `pytest tests/test_trader_agent.py -v`
Expected: ALL PASS

- [ ] **Step 5: Commit**

```bash
git add src/agent/trader.py tests/test_trader_agent.py
git commit -m "feat: update trader.py tool registration for new agent workflow"
```

---

### Task 9: MetricsService 重写

**Files:**
- Modify: `src/services/metrics.py`
- Modify: `tests/test_metrics.py`

- [ ] **Step 1: Rewrite tests for async MetricsService**

```python
# tests/test_metrics.py — 完整替换

import pytest
from src.storage.database import init_db, get_session
from src.storage.models import Session, TradeAction


@pytest.fixture
async def metrics_db(tmp_path):
    engine = await init_db(f"sqlite+aiosqlite:///{tmp_path}/metrics_test.db")
    async with get_session(engine) as session:
        session.add(Session(id="test-session", name="metrics-test", initial_balance=10000.0))
        await session.commit()
    yield engine
    await engine.dispose()


async def _add_fill(engine, pnl, trigger_reason="market"):
    async with get_session(engine) as session:
        session.add(TradeAction(
            session_id="test-session", action="order_filled",
            order_id=f"o-{pnl}", symbol="BTC/USDT:USDT", side="long",
            trigger_reason=trigger_reason, pnl=pnl,
            reasoning=f"(exchange: {trigger_reason} filled)",
        ))
        await session.commit()


async def test_compute_metrics(metrics_db):
    from src.services.metrics import MetricsService
    await _add_fill(metrics_db, 30.0)
    await _add_fill(metrics_db, -15.0)
    await _add_fill(metrics_db, 180.0)

    service = MetricsService(initial_balance=10000.0)
    metrics = await service.compute(metrics_db, "test-session")
    assert metrics.total_trades == 3
    assert metrics.win_rate == pytest.approx(2 / 3, abs=0.01)
    assert metrics.total_pnl == pytest.approx(195.0)
    assert metrics.profit_factor > 1.0


async def test_compute_metrics_empty(metrics_db):
    from src.services.metrics import MetricsService
    service = MetricsService(initial_balance=10000.0)
    metrics = await service.compute(metrics_db, "test-session")
    assert metrics.total_trades == 0
    assert metrics.win_rate == 0.0


async def test_compute_metrics_with_position(metrics_db):
    from src.services.metrics import MetricsService
    service = MetricsService(initial_balance=10000.0)
    metrics = await service.compute(metrics_db, "test-session", current_position="long 0.001")
    assert metrics.current_position == "long 0.001"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_metrics.py -v`
Expected: FAIL

- [ ] **Step 3: Rewrite src/services/metrics.py**

```python
from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncEngine

from src.storage.database import get_session
from src.storage.models import TradeAction


@dataclass
class PerformanceMetrics:
    total_return_pct: float = 0.0
    total_pnl: float = 0.0
    win_rate: float = 0.0
    max_drawdown_pct: float = 0.0
    profit_factor: float = 0.0
    total_trades: int = 0
    winning_trades: int = 0
    losing_trades: int = 0
    current_position: str = "none"


class MetricsService:
    def __init__(self, initial_balance: float = 10000.0):
        self._initial_balance = initial_balance

    async def compute(
        self,
        engine: AsyncEngine,
        session_id: str,
        current_position: str = "none",
    ) -> PerformanceMetrics:
        async with get_session(engine) as session:
            result = await session.execute(
                select(TradeAction)
                .where(TradeAction.session_id == session_id)
                .where(TradeAction.action == "order_filled")
                .where(TradeAction.pnl.isnot(None))
                .order_by(TradeAction.created_at)
            )
            fills = result.scalars().all()

        pnls: list[float] = [f.pnl for f in fills]
        if not pnls:
            return PerformanceMetrics(current_position=current_position)

        total_pnl = sum(pnls)
        winning_pnls = [p for p in pnls if p > 0]
        losing_pnls = [p for p in pnls if p <= 0]
        gross_profit = sum(winning_pnls) if winning_pnls else 0.0
        gross_loss = abs(sum(losing_pnls)) if losing_pnls else 0.0

        cumulative = 0.0
        peak = 0.0
        max_dd = 0.0
        for p in pnls:
            cumulative += p
            if cumulative > peak:
                peak = cumulative
            dd = peak - cumulative
            if dd > max_dd:
                max_dd = dd

        return PerformanceMetrics(
            total_return_pct=(total_pnl / self._initial_balance) * 100,
            total_pnl=total_pnl,
            win_rate=len(winning_pnls) / len(pnls),
            max_drawdown_pct=(max_dd / self._initial_balance) * 100,
            profit_factor=gross_profit / gross_loss if gross_loss > 0 else float("inf"),
            total_trades=len(pnls),
            winning_trades=len(winning_pnls),
            losing_trades=len(losing_pnls),
            current_position=current_position,
        )
```

- [ ] **Step 4: Run tests**

Run: `pytest tests/test_metrics.py -v`
Expected: ALL PASS

- [ ] **Step 5: Commit**

```bash
git add src/services/metrics.py tests/test_metrics.py
git commit -m "feat: rewrite MetricsService to use TradeAction.pnl (async)"
```

---

### Task 10: System Prompt 更新 (persona.py)

**Files:**
- Modify: `src/agent/persona.py`
- Modify: `tests/test_persona.py`

- [ ] **Step 1: Update tests for event-driven workflow prompt**

```python
# tests/test_persona.py — 完整替换

from src.config import PersonaConfig


def test_generate_system_prompt():
    from src.agent.persona import generate_system_prompt
    config = PersonaConfig(risk_tolerance="moderate", trading_style="trend_following",
        max_position_pct=30, preferred_leverage=3, stop_loss_pct=3.0, take_profit_pct=6.0)
    prompt = generate_system_prompt(config)
    assert "moderate" in prompt.lower()
    assert "trend" in prompt.lower()
    assert "30" in prompt
    assert len(prompt) > 100


def test_prompt_includes_event_driven_workflow():
    from src.agent.persona import generate_system_prompt
    prompt = generate_system_prompt(PersonaConfig())
    assert "scheduled trigger" in prompt.lower() or "scheduled" in prompt.lower()
    assert "fill event" in prompt.lower() or "fill" in prompt.lower()


def test_prompt_includes_naked_position_check():
    from src.agent.persona import generate_system_prompt
    prompt = generate_system_prompt(PersonaConfig())
    assert "stop loss" in prompt.lower()
    assert "take profit" in prompt.lower() or "protective" in prompt.lower()


def test_prompt_includes_reasoning_instruction():
    from src.agent.persona import generate_system_prompt
    prompt = generate_system_prompt(PersonaConfig())
    assert "reasoning" in prompt.lower()
```

- [ ] **Step 2: Run tests to verify some fail**

Run: `pytest tests/test_persona.py -v`
Expected: test_prompt_includes_event_driven_workflow FAIL

- [ ] **Step 3: Rewrite persona.py system prompt**

替换 `generate_system_prompt` 的 prompt 内容，加入事件驱动决策流程、去掉 SL/TP 捆绑、裸仓检测提示。保留 Trading Personality 和 Hard Rules 部分。关键新增内容：

```
## Decision Workflow

You operate in event-driven cycles. Each cycle is triggered by either a scheduled timer or a fill event (order was filled).

### On scheduled trigger (routine market check):
1. Gather information using your tools: market data, positions, open orders, trade journal, memories
2. Analyze the market and your current state
3. Decide: open position, close position, adjust stops, or skip
4. Always provide your reasoning when executing trades

### On fill event (order was filled):
1. Review the fill details provided in your prompt
2. If a position was just opened: set stop loss and take profit based on the actual fill price
3. If a position was closed: review the outcome and save lessons to memory
4. Check for naked positions (positions without protective orders)

### Important:
- ALWAYS provide clear reasoning in the 'reasoning' parameter when calling execution tools
- After opening a position, you MUST set stop loss and take profit in the follow-up cycle
- If you see a position without protective orders, set them immediately
```

- [ ] **Step 4: Run tests**

Run: `pytest tests/test_persona.py -v`
Expected: ALL PASS

- [ ] **Step 5: Commit**

```bash
git add src/agent/persona.py tests/test_persona.py
git commit -m "feat: update system prompt for event-driven decision workflow"
```

---

### Task 11: App 层集成 — FillEvent handler + drain + metrics + prompt

**Files:**
- Modify: `src/cli/app.py`
- Modify: `tests/test_cli.py`

- [ ] **Step 1: Update app.py — FillEvent handler**

在 `src/cli/app.py` 中：

1. 更新 imports：添加 `from src.integrations.exchange.base import FillEvent`，添加 `from src.storage.models import TradeAction`（替换 `TradeRecord`）

2. 将 `_create_fill_handler` 中的 `pass` 替换为 TradeAction 写入：

```python
def _create_fill_handler(sched, engine, session_id):
    async def handle_fill(event: FillEvent):
        try:
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
        except Exception:
            logger.warning("Failed to record fill event", exc_info=True)
        finally:
            await sched.trigger("conditional", context=event)
    return handle_fill
```

3. 更新 `on_tick` — drain 队列：

```python
async def on_tick(trigger_type: str, context=None):
    if shutdown_event.is_set():
        return
    try:
        await run_agent_cycle(agent, deps, trigger_type, budget, engine, context)
    except Exception:
        logger.exception("Agent cycle failed")
    finally:
        for fill in exchange.drain_pending_fills():
            try:
                await handle_fill(fill)
            except Exception:
                logger.exception("Fill handler failed for order %s", fill.order_id)
```

4. 更新 `run_agent_cycle` prompt — 追加 pnl：

```python
if context is not None and hasattr(context, "trigger_reason"):
    msg = (
        f"\n\nIMPORTANT EVENT: {context.trigger_reason} triggered "
        f"— {context.symbol} {context.amount} @ {context.fill_price}"
    )
    if context.pnl is not None:
        msg += f", PnL: {context.pnl:.2f} USDT"
    prompt += msg
```

5. 更新初始 metrics 展示 — 替换 `select(TradeRecord)` 为 `MetricsService.compute()`：

```python
metrics = await metrics_service.compute(engine, session_id, current_position=pos_str)
display_metrics(metrics)
```

删除旧的 `select(TradeRecord)` 查询块。

6. 更新 `_create_fill_handler` 调用（去掉 symbol 参数，简化）。

- [ ] **Step 2: Run all tests to verify nothing is broken**

Run: `pytest tests/ -v --ignore=tests/test_cli.py`
Expected: ALL PASS

Run: `pytest tests/test_cli.py -v`（如果有导入问题，修复）

- [ ] **Step 3: Commit**

```bash
git add src/cli/app.py tests/test_cli.py
git commit -m "feat: integrate FillEvent handler, drain queue, pnl prompt, async metrics"
```

---

### Task 12: 全量测试 + 清理

**Files:**
- 所有文件

- [ ] **Step 1: Run full test suite**

Run: `pytest tests/ -v`
Expected: ALL PASS，无 warnings 关于 deprecated imports

- [ ] **Step 2: Fix any remaining import issues**

检查是否有文件仍引用 `TradeRecord` 或旧的 `get_trade_history`。

Run: `grep -r "TradeRecord" src/ tests/` — 应无结果
Run: `grep -r "get_trade_history" src/ tests/` — 应无结果
Run: `grep -r "from src.integrations.exchange.simulated import FillEvent" src/ tests/` — 应无结果（FillEvent 现在从 base.py 导入）

- [ ] **Step 3: Run full test suite again**

Run: `pytest tests/ -v`
Expected: ALL PASS

- [ ] **Step 4: Commit any cleanup**

```bash
git add -A
git commit -m "chore: cleanup deprecated references (TradeRecord, get_trade_history)"
```

---

### Task 13: 端到端冒烟测试

**Files:**
- 无代码变更，运行验证

- [ ] **Step 1: Verify config/settings_sim.yaml exists and is correct**

确认 `exchange.name: simulated` 配置正确。

- [ ] **Step 2: Delete old database (if exists)**

```bash
rm -f data/tradebot.db
```

- [ ] **Step 3: Run the system briefly**

```bash
timeout 30 python main.py || true
```

验证：
- 启动无报错
- 显示 "Exchange: simulated"
- Scheduler 启动
- 首个 agent cycle 执行（可能因为没有 ANTHROPIC_API_KEY 而跳过 LLM 调用，但不应该有导入错误或 DB 错误）

- [ ] **Step 4: Verify database schema**

```bash
python -c "
import asyncio
from sqlalchemy import inspect
from src.storage.database import init_db
async def check():
    engine = await init_db('sqlite+aiosqlite:///data/tradebot.db')
    async with engine.connect() as conn:
        tables = await conn.run_sync(lambda c: inspect(c).get_table_names())
    print('Tables:', sorted(tables))
    assert 'trade_actions' in tables
    assert 'trade_records' not in tables
    await engine.dispose()
asyncio.run(check())
"
```

- [ ] **Step 5: Document results**

记录冒烟测试结果。如有问题，回到对应 task 修复。
