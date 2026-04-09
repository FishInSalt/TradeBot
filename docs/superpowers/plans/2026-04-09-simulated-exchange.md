# SimulatedExchange Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Implement a local simulated exchange (SimulatedExchange) that receives real-time OKX market data via WebSocket, matches orders locally, and behaves like a real exchange — enabling zero-risk agent development.

**Architecture:** SimulatedExchange implements BaseExchange, manages its own internal state (balance/positions/orders), persists to sim_* tables, and uses a WebSocket-driven matching engine. It communicates order fills via FillEvent callbacks. The Scheduler is extended with event-based triggering to wake the agent on conditional order fills.

**Tech Stack:** Python 3.12, asyncio, SQLAlchemy async (aiosqlite), ccxt Pro (WebSocket), pytest + pytest-asyncio

**Spec:** `docs/superpowers/specs/2026-04-09-simulated-exchange-design.md`

---

## File Structure

### New Files

| File | Responsibility |
|---|---|
| `src/integrations/exchange/simulated.py` | SimulatedExchange: BaseExchange implementation with matching engine, internal state, persistence |
| `tests/test_simulated_exchange.py` | All tests for SimulatedExchange |

### Modified Files

| File | Changes |
|---|---|
| `src/integrations/exchange/base.py` | Order dataclass: add `fee` field. BaseExchange: add 3 abstract methods |
| `src/integrations/exchange/okx.py` | Implement 3 new abstract methods; parse fee in create_order |
| `src/storage/models.py` | Add SimBalance, SimPosition, SimOrder tables |
| `src/config.py` | ExchangeConfig: add `fee_rate`, `precision` (Optional) |
| `config/settings.yaml` | Add `fee_rate`, `precision` entries |
| `pyproject.toml` | `ccxt>=4.0` → `ccxt[pro]>=4.0` |
| `src/scheduler/scheduler.py` | Rewrite: add trigger(), _interruptible_sleep, callback signature change |
| `tests/test_scheduler.py` | Rewrite tests for new scheduler API |
| `src/cli/app.py` | Exchange factory routing, fill_handler registration, on_tick signature |

---

## Task 1: BaseExchange Interface Extensions

**Files:**
- Modify: `src/integrations/exchange/base.py`
- Test: `tests/test_exchange.py`

- [ ] **Step 1: Write failing test for Order.fee field**

```python
# Add to tests/test_exchange.py
def test_order_has_fee_field():
    from src.integrations.exchange.base import Order
    order = Order(id="1", symbol="BTC/USDT:USDT", side="buy", order_type="market",
                  amount=0.001, price=95000.0, status="closed", fee=0.0475)
    assert order.fee == 0.0475

def test_order_fee_defaults_to_none():
    from src.integrations.exchange.base import Order
    order = Order(id="1", symbol="BTC/USDT:USDT", side="buy", order_type="market",
                  amount=0.001, price=95000.0, status="closed")
    assert order.fee is None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd /Users/z/Z/TradeBot && python -m pytest tests/test_exchange.py::test_order_has_fee_field tests/test_exchange.py::test_order_fee_defaults_to_none -v`
Expected: FAIL (TypeError — unexpected keyword argument 'fee')

- [ ] **Step 3: Add fee field to Order dataclass**

In `src/integrations/exchange/base.py`, add to the Order dataclass:

```python
@dataclass
class Order:
    id: str
    symbol: str
    side: str
    order_type: str
    amount: float
    price: float | None
    status: str
    fee: float | None = None
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd /Users/z/Z/TradeBot && python -m pytest tests/test_exchange.py::test_order_has_fee_field tests/test_exchange.py::test_order_fee_defaults_to_none -v`
Expected: PASS

- [ ] **Step 5: Write failing test for new abstract methods**

```python
# Add to tests/test_exchange.py
def test_base_exchange_requires_new_methods():
    """BaseExchange subclass must implement fetch_order, fetch_open_orders, fetch_closed_orders."""
    from src.integrations.exchange.base import BaseExchange

    class IncompleteExchange(BaseExchange):
        async def fetch_ticker(self, symbol): ...
        async def fetch_ohlcv(self, symbol, timeframe, limit=100): ...
        async def create_order(self, symbol, side, order_type, amount, price=None): ...
        async def fetch_balance(self): ...
        async def fetch_positions(self, symbol): ...
        async def set_leverage(self, symbol, leverage): ...
        def amount_to_precision(self, symbol, amount): ...
        async def close(self): ...
        # Missing: fetch_order, fetch_open_orders, fetch_closed_orders

    with pytest.raises(TypeError):
        IncompleteExchange()
```

- [ ] **Step 6: Run test to verify it fails**

Run: `cd /Users/z/Z/TradeBot && python -m pytest tests/test_exchange.py::test_base_exchange_requires_new_methods -v`
Expected: FAIL (IncompleteExchange instantiates without error — methods not yet required)

- [ ] **Step 7: Add abstract methods to BaseExchange**

In `src/integrations/exchange/base.py`, add to BaseExchange class:

```python
@abstractmethod
async def fetch_order(self, order_id: str) -> Order: ...
@abstractmethod
async def fetch_open_orders(self, symbol: str) -> list[Order]: ...
@abstractmethod
async def fetch_closed_orders(self, symbol: str, limit: int = 20) -> list[Order]: ...
```

- [ ] **Step 8: Run test to verify it passes**

Run: `cd /Users/z/Z/TradeBot && python -m pytest tests/test_exchange.py::test_base_exchange_requires_new_methods -v`
Expected: PASS

- [ ] **Step 9: Run all existing tests to check nothing is broken**

Run: `cd /Users/z/Z/TradeBot && python -m pytest tests/ -v`
Expected: OKXExchange tests FAIL (missing new abstract methods — fixed in Task 2)

- [ ] **Step 10: Commit**

```bash
git add src/integrations/exchange/base.py tests/test_exchange.py
git commit -m "feat: extend BaseExchange — Order.fee field, 3 order query methods"
```

---

## Task 2: OKXExchange — Implement New Abstract Methods

**Files:**
- Modify: `src/integrations/exchange/okx.py`
- Test: `tests/test_exchange.py`

- [ ] **Step 1: Write failing tests for OKX new methods**

```python
# Add to tests/test_exchange.py
async def test_okx_fetch_order():
    from src.integrations.exchange.okx import OKXExchange
    mock_ccxt = AsyncMock()
    mock_ccxt.fetch_order.return_value = {
        "id": "order_123", "symbol": "BTC/USDT:USDT",
        "side": "buy", "type": "market", "amount": 0.01,
        "price": 65000.0, "status": "closed", "fee": {"cost": 0.325},
    }
    exchange = OKXExchange.__new__(OKXExchange)
    exchange._client = mock_ccxt
    order = await exchange.fetch_order("order_123")
    assert order.id == "order_123"
    assert order.fee == 0.325


async def test_okx_fetch_open_orders():
    from src.integrations.exchange.okx import OKXExchange
    mock_ccxt = AsyncMock()
    mock_ccxt.fetch_open_orders.return_value = [
        {"id": "o1", "symbol": "BTC/USDT:USDT", "side": "sell", "type": "stop",
         "amount": 0.01, "price": 93000.0, "status": "open"},
    ]
    exchange = OKXExchange.__new__(OKXExchange)
    exchange._client = mock_ccxt
    orders = await exchange.fetch_open_orders("BTC/USDT:USDT")
    assert len(orders) == 1
    assert orders[0].status == "open"


async def test_okx_fetch_closed_orders():
    from src.integrations.exchange.okx import OKXExchange
    mock_ccxt = AsyncMock()
    mock_ccxt.fetch_orders.return_value = [
        {"id": "o1", "symbol": "BTC/USDT:USDT", "side": "buy", "type": "market",
         "amount": 0.01, "price": 95000.0, "status": "closed", "fee": {"cost": 0.475}},
    ]
    exchange = OKXExchange.__new__(OKXExchange)
    exchange._client = mock_ccxt
    orders = await exchange.fetch_closed_orders("BTC/USDT:USDT", limit=10)
    assert len(orders) == 1
    assert orders[0].fee == 0.475


async def test_okx_create_order_parses_fee():
    from src.integrations.exchange.okx import OKXExchange
    mock_ccxt = AsyncMock()
    mock_ccxt.create_order.return_value = {
        "id": "order_456", "symbol": "BTC/USDT:USDT",
        "side": "buy", "type": "market", "amount": 0.01,
        "price": 65000.0, "status": "closed", "fee": {"cost": 0.325},
    }
    exchange = OKXExchange.__new__(OKXExchange)
    exchange._client = mock_ccxt
    order = await exchange.create_order(symbol="BTC/USDT:USDT", side="buy", order_type="market", amount=0.01)
    assert order.fee == 0.325


async def test_okx_create_order_fee_none_when_missing():
    from src.integrations.exchange.okx import OKXExchange
    mock_ccxt = AsyncMock()
    mock_ccxt.create_order.return_value = {
        "id": "order_789", "symbol": "BTC/USDT:USDT",
        "side": "buy", "type": "market", "amount": 0.01,
        "price": 65000.0, "status": "closed",
    }
    exchange = OKXExchange.__new__(OKXExchange)
    exchange._client = mock_ccxt
    order = await exchange.create_order(symbol="BTC/USDT:USDT", side="buy", order_type="market", amount=0.01)
    assert order.fee is None
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd /Users/z/Z/TradeBot && python -m pytest tests/test_exchange.py::test_okx_fetch_order tests/test_exchange.py::test_okx_fetch_open_orders tests/test_exchange.py::test_okx_fetch_closed_orders tests/test_exchange.py::test_okx_create_order_parses_fee tests/test_exchange.py::test_okx_create_order_fee_none_when_missing -v`
Expected: FAIL

- [ ] **Step 3: Implement new methods and fee parsing in OKXExchange**

In `src/integrations/exchange/okx.py`:

```python
def _parse_fee(self, data: dict) -> float | None:
    """Extract fee from ccxt response. Returns None if not available."""
    fee_info = data.get("fee")
    if fee_info and fee_info.get("cost") is not None:
        return float(fee_info["cost"])
    return None

@_retry()
async def fetch_order(self, order_id: str) -> Order:  # type: ignore[override]
    data = await self._client.fetch_order(order_id)
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
async def fetch_open_orders(self, symbol: str) -> list[Order]:  # type: ignore[override]
    data = await self._client.fetch_open_orders(symbol)
    return [
        Order(
            id=o["id"],  # type: ignore[arg-type]
            symbol=o["symbol"],  # type: ignore[arg-type]
            side=o["side"],  # type: ignore[arg-type]
            order_type=o["type"],  # type: ignore[arg-type]
            amount=float(o["amount"]),  # type: ignore[arg-type]
            price=float(o["price"]) if o.get("price") else None,  # type: ignore[arg-type]
            status=o["status"],  # type: ignore[arg-type]
            fee=self._parse_fee(o),
        )
        for o in data
    ]

@_retry()
async def fetch_closed_orders(self, symbol: str, limit: int = 20) -> list[Order]:  # type: ignore[override]
    # OKX ccxt adapter: use fetch_orders with state filter
    data = await self._client.fetch_orders(symbol, limit=limit, params={"state": "filled"})
    return [
        Order(
            id=o["id"],  # type: ignore[arg-type]
            symbol=o["symbol"],  # type: ignore[arg-type]
            side=o["side"],  # type: ignore[arg-type]
            order_type=o["type"],  # type: ignore[arg-type]
            amount=float(o["amount"]),  # type: ignore[arg-type]
            price=float(o["price"]) if o.get("price") else None,  # type: ignore[arg-type]
            status=o["status"],  # type: ignore[arg-type]
            fee=self._parse_fee(o),
        )
        for o in data
    ]
```

Also update existing `create_order` to parse fee:

```python
# In create_order, change the return to:
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
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd /Users/z/Z/TradeBot && python -m pytest tests/test_exchange.py -v`
Expected: ALL PASS

- [ ] **Step 5: Commit**

```bash
git add src/integrations/exchange/okx.py tests/test_exchange.py
git commit -m "feat: OKXExchange — implement order query methods, parse fee"
```

---

## Task 3: Config and Dependency Updates

**Files:**
- Modify: `src/config.py`, `config/settings.yaml`, `pyproject.toml`
- Test: `tests/test_config.py`

- [ ] **Step 1: Write failing test for new config fields**

```python
# Add to tests/test_config.py
def test_exchange_config_simulated_fields():
    from src.config import ExchangeConfig
    config = ExchangeConfig(name="simulated", fee_rate=0.0005, precision={"BTC/USDT:USDT": 3})
    assert config.fee_rate == 0.0005
    assert config.precision["BTC/USDT:USDT"] == 3

def test_exchange_config_okx_ignores_sim_fields():
    from src.config import ExchangeConfig
    config = ExchangeConfig(name="okx")
    assert config.fee_rate is None
    assert config.precision is None
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd /Users/z/Z/TradeBot && python -m pytest tests/test_config.py::test_exchange_config_simulated_fields tests/test_config.py::test_exchange_config_okx_ignores_sim_fields -v`
Expected: FAIL

- [ ] **Step 3: Add fields to ExchangeConfig**

In `src/config.py`, update `ExchangeConfig`:

```python
class ExchangeConfig(BaseModel):
    name: str = "okx"
    api_key: str = ""
    secret: str = ""
    password: str = ""
    fee_rate: float | None = None
    precision: dict[str, int] | None = None
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd /Users/z/Z/TradeBot && python -m pytest tests/test_config.py -v`
Expected: ALL PASS

- [ ] **Step 5: Update settings.yaml**

Add to `config/settings.yaml` under `exchange:`:

```yaml
exchange:
  name: okx
  # fee_rate: 0.0005            # simulated mode: taker fee rate (0.05%)
  # precision:                  # simulated mode: symbol → decimal places
  #   BTC/USDT:USDT: 3
  #   ETH/USDT:USDT: 2
```

- [ ] **Step 6: Update pyproject.toml**

Change `ccxt>=4.0` to `ccxt[pro]>=4.0` in dependencies.

- [ ] **Step 7: Install updated dependency**

Run: `cd /Users/z/Z/TradeBot && uv sync`

- [ ] **Step 8: Run all tests**

Run: `cd /Users/z/Z/TradeBot && python -m pytest tests/ -v`
Expected: ALL PASS

- [ ] **Step 9: Commit**

```bash
git add src/config.py config/settings.yaml pyproject.toml uv.lock
git commit -m "feat: config — add fee_rate/precision, upgrade ccxt to ccxt[pro]"
```

---

## Task 4: Database Models — sim_* Tables

**Files:**
- Modify: `src/storage/models.py`
- Test: `tests/test_storage.py`

- [ ] **Step 1: Write failing test for new models**

```python
# Add to tests/test_storage.py
async def test_sim_tables_exist():
    """Verify sim_balances, sim_positions, sim_orders tables are created."""
    from sqlalchemy import inspect
    from src.storage.database import init_db
    from src.storage.models import SimBalance, SimPosition, SimOrder

    engine = await init_db("sqlite+aiosqlite:///:memory:")
    async with engine.connect() as conn:
        table_names = await conn.run_sync(lambda c: inspect(c).get_table_names())
    assert "sim_balances" in table_names
    assert "sim_positions" in table_names
    assert "sim_orders" in table_names
    await engine.dispose()


async def test_sim_balance_session_id_is_pk():
    from src.storage.models import SimBalance
    from src.storage.database import init_db, get_session

    engine = await init_db("sqlite+aiosqlite:///:memory:")
    async with get_session(engine) as session:
        session.add(SimBalance(session_id="s1", free_usdt=100.0, used_usdt=0.0))
        await session.commit()

        # Duplicate session_id should fail
        import sqlalchemy
        session.add(SimBalance(session_id="s1", free_usdt=200.0, used_usdt=0.0))
        with pytest.raises(sqlalchemy.exc.IntegrityError):
            await session.commit()
    await engine.dispose()


async def test_sim_position_unique_constraint():
    from src.storage.models import SimPosition
    from src.storage.database import init_db, get_session

    engine = await init_db("sqlite+aiosqlite:///:memory:")
    async with get_session(engine) as session:
        session.add(SimPosition(session_id="s1", symbol="BTC/USDT:USDT", side="long",
                                contracts=0.001, entry_price=95000.0, leverage=3))
        await session.commit()

        import sqlalchemy
        session.add(SimPosition(session_id="s1", symbol="BTC/USDT:USDT", side="long",
                                contracts=0.002, entry_price=96000.0, leverage=3))
        with pytest.raises(sqlalchemy.exc.IntegrityError):
            await session.commit()
    await engine.dispose()


async def test_sim_order_fields():
    from src.storage.models import SimOrder
    from src.storage.database import init_db, get_session

    engine = await init_db("sqlite+aiosqlite:///:memory:")
    async with get_session(engine) as session:
        order = SimOrder(
            session_id="s1", order_id="uuid-1", symbol="BTC/USDT:USDT",
            side="buy", position_side="long", order_type="market",
            amount=0.001, status="closed", filled_price=95010.0, fee=0.0475,
        )
        session.add(order)
        await session.commit()
        await session.refresh(order)
        assert order.filled_price == 95010.0
        assert order.fee == 0.0475
        assert order.trigger_price is None  # market order has no trigger_price
    await engine.dispose()
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd /Users/z/Z/TradeBot && python -m pytest tests/test_storage.py::test_sim_tables_exist tests/test_storage.py::test_sim_balance_session_id_is_pk tests/test_storage.py::test_sim_position_unique_constraint tests/test_storage.py::test_sim_order_fields -v`
Expected: FAIL

- [ ] **Step 3: Add sim_* models to models.py**

In `src/storage/models.py`, add after existing models:

```python
from sqlalchemy import UniqueConstraint


class SimBalance(Base):
    """Simulated exchange account balance — one row per session."""

    __tablename__ = "sim_balances"

    session_id: Mapped[str] = mapped_column(String(36), ForeignKey("sessions.id"), primary_key=True)
    free_usdt: Mapped[float] = mapped_column(Float)
    used_usdt: Mapped[float] = mapped_column(Float)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow, onupdate=_utcnow)


class SimPosition(Base):
    """Simulated exchange open position."""

    __tablename__ = "sim_positions"
    __table_args__ = (UniqueConstraint("session_id", "symbol"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    session_id: Mapped[str] = mapped_column(String(36), ForeignKey("sessions.id"), index=True)
    symbol: Mapped[str] = mapped_column(String(50))
    side: Mapped[str] = mapped_column(String(10))
    contracts: Mapped[float] = mapped_column(Float)
    entry_price: Mapped[float] = mapped_column(Float)
    leverage: Mapped[int] = mapped_column(Integer)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow, onupdate=_utcnow)


class SimOrder(Base):
    """Simulated exchange order record — full lifecycle."""

    __tablename__ = "sim_orders"

    id: Mapped[int] = mapped_column(primary_key=True)
    session_id: Mapped[str] = mapped_column(String(36), ForeignKey("sessions.id"), index=True)
    order_id: Mapped[str] = mapped_column(String(36), unique=True)
    symbol: Mapped[str] = mapped_column(String(50))
    side: Mapped[str] = mapped_column(String(10))
    position_side: Mapped[str] = mapped_column(String(10))
    order_type: Mapped[str] = mapped_column(String(20))
    amount: Mapped[float] = mapped_column(Float)
    trigger_price: Mapped[float | None] = mapped_column(Float, nullable=True)
    status: Mapped[str] = mapped_column(String(20))
    filled_price: Mapped[float | None] = mapped_column(Float, nullable=True)
    fee: Mapped[float | None] = mapped_column(Float, nullable=True)
    filled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)
```

Note: `UniqueConstraint` needs to be imported. Add `from sqlalchemy import UniqueConstraint` at the top (or add to existing import line).

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd /Users/z/Z/TradeBot && python -m pytest tests/test_storage.py -v`
Expected: ALL PASS

- [ ] **Step 5: Run full test suite**

Run: `cd /Users/z/Z/TradeBot && python -m pytest tests/ -v`
Expected: ALL PASS

- [ ] **Step 6: Commit**

```bash
git add src/storage/models.py tests/test_storage.py
git commit -m "feat: add SimBalance, SimPosition, SimOrder database models"
```

---

## Task 5: Scheduler — Event-Based Triggering

**Files:**
- Modify: `src/scheduler/scheduler.py`
- Rewrite: `tests/test_scheduler.py`

This is a significant rewrite. The new Scheduler supports both timed triggers and event-based triggers via `trigger()`.

- [ ] **Step 1: Write new tests for Scheduler**

Replace `tests/test_scheduler.py` with:

```python
import asyncio
import pytest


async def test_scheduler_fires_on_interval():
    from src.scheduler.scheduler import Scheduler

    fired = []

    async def callback(trigger_type: str, context):
        fired.append(trigger_type)

    scheduler = Scheduler(interval_seconds=0.1, callback=callback)
    task = asyncio.create_task(scheduler.start())
    await asyncio.sleep(0.35)
    scheduler.stop()
    await task
    # First immediate + at least 1 interval-based
    assert len(fired) >= 2
    assert fired[0] == "scheduled"


async def test_scheduler_trigger_wakes_from_sleep():
    from src.scheduler.scheduler import Scheduler

    fired = []

    async def callback(trigger_type: str, context):
        fired.append((trigger_type, context))

    scheduler = Scheduler(interval_seconds=10, callback=callback)  # long interval
    task = asyncio.create_task(scheduler.start())
    await asyncio.sleep(0.05)  # let first scheduled cycle run

    # Trigger should wake scheduler immediately
    await scheduler.trigger("conditional", context="fill_event_1")
    await asyncio.sleep(0.1)  # let triggered cycle run

    scheduler.stop()
    await task
    assert ("scheduled", None) in fired
    assert any(t == "conditional" for t, _ in fired)


async def test_scheduler_trigger_merges_multiple_events():
    from src.scheduler.scheduler import Scheduler

    fired = []

    async def callback(trigger_type: str, context):
        fired.append((trigger_type, context))
        await asyncio.sleep(0.05)  # simulate work

    scheduler = Scheduler(interval_seconds=10, callback=callback)
    task = asyncio.create_task(scheduler.start())
    await asyncio.sleep(0.05)  # let first scheduled cycle run

    # Multiple triggers during cycle — should merge
    await scheduler.trigger("conditional", context="event1")
    await scheduler.trigger("conditional", context="event2")
    await asyncio.sleep(0.2)

    scheduler.stop()
    await task
    # Second trigger should have context=None (merged)
    conditional_contexts = [ctx for t, ctx in fired if t == "conditional"]
    assert len(conditional_contexts) >= 1


async def test_scheduler_stop():
    from src.scheduler.scheduler import Scheduler

    async def noop(trigger_type: str, context):
        pass

    scheduler = Scheduler(interval_seconds=10, callback=noop)
    task = asyncio.create_task(scheduler.start())
    await asyncio.sleep(0.05)
    scheduler.stop()
    await task
    assert scheduler._running is False


async def test_scheduler_trigger_before_start():
    from src.scheduler.scheduler import Scheduler

    fired = []

    async def callback(trigger_type: str, context):
        fired.append(trigger_type)

    scheduler = Scheduler(interval_seconds=10, callback=callback)
    # Trigger before start — flag should be consumed after start
    await scheduler.trigger("conditional", context="early")
    task = asyncio.create_task(scheduler.start())
    await asyncio.sleep(0.1)

    scheduler.stop()
    await task
    # Should have processed the early trigger
    assert "conditional" in fired
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd /Users/z/Z/TradeBot && python -m pytest tests/test_scheduler.py -v`
Expected: FAIL (Scheduler signature changed)

- [ ] **Step 3: Rewrite Scheduler**

Replace `src/scheduler/scheduler.py`:

```python
from __future__ import annotations

import asyncio
import logging
from typing import Any, Awaitable, Callable

logger = logging.getLogger(__name__)


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
        self._pending_trigger = False
        self._pending_context: Any | None = None
        self._wake_event = asyncio.Event()

    async def trigger(self, trigger_type: str, context: Any | None = None) -> None:
        """Signal a conditional trigger. Does not run cycle directly."""
        if self._pending_trigger:
            self._pending_context = None  # merge: let agent query latest state
        else:
            self._pending_trigger = True
            self._pending_context = context
        self._wake_event.set()

    async def start(self) -> None:
        self._running = True
        logger.info(f"Scheduler started (interval={self._interval}s)")

        # First cycle runs immediately (preserving existing behavior)
        await self._run_cycle("scheduled", None)

        while self._running:
            await self._interruptible_sleep(self._interval)
            if not self._running:
                break

            if self._pending_trigger:
                self._pending_trigger = False
                ctx = self._pending_context
                self._pending_context = None
                await self._run_cycle("conditional", ctx)
            else:
                await self._run_cycle("scheduled", None)

            # Post-cycle check: new trigger during cycle execution
            if self._pending_trigger:
                self._pending_trigger = False
                self._pending_context = None
                await self._run_cycle("conditional", None)

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
        if self._pending_trigger:
            return
        self._wake_event.clear()
        try:
            await asyncio.wait_for(self._wake_event.wait(), timeout=duration)
        except asyncio.TimeoutError:
            pass
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd /Users/z/Z/TradeBot && python -m pytest tests/test_scheduler.py -v`
Expected: ALL PASS

- [ ] **Step 5: Commit**

```bash
git add src/scheduler/scheduler.py tests/test_scheduler.py
git commit -m "feat: scheduler — event-based trigger, interruptible sleep, new callback signature"
```

---

## Task 6: SimulatedExchange — Core State and Market Order

This is the main implementation file. We build it incrementally: first the state management and market orders, then conditionals and matching engine.

**Files:**
- Create: `src/integrations/exchange/simulated.py`
- Create: `tests/test_simulated_exchange.py`

- [ ] **Step 1: Write tests for initialization and fetch_balance**

```python
# tests/test_simulated_exchange.py
import pytest
from unittest.mock import AsyncMock, MagicMock
from src.integrations.exchange.base import Ticker


def _make_exchange(initial_balance=100.0, fee_rate=0.0005, symbol="BTC/USDT:USDT"):
    """Helper: create a SimulatedExchange without async start()."""
    from src.integrations.exchange.simulated import SimulatedExchange

    config = MagicMock()
    config.fee_rate = fee_rate
    config.precision = {"BTC/USDT:USDT": 3, "ETH/USDT:USDT": 2}

    exchange = SimulatedExchange(config=config, db_engine=None, session_id="test-session", symbol=symbol)
    # Initialize internal state directly for unit tests (bypass async start)
    exchange._free_usdt = initial_balance
    exchange._used_usdt = 0.0
    exchange._positions = {}
    exchange._pending_orders = []
    exchange._leverage = {}
    exchange._latest_ticker = Ticker(
        symbol=symbol, last=95000.0, bid=94990.0, ask=95010.0,
        high=96000.0, low=94000.0, base_volume=1000.0, timestamp=1712534400000,
    )
    exchange._running = True
    return exchange


async def test_fetch_balance_initial():
    ex = _make_exchange(initial_balance=100.0)
    balance = await ex.fetch_balance()
    assert balance.free_usdt == 100.0
    assert balance.used_usdt == 0.0
    assert balance.total_usdt == 100.0


async def test_fetch_balance_with_unrealized_pnl():
    ex = _make_exchange(initial_balance=70.0)
    ex._used_usdt = 30.0
    ex._positions["BTC/USDT:USDT"] = MagicMock(
        side="long", contracts=0.001, entry_price=94000.0, leverage=3,
    )
    balance = await ex.fetch_balance()
    # unrealized = (bid - entry) * contracts = (94990 - 94000) * 0.001 = 0.99
    assert balance.total_usdt == pytest.approx(100.99)
    assert balance.free_usdt == pytest.approx(70.99)
    assert balance.used_usdt == 30.0


async def test_fetch_balance_free_clamps_to_zero():
    ex = _make_exchange(initial_balance=5.0)
    ex._used_usdt = 30.0
    ex._positions["BTC/USDT:USDT"] = MagicMock(
        side="long", contracts=0.001, entry_price=100000.0, leverage=3,
    )
    balance = await ex.fetch_balance()
    # unrealized = (94990 - 100000) * 0.001 = -5.01
    assert balance.free_usdt == 0.0  # clamped
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd /Users/z/Z/TradeBot && python -m pytest tests/test_simulated_exchange.py -v`
Expected: FAIL (module not found)

- [ ] **Step 3: Create SimulatedExchange skeleton with state and fetch_balance**

Create `src/integrations/exchange/simulated.py`:

```python
from __future__ import annotations

import asyncio
import logging
import math
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Callable, Awaitable

from src.integrations.exchange.base import (
    Balance,
    BaseExchange,
    Candle,
    Order,
    Position,
    Ticker,
)

logger = logging.getLogger(__name__)


@dataclass
class FillEvent:
    order_id: str
    symbol: str
    side: str
    position_side: str
    trigger_reason: str
    fill_price: float
    amount: float
    fee: float
    timestamp: int


@dataclass
class _Position:
    """Internal position representation."""
    side: str
    contracts: float
    entry_price: float
    leverage: int
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    updated_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))


@dataclass
class _PendingOrder:
    """Internal pending order representation."""
    id: str
    symbol: str
    side: str
    position_side: str
    order_type: str
    amount: float
    trigger_price: float


class SimulatedExchange(BaseExchange):
    def __init__(self, config, db_engine, session_id: str, symbol: str):
        self._config = config
        self._db_engine = db_engine
        self._session_id = session_id
        self._symbol = symbol
        self._fee_rate: float = config.fee_rate or 0.0005
        self._precision: dict[str, int] = config.precision or {}

        # Internal state (initialized in start() or directly for tests)
        self._free_usdt: float = 0.0
        self._used_usdt: float = 0.0
        self._positions: dict[str, _Position] = {}
        self._pending_orders: list[_PendingOrder] = []
        self._leverage: dict[str, int] = {}
        self._latest_ticker: Ticker | None = None
        self._running = False
        self._lock = asyncio.Lock()
        self._fill_callback: Callable[[FillEvent], Awaitable[None]] | None = None
        self._error_count = 0

    def _validate_symbol(self, symbol: str) -> None:
        if symbol != self._symbol:
            raise ValueError(f"Symbol mismatch: expected {self._symbol}, got {symbol}")

    def _calc_unrealized_pnl(self, pos: _Position) -> float:
        if self._latest_ticker is None:
            return 0.0
        if pos.side == "long":
            return (self._latest_ticker.bid - pos.entry_price) * pos.contracts
        else:
            return (pos.entry_price - self._latest_ticker.ask) * pos.contracts

    def _calc_liquidation_price(self, pos: _Position) -> float:
        if pos.side == "long":
            return pos.entry_price * (1 - 1 / pos.leverage) / (1 - self._fee_rate)
        else:
            return pos.entry_price * (1 + 1 / pos.leverage) / (1 + self._fee_rate)

    # --- BaseExchange interface ---

    async def fetch_ticker(self, symbol: str) -> Ticker:
        self._validate_symbol(symbol)
        if self._latest_ticker is None:
            raise RuntimeError("No ticker data available yet")
        return self._latest_ticker

    async def fetch_ohlcv(self, symbol: str, timeframe: str, limit: int = 100) -> list[Candle]:
        self._validate_symbol(symbol)
        raise NotImplementedError("fetch_ohlcv requires ccxt instance — implemented in start()")

    async def fetch_balance(self) -> Balance:
        unrealized = sum(
            self._calc_unrealized_pnl(pos)
            for pos in self._positions.values()
        )
        return Balance(
            total_usdt=self._free_usdt + self._used_usdt + unrealized,
            free_usdt=max(0.0, self._free_usdt + unrealized),
            used_usdt=self._used_usdt,
        )

    async def fetch_positions(self, symbol: str) -> list[Position]:
        self._validate_symbol(symbol)
        pos = self._positions.get(symbol)
        if pos is None:
            return []
        return [Position(
            symbol=symbol,
            side=pos.side,
            contracts=pos.contracts,
            entry_price=pos.entry_price,
            unrealized_pnl=self._calc_unrealized_pnl(pos),
            leverage=pos.leverage,
            liquidation_price=self._calc_liquidation_price(pos),
        )]

    async def set_leverage(self, symbol: str, leverage: int) -> None:
        self._validate_symbol(symbol)
        if not isinstance(leverage, int):
            raise TypeError(f"leverage must be int, got {type(leverage).__name__}")
        if not 1 <= leverage <= 125:
            raise ValueError(f"leverage must be 1-125, got {leverage}")
        pos = self._positions.get(symbol)
        if pos is not None and pos.leverage != leverage:
            raise ValueError(
                f"Cannot change leverage from {pos.leverage} to {leverage} while holding position. "
                f"Close position first."
            )
        self._leverage[symbol] = leverage

    def amount_to_precision(self, symbol: str, amount: float) -> float:
        decimals = self._precision[symbol]  # KeyError if missing = fail fast
        factor = 10 ** decimals
        return math.floor(amount * factor) / factor

    async def create_order(
        self, symbol: str, side: str, order_type: str, amount: float, price: float | None = None,
    ) -> Order:
        self._validate_symbol(symbol)
        if amount <= 0:
            raise ValueError(f"amount must be > 0, got {amount}")
        if order_type not in ("market", "stop", "take_profit"):
            raise ValueError(f"Unknown order_type: {order_type}")
        if order_type in ("stop", "take_profit") and price is None:
            raise ValueError(f"price is required for {order_type} orders")

        async with self._lock:
            if order_type == "market":
                return self._execute_market_order(symbol, side, amount)
            else:
                return self._create_conditional_order(symbol, side, order_type, amount, price)  # type: ignore[arg-type]

    def _execute_market_order(self, symbol: str, side: str, amount: float) -> Order:
        if self._latest_ticker is None:
            raise RuntimeError("No ticker data available")

        pos = self._positions.get(symbol)
        is_close = (
            (pos is not None and pos.side == "long" and side == "sell") or
            (pos is not None and pos.side == "short" and side == "buy")
        )

        if is_close:
            return self._close_market_order(symbol, side, amount, pos)  # type: ignore[arg-type]
        else:
            return self._open_market_order(symbol, side, amount)

    def _open_market_order(self, symbol: str, side: str, amount: float) -> Order:
        ticker = self._latest_ticker
        assert ticker is not None
        fill_price = ticker.ask if side == "buy" else ticker.bid
        leverage = self._leverage.get(symbol, 1)
        margin = (fill_price * amount) / leverage
        fee = fill_price * amount * self._fee_rate
        required = margin + fee

        if self._free_usdt < required:
            raise ValueError(
                f"Insufficient balance: need {required:.2f}, have {self._free_usdt:.2f}"
            )

        # Check leverage consistency for add-to-position
        pos = self._positions.get(symbol)
        if pos is not None:
            if pos.leverage != leverage:
                raise ValueError(
                    f"Leverage mismatch: position has {pos.leverage}x, "
                    f"current is {leverage}x. Close position first."
                )
            # Merge position
            new_contracts = pos.contracts + amount
            new_entry = (pos.entry_price * pos.contracts + fill_price * amount) / new_contracts
            pos.contracts = new_contracts
            pos.entry_price = new_entry
            pos.updated_at = datetime.now(timezone.utc)
        else:
            position_side = "long" if side == "buy" else "short"
            self._positions[symbol] = _Position(
                side=position_side,
                contracts=amount,
                entry_price=fill_price,
                leverage=leverage,
            )

        self._free_usdt -= required
        self._used_usdt += margin
        self._free_usdt = round(self._free_usdt, 8)
        self._used_usdt = round(self._used_usdt, 8)

        position_side = "long" if side == "buy" else "short"
        order_id = str(uuid.uuid4())
        logger.info(f"Market order filled: {side} {amount} {symbol} @ {fill_price:.2f}, fee={fee:.4f}")
        return Order(
            id=order_id, symbol=symbol, side=side, order_type="market",
            amount=amount, price=fill_price, status="closed", fee=fee,
        )

    def _close_market_order(self, symbol: str, side: str, amount: float, pos: _Position) -> Order:
        ticker = self._latest_ticker
        assert ticker is not None
        # Clamp amount
        actual_amount = min(amount, pos.contracts)
        fill_price = ticker.bid if pos.side == "long" else ticker.ask
        pnl, fee, released_margin = self._close_position_core(
            symbol, pos.side, actual_amount, fill_price,
        )

        # Cancel orphaned orders if position fully closed
        if symbol not in self._positions:
            self._cancel_orphaned_orders()

        order_id = str(uuid.uuid4())
        logger.info(
            f"Market close filled: {side} {actual_amount} {symbol} @ {fill_price:.2f}, "
            f"pnl={pnl:.4f}, fee={fee:.4f}"
        )
        return Order(
            id=order_id, symbol=symbol, side=side, order_type="market",
            amount=actual_amount, price=fill_price, status="closed", fee=fee,
        )

    def _close_position_core(
        self, symbol: str, position_side: str, amount: float, fill_price: float,
        *, pnl_cap: bool = False,
    ) -> tuple[float, float, float]:
        """Core close logic shared by market close, conditional fill, and liquidation.
        Returns (pnl, fee, released_margin). Does NOT cancel orders."""
        pos = self._positions[symbol]
        released_margin = (pos.entry_price * amount) / pos.leverage
        fee = fill_price * amount * self._fee_rate

        if position_side == "long":
            pnl = (fill_price - pos.entry_price) * amount
        else:
            pnl = (pos.entry_price - fill_price) * amount

        if pnl_cap:
            pnl = max(pnl, -(released_margin - fee))

        self._used_usdt -= released_margin
        self._free_usdt += released_margin + pnl - fee
        self._free_usdt = round(self._free_usdt, 8)
        self._used_usdt = round(self._used_usdt, 8)

        if self._free_usdt < -0.01:
            raise RuntimeError(
                f"CRITICAL: free_usdt went negative ({self._free_usdt:.4f}). "
                f"This should never happen."
            )

        if amount >= pos.contracts:
            del self._positions[symbol]
        else:
            pos.contracts -= amount
            pos.contracts = round(pos.contracts, 8)
            pos.updated_at = datetime.now(timezone.utc)

        return pnl, fee, released_margin

    def _create_conditional_order(
        self, symbol: str, side: str, order_type: str, amount: float, price: float,
    ) -> Order:
        pos = self._positions.get(symbol)
        if pos is None:
            raise ValueError("Cannot create conditional order without a position")

        # Force amount = position contracts
        actual_amount = pos.contracts
        position_side = pos.side
        order_id = str(uuid.uuid4())

        self._pending_orders.append(_PendingOrder(
            id=order_id, symbol=symbol, side=side,
            position_side=position_side, order_type=order_type,
            amount=actual_amount, trigger_price=price,
        ))

        logger.info(f"Conditional order created: {order_type} {side} {actual_amount} {symbol} @ {price:.2f}")
        return Order(
            id=order_id, symbol=symbol, side=side, order_type=order_type,
            amount=actual_amount, price=price, status="open",
        )

    def _cancel_orphaned_orders(self) -> None:
        """Remove pending orders for symbols that no longer have positions."""
        self._pending_orders = [
            o for o in self._pending_orders
            if o.symbol in self._positions
        ]

    def _remove_order_by_id(self, order_id: str) -> None:
        self._pending_orders = [o for o in self._pending_orders if o.id != order_id]

    def _has_position(self, symbol: str) -> bool:
        return symbol in self._positions

    # --- Order query methods ---

    async def fetch_order(self, order_id: str) -> Order:
        raise NotImplementedError("fetch_order requires DB — implemented with persistence")

    async def fetch_open_orders(self, symbol: str) -> list[Order]:
        self._validate_symbol(symbol)
        return [
            Order(
                id=o.id, symbol=o.symbol, side=o.side, order_type=o.order_type,
                amount=o.amount, price=o.trigger_price, status="open",
            )
            for o in self._pending_orders
            if o.symbol == symbol
        ]

    async def fetch_closed_orders(self, symbol: str, limit: int = 20) -> list[Order]:
        raise NotImplementedError("fetch_closed_orders requires DB — implemented with persistence")

    # --- Fill callback ---

    def on_fill(self, callback: Callable[[FillEvent], Awaitable[None]]) -> None:
        self._fill_callback = callback

    # --- Lifecycle ---

    async def start(self) -> None:
        raise NotImplementedError("start() implemented in Task 8")

    async def close(self) -> None:
        raise NotImplementedError("close() implemented in Task 8")
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd /Users/z/Z/TradeBot && python -m pytest tests/test_simulated_exchange.py -v`
Expected: PASS

- [ ] **Step 5: Write tests for market order — open position**

```python
# Add to tests/test_simulated_exchange.py
async def test_market_buy_opens_long():
    ex = _make_exchange(initial_balance=100.0)
    ex._leverage["BTC/USDT:USDT"] = 3
    order = await ex.create_order("BTC/USDT:USDT", "buy", "market", 0.001)

    assert order.status == "closed"
    assert order.price == 95010.0  # ask
    assert order.fee == pytest.approx(95010.0 * 0.001 * 0.0005)
    assert order.amount == 0.001

    # Position created
    positions = await ex.fetch_positions("BTC/USDT:USDT")
    assert len(positions) == 1
    assert positions[0].side == "long"
    assert positions[0].contracts == 0.001

    # Balance updated
    balance = await ex.fetch_balance()
    margin = 95010.0 * 0.001 / 3
    fee = 95010.0 * 0.001 * 0.0005
    assert balance.used_usdt == pytest.approx(margin)


async def test_market_sell_opens_short():
    ex = _make_exchange(initial_balance=100.0)
    ex._leverage["BTC/USDT:USDT"] = 3
    order = await ex.create_order("BTC/USDT:USDT", "sell", "market", 0.001)

    assert order.price == 94990.0  # bid
    positions = await ex.fetch_positions("BTC/USDT:USDT")
    assert positions[0].side == "short"


async def test_market_order_insufficient_balance():
    ex = _make_exchange(initial_balance=1.0)
    ex._leverage["BTC/USDT:USDT"] = 1  # no leverage, need full amount
    with pytest.raises(ValueError, match="Insufficient balance"):
        await ex.create_order("BTC/USDT:USDT", "buy", "market", 0.001)


async def test_market_order_wrong_symbol():
    ex = _make_exchange()
    with pytest.raises(ValueError, match="Symbol mismatch"):
        await ex.create_order("ETH/USDT:USDT", "buy", "market", 0.001)


async def test_market_order_invalid_amount():
    ex = _make_exchange()
    with pytest.raises(ValueError, match="amount must be > 0"):
        await ex.create_order("BTC/USDT:USDT", "buy", "market", 0)


async def test_market_order_unknown_type():
    ex = _make_exchange()
    with pytest.raises(ValueError, match="Unknown order_type"):
        await ex.create_order("BTC/USDT:USDT", "buy", "limit", 0.001)
```

- [ ] **Step 6: Run tests to verify they pass**

Run: `cd /Users/z/Z/TradeBot && python -m pytest tests/test_simulated_exchange.py -v`
Expected: ALL PASS

- [ ] **Step 7: Write tests for market order — close position**

```python
# Add to tests/test_simulated_exchange.py
async def test_market_close_long():
    ex = _make_exchange(initial_balance=100.0)
    ex._leverage["BTC/USDT:USDT"] = 3
    await ex.create_order("BTC/USDT:USDT", "buy", "market", 0.001)

    # Now close
    order = await ex.create_order("BTC/USDT:USDT", "sell", "market", 0.001)
    assert order.status == "closed"
    assert order.price == 94990.0  # bid for closing long

    positions = await ex.fetch_positions("BTC/USDT:USDT")
    assert len(positions) == 0

    balance = await ex.fetch_balance()
    assert balance.used_usdt == 0.0


async def test_market_close_clamps_amount():
    ex = _make_exchange(initial_balance=100.0)
    ex._leverage["BTC/USDT:USDT"] = 3
    await ex.create_order("BTC/USDT:USDT", "buy", "market", 0.001)

    # Try to close more than held
    order = await ex.create_order("BTC/USDT:USDT", "sell", "market", 0.999)
    assert order.amount == 0.001  # clamped


async def test_add_to_position():
    ex = _make_exchange(initial_balance=200.0)
    ex._leverage["BTC/USDT:USDT"] = 3
    await ex.create_order("BTC/USDT:USDT", "buy", "market", 0.001)

    # Change ticker price slightly
    ex._latest_ticker = Ticker(
        symbol="BTC/USDT:USDT", last=96000.0, bid=95990.0, ask=96010.0,
        high=97000.0, low=94000.0, base_volume=1000.0, timestamp=1712534500000,
    )
    await ex.create_order("BTC/USDT:USDT", "buy", "market", 0.001)

    positions = await ex.fetch_positions("BTC/USDT:USDT")
    assert positions[0].contracts == 0.002
    # Weighted average: (95010 * 0.001 + 96010 * 0.001) / 0.002
    expected_entry = (95010.0 * 0.001 + 96010.0 * 0.001) / 0.002
    assert positions[0].entry_price == pytest.approx(expected_entry)


async def test_add_position_leverage_mismatch():
    ex = _make_exchange(initial_balance=200.0)
    ex._leverage["BTC/USDT:USDT"] = 3
    await ex.create_order("BTC/USDT:USDT", "buy", "market", 0.001)

    ex._leverage["BTC/USDT:USDT"] = 5
    with pytest.raises(ValueError, match="Leverage mismatch"):
        await ex.create_order("BTC/USDT:USDT", "buy", "market", 0.001)
```

- [ ] **Step 8: Run tests to verify they pass**

Run: `cd /Users/z/Z/TradeBot && python -m pytest tests/test_simulated_exchange.py -v`
Expected: ALL PASS

- [ ] **Step 9: Commit**

```bash
git add src/integrations/exchange/simulated.py tests/test_simulated_exchange.py
git commit -m "feat: SimulatedExchange — core state, market orders, close position"
```

---

## Task 7: SimulatedExchange — Conditional Orders and Matching Engine

**Files:**
- Modify: `src/integrations/exchange/simulated.py`
- Modify: `tests/test_simulated_exchange.py`

- [ ] **Step 1: Write tests for conditional orders**

```python
# Add to tests/test_simulated_exchange.py
async def test_stop_order_creation():
    ex = _make_exchange(initial_balance=100.0)
    ex._leverage["BTC/USDT:USDT"] = 3
    await ex.create_order("BTC/USDT:USDT", "buy", "market", 0.001)

    order = await ex.create_order("BTC/USDT:USDT", "sell", "stop", 0.001, price=93000.0)
    assert order.status == "open"
    assert order.price == 93000.0
    assert order.order_type == "stop"

    open_orders = await ex.fetch_open_orders("BTC/USDT:USDT")
    assert len(open_orders) == 1


async def test_stop_order_without_position():
    ex = _make_exchange()
    with pytest.raises(ValueError, match="Cannot create conditional order without a position"):
        await ex.create_order("BTC/USDT:USDT", "sell", "stop", 0.001, price=93000.0)


async def test_stop_order_without_price():
    ex = _make_exchange(initial_balance=100.0)
    ex._leverage["BTC/USDT:USDT"] = 3
    await ex.create_order("BTC/USDT:USDT", "buy", "market", 0.001)

    with pytest.raises(ValueError, match="price is required"):
        await ex.create_order("BTC/USDT:USDT", "sell", "stop", 0.001)


async def test_conditional_order_forces_full_amount():
    ex = _make_exchange(initial_balance=100.0)
    ex._leverage["BTC/USDT:USDT"] = 3
    await ex.create_order("BTC/USDT:USDT", "buy", "market", 0.001)

    # Request amount != position.contracts — should be forced to contracts
    order = await ex.create_order("BTC/USDT:USDT", "sell", "stop", 0.0005, price=93000.0)
    assert order.amount == 0.001  # forced to position.contracts
```

- [ ] **Step 2: Run tests to verify they pass**

Run: `cd /Users/z/Z/TradeBot && python -m pytest tests/test_simulated_exchange.py -v`
Expected: ALL PASS (conditional order creation already implemented in Task 6)

- [ ] **Step 3: Write tests for trigger conditions and matching**

```python
# Add to tests/test_simulated_exchange.py
async def test_should_trigger_stop_long():
    ex = _make_exchange(initial_balance=100.0)
    ex._leverage["BTC/USDT:USDT"] = 3
    await ex.create_order("BTC/USDT:USDT", "buy", "market", 0.001)
    await ex.create_order("BTC/USDT:USDT", "sell", "stop", 0.001, price=93000.0)

    # Simulate price drop below stop
    fill_events = []
    async def on_fill(event: FillEvent):
        fill_events.append(event)
    ex.on_fill(on_fill)

    tick = Ticker(
        symbol="BTC/USDT:USDT", last=92800.0, bid=92790.0, ask=92810.0,
        high=96000.0, low=92000.0, base_volume=1000.0, timestamp=1712535000000,
    )
    await ex._process_tick(tick)

    assert len(fill_events) == 1
    assert fill_events[0].trigger_reason == "stop"
    assert fill_events[0].fill_price == 92790.0  # bid for long close
    assert fill_events[0].position_side == "long"

    # Position should be closed
    positions = await ex.fetch_positions("BTC/USDT:USDT")
    assert len(positions) == 0

    # Pending orders should be cleared
    open_orders = await ex.fetch_open_orders("BTC/USDT:USDT")
    assert len(open_orders) == 0


async def test_should_trigger_take_profit_long():
    ex = _make_exchange(initial_balance=100.0)
    ex._leverage["BTC/USDT:USDT"] = 3
    await ex.create_order("BTC/USDT:USDT", "buy", "market", 0.001)
    await ex.create_order("BTC/USDT:USDT", "sell", "take_profit", 0.001, price=97000.0)

    fill_events = []
    async def on_fill(event: FillEvent):
        fill_events.append(event)
    ex.on_fill(on_fill)

    tick = Ticker(
        symbol="BTC/USDT:USDT", last=97500.0, bid=97490.0, ask=97510.0,
        high=98000.0, low=94000.0, base_volume=1000.0, timestamp=1712535000000,
    )
    await ex._process_tick(tick)

    assert len(fill_events) == 1
    assert fill_events[0].trigger_reason == "take_profit"


async def test_liquidation_triggers_before_stop():
    """Liquidation should fire before stop loss when price gaps through both."""
    ex = _make_exchange(initial_balance=100.0)
    ex._leverage["BTC/USDT:USDT"] = 10
    await ex.create_order("BTC/USDT:USDT", "buy", "market", 0.001)
    await ex.create_order("BTC/USDT:USDT", "sell", "stop", 0.001, price=90000.0)

    fill_events = []
    async def on_fill(event: FillEvent):
        fill_events.append(event)
    ex.on_fill(on_fill)

    # Price crashes well below liquidation price
    tick = Ticker(
        symbol="BTC/USDT:USDT", last=80000.0, bid=79990.0, ask=80010.0,
        high=96000.0, low=79000.0, base_volume=1000.0, timestamp=1712535000000,
    )
    await ex._process_tick(tick)

    # Liquidation should trigger, not stop
    assert len(fill_events) == 1
    assert fill_events[0].trigger_reason == "liquidation"

    # Balance should not be negative
    balance = await ex.fetch_balance()
    assert balance.free_usdt >= 0.0


async def test_no_trigger_when_price_above_stop():
    ex = _make_exchange(initial_balance=100.0)
    ex._leverage["BTC/USDT:USDT"] = 3
    await ex.create_order("BTC/USDT:USDT", "buy", "market", 0.001)
    await ex.create_order("BTC/USDT:USDT", "sell", "stop", 0.001, price=93000.0)

    fill_events = []
    async def on_fill(event: FillEvent):
        fill_events.append(event)
    ex.on_fill(on_fill)

    # Price stays above stop
    tick = Ticker(
        symbol="BTC/USDT:USDT", last=94000.0, bid=93990.0, ask=94010.0,
        high=96000.0, low=93000.0, base_volume=1000.0, timestamp=1712535000000,
    )
    await ex._process_tick(tick)

    assert len(fill_events) == 0
    open_orders = await ex.fetch_open_orders("BTC/USDT:USDT")
    assert len(open_orders) == 1  # still pending
```

- [ ] **Step 4: Implement _process_tick (matching engine core without WebSocket)**

Add to `SimulatedExchange` class in `src/integrations/exchange/simulated.py`:

```python
def _should_trigger(self, order: _PendingOrder, ticker: Ticker) -> bool:
    if order.order_type == "stop":
        if order.position_side == "long":
            return ticker.bid <= order.trigger_price
        else:
            return ticker.ask >= order.trigger_price
    elif order.order_type == "take_profit":
        if order.position_side == "long":
            return ticker.bid >= order.trigger_price
        else:
            return ticker.ask <= order.trigger_price
    return False

def _execute_fill(self, order: _PendingOrder, ticker: Ticker) -> FillEvent:
    pos = self._positions[order.symbol]
    actual_amount = min(order.amount, pos.contracts)
    fill_price = ticker.bid if pos.side == "long" else ticker.ask
    pnl, fee, _ = self._close_position_core(
        order.symbol, pos.side, actual_amount, fill_price,
    )
    now_ms = int(datetime.now(timezone.utc).timestamp() * 1000)
    return FillEvent(
        order_id=order.id, symbol=order.symbol, side=order.side,
        position_side=order.position_side, trigger_reason=order.order_type,
        fill_price=fill_price, amount=actual_amount, fee=fee,
        timestamp=now_ms,
    )

def _force_liquidate(self, pos: _Position, symbol: str, price: float) -> FillEvent:
    pnl, fee, _ = self._close_position_core(
        symbol, pos.side, pos.contracts, price, pnl_cap=True,
    )
    order_id = str(uuid.uuid4())
    now_ms = int(datetime.now(timezone.utc).timestamp() * 1000)
    logger.warning(f"LIQUIDATION: {pos.side} {pos.contracts} {symbol} @ {price:.2f}")
    return FillEvent(
        order_id=order_id, symbol=symbol,
        side="sell" if pos.side == "long" else "buy",
        position_side=pos.side, trigger_reason="liquidation",
        fill_price=price, amount=pos.contracts, fee=fee,
        timestamp=now_ms,
    )

async def _process_tick(self, ticker: Ticker) -> None:
    """Process a single tick — check liquidations and conditional orders.
    Extracted from _matching_loop for testability."""
    self._latest_ticker = ticker

    triggered: list[FillEvent] = []
    async with self._lock:
        # 1. Liquidation check (must be before conditional orders)
        for symbol, pos in list(self._positions.items()):
            liq = self._calc_liquidation_price(pos)
            if pos.side == "long" and ticker.bid <= liq:
                fill = self._force_liquidate(pos, symbol, ticker.bid)
                triggered.append(fill)
            elif pos.side == "short" and ticker.ask >= liq:
                fill = self._force_liquidate(pos, symbol, ticker.ask)
                triggered.append(fill)

        # 2. Conditional order check
        for order in list(self._pending_orders):
            if self._should_trigger(order, ticker):
                if not self._has_position(order.symbol):
                    continue
                fill = self._execute_fill(order, ticker)
                triggered.append(fill)

        if triggered:
            for fill in triggered:
                self._remove_order_by_id(fill.order_id)
            self._cancel_orphaned_orders()
            # Persistence handled in Task 8

    # Notify outside lock
    for fill in triggered:
        if self._fill_callback:
            await self._fill_callback(fill)
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `cd /Users/z/Z/TradeBot && python -m pytest tests/test_simulated_exchange.py -v`
Expected: ALL PASS

- [ ] **Step 6: Write tests for set_leverage and amount_to_precision**

```python
# Add to tests/test_simulated_exchange.py
async def test_set_leverage():
    ex = _make_exchange()
    await ex.set_leverage("BTC/USDT:USDT", 5)
    assert ex._leverage["BTC/USDT:USDT"] == 5


async def test_set_leverage_rejects_float():
    ex = _make_exchange()
    with pytest.raises(TypeError):
        await ex.set_leverage("BTC/USDT:USDT", 2.5)


async def test_set_leverage_rejects_out_of_range():
    ex = _make_exchange()
    with pytest.raises(ValueError):
        await ex.set_leverage("BTC/USDT:USDT", 200)


async def test_set_leverage_rejects_with_position():
    ex = _make_exchange(initial_balance=100.0)
    ex._leverage["BTC/USDT:USDT"] = 3
    await ex.create_order("BTC/USDT:USDT", "buy", "market", 0.001)

    with pytest.raises(ValueError, match="Cannot change leverage"):
        await ex.set_leverage("BTC/USDT:USDT", 5)


def test_amount_to_precision():
    ex = _make_exchange()
    assert ex.amount_to_precision("BTC/USDT:USDT", 0.001567) == 0.001
    assert ex.amount_to_precision("BTC/USDT:USDT", 0.0019999) == 0.001


def test_amount_to_precision_unknown_symbol():
    ex = _make_exchange()
    with pytest.raises(KeyError):
        ex.amount_to_precision("UNKNOWN/USDT:USDT", 1.0)
```

- [ ] **Step 7: Run tests to verify they pass**

Run: `cd /Users/z/Z/TradeBot && python -m pytest tests/test_simulated_exchange.py -v`
Expected: ALL PASS

- [ ] **Step 8: Commit**

```bash
git add src/integrations/exchange/simulated.py tests/test_simulated_exchange.py
git commit -m "feat: SimulatedExchange — conditional orders, matching engine, liquidation"
```

---

## Task 8: SimulatedExchange — Persistence and Lifecycle

**Files:**
- Modify: `src/integrations/exchange/simulated.py`
- Modify: `tests/test_simulated_exchange.py`

- [ ] **Step 1: Write tests for persistence and recovery**

```python
# Add to tests/test_simulated_exchange.py
async def test_persist_and_restore():
    """State should survive persist → new instance → start()."""
    from src.storage.database import init_db, get_session
    from src.storage.models import SimBalance
    from src.integrations.exchange.simulated import SimulatedExchange

    engine = await init_db("sqlite+aiosqlite:///:memory:")

    # Create a session record (required for FK)
    from src.storage.models import Session
    async with get_session(engine) as sess:
        sess.add(Session(id="test-s", name="test", initial_balance=100.0))
        await sess.commit()

    config = MagicMock()
    config.fee_rate = 0.0005
    config.precision = {"BTC/USDT:USDT": 3}

    # First instance: create state
    ex1 = SimulatedExchange(config, engine, "test-s", "BTC/USDT:USDT")
    await ex1._init_state(initial_balance=100.0)
    ex1._latest_ticker = Ticker(
        symbol="BTC/USDT:USDT", last=95000.0, bid=94990.0, ask=95010.0,
        high=96000.0, low=94000.0, base_volume=1000.0, timestamp=1712534400000,
    )
    ex1._leverage["BTC/USDT:USDT"] = 3
    await ex1.create_order("BTC/USDT:USDT", "buy", "market", 0.001)
    await ex1._persist_state()

    # Second instance: restore
    ex2 = SimulatedExchange(config, engine, "test-s", "BTC/USDT:USDT")
    await ex2._restore_state()
    ex2._latest_ticker = ex1._latest_ticker

    balance = await ex2.fetch_balance()
    assert balance.used_usdt > 0

    positions = await ex2.fetch_positions("BTC/USDT:USDT")
    assert len(positions) == 1
    assert positions[0].side == "long"

    await engine.dispose()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd /Users/z/Z/TradeBot && python -m pytest tests/test_simulated_exchange.py::test_persist_and_restore -v`
Expected: FAIL

- [ ] **Step 3: Implement _init_state, _persist_state, _restore_state**

Add to `SimulatedExchange` in `src/integrations/exchange/simulated.py`:

```python
async def _init_state(self, initial_balance: float) -> None:
    """Initialize state for a new session."""
    self._free_usdt = initial_balance
    self._used_usdt = 0.0
    self._positions = {}
    self._pending_orders = []
    self._leverage = {}

async def _restore_state(self) -> None:
    """Restore state from sim_* tables."""
    from sqlalchemy import select
    from src.storage.database import get_session
    from src.storage.models import SimBalance, SimPosition, SimOrder

    async with get_session(self._db_engine) as session:
        # Balance
        result = await session.execute(
            select(SimBalance).where(SimBalance.session_id == self._session_id)
        )
        bal = result.scalar_one_or_none()
        if bal:
            self._free_usdt = bal.free_usdt
            self._used_usdt = bal.used_usdt
        else:
            return  # No state to restore — caller should _init_state

        # Positions
        result = await session.execute(
            select(SimPosition).where(SimPosition.session_id == self._session_id)
        )
        for pos in result.scalars().all():
            self._positions[pos.symbol] = _Position(
                side=pos.side, contracts=pos.contracts,
                entry_price=pos.entry_price, leverage=pos.leverage,
                created_at=pos.created_at, updated_at=pos.updated_at,
            )
            self._leverage[pos.symbol] = pos.leverage

        # Pending orders (only open)
        result = await session.execute(
            select(SimOrder)
            .where(SimOrder.session_id == self._session_id)
            .where(SimOrder.status == "open")
        )
        for o in result.scalars().all():
            self._pending_orders.append(_PendingOrder(
                id=o.order_id, symbol=o.symbol, side=o.side,
                position_side=o.position_side, order_type=o.order_type,
                amount=o.amount, trigger_price=o.trigger_price,  # type: ignore[arg-type]
            ))

    logger.info(
        f"Restored state: balance={self._free_usdt:.2f}/{self._used_usdt:.2f}, "
        f"positions={len(self._positions)}, pending_orders={len(self._pending_orders)}"
    )

async def _persist_state(self, new_orders: list[Order] | None = None) -> None:
    """Persist all internal state to sim_* tables in a single transaction."""
    from sqlalchemy import delete, update
    from src.storage.database import get_session
    from src.storage.models import SimBalance, SimPosition, SimOrder

    async with get_session(self._db_engine) as session:
        # 1. Upsert balance
        from sqlalchemy.dialects.sqlite import insert as sqlite_insert
        stmt = sqlite_insert(SimBalance).values(
            session_id=self._session_id,
            free_usdt=self._free_usdt,
            used_usdt=self._used_usdt,
        )
        stmt = stmt.on_conflict_do_update(
            index_elements=["session_id"],
            set_={"free_usdt": stmt.excluded.free_usdt, "used_usdt": stmt.excluded.used_usdt},
        )
        await session.execute(stmt)

        # 2. Positions: delete → insert
        await session.execute(
            delete(SimPosition).where(SimPosition.session_id == self._session_id)
        )
        for symbol, pos in self._positions.items():
            session.add(SimPosition(
                session_id=self._session_id, symbol=symbol,
                side=pos.side, contracts=pos.contracts,
                entry_price=pos.entry_price, leverage=pos.leverage,
                created_at=pos.created_at, updated_at=pos.updated_at,
            ))

        # 3. Orders: reconciliation + insert new
        pending_ids = [o.id for o in self._pending_orders]
        if pending_ids:
            await session.execute(
                update(SimOrder)
                .where(SimOrder.session_id == self._session_id)
                .where(SimOrder.status == "open")
                .where(SimOrder.order_id.notin_(pending_ids))
                .values(status="cancelled")
            )
        else:
            await session.execute(
                update(SimOrder)
                .where(SimOrder.session_id == self._session_id)
                .where(SimOrder.status == "open")
                .values(status="cancelled")
            )

        if new_orders:
            for o in new_orders:
                session.add(SimOrder(
                    session_id=self._session_id, order_id=o.id,
                    symbol=o.symbol, side=o.side,
                    position_side=self._positions.get(o.symbol, _Position("", 0, 0, 1)).side if o.order_type != "market" else ("long" if o.side == "buy" else "short"),
                    order_type=o.order_type, amount=o.amount,
                    trigger_price=o.price if o.status == "open" else None,
                    status=o.status,
                    filled_price=o.price if o.status == "closed" else None,
                    fee=o.fee,
                    filled_at=datetime.now(timezone.utc) if o.status == "closed" else None,
                ))

        await session.commit()
```

Note: This is a simplified implementation. The full version will handle all edge cases during implementation. The key pattern is: single transaction, upsert balance, delete+insert positions, reconciliation for orders.

- [ ] **Step 4: Run test to verify it passes**

Run: `cd /Users/z/Z/TradeBot && python -m pytest tests/test_simulated_exchange.py::test_persist_and_restore -v`
Expected: PASS

- [ ] **Step 5: Implement start() and close()**

Add to `SimulatedExchange`:

```python
async def start(self) -> None:
    """Async initialization: restore state, connect WebSocket, start matching."""
    import ccxt.pro as ccxtpro

    # Restore or initialize state
    from sqlalchemy import select
    from src.storage.database import get_session
    from src.storage.models import SimBalance, Session

    async with get_session(self._db_engine) as session:
        result = await session.execute(
            select(SimBalance).where(SimBalance.session_id == self._session_id)
        )
        has_state = result.scalar_one_or_none() is not None

    if has_state:
        await self._restore_state()
    else:
        # Get initial balance from Session
        async with get_session(self._db_engine) as session:
            result = await session.execute(
                select(Session).where(Session.id == self._session_id)
            )
            trading_session = result.scalar_one()
        await self._init_state(trading_session.initial_balance)
        await self._persist_state()

    # Connect ccxt Pro for market data
    self._ccxt = ccxtpro.okx()
    seed_ticker = await self._ccxt.fetch_ticker(self._symbol)
    self._latest_ticker = Ticker(
        symbol=seed_ticker["symbol"],
        last=float(seed_ticker["last"]),
        bid=float(seed_ticker["bid"]),
        ask=float(seed_ticker["ask"]),
        high=float(seed_ticker["high"]),
        low=float(seed_ticker["low"]),
        base_volume=float(seed_ticker["baseVolume"]),
        timestamp=seed_ticker["timestamp"],
    )

    self._running = True
    self._matching_task = asyncio.create_task(self._matching_loop())
    logger.info(f"SimulatedExchange started: {self._symbol}, seed ticker @ {self._latest_ticker.last}")

async def _matching_loop(self) -> None:
    while self._running:
        try:
            raw = await self._ccxt.watch_ticker(self._symbol)
            ticker = Ticker(
                symbol=raw["symbol"], last=float(raw["last"]),
                bid=float(raw["bid"]), ask=float(raw["ask"]),
                high=float(raw["high"]), low=float(raw["low"]),
                base_volume=float(raw["baseVolume"]), timestamp=raw["timestamp"],
            )
            await self._process_tick(ticker)
        except asyncio.CancelledError:
            break
        except Exception:
            self._error_count += 1
            logger.error("Matching loop error (count=%d)", self._error_count, exc_info=True)
            if self._error_count >= 3:
                await asyncio.sleep(min(5 * self._error_count, 60))
        else:
            self._error_count = 0

async def close(self) -> None:
    self._running = False
    if hasattr(self, "_matching_task"):
        self._matching_task.cancel()
        try:
            await self._matching_task
        except asyncio.CancelledError:
            pass
    if hasattr(self, "_ccxt"):
        await self._ccxt.close()
    logger.info("SimulatedExchange closed")
```

Also update `_process_tick` to call `_persist_state` when there are triggers:

```python
# In _process_tick, after the lock block, before notify:
        if triggered:
            for fill in triggered:
                self._remove_order_by_id(fill.order_id)
            self._cancel_orphaned_orders()
            if self._db_engine:
                await self._persist_state()
```

And update `fetch_ohlcv`:

```python
async def fetch_ohlcv(self, symbol: str, timeframe: str, limit: int = 100) -> list[Candle]:
    self._validate_symbol(symbol)
    if not hasattr(self, "_ccxt"):
        raise RuntimeError("Exchange not started — call start() first")
    data = await self._ccxt.fetch_ohlcv(symbol, timeframe, limit=limit)
    return [
        Candle(timestamp=int(r[0]), open=float(r[1]), high=float(r[2]),
               low=float(r[3]), close=float(r[4]), volume=float(r[5]))
        for r in data
    ]
```

- [ ] **Step 6: Run full test suite**

Run: `cd /Users/z/Z/TradeBot && python -m pytest tests/ -v`
Expected: ALL PASS (WebSocket tests skipped in unit tests — lifecycle tested via _process_tick)

- [ ] **Step 7: Commit**

```bash
git add src/integrations/exchange/simulated.py tests/test_simulated_exchange.py
git commit -m "feat: SimulatedExchange — persistence, state recovery, lifecycle (start/close)"
```

---

## Task 9: App Integration — Exchange Factory and Fill Handler

**Files:**
- Modify: `src/cli/app.py`

- [ ] **Step 1: Update app.py exchange creation and scheduler wiring**

In `src/cli/app.py`, modify the `run()` function:

```python
# Replace the exchange creation block with:
if settings.exchange.name == "simulated":
    from src.integrations.exchange.simulated import SimulatedExchange
    exchange = SimulatedExchange(
        config=settings.exchange,
        db_engine=engine,
        session_id=session_id,
        symbol=settings.trading.symbol,
    )
    console.print(f"Exchange: simulated (local matching)")
else:
    exchange = OKXExchange(
        api_key=settings.exchange.api_key,
        secret=settings.exchange.secret,
        password=settings.exchange.password,
    )
    console.print(f"Exchange: {settings.exchange.name} (REAL account)")
```

Update `on_tick` to accept new parameters:

```python
async def on_tick(trigger_type: str, context: Any | None):
    if shutdown_event.is_set():
        return
    try:
        await run_agent_cycle(agent, deps, trigger_type, budget, engine)
    except Exception:
        logger.exception("Agent cycle failed")
```

Register fill handler and start exchange:

```python
# After creating exchange, before scheduler:
if settings.exchange.name == "simulated":
    from src.integrations.exchange.simulated import FillEvent

    def _create_fill_handler(sched):
        async def handle_fill(event: FillEvent):
            try:
                pass  # Agent layer recording — out of scope for this phase
            finally:
                await sched.trigger("conditional", context=event)
        return handle_fill

    # Register fill handler before start
    exchange.on_fill(_create_fill_handler(scheduler))
    await exchange.start()
```

Update `run_agent_cycle` signature to accept `context`:

```python
async def run_agent_cycle(agent, deps, trigger_type, budget, engine, context=None):
    # ... existing code ...
    # Update prompt to include context info
    if context is not None and hasattr(context, 'trigger_reason'):
        prompt += (
            f"\n\nIMPORTANT EVENT: {context.trigger_reason} triggered "
            f"— {context.symbol} {context.amount} @ {context.fill_price}"
        )
```

- [ ] **Step 2: Run full test suite**

Run: `cd /Users/z/Z/TradeBot && python -m pytest tests/ -v`
Expected: ALL PASS

- [ ] **Step 3: Commit**

```bash
git add src/cli/app.py
git commit -m "feat: app integration — exchange factory, fill handler, scheduler wiring"
```

---

## Task 10: Integration Test and Final Verification

- [ ] **Step 1: Run full test suite**

Run: `cd /Users/z/Z/TradeBot && python -m pytest tests/ -v --tb=short`
Expected: ALL PASS

- [ ] **Step 2: Verify simulated config works**

Create a test config:
```bash
cp config/settings.yaml config/settings_sim.yaml
```

Edit `config/settings_sim.yaml`: set `exchange.name: simulated`, add `fee_rate: 0.0005`, add `precision`.

- [ ] **Step 3: Commit all remaining changes**

```bash
git add -A
git commit -m "feat: SimulatedExchange integration complete — config, tests, app wiring"
```

---

## Summary

| Task | What it builds | Key files |
|---|---|---|
| 1 | BaseExchange interface extensions | base.py |
| 2 | OKXExchange implements new methods | okx.py |
| 3 | Config + dependency updates | config.py, pyproject.toml |
| 4 | Database models (sim_* tables) | models.py |
| 5 | Scheduler event triggering | scheduler.py |
| 6 | SimulatedExchange core + market orders | simulated.py |
| 7 | Conditional orders + matching engine | simulated.py |
| 8 | Persistence + lifecycle | simulated.py |
| 9 | App integration | app.py |
| 10 | Integration test | full suite |
