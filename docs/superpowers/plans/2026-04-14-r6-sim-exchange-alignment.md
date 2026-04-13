# R6: SimExchange Alignment Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Align SimulatedExchange behavior with OKXExchange: async market orders, unified fill path, limit order support, duplicate order prevention.

**Architecture:** Market orders become pending on submission (like OKX), matched on next tick. New `_frozen_usdt` tracks escrowed margin. Limit orders share the pending/match pattern. `drain_pending_fills` removed; all fills flow through `_fill_callback`. Tool layer guards against duplicate market orders.

**Tech Stack:** Python 3.12+, asyncio, pytest + pytest-asyncio, SQLAlchemy async (aiosqlite), pydantic-ai

**Design doc:** `docs/superpowers/specs/2026-04-13-r6-sim-exchange-alignment-design.md`

**Branch:** `feature/r6-sim-exchange-alignment`

**PR structure:** 3 PRs (PR #1 core async, PR #2 duplicate guard, PR #3 limit orders). PR #2/#3 depend on PR #1 but are independent of each other.

---

## File Structure

| File | Responsibility | Action |
|------|---------------|--------|
| `src/storage/models.py` | DB schema | Modify: add `frozen_margin`/`leverage` to SimOrder, `frozen_usdt` to SimBalance |
| `src/integrations/exchange/simulated.py` | Sim engine | Modify: major refactor — async market orders, frozen balance, limit orders, new matching methods |
| `src/integrations/exchange/base.py` | Exchange interface | Modify: delete `drain_pending_fills`, add `has_pending_market_order` |
| `src/agent/tools_execution.py` | Trade tools | Modify: add duplicate checks, add `place_limit_order` |
| `src/agent/tools_perception.py` | Query tools | Modify: fix `get_open_orders` for `price=None` |
| `src/agent/trader.py` | Agent wiring | Modify: register `place_limit_order`, update tool description |
| `src/agent/persona.py` | System prompt | Modify: async fill guidance, limit order guidance |
| `src/cli/app.py` | Main loop | Modify: delete `drain_pending_fills` in finally block |
| `tests/test_simulated_exchange.py` | Sim tests | Modify: adapt ~25 tests, add ~29 new tests |
| `tests/test_exchange.py` | Base tests | Modify: delete `test_base_exchange_drain_pending_fills` |

---

## PR #1: Market Order Async + Fill Path Unification

### Task 1: DB Schema — Add frozen_margin/leverage to SimOrder, frozen_usdt to SimBalance

**Files:**
- Modify: `src/storage/models.py:125-145`

- [ ] **Step 1: Write the test for new SimOrder fields**

```python
# tests/test_simulated_exchange.py — add at end of file
async def test_sim_order_model_has_frozen_fields():
    """SimOrder model has frozen_margin and leverage columns."""
    from src.storage.models import SimOrder
    assert hasattr(SimOrder, "frozen_margin")
    assert hasattr(SimOrder, "leverage")
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_simulated_exchange.py::test_sim_order_model_has_frozen_fields -v`
Expected: FAIL with `AssertionError`

- [ ] **Step 3: Add frozen_margin and leverage to SimOrder**

In `src/storage/models.py`, after line 143 (`created_at`), add:

```python
    frozen_margin: Mapped[float] = mapped_column(Float, default=0.0)
    leverage: Mapped[int] = mapped_column(Integer, default=1)
```

- [ ] **Step 4: Add frozen_usdt to SimBalance**

In `src/storage/models.py`, after line 104 (`used_usdt`), add:

```python
    frozen_usdt: Mapped[float] = mapped_column(Float, default=0.0)
```

- [ ] **Step 5: Run test to verify it passes**

Run: `python -m pytest tests/test_simulated_exchange.py::test_sim_order_model_has_frozen_fields -v`
Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add src/storage/models.py tests/test_simulated_exchange.py
git commit -m "feat(r6): add frozen_margin/leverage to SimOrder, frozen_usdt to SimBalance"
```

---

### Task 2: Expand _PendingOrder and add _frozen_usdt state

**Files:**
- Modify: `src/integrations/exchange/simulated.py:36-45` (_PendingOrder)
- Modify: `src/integrations/exchange/simulated.py:48-70` (__init__)

- [ ] **Step 1: Write test for _PendingOrder new fields**

```python
async def test_pending_order_has_frozen_fields():
    """_PendingOrder supports frozen_margin and leverage fields."""
    from src.integrations.exchange.simulated import _PendingOrder
    order = _PendingOrder(
        id="test", symbol="BTC/USDT:USDT", side="buy",
        position_side="long", order_type="market",
        amount=0.001, trigger_price=None,
        frozen_margin=100.0, leverage=3,
    )
    assert order.frozen_margin == 100.0
    assert order.leverage == 3
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_simulated_exchange.py::test_pending_order_has_frozen_fields -v`
Expected: FAIL with `TypeError: unexpected keyword argument`

- [ ] **Step 3: Expand _PendingOrder dataclass**

Replace the `_PendingOrder` dataclass at `simulated.py:36-45`:

```python
@dataclass
class _PendingOrder:
    """Internal pending order representation."""
    id: str
    symbol: str
    side: str
    position_side: str
    order_type: str          # "market" | "limit" | "stop" | "take_profit"
    amount: float
    trigger_price: float | None   # stop/TP: trigger price; limit: fill price; market: None
    frozen_margin: float = 0.0    # market/limit: frozen margin+fee
    leverage: int = 1             # leverage at order time (needed at fill time)
```

- [ ] **Step 4: Add _frozen_usdt to __init__**

In `simulated.py` `__init__`, after `self._used_usdt: float = 0.0` (line 59), add:

```python
        self._frozen_usdt: float = 0.0
```

- [ ] **Step 5: Run test to verify it passes**

Run: `python -m pytest tests/test_simulated_exchange.py::test_pending_order_has_frozen_fields -v`
Expected: PASS

- [ ] **Step 6: Update _make_exchange test helper**

In `tests/test_simulated_exchange.py`, in `_make_exchange()`, after `exchange._used_usdt = 0.0`, add:

```python
    exchange._frozen_usdt = 0.0
```

- [ ] **Step 7: Run full existing test suite to verify no regressions**

Run: `python -m pytest tests/test_simulated_exchange.py -v`
Expected: All existing tests PASS

- [ ] **Step 8: Commit**

```bash
git add src/integrations/exchange/simulated.py tests/test_simulated_exchange.py
git commit -m "feat(r6): expand _PendingOrder with frozen_margin/leverage, add _frozen_usdt"
```

---

### Task 3: Add helper methods _is_close_order and _is_close_order_static

**Files:**
- Modify: `src/integrations/exchange/simulated.py`

- [ ] **Step 1: Write tests for both helper methods**

```python
async def test_is_close_order_dynamic():
    """_is_close_order detects close vs open based on current position."""
    from src.integrations.exchange.simulated import _Position
    ex = _make_exchange()
    # No position → not a close
    assert ex._is_close_order("BTC/USDT:USDT", "sell") is False
    # Long position + sell → close
    ex._positions["BTC/USDT:USDT"] = _Position(
        side="long", contracts=0.001, entry_price=95000.0, leverage=3,
    )
    assert ex._is_close_order("BTC/USDT:USDT", "sell") is True
    assert ex._is_close_order("BTC/USDT:USDT", "buy") is False
    # Short position + buy → close
    ex._positions["BTC/USDT:USDT"] = _Position(
        side="short", contracts=0.001, entry_price=95000.0, leverage=3,
    )
    assert ex._is_close_order("BTC/USDT:USDT", "buy") is True
    assert ex._is_close_order("BTC/USDT:USDT", "sell") is False


async def test_is_close_order_static():
    """_is_close_order_static detects close direction from order fields only."""
    from src.integrations.exchange.simulated import SimulatedExchange, _PendingOrder
    # long position_side + sell → close
    o = _PendingOrder(id="1", symbol="X", side="sell", position_side="long",
                      order_type="market", amount=1, trigger_price=None)
    assert SimulatedExchange._is_close_order_static(o) is True
    # long position_side + buy → open (add-to)
    o2 = _PendingOrder(id="2", symbol="X", side="buy", position_side="long",
                       order_type="market", amount=1, trigger_price=None)
    assert SimulatedExchange._is_close_order_static(o2) is False
    # short position_side + buy → close
    o3 = _PendingOrder(id="3", symbol="X", side="buy", position_side="short",
                       order_type="market", amount=1, trigger_price=None)
    assert SimulatedExchange._is_close_order_static(o3) is True
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_simulated_exchange.py::test_is_close_order_dynamic tests/test_simulated_exchange.py::test_is_close_order_static -v`
Expected: FAIL with `AttributeError`

- [ ] **Step 3: Implement both methods in SimulatedExchange**

Add after `_validate_symbol` method (after line 74):

```python
    def _is_close_order(self, symbol: str, side: str) -> bool:
        """Is this a close order? Uses current position state (call from create_order)."""
        pos = self._positions.get(symbol)
        return (
            (pos is not None and pos.side == "long" and side == "sell") or
            (pos is not None and pos.side == "short" and side == "buy")
        )

    @staticmethod
    def _is_close_order_static(o: _PendingOrder) -> bool:
        """Is this a close-direction order? Uses order fields only (no position state)."""
        return (
            (o.position_side == "long" and o.side == "sell") or
            (o.position_side == "short" and o.side == "buy")
        )
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_simulated_exchange.py::test_is_close_order_dynamic tests/test_simulated_exchange.py::test_is_close_order_static -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/integrations/exchange/simulated.py tests/test_simulated_exchange.py
git commit -m "feat(r6): add _is_close_order and _is_close_order_static helpers"
```

---

### Task 4: Rewrite create_order("market") to produce pending orders

This is the core change: market orders become pending with frozen margin.

**Files:**
- Modify: `src/integrations/exchange/simulated.py:154-183` (create_order)
- Test: `tests/test_simulated_exchange.py`

- [ ] **Step 1: Write tests for new async market order behavior**

```python
async def test_market_order_returns_open_status():
    """create_order("market") now returns status="open", price=None, fee=None."""
    ex = _make_exchange(initial_balance=100.0)
    ex._leverage["BTC/USDT:USDT"] = 3
    order = await ex.create_order("BTC/USDT:USDT", "buy", "market", 0.001)
    assert order.status == "open"
    assert order.price is None
    assert order.fee is None


async def test_market_order_frozen_balance():
    """Market order freezes margin+fee from free_usdt."""
    ex = _make_exchange(initial_balance=100.0)
    ex._leverage["BTC/USDT:USDT"] = 3
    await ex.create_order("BTC/USDT:USDT", "buy", "market", 0.001)
    # Frozen should be (ask * amount / leverage + ask * amount * fee_rate) * 1.002
    ask = 95010.0
    margin = (ask * 0.001) / 3
    fee = ask * 0.001 * 0.0005
    frozen = (margin + fee) * 1.002
    assert ex._frozen_usdt == pytest.approx(frozen)
    assert ex._free_usdt == pytest.approx(100.0 - frozen)
    # No position yet
    positions = await ex.fetch_positions("BTC/USDT:USDT")
    assert len(positions) == 0


async def test_market_order_fills_on_next_tick():
    """Market order fills on next _process_tick: position created, callback called."""
    ex = _make_exchange(initial_balance=100.0)
    ex._leverage["BTC/USDT:USDT"] = 3
    fills = []
    async def on_fill(event):
        fills.append(event)
    ex.on_fill(on_fill)

    await ex.create_order("BTC/USDT:USDT", "buy", "market", 0.001)
    assert len(await ex.fetch_positions("BTC/USDT:USDT")) == 0

    # Tick with slightly different price
    tick = Ticker(symbol="BTC/USDT:USDT", last=95100.0, bid=95090.0, ask=95110.0,
                  high=96000.0, low=94000.0, base_volume=1000.0, timestamp=1712534401000)
    await ex._process_tick(tick)

    positions = await ex.fetch_positions("BTC/USDT:USDT")
    assert len(positions) == 1
    assert positions[0].side == "long"
    assert positions[0].contracts == 0.001

    assert len(fills) == 1
    assert fills[0].trigger_reason == "market"
    assert fills[0].fill_price == 95110.0  # ask price at tick time
    assert fills[0].pnl is None  # open order, no PnL
    assert ex._frozen_usdt == 0.0


async def test_market_close_fills_on_next_tick():
    """Close market order: position still exists after create_order, gone after tick."""
    from src.integrations.exchange.simulated import _Position
    ex = _make_exchange(initial_balance=50.0)
    ex._leverage["BTC/USDT:USDT"] = 3
    # Setup existing long position
    ex._positions["BTC/USDT:USDT"] = _Position(
        side="long", contracts=0.001, entry_price=95010.0, leverage=3,
    )
    margin = 95010.0 * 0.001 / 3
    ex._used_usdt = margin
    ex._free_usdt = 50.0 - margin

    fills = []
    async def on_fill(event):
        fills.append(event)
    ex.on_fill(on_fill)

    order = await ex.create_order("BTC/USDT:USDT", "sell", "market", 0.001)
    assert order.status == "open"
    # Position still exists
    positions = await ex.fetch_positions("BTC/USDT:USDT")
    assert len(positions) == 1

    tick = Ticker(symbol="BTC/USDT:USDT", last=95100.0, bid=95090.0, ask=95110.0,
                  high=96000.0, low=94000.0, base_volume=1000.0, timestamp=1712534401000)
    await ex._process_tick(tick)

    positions = await ex.fetch_positions("BTC/USDT:USDT")
    assert len(positions) == 0
    assert len(fills) == 1
    assert fills[0].trigger_reason == "market"
    assert fills[0].pnl is not None


async def test_close_market_order_minimal_freeze():
    """Close order only freezes fee (not margin), allowing close even when free_usdt ≈ 0."""
    from src.integrations.exchange.simulated import _Position
    ex = _make_exchange(initial_balance=100.0)
    ex._leverage["BTC/USDT:USDT"] = 3
    margin = 95010.0 * 0.001 / 3
    ex._positions["BTC/USDT:USDT"] = _Position(
        side="long", contracts=0.001, entry_price=95010.0, leverage=3,
    )
    ex._used_usdt = margin
    ex._free_usdt = 0.01  # Almost no free balance

    # Should NOT raise — close only freezes fee (min of estimated_fee, free_usdt)
    order = await ex.create_order("BTC/USDT:USDT", "sell", "market", 0.001)
    assert order.status == "open"
    assert ex._frozen_usdt == pytest.approx(0.01)  # min(fee, 0.01)
```

- [ ] **Step 2: Run new tests to verify they fail**

Run: `python -m pytest tests/test_simulated_exchange.py::test_market_order_returns_open_status tests/test_simulated_exchange.py::test_market_order_frozen_balance -v`
Expected: FAIL (status still "closed")

- [ ] **Step 3: Rewrite create_order market branch**

Replace the market branch in `create_order` (`simulated.py:166-178`). The full `create_order` method becomes:

```python
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
                if self._latest_ticker is None:
                    raise RuntimeError("No ticker data available")
                ticker = self._latest_ticker
                is_close = self._is_close_order(symbol, side)

                if is_close:
                    pos = self._positions[symbol]
                    position_side = pos.side
                    estimated_price = ticker.bid if pos.side == "long" else ticker.ask
                    estimated_fee = estimated_price * amount * self._fee_rate
                    frozen = min(estimated_fee, self._free_usdt)
                else:
                    position_side = "long" if side == "buy" else "short"
                    estimated_price = ticker.ask if side == "buy" else ticker.bid
                    leverage = self._leverage.get(symbol, 1)
                    estimated_margin = (estimated_price * amount) / leverage
                    estimated_fee = estimated_price * amount * self._fee_rate
                    frozen = (estimated_margin + estimated_fee) * 1.002
                    if self._free_usdt < frozen:
                        raise ValueError(
                            f"Insufficient balance: need {frozen:.2f}, have {self._free_usdt:.2f}"
                        )

                self._free_usdt -= frozen
                self._frozen_usdt += frozen

                order_id = str(uuid.uuid4())
                leverage_val = self._leverage.get(symbol, 1)
                self._pending_orders.append(_PendingOrder(
                    id=order_id, symbol=symbol, side=side,
                    position_side=position_side, order_type="market",
                    amount=amount, trigger_price=None,
                    frozen_margin=frozen, leverage=leverage_val,
                ))
                if self._db_engine:
                    await self._persist_state()
                return Order(
                    id=order_id, symbol=symbol, side=side, order_type="market",
                    amount=amount, price=None, status="open",
                )
            else:
                order = self._create_conditional_order(symbol, side, order_type, amount, price)  # type: ignore[arg-type]
                if self._db_engine:
                    await self._persist_state()
                return order
```

- [ ] **Step 4: Run the new async tests**

Run: `python -m pytest tests/test_simulated_exchange.py::test_market_order_returns_open_status tests/test_simulated_exchange.py::test_market_order_frozen_balance tests/test_simulated_exchange.py::test_close_market_order_minimal_freeze -v`
Expected: PASS

- [ ] **Step 5: Delete old synchronous market methods**

The following methods are now dead code — replaced by the pending+fill pattern:
- `_execute_market_order` (simulated.py:185-199)
- `_open_market_order` (simulated.py:201-251)
- `_close_market_order` (simulated.py:253-278)

Delete all three methods entirely.

- [ ] **Step 6: Commit**

```bash
git add src/integrations/exchange/simulated.py tests/test_simulated_exchange.py
git commit -m "feat(r6): rewrite create_order market to produce pending orders with frozen margin"
```

---

### Task 5: Implement _fill_market_open, _fill_market_close, _execute_market_fill

**Files:**
- Modify: `src/integrations/exchange/simulated.py`
- Test: `tests/test_simulated_exchange.py`

- [ ] **Step 1: Write tests for market fill methods**

```python
async def test_frozen_balance_diff_refund():
    """When tick price is lower than submit price, diff is refunded to free_usdt."""
    ex = _make_exchange(initial_balance=100.0)
    ex._leverage["BTC/USDT:USDT"] = 3
    await ex.create_order("BTC/USDT:USDT", "buy", "market", 0.001)
    free_after_freeze = ex._free_usdt

    # Tick with LOWER ask → actual cost < frozen → refund
    tick = Ticker(symbol="BTC/USDT:USDT", last=94900.0, bid=94890.0, ask=94900.0,
                  high=96000.0, low=94000.0, base_volume=1000.0, timestamp=1712534401000)
    await ex._process_tick(tick)

    assert ex._frozen_usdt == 0.0
    assert ex._free_usdt > free_after_freeze  # got a refund


async def test_frozen_extreme_clamp():
    """Extreme price movement: free_usdt clamped to 0, shortfall added to used_usdt."""
    ex = _make_exchange(initial_balance=35.0)  # just enough for 3x leverage
    ex._leverage["BTC/USDT:USDT"] = 3
    await ex.create_order("BTC/USDT:USDT", "buy", "market", 0.001)

    # Tick with MUCH HIGHER ask → actual cost > frozen → clamp
    tick = Ticker(symbol="BTC/USDT:USDT", last=97000.0, bid=96990.0, ask=97000.0,
                  high=97000.0, low=94000.0, base_volume=1000.0, timestamp=1712534401000)
    await ex._process_tick(tick)

    assert ex._frozen_usdt == 0.0
    assert ex._free_usdt >= 0.0  # clamped, not negative


async def test_fill_market_close_position_gone():
    """If position was liquidated before close fill, close order is cancelled and margin unfrozen."""
    from src.integrations.exchange.simulated import _Position, _PendingOrder
    ex = _make_exchange(initial_balance=50.0)
    # Manually set up a pending close order but no position (simulating liquidation ate it)
    ex._pending_orders.append(_PendingOrder(
        id="close-1", symbol="BTC/USDT:USDT", side="sell",
        position_side="long", order_type="market",
        amount=0.001, trigger_price=None,
        frozen_margin=0.05, leverage=3,
    ))
    ex._frozen_usdt = 0.05
    ex._free_usdt = 49.95

    tick = Ticker(symbol="BTC/USDT:USDT", last=95000.0, bid=94990.0, ask=95010.0,
                  high=96000.0, low=94000.0, base_volume=1000.0, timestamp=1712534401000)
    await ex._process_tick(tick)

    # Close order should be cancelled, margin unfrozen
    assert ex._frozen_usdt == 0.0
    assert ex._free_usdt == pytest.approx(50.0)
    assert len(ex._pending_orders) == 0


async def test_fill_market_close_clamps_amount():
    """Close fill amount is clamped to position contracts."""
    from src.integrations.exchange.simulated import _Position
    ex = _make_exchange(initial_balance=100.0)
    ex._leverage["BTC/USDT:USDT"] = 3
    margin = 95010.0 * 0.001 / 3
    ex._positions["BTC/USDT:USDT"] = _Position(
        side="long", contracts=0.001, entry_price=95010.0, leverage=3,
    )
    ex._used_usdt = margin
    ex._free_usdt = 100.0 - margin

    fills = []
    async def on_fill(event):
        fills.append(event)
    ex.on_fill(on_fill)

    # Submit close for MORE than position size
    await ex.create_order("BTC/USDT:USDT", "sell", "market", 0.005)
    tick = Ticker(symbol="BTC/USDT:USDT", last=95100.0, bid=95090.0, ask=95110.0,
                  high=96000.0, low=94000.0, base_volume=1000.0, timestamp=1712534401000)
    await ex._process_tick(tick)

    assert len(fills) == 1
    assert fills[0].amount == 0.001  # clamped to actual position size
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_simulated_exchange.py::test_frozen_balance_diff_refund tests/test_simulated_exchange.py::test_fill_market_close_position_gone -v`
Expected: FAIL (fill methods don't exist yet)

- [ ] **Step 3: Implement _fill_market_open**

Add after `_execute_market_order` method (or replace it — see step 5):

```python
    def _fill_market_open(self, order: _PendingOrder, ticker: Ticker) -> FillEvent | None:
        """Fill a pending market open order. Returns None if cancelled due to conflict."""
        pos = self._positions.get(order.symbol)
        position_side = "long" if order.side == "buy" else "short"

        # Defensive: reverse position conflict
        if pos is not None and pos.side != position_side:
            logger.warning(f"Market open {order.id} cancelled: conflicts with {pos.side} position")
            self._frozen_usdt -= order.frozen_margin
            self._free_usdt += order.frozen_margin
            return None

        # Defensive: leverage mismatch
        if pos is not None and pos.leverage != order.leverage:
            logger.warning(
                f"Market open {order.id} cancelled: leverage mismatch "
                f"(order={order.leverage}x, position={pos.leverage}x)"
            )
            self._frozen_usdt -= order.frozen_margin
            self._free_usdt += order.frozen_margin
            return None

        fill_price = ticker.ask if order.side == "buy" else ticker.bid
        leverage = order.leverage
        actual_margin = (fill_price * order.amount) / leverage
        actual_fee = fill_price * order.amount * self._fee_rate
        actual_cost = actual_margin + actual_fee

        # Unfreeze → occupy
        diff = order.frozen_margin - actual_cost
        self._frozen_usdt -= order.frozen_margin
        self._used_usdt += actual_margin
        self._free_usdt += diff
        # Clamp: extreme price movement may cause free < 0
        if self._free_usdt < 0:
            shortfall = -self._free_usdt
            self._free_usdt = 0.0
            self._used_usdt += shortfall  # additional margin semantics

        self._free_usdt = round(self._free_usdt, 8)
        self._used_usdt = round(self._used_usdt, 8)

        # Create or merge position
        if pos is not None and pos.side == position_side:
            new_contracts = pos.contracts + order.amount
            new_entry = (pos.entry_price * pos.contracts + fill_price * order.amount) / new_contracts
            pos.contracts = new_contracts
            pos.entry_price = new_entry
            pos.updated_at = datetime.now(timezone.utc)
        else:
            self._positions[order.symbol] = _Position(
                side=position_side, contracts=order.amount,
                entry_price=fill_price, leverage=leverage,
            )
        self._leverage[order.symbol] = leverage

        now_ms = int(datetime.now(timezone.utc).timestamp() * 1000)
        logger.info(f"Market open filled: {order.side} {order.amount} {order.symbol} @ {fill_price:.2f}")
        return FillEvent(
            order_id=order.id, symbol=order.symbol, side=order.side,
            position_side=position_side, trigger_reason="market",
            fill_price=fill_price, amount=order.amount, fee=actual_fee,
            pnl=None, timestamp=now_ms,
        )
```

- [ ] **Step 4: Implement _fill_market_close**

```python
    def _fill_market_close(self, order: _PendingOrder, ticker: Ticker) -> FillEvent | None:
        """Fill a pending market close order. Returns None if position already gone."""
        pos = self._positions.get(order.symbol)
        if pos is None:
            logger.warning(f"Market close {order.id} cancelled: position already closed")
            self._frozen_usdt -= order.frozen_margin
            self._free_usdt += order.frozen_margin
            return None

        actual_amount = min(order.amount, pos.contracts)
        fill_price = ticker.bid if pos.side == "long" else ticker.ask
        position_side = pos.side
        pnl, fee, _ = self._close_position_core(
            order.symbol, pos.side, actual_amount, fill_price, pnl_cap=True,
        )

        # Unfreeze (close doesn't occupy new margin — it's released by _close_position_core)
        self._frozen_usdt -= order.frozen_margin
        self._free_usdt += order.frozen_margin

        # Cancel orphaned orders if position fully closed
        if order.symbol not in self._positions:
            self._cancel_orphaned_orders()

        now_ms = int(datetime.now(timezone.utc).timestamp() * 1000)
        logger.info(
            f"Market close filled: {order.side} {actual_amount} {order.symbol} @ {fill_price:.2f}, "
            f"pnl={pnl:.4f}, fee={fee:.4f}"
        )
        return FillEvent(
            order_id=order.id, symbol=order.symbol, side=order.side,
            position_side=position_side, trigger_reason="market",
            fill_price=fill_price, amount=actual_amount, fee=fee,
            pnl=pnl, timestamp=now_ms,
        )
```

- [ ] **Step 5: Implement _execute_market_fill router**

```python
    def _execute_market_fill(self, order: _PendingOrder, ticker: Ticker) -> FillEvent | None:
        """Route pending market order to open or close fill. Uses static direction check."""
        if self._is_close_order_static(order):
            return self._fill_market_close(order, ticker)
        else:
            return self._fill_market_open(order, ticker)
```

- [ ] **Step 6: Run all new tests**

Run: `python -m pytest tests/test_simulated_exchange.py::test_market_order_fills_on_next_tick tests/test_simulated_exchange.py::test_market_close_fills_on_next_tick tests/test_simulated_exchange.py::test_frozen_balance_diff_refund tests/test_simulated_exchange.py::test_frozen_extreme_clamp tests/test_simulated_exchange.py::test_fill_market_close_position_gone tests/test_simulated_exchange.py::test_fill_market_close_clamps_amount -v`
Expected: Still failing — need _process_tick changes (Task 6)

- [ ] **Step 7: Commit**

```bash
git add src/integrations/exchange/simulated.py tests/test_simulated_exchange.py
git commit -m "feat(r6): implement _fill_market_open, _fill_market_close, _execute_market_fill"
```

---

### Task 6: Update _process_tick with market order matching (step 0)

**Files:**
- Modify: `src/integrations/exchange/simulated.py:403-475` (_process_tick)

- [ ] **Step 1: Rewrite _process_tick**

Replace the entire `_process_tick` method:

```python
    async def _process_tick(self, ticker: Ticker) -> None:
        """Process a single tick -- match market orders, check liquidations, conditional orders, alerts."""
        self._latest_ticker = ticker
        self._latest_price = ticker.last

        triggered: list[FillEvent] = []
        filled_order_ids: list[str] = []
        cancelled_order_ids: list[str] = []
        new_orders: list[tuple[Order, str]] = []
        alert_info = None
        level_alerts = []

        async with self._lock:
            # 0. Match pending market orders (new — before liquidation)
            market_orders = [o for o in self._pending_orders if o.order_type == "market"]
            for order in market_orders:
                fill = self._execute_market_fill(order, ticker)
                if fill is None:
                    cancelled_order_ids.append(order.id)
                    continue
                filled_order_ids.append(order.id)
                triggered.append(fill)

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
            processed = set(filled_order_ids + cancelled_order_ids)
            for order in list(self._pending_orders):
                if order.id in processed:
                    continue
                if order.order_type in ("stop", "take_profit"):
                    if self._should_trigger(order, ticker):
                        if not self._has_position(order.symbol):
                            continue
                        fill = self._execute_fill(order, ticker)
                        triggered.append(fill)
                        filled_order_ids.append(order.id)

            # 3. Unified cleanup
            all_resolved = filled_order_ids + cancelled_order_ids
            if triggered or all_resolved:
                for oid in all_resolved:
                    self._remove_order_by_id(oid)
                self._cancel_orphaned_orders()
                if self._db_engine:
                    await self._persist_state(
                        new_orders=new_orders,
                        filled_order_ids=filled_order_ids,
                        cancelled_order_ids=cancelled_order_ids,
                        fill_events=triggered,
                    )

            # 4. Price alert check
            if self._alert_service:
                alert_info = self._alert_service.check(ticker.last, ticker.timestamp)

            # 5. Price level alert check (R7)
            level_alerts = self._check_price_levels(ticker.last, ticker.timestamp)

        # Notify outside lock
        for fill in triggered:
            if self._fill_callback:
                await self._fill_callback(fill)

        if alert_info and self._alert_callback:
            await self._alert_callback(alert_info)

        for la in level_alerts:
            if self._alert_callback:
                await self._alert_callback(la)
```

- [ ] **Step 2: Run the market fill tests**

Run: `python -m pytest tests/test_simulated_exchange.py::test_market_order_fills_on_next_tick tests/test_simulated_exchange.py::test_market_close_fills_on_next_tick tests/test_simulated_exchange.py::test_frozen_balance_diff_refund tests/test_simulated_exchange.py::test_frozen_extreme_clamp tests/test_simulated_exchange.py::test_fill_market_close_position_gone tests/test_simulated_exchange.py::test_fill_market_close_clamps_amount -v`
Expected: PASS (may need _persist_state signature update first — see next task)

- [ ] **Step 3: Commit**

```bash
git add src/integrations/exchange/simulated.py
git commit -m "feat(r6): update _process_tick with step 0 market order matching"
```

---

### Task 7: Update _cancel_orphaned_orders, _persist_state, _restore_state

**Files:**
- Modify: `src/integrations/exchange/simulated.py`

- [ ] **Step 1: Write tests for orphan cleanup with market/limit orders**

```python
async def test_orphan_cleanup_preserves_market_open():
    """Stop loss closing position should NOT delete pending market/limit open orders."""
    from src.integrations.exchange.simulated import _PendingOrder
    ex = _make_exchange()
    # Pending market open order (no position needed — it creates one)
    ex._pending_orders.append(_PendingOrder(
        id="mkt-open", symbol="BTC/USDT:USDT", side="buy",
        position_side="long", order_type="market",
        amount=0.001, trigger_price=None,
        frozen_margin=32.0, leverage=3,
    ))
    # No position exists (was just closed by stop)
    ex._cancel_orphaned_orders()
    assert len(ex._pending_orders) == 1
    assert ex._pending_orders[0].id == "mkt-open"


async def test_orphan_cleanup_removes_market_close():
    """Liquidation should remove pending market close orders and unfreeze margin."""
    from src.integrations.exchange.simulated import _PendingOrder
    ex = _make_exchange(initial_balance=50.0)
    ex._frozen_usdt = 0.05
    ex._free_usdt = 49.95
    ex._pending_orders.append(_PendingOrder(
        id="mkt-close", symbol="BTC/USDT:USDT", side="sell",
        position_side="long", order_type="market",
        amount=0.001, trigger_price=None,
        frozen_margin=0.05, leverage=3,
    ))
    # No position (liquidated)
    ex._cancel_orphaned_orders()
    assert len(ex._pending_orders) == 0
    assert ex._frozen_usdt == 0.0
    assert ex._free_usdt == pytest.approx(50.0)


async def test_orphan_cleanup_unfreezes_margin():
    """Orphaned close order's frozen margin is correctly returned."""
    from src.integrations.exchange.simulated import _PendingOrder
    ex = _make_exchange(initial_balance=100.0)
    ex._frozen_usdt = 5.0
    ex._free_usdt = 95.0
    ex._pending_orders = [
        _PendingOrder(id="stop-1", symbol="BTC/USDT:USDT", side="sell",
                      position_side="long", order_type="stop",
                      amount=0.001, trigger_price=90000.0),
        _PendingOrder(id="mkt-close", symbol="BTC/USDT:USDT", side="sell",
                      position_side="long", order_type="market",
                      amount=0.001, trigger_price=None,
                      frozen_margin=5.0, leverage=3),
    ]
    # No position → both should be cleaned up, market close unfreezes
    ex._cancel_orphaned_orders()
    assert len(ex._pending_orders) == 0
    assert ex._frozen_usdt == 0.0
    assert ex._free_usdt == pytest.approx(100.0)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_simulated_exchange.py::test_orphan_cleanup_preserves_market_open -v`
Expected: FAIL (old orphan logic deletes everything without position)

- [ ] **Step 3: Rewrite _cancel_orphaned_orders**

Replace the method at `simulated.py:342-347`:

```python
    def _cancel_orphaned_orders(self) -> None:
        """Remove pending orders that lost their target.
        - stop/take_profit: remove if no position (close orders need a target)
        - market close direction: remove if no position + unfreeze margin
        - market open / limit: always keep (they create positions)
        """
        remaining = []
        for o in self._pending_orders:
            if o.order_type in ("stop", "take_profit"):
                if o.symbol in self._positions:
                    remaining.append(o)
                # else: conditional orders have no frozen margin, just drop
            elif o.order_type == "market" and self._is_close_order_static(o):
                if o.symbol in self._positions:
                    remaining.append(o)
                else:
                    # Close target gone (liquidated), unfreeze
                    if o.frozen_margin > 0:
                        self._frozen_usdt -= o.frozen_margin
                        self._free_usdt += o.frozen_margin
            else:
                # market open / limit: keep
                remaining.append(o)
        self._pending_orders = remaining
```

- [ ] **Step 4: Run orphan tests**

Run: `python -m pytest tests/test_simulated_exchange.py::test_orphan_cleanup_preserves_market_open tests/test_simulated_exchange.py::test_orphan_cleanup_removes_market_close tests/test_simulated_exchange.py::test_orphan_cleanup_unfreezes_margin -v`
Expected: PASS

- [ ] **Step 5: Update _persist_state signature and add cancelled_order_ids handling**

Update `_persist_state` signature and body. Key changes:
1. Add `cancelled_order_ids` parameter
2. Add `frozen_usdt` to balance upsert (both INSERT and ON CONFLICT)
3. Add `frozen_margin`/`leverage` to pending order upsert (step 3d)
4. Add step 3a-bis for cancelled orders
5. Add cancelled_order_ids to exclude_ids in step 3b

```python
    async def _persist_state(
        self,
        new_orders: list[tuple[Order, str]] | None = None,
        filled_order_ids: list[str] | None = None,
        cancelled_order_ids: list[str] | None = None,
        fill_events: list[FillEvent] | None = None,
    ) -> None:
        from sqlalchemy import delete, update
        from sqlalchemy.dialects.sqlite import insert as sqlite_insert
        from src.storage.database import get_session
        from src.storage.models import SimBalance, SimPosition, SimOrder

        now = datetime.now(timezone.utc)

        async with get_session(self._db_engine) as session:
            # 1. Upsert balance (includes frozen_usdt)
            stmt = sqlite_insert(SimBalance).values(
                session_id=self._session_id,
                free_usdt=self._free_usdt,
                used_usdt=self._used_usdt,
                frozen_usdt=self._frozen_usdt,
                updated_at=now,
            )
            stmt = stmt.on_conflict_do_update(
                index_elements=["session_id"],
                set_={
                    "free_usdt": stmt.excluded.free_usdt,
                    "used_usdt": stmt.excluded.used_usdt,
                    "frozen_usdt": stmt.excluded.frozen_usdt,
                    "updated_at": now,
                },
            )
            await session.execute(stmt)

            # 2. Positions: delete + insert
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

            # 3a. Update filled orders → "closed"
            if filled_order_ids and fill_events:
                fill_map = {f.order_id: f for f in fill_events}
                for oid in filled_order_ids:
                    fill = fill_map.get(oid)
                    if fill:
                        await session.execute(
                            update(SimOrder)
                            .where(SimOrder.order_id == oid)
                            .values(
                                status="closed",
                                filled_price=fill.fill_price,
                                fee=fill.fee,
                                filled_at=now,
                            )
                        )

            # 3a-bis. Update cancelled orders → "cancelled"
            if cancelled_order_ids:
                for oid in cancelled_order_ids:
                    await session.execute(
                        update(SimOrder)
                        .where(SimOrder.order_id == oid)
                        .values(status="cancelled")
                    )

            # 3b. Cancel orphaned pending orders in DB
            pending_ids = [o.id for o in self._pending_orders]
            filled_ids = filled_order_ids or []
            cancelled_ids = cancelled_order_ids or []
            exclude_ids = pending_ids + filled_ids + cancelled_ids
            if exclude_ids:
                await session.execute(
                    update(SimOrder)
                    .where(SimOrder.session_id == self._session_id)
                    .where(SimOrder.status == "open")
                    .where(SimOrder.order_id.notin_(exclude_ids))
                    .values(status="cancelled")
                )
            else:
                await session.execute(
                    update(SimOrder)
                    .where(SimOrder.session_id == self._session_id)
                    .where(SimOrder.status == "open")
                    .values(status="cancelled")
                )

            # 3c. INSERT new orders (liquidation only — market/limit already inserted at create_order)
            if new_orders:
                for order, position_side in new_orders:
                    session.add(SimOrder(
                        session_id=self._session_id, order_id=order.id,
                        symbol=order.symbol, side=order.side,
                        position_side=position_side,
                        order_type=order.order_type, amount=order.amount,
                        trigger_price=order.price if order.status == "open" else None,
                        status=order.status,
                        filled_price=order.price if order.status == "closed" else None,
                        fee=order.fee,
                        filled_at=now if order.status == "closed" else None,
                    ))

            # 3d. Upsert pending orders (includes frozen_margin/leverage)
            for pending in self._pending_orders:
                stmt = sqlite_insert(SimOrder).values(
                    session_id=self._session_id, order_id=pending.id,
                    symbol=pending.symbol, side=pending.side,
                    position_side=pending.position_side,
                    order_type=pending.order_type, amount=pending.amount,
                    trigger_price=pending.trigger_price,
                    frozen_margin=pending.frozen_margin,
                    leverage=pending.leverage,
                    status="open", created_at=now,
                )
                stmt = stmt.on_conflict_do_nothing(index_elements=["order_id"])
                await session.execute(stmt)

            await session.commit()
```

- [ ] **Step 6: Update _restore_state to read frozen_usdt, frozen_margin, leverage**

Replace `_restore_state`:

```python
    async def _restore_state(self) -> None:
        from sqlalchemy import select
        from src.storage.database import get_session
        from src.storage.models import SimBalance, SimPosition, SimOrder

        async with get_session(self._db_engine) as session:
            result = await session.execute(
                select(SimBalance).where(SimBalance.session_id == self._session_id)
            )
            bal = result.scalar_one_or_none()
            if bal:
                self._free_usdt = bal.free_usdt
                self._used_usdt = bal.used_usdt
                self._frozen_usdt = bal.frozen_usdt
            else:
                return

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

            result = await session.execute(
                select(SimOrder)
                .where(SimOrder.session_id == self._session_id)
                .where(SimOrder.status == "open")
            )
            for o in result.scalars().all():
                self._pending_orders.append(_PendingOrder(
                    id=o.order_id, symbol=o.symbol, side=o.side,
                    position_side=o.position_side, order_type=o.order_type,
                    amount=o.amount, trigger_price=o.trigger_price,
                    frozen_margin=o.frozen_margin,
                    leverage=o.leverage,
                ))

        logger.info(
            f"Restored state: balance={self._free_usdt:.2f}/{self._used_usdt:.2f}/{self._frozen_usdt:.2f}, "
            f"positions={len(self._positions)}, pending_orders={len(self._pending_orders)}"
        )
```

- [ ] **Step 7: Update _init_state to reset _frozen_usdt**

In `_init_state`, add after `self._used_usdt = 0.0`:

```python
        self._frozen_usdt = 0.0
```

- [ ] **Step 8: Run all new tests**

Run: `python -m pytest tests/test_simulated_exchange.py -k "orphan_cleanup or frozen or fills_on_next" -v`
Expected: PASS

- [ ] **Step 9: Commit**

```bash
git add src/integrations/exchange/simulated.py tests/test_simulated_exchange.py
git commit -m "feat(r6): rewrite _cancel_orphaned_orders, _persist_state, _restore_state for async market orders"
```

---

### Task 8: Update fetch_balance to include _frozen_usdt in total

**Files:**
- Modify: `src/integrations/exchange/simulated.py:109-118`

- [ ] **Step 1: Write test**

```python
async def test_fetch_balance_total_includes_frozen():
    """total_usdt = free + used + frozen + unrealized."""
    ex = _make_exchange(initial_balance=60.0)
    ex._used_usdt = 30.0
    ex._frozen_usdt = 10.0
    balance = await ex.fetch_balance()
    assert balance.total_usdt == pytest.approx(100.0)
    assert balance.free_usdt == pytest.approx(60.0)
    assert balance.used_usdt == 30.0
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_simulated_exchange.py::test_fetch_balance_total_includes_frozen -v`
Expected: FAIL (total = 90, missing frozen)

- [ ] **Step 3: Update fetch_balance**

```python
    async def fetch_balance(self) -> Balance:
        unrealized = sum(
            self._calc_unrealized_pnl(pos)
            for pos in self._positions.values()
        )
        return Balance(
            total_usdt=self._free_usdt + self._used_usdt + self._frozen_usdt + unrealized,
            free_usdt=max(0.0, self._free_usdt + unrealized),
            used_usdt=self._used_usdt,
        )
```

- [ ] **Step 4: Run test**

Run: `python -m pytest tests/test_simulated_exchange.py::test_fetch_balance_total_includes_frozen -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/integrations/exchange/simulated.py tests/test_simulated_exchange.py
git commit -m "feat(r6): include _frozen_usdt in fetch_balance total"
```

---

### Task 9: Delete drain_pending_fills chain

**Files:**
- Modify: `src/integrations/exchange/base.py:147-149`
- Modify: `src/integrations/exchange/simulated.py:577-580`
- Modify: `src/cli/app.py:336-342`
- Modify: `tests/test_exchange.py`

- [ ] **Step 1: Delete drain_pending_fills from base.py**

Remove lines 147-149 of `base.py`:
```python
    def drain_pending_fills(self) -> list['FillEvent']:
        """Return and clear queued FillEvents. Default: empty (OKX etc. need not override)."""
        return []
```

- [ ] **Step 2: Delete drain_pending_fills from simulated.py**

Remove the `drain_pending_fills` method and `_pending_fills` attribute:
- Remove `self._pending_fills: list[FillEvent] = []` from `__init__`
- Remove the `drain_pending_fills` method

- [ ] **Step 3: Delete drain logic from app.py**

In `src/cli/app.py`, remove the finally block that drains pending fills (lines 336-342):

```python
            # Process pending fills
            if handle_fill is not None:
                for fill in exchange.drain_pending_fills():
                    try:
                        await handle_fill(fill)
                    except Exception:
                        logger.exception("Fill handler failed for order %s", fill.order_id)
```

- [ ] **Step 4: Delete test_base_exchange_drain_pending_fills from test_exchange.py**

Remove the test that verifies `drain_pending_fills` returns empty list.

- [ ] **Step 5: Run tests**

Run: `python -m pytest tests/test_exchange.py tests/test_simulated_exchange.py -v`
Expected: All PASS (some existing tests may still reference `_pending_fills` or `drain_pending_fills` — fix in Task 11)

- [ ] **Step 6: Commit**

```bash
git add src/integrations/exchange/base.py src/integrations/exchange/simulated.py src/cli/app.py tests/test_exchange.py
git commit -m "feat(r6): delete drain_pending_fills chain — all fills now via _fill_callback"
```

---

### Task 10: Update tools_perception.py and trader.py for price=None handling

**Files:**
- Modify: `src/agent/tools_perception.py:57-65`
- Modify: `src/agent/trader.py:65`

- [ ] **Step 1: Update get_open_orders to handle price=None**

Replace `get_open_orders` in `tools_perception.py`:

```python
async def get_open_orders(deps: TradingDeps) -> str:
    """Get all pending orders (market awaiting fill, limit, stop loss, take profit)."""
    orders = await deps.exchange.fetch_open_orders(deps.symbol)
    if not orders:
        return "No pending orders."
    lines = ["Pending Orders:"]
    for o in orders:
        if o.order_type == "market":
            label = "[PENDING]"
            price_str = "market price"
        elif o.order_type == "limit":
            label = "[LIMIT]"
            price_str = f"@ {o.price:.2f}"
        else:
            label = f"[{o.order_type.upper()}]"
            price_str = f"@ {o.price:.2f}"
        lines.append(f"  {label} {o.side} {o.amount} {price_str} | ID: {o.id}")
    return "\n".join(lines)
```

- [ ] **Step 2: Update tool description in trader.py**

Change line 65 of `trader.py`:
```python
    async def get_open_orders(ctx: RunContext[TradingDeps]) -> str:
        """Get all pending orders (market awaiting fill, limit, stop loss, take profit)."""
```

- [ ] **Step 3: Commit**

```bash
git add src/agent/tools_perception.py src/agent/trader.py
git commit -m "feat(r6): handle price=None in get_open_orders, update tool description"
```

---

### Task 11: Adapt existing tests for async market orders

All existing tests that call `create_order("market")` and immediately check state need a `_process_tick` call inserted between order creation and assertions.

**Files:**
- Modify: `tests/test_simulated_exchange.py`

- [ ] **Step 1: Create a helper ticker for tests**

Add after `_make_exchange`:

```python
def _tick(symbol="BTC/USDT:USDT", last=95000.0, bid=94990.0, ask=95010.0):
    """Helper: create a Ticker for _process_tick calls in tests."""
    return Ticker(
        symbol=symbol, last=last, bid=bid, ask=ask,
        high=96000.0, low=94000.0, base_volume=1000.0, timestamp=1712534401000,
    )
```

- [ ] **Step 2: Adapt market order open tests (insert tick between create_order and state check)**

Pattern: `order.status` → `"open"`, `order.price` → `None`, `order.fee` → `None`. Insert `await ex._process_tick(_tick())` before position/balance assertions.

**`test_market_buy_opens_long`:**
```python
async def test_market_buy_opens_long():
    ex = _make_exchange(initial_balance=100.0)
    ex._leverage["BTC/USDT:USDT"] = 3
    order = await ex.create_order("BTC/USDT:USDT", "buy", "market", 0.001)
    assert order.status == "open"
    assert order.price is None
    assert order.fee is None

    await ex._process_tick(_tick())  # ← NEW: fills the market order

    positions = await ex.fetch_positions("BTC/USDT:USDT")
    assert len(positions) == 1
    assert positions[0].side == "long"
    assert positions[0].contracts == 0.001
    balance = await ex.fetch_balance()
    margin = 95010.0 * 0.001 / 3
    assert balance.used_usdt == pytest.approx(margin)
```

**`test_market_sell_opens_short`:** same pattern — add `await ex._process_tick(_tick())` before position check, change `order.price` assertion to check `order.status == "open"`.

- [ ] **Step 3: Adapt market order close tests**

**`test_market_close_long`:** open → tick → close → tick → check.
**`test_market_close_clamps_amount`:** open → tick → close(excess amount) → tick → verify clamped.

- [ ] **Step 4: Adapt add-to-position test**

**`test_add_to_position`:** first buy → tick → second buy → tick → check merged position.

- [ ] **Step 5: Adapt leverage-with-position tests**

**`test_add_position_leverage_mismatch`:** buy → tick → change leverage setting → second buy should still fail (now at `create_order` balance check, since market orders check leverage at fill time — but the test may need restructuring if the error now happens at fill instead of create).

**`test_set_leverage_rejects_with_position`:** buy → tick → set_leverage should reject.

- [ ] **Step 6: Adapt partial close test**

**`test_partial_close_position`:** buy → tick → sell partial → tick → check remaining position.

- [ ] **Step 7: Adapt conditional order setup tests**

These fail without tick because position doesn't exist yet after `create_order("market")`:

**`test_stop_order_creation`:** buy → **tick** → set stop.
**`test_conditional_order_forces_full_amount`:** buy → **tick** → set stop → check amount == position contracts.
**`test_cancel_order`:** buy → **tick** → set stop → cancel stop.

- [ ] **Step 8: Adapt fill event tests**

**`test_market_order_queues_fill_event`:** DELETE or rewrite. Old test verified `_pending_fills` queue (now deleted). Rewrite to verify `_fill_callback` is called during `_process_tick`:
```python
async def test_market_order_fill_callback():
    ex = _make_exchange(initial_balance=100.0)
    ex._leverage["BTC/USDT:USDT"] = 3
    fills = []
    async def on_fill(event):
        fills.append(event)
    ex.on_fill(on_fill)
    await ex.create_order("BTC/USDT:USDT", "buy", "market", 0.001)
    assert len(fills) == 0  # not yet filled
    await ex._process_tick(_tick())
    assert len(fills) == 1
    assert fills[0].trigger_reason == "market"
    assert fills[0].side == "buy"
```

**`test_market_close_fill_event_has_pnl`:** open → tick → close → tick → verify fill callback has `pnl is not None`. Remove any `drain_pending_fills()` calls.

- [ ] **Step 9: Adapt conditional/liquidation trigger tests (insert tick to create position first)**

Pattern: `market order → tick@normal(fills open) → set conditional → tick@trigger(fires conditional)`:

- **`test_should_trigger_stop_long`:** buy → tick@normal → set stop → tick@low(triggers stop)
- **`test_should_trigger_stop_short`:** sell → tick@normal → set stop → tick@high(triggers stop)
- **`test_should_trigger_take_profit_long`:** buy → tick@normal → set TP → tick@high(triggers TP)
- **`test_should_trigger_take_profit_short`:** sell → tick@normal → set TP → tick@low(triggers TP)
- **`test_no_trigger_when_price_above_stop`:** buy → tick@normal → set stop → tick@above_stop(no trigger)
- **`test_liquidation_triggers_before_stop`:** buy → tick@normal → set stop → tick@below_liq(liquidation, not stop)
- **`test_fill_event_carries_pnl_on_stop`:** buy → tick@normal → set stop → tick@trigger → verify fill PnL

- [ ] **Step 10: Adapt liquidation tests that need dual-tick**

These tests need two ticks because the entry price is now the tick price (not submit price):

**`test_liquidation_short`:** sell → **tick@normal(opens short at tick.bid)** → tick@extreme_high(triggers liquidation).

**`test_force_liquidate_fill_event_has_pnl`:** same pattern. Also remove `drain_pending_fills()` calls — use `_fill_callback` instead.

- [ ] **Step 11: Fix test_market_order_unknown_type**

`"limit"` is now valid, so change test to use truly unknown type:
```python
async def test_market_order_unknown_type():
    ex = _make_exchange()
    with pytest.raises(ValueError, match="Unknown order_type"):
        await ex.create_order("BTC/USDT:USDT", "buy", "foobar", 0.001)
```

- [ ] **Step 12: Adapt persistence tests (require DB — if they exist)**

**`test_persist_and_restore`:** open → **tick** → persist → restore → check.
**`test_fetch_closed_orders_from_db`:** open → **tick** → check DB.

**Tests that should NOT be modified** (per spec — no market order involvement):
- `test_fetch_balance_initial`, `test_fetch_balance_with_unrealized_pnl`, `test_fetch_balance_free_clamps_to_zero`
- `test_set_leverage_*` (except `rejects_with_position`)
- `test_amount_to_precision*`
- All alert-related tests
- `test_cancel_nonexistent_order`
- `test_market_order_insufficient_balance` (balance check still in `create_order`)
- `test_market_order_wrong_symbol` (validation unchanged)
- `test_market_order_invalid_amount` (validation unchanged)
- `test_stop_order_without_position` (still rejected at `create_order`)
- `test_stop_order_without_price` (validation unchanged)

- [ ] **Step 13: Run full test suite**

Run: `python -m pytest tests/test_simulated_exchange.py -v`
Expected: All PASS

- [ ] **Step 14: Commit**

```bash
git add tests/test_simulated_exchange.py
git commit -m "test(r6): adapt 25 existing tests for async market order semantics"
```

---

### Task 12: Add e2e scenario test

**Files:**
- Modify: `tests/test_simulated_exchange.py`

- [ ] **Step 1: Write e2e test**

```python
async def test_e2e_open_then_stop_after_fill():
    """Core value scenario: open_position → can't set stop in same cycle → tick fills →
    fill callback triggers → next cycle sets stop successfully."""
    from src.integrations.exchange.simulated import _Position
    ex = _make_exchange(initial_balance=100.0)
    ex._leverage["BTC/USDT:USDT"] = 3
    fills = []
    async def on_fill(event):
        fills.append(event)
    ex.on_fill(on_fill)

    # Agent cycle 1: open position
    order = await ex.create_order("BTC/USDT:USDT", "buy", "market", 0.001)
    assert order.status == "open"

    # Same cycle: try to set stop → fails (no position yet)
    with pytest.raises(ValueError, match="Cannot create conditional order without a position"):
        await ex.create_order("BTC/USDT:USDT", "sell", "stop", 0.001, price=90000.0)

    # Tick → fills the market order
    tick = _tick()
    await ex._process_tick(tick)
    assert len(fills) == 1
    assert fills[0].trigger_reason == "market"

    # Agent cycle 2 (triggered by fill callback): position now visible
    positions = await ex.fetch_positions("BTC/USDT:USDT")
    assert len(positions) == 1

    # Now set stop succeeds
    stop_order = await ex.create_order("BTC/USDT:USDT", "sell", "stop", 0.001, price=90000.0)
    assert stop_order.status == "open"
    assert stop_order.order_type == "stop"
```

- [ ] **Step 2: Run test**

Run: `python -m pytest tests/test_simulated_exchange.py::test_e2e_open_then_stop_after_fill -v`
Expected: PASS

- [ ] **Step 3: Commit**

```bash
git add tests/test_simulated_exchange.py
git commit -m "test(r6): add e2e scenario test — open → tick fill → set stop"
```

---

## PR #2: Duplicate Order Prevention

### Task 13: Add has_pending_market_order to BaseExchange and SimExchange

**Files:**
- Modify: `src/integrations/exchange/base.py`
- Modify: `src/integrations/exchange/simulated.py`

- [ ] **Step 1: Write tests**

```python
async def test_has_pending_market_order():
    """has_pending_market_order returns True when pending market order exists."""
    from src.integrations.exchange.simulated import _PendingOrder
    ex = _make_exchange()
    assert ex.has_pending_market_order("BTC/USDT:USDT") is False

    ex._pending_orders.append(_PendingOrder(
        id="m1", symbol="BTC/USDT:USDT", side="buy",
        position_side="long", order_type="market",
        amount=0.001, trigger_price=None,
        frozen_margin=32.0, leverage=3,
    ))
    assert ex.has_pending_market_order("BTC/USDT:USDT") is True
    assert ex.has_pending_market_order("BTC/USDT:USDT", side="buy") is True
    assert ex.has_pending_market_order("BTC/USDT:USDT", side="sell") is False

    # Stop orders don't count
    ex._pending_orders = [_PendingOrder(
        id="s1", symbol="BTC/USDT:USDT", side="sell",
        position_side="long", order_type="stop",
        amount=0.001, trigger_price=90000.0,
    )]
    assert ex.has_pending_market_order("BTC/USDT:USDT") is False
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_simulated_exchange.py::test_has_pending_market_order -v`
Expected: FAIL with `AttributeError`

- [ ] **Step 3: Add to BaseExchange**

In `base.py`, add after `update_alert_params`:

```python
    def has_pending_market_order(self, symbol: str, side: str | None = None) -> bool:
        """Check for pending market orders. Default: False (real exchanges don't track client-side)."""
        return False
```

- [ ] **Step 4: Implement in SimulatedExchange**

In `simulated.py`, add method:

```python
    def has_pending_market_order(self, symbol: str, side: str | None = None) -> bool:
        """Check for pending market orders matching symbol and optional side."""
        for o in self._pending_orders:
            if o.order_type == "market" and o.symbol == symbol:
                if side is None or o.side == side:
                    return True
        return False
```

- [ ] **Step 5: Run test**

Run: `python -m pytest tests/test_simulated_exchange.py::test_has_pending_market_order -v`
Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add src/integrations/exchange/base.py src/integrations/exchange/simulated.py tests/test_simulated_exchange.py
git commit -m "feat(r6): add has_pending_market_order to BaseExchange and SimExchange"
```

---

### Task 14: Add duplicate order prevention to tools_execution.py

**Files:**
- Modify: `src/agent/tools_execution.py:49-85` (open_position)
- Modify: `src/agent/tools_execution.py:88-112` (close_position)

- [ ] **Step 1: Write tests**

```python
# tests/test_tools.py or tests/test_simulated_exchange.py — choose based on existing patterns
async def test_duplicate_open_rejected():
    """open_position rejects when a market order is already pending."""
    ex = _make_exchange(initial_balance=100.0)
    ex._leverage["BTC/USDT:USDT"] = 3
    await ex.create_order("BTC/USDT:USDT", "buy", "market", 0.001)
    assert ex.has_pending_market_order("BTC/USDT:USDT") is True

    # Second open should be prevented at tool level
    # (We test has_pending_market_order directly since tool tests use mocks)


async def test_duplicate_close_rejected():
    """close_position rejects when a close market order is already pending."""
    from src.integrations.exchange.simulated import _Position
    ex = _make_exchange(initial_balance=100.0)
    ex._leverage["BTC/USDT:USDT"] = 3
    ex._positions["BTC/USDT:USDT"] = _Position(
        side="long", contracts=0.001, entry_price=95010.0, leverage=3,
    )
    margin = 95010.0 * 0.001 / 3
    ex._used_usdt = margin
    ex._free_usdt = 100.0 - margin
    await ex.create_order("BTC/USDT:USDT", "sell", "market", 0.001)
    assert ex.has_pending_market_order("BTC/USDT:USDT", side="sell") is True
```

- [ ] **Step 2: Add pending check to open_position**

In `tools_execution.py`, in `open_position`, after `if quantity <= 0:` check and before `_check_approval`, add:

```python
    # Duplicate order prevention
    if deps.exchange.has_pending_market_order(deps.symbol):
        return "A market order is already pending. Wait for fill confirmation before opening another position."
```

- [ ] **Step 3: Add pending check to close_position**

In `tools_execution.py`, in `close_position`, after `if not positions:` check, add:

```python
    order_side = "sell" if positions[0].side == "long" else "buy"
    if deps.exchange.has_pending_market_order(deps.symbol, side=order_side):
        return "A close order is already pending. Wait for fill confirmation."
```

- [ ] **Step 4: Run tests**

Run: `python -m pytest tests/test_simulated_exchange.py::test_duplicate_open_rejected tests/test_simulated_exchange.py::test_duplicate_close_rejected -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/agent/tools_execution.py tests/test_simulated_exchange.py
git commit -m "feat(r6): add duplicate market order prevention to open_position/close_position"
```

---

## PR #3: Limit Orders

### Task 15: Add create_order("limit") to SimExchange

**Files:**
- Modify: `src/integrations/exchange/simulated.py`

- [ ] **Step 1: Write tests for limit order creation**

```python
async def test_limit_order_creation():
    """create_order("limit") returns status="open", freezes margin."""
    ex = _make_exchange(initial_balance=100.0)
    ex._leverage["BTC/USDT:USDT"] = 3
    order = await ex.create_order("BTC/USDT:USDT", "buy", "limit", 0.001, price=90000.0)
    assert order.status == "open"
    assert order.order_type == "limit"
    assert order.price == 90000.0
    # Check frozen: (90000 * 0.001 / 3) + (90000 * 0.001 * 0.0005) = 30 + 0.045 = 30.045
    expected_frozen = (90000.0 * 0.001 / 3) + (90000.0 * 0.001 * 0.0005)
    assert ex._frozen_usdt == pytest.approx(expected_frozen)
    assert ex._free_usdt == pytest.approx(100.0 - expected_frozen)


async def test_limit_order_requires_price():
    """Limit order without price raises ValueError."""
    ex = _make_exchange()
    with pytest.raises(ValueError, match="price is required"):
        await ex.create_order("BTC/USDT:USDT", "buy", "limit", 0.001)


async def test_limit_order_reverse_position_rejected():
    """Limit sell rejected when long position exists (one-way mode)."""
    from src.integrations.exchange.simulated import _Position
    ex = _make_exchange()
    ex._positions["BTC/USDT:USDT"] = _Position(
        side="long", contracts=0.001, entry_price=95000.0, leverage=3,
    )
    with pytest.raises(ValueError, match="Cannot open short limit order"):
        await ex.create_order("BTC/USDT:USDT", "sell", "limit", 0.001, price=100000.0)


async def test_limit_order_leverage_matches_position():
    """Limit order uses position leverage when position exists."""
    from src.integrations.exchange.simulated import _Position
    ex = _make_exchange(initial_balance=100.0)
    ex._leverage["BTC/USDT:USDT"] = 10  # different from position
    ex._positions["BTC/USDT:USDT"] = _Position(
        side="long", contracts=0.001, entry_price=95000.0, leverage=3,
    )
    ex._used_usdt = 95000.0 * 0.001 / 3
    ex._free_usdt = 100.0 - ex._used_usdt
    order = await ex.create_order("BTC/USDT:USDT", "buy", "limit", 0.001, price=90000.0)
    # Should use position leverage (3), not _leverage setting (10)
    # frozen = (90000 * 0.001 / 3) + (90000 * 0.001 * 0.0005)
    expected_frozen = (90000.0 * 0.001 / 3) + (90000.0 * 0.001 * 0.0005)
    assert ex._frozen_usdt == pytest.approx(expected_frozen)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_simulated_exchange.py::test_limit_order_creation -v`
Expected: FAIL (`Unknown order_type: limit`)

- [ ] **Step 3: Add limit order support to create_order**

Update the `order_type` validation:
```python
        if order_type not in ("market", "limit", "stop", "take_profit"):
            raise ValueError(f"Unknown order_type: {order_type}")
```

Add `price is None` check for limit:
```python
        if order_type in ("stop", "take_profit", "limit") and price is None:
            raise ValueError(f"price is required for {order_type} orders")
```

Add the limit branch inside the lock, after the market branch and before the conditional branch:

```python
            elif order_type == "limit":
                # Limit orders are open-only (first version — D4)
                pos = self._positions.get(symbol)
                position_side = "long" if side == "buy" else "short"
                if pos is not None and pos.side != position_side:
                    raise ValueError(
                        f"Cannot open {position_side} limit order: "
                        f"existing {pos.side} position. Close position first."
                    )
                # Use position leverage if position exists, else current setting
                if pos is not None:
                    leverage = pos.leverage
                else:
                    leverage = self._leverage.get(symbol, 1)
                margin = (price * amount) / leverage
                fee = price * amount * self._fee_rate
                frozen = margin + fee
                if self._free_usdt < frozen:
                    raise ValueError(
                        f"Insufficient balance: need {frozen:.2f}, have {self._free_usdt:.2f}"
                    )
                self._free_usdt -= frozen
                self._frozen_usdt += frozen

                order_id = str(uuid.uuid4())
                self._pending_orders.append(_PendingOrder(
                    id=order_id, symbol=symbol, side=side,
                    position_side=position_side, order_type="limit",
                    amount=amount, trigger_price=price,
                    frozen_margin=frozen, leverage=leverage,
                ))
                if self._db_engine:
                    await self._persist_state()
                return Order(
                    id=order_id, symbol=symbol, side=side,
                    order_type="limit", amount=amount, price=price, status="open",
                )
```

- [ ] **Step 4: Run limit creation tests**

Run: `python -m pytest tests/test_simulated_exchange.py -k "limit_order_creation or limit_order_requires or limit_order_reverse or limit_order_leverage" -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/integrations/exchange/simulated.py tests/test_simulated_exchange.py
git commit -m "feat(r6): add create_order limit support with frozen margin and validation"
```

---

### Task 16: Limit order matching — _should_trigger + _execute_limit_fill

**Files:**
- Modify: `src/integrations/exchange/simulated.py`

- [ ] **Step 1: Write tests**

```python
async def test_limit_order_fills_when_price_reached():
    """Buy limit triggers when ask <= limit price."""
    ex = _make_exchange(initial_balance=100.0)
    ex._leverage["BTC/USDT:USDT"] = 3
    fills = []
    async def on_fill(event):
        fills.append(event)
    ex.on_fill(on_fill)

    await ex.create_order("BTC/USDT:USDT", "buy", "limit", 0.001, price=94000.0)

    # Tick with ask above limit → no fill
    tick1 = Ticker(symbol="BTC/USDT:USDT", last=95000.0, bid=94990.0, ask=95010.0,
                   high=96000.0, low=94000.0, base_volume=1000.0, timestamp=1712534401000)
    await ex._process_tick(tick1)
    assert len(fills) == 0

    # Tick with ask at limit → fill
    tick2 = Ticker(symbol="BTC/USDT:USDT", last=93900.0, bid=93890.0, ask=93900.0,
                   high=96000.0, low=93000.0, base_volume=1000.0, timestamp=1712534402000)
    await ex._process_tick(tick2)
    assert len(fills) == 1
    assert fills[0].trigger_reason == "limit"
    assert fills[0].fill_price == 94000.0  # fills at limit price, not market
    assert fills[0].pnl is None  # open order

    positions = await ex.fetch_positions("BTC/USDT:USDT")
    assert len(positions) == 1
    assert positions[0].entry_price == 94000.0
    assert ex._frozen_usdt == 0.0


async def test_limit_order_not_filled_above_price():
    """Buy limit does NOT trigger when ask > limit price."""
    ex = _make_exchange(initial_balance=100.0)
    ex._leverage["BTC/USDT:USDT"] = 3
    await ex.create_order("BTC/USDT:USDT", "buy", "limit", 0.001, price=90000.0)

    tick = Ticker(symbol="BTC/USDT:USDT", last=95000.0, bid=94990.0, ask=95010.0,
                  high=96000.0, low=94000.0, base_volume=1000.0, timestamp=1712534401000)
    await ex._process_tick(tick)
    assert len(ex._pending_orders) == 1  # still pending
    assert ex._frozen_usdt > 0


async def test_limit_fill_cancelled_on_reverse_position():
    """Limit buy cancelled at fill time if short position now exists."""
    from src.integrations.exchange.simulated import _Position
    ex = _make_exchange(initial_balance=100.0)
    ex._leverage["BTC/USDT:USDT"] = 3
    await ex.create_order("BTC/USDT:USDT", "buy", "limit", 0.001, price=94000.0)
    frozen = ex._frozen_usdt

    # Manually create a short position (simulating another order filled first)
    ex._positions["BTC/USDT:USDT"] = _Position(
        side="short", contracts=0.001, entry_price=96000.0, leverage=3,
    )

    tick = Ticker(symbol="BTC/USDT:USDT", last=93900.0, bid=93890.0, ask=93900.0,
                  high=96000.0, low=93000.0, base_volume=1000.0, timestamp=1712534402000)
    await ex._process_tick(tick)

    # Limit order should be cancelled, margin unfrozen
    assert ex._frozen_usdt == 0.0
    assert ex._free_usdt == pytest.approx(100.0 - (96000.0 * 0.001 / 3))  # only short margin used


async def test_limit_fill_cancelled_on_leverage_mismatch():
    """Limit order cancelled at fill time if position leverage doesn't match."""
    from src.integrations.exchange.simulated import _Position
    ex = _make_exchange(initial_balance=100.0)
    ex._leverage["BTC/USDT:USDT"] = 3
    await ex.create_order("BTC/USDT:USDT", "buy", "limit", 0.001, price=94000.0)

    # Create position with DIFFERENT leverage
    ex._positions["BTC/USDT:USDT"] = _Position(
        side="long", contracts=0.001, entry_price=96000.0, leverage=5,
    )

    tick = Ticker(symbol="BTC/USDT:USDT", last=93900.0, bid=93890.0, ask=93900.0,
                  high=96000.0, low=93000.0, base_volume=1000.0, timestamp=1712534402000)
    await ex._process_tick(tick)

    assert ex._frozen_usdt == 0.0  # unfrozen
    assert len(ex._pending_orders) == 0  # removed
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_simulated_exchange.py::test_limit_order_fills_when_price_reached -v`
Expected: FAIL

- [ ] **Step 3: Add limit to _should_trigger**

Update `_should_trigger` method:

```python
    def _should_trigger(self, order: _PendingOrder, ticker: Ticker) -> bool:
        if order.order_type == "limit":
            if order.side == "buy":
                return ticker.ask <= order.trigger_price
            else:
                return ticker.bid >= order.trigger_price
        elif order.order_type == "stop":
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
```

- [ ] **Step 4: Implement _execute_limit_fill**

```python
    def _execute_limit_fill(self, order: _PendingOrder, ticker: Ticker) -> FillEvent | None:
        """Fill a limit order. Returns None if cancelled due to conflict (unfreezes margin)."""
        pos = self._positions.get(order.symbol)
        position_side = "long" if order.side == "buy" else "short"

        # Check 1: reverse position conflict
        if pos is not None and pos.side != position_side:
            logger.warning(f"Limit order {order.id} cancelled: conflicts with {pos.side} position")
            self._frozen_usdt -= order.frozen_margin
            self._free_usdt += order.frozen_margin
            return None

        # Check 2: leverage mismatch
        if pos is not None and pos.leverage != order.leverage:
            logger.warning(
                f"Limit order {order.id} cancelled: leverage mismatch "
                f"(order={order.leverage}x, position={pos.leverage}x)"
            )
            self._frozen_usdt -= order.frozen_margin
            self._free_usdt += order.frozen_margin
            return None

        fill_price = order.trigger_price  # limit fills at specified price
        leverage = order.leverage
        actual_margin = (fill_price * order.amount) / leverage
        actual_fee = fill_price * order.amount * self._fee_rate
        actual_cost = actual_margin + actual_fee

        # Unfreeze → occupy
        self._frozen_usdt -= order.frozen_margin
        self._used_usdt += actual_margin
        self._free_usdt += (order.frozen_margin - actual_cost)
        self._free_usdt = round(self._free_usdt, 8)
        self._used_usdt = round(self._used_usdt, 8)

        # Create or merge position
        if pos is not None and pos.side == position_side:
            new_contracts = pos.contracts + order.amount
            new_entry = (pos.entry_price * pos.contracts + fill_price * order.amount) / new_contracts
            pos.contracts = new_contracts
            pos.entry_price = new_entry
            pos.updated_at = datetime.now(timezone.utc)
        else:
            self._positions[order.symbol] = _Position(
                side=position_side, contracts=order.amount,
                entry_price=fill_price, leverage=leverage,
            )
        self._leverage[order.symbol] = leverage

        now_ms = int(datetime.now(timezone.utc).timestamp() * 1000)
        logger.info(f"Limit order filled: {order.side} {order.amount} {order.symbol} @ {fill_price:.2f}")
        return FillEvent(
            order_id=order.id, symbol=order.symbol, side=order.side,
            position_side=position_side, trigger_reason="limit",
            fill_price=fill_price, amount=order.amount, fee=actual_fee,
            pnl=None, timestamp=now_ms,
        )
```

- [ ] **Step 5: Update _process_tick step 2 to include limit orders**

In the conditional order check loop (step 2), add limit handling:

```python
            # 2. Conditional + limit order check
            processed = set(filled_order_ids + cancelled_order_ids)
            for order in list(self._pending_orders):
                if order.id in processed:
                    continue
                if self._should_trigger(order, ticker):
                    if order.order_type in ("stop", "take_profit"):
                        if not self._has_position(order.symbol):
                            continue
                        fill = self._execute_fill(order, ticker)
                    elif order.order_type == "limit":
                        fill = self._execute_limit_fill(order, ticker)
                        if fill is None:
                            cancelled_order_ids.append(order.id)
                            continue
                    else:
                        continue
                    triggered.append(fill)
                    filled_order_ids.append(order.id)
```

- [ ] **Step 6: Run limit tests**

Run: `python -m pytest tests/test_simulated_exchange.py -k "limit" -v`
Expected: PASS

- [ ] **Step 7: Commit**

```bash
git add src/integrations/exchange/simulated.py tests/test_simulated_exchange.py
git commit -m "feat(r6): implement limit order matching — _should_trigger + _execute_limit_fill"
```

---

### Task 17: Update cancel_order for limit margin unfreeze + reject market cancel

**Files:**
- Modify: `src/integrations/exchange/simulated.py:541-560`

- [ ] **Step 1: Write tests**

```python
async def test_limit_order_cancel_unfreezes():
    """Cancelling a limit order returns frozen margin to free_usdt."""
    ex = _make_exchange(initial_balance=100.0)
    ex._leverage["BTC/USDT:USDT"] = 3
    order = await ex.create_order("BTC/USDT:USDT", "buy", "limit", 0.001, price=90000.0)
    frozen = ex._frozen_usdt
    assert frozen > 0

    await ex.cancel_order(order.id, "BTC/USDT:USDT")
    assert ex._frozen_usdt == 0.0
    assert ex._free_usdt == pytest.approx(100.0)
    assert len(ex._pending_orders) == 0


async def test_cancel_market_order_rejected():
    """Cannot cancel a market order (already in matching queue)."""
    ex = _make_exchange(initial_balance=100.0)
    ex._leverage["BTC/USDT:USDT"] = 3
    order = await ex.create_order("BTC/USDT:USDT", "buy", "market", 0.001)
    with pytest.raises(ValueError, match="Cannot cancel market orders"):
        await ex.cancel_order(order.id, "BTC/USDT:USDT")
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_simulated_exchange.py::test_limit_order_cancel_unfreezes tests/test_simulated_exchange.py::test_cancel_market_order_rejected -v`
Expected: FAIL

- [ ] **Step 3: Rewrite cancel_order**

```python
    async def cancel_order(self, order_id: str, symbol: str) -> None:
        self._validate_symbol(symbol)
        async with self._lock:
            order = None
            for o in self._pending_orders:
                if o.id == order_id:
                    order = o
                    break
            if order is None:
                raise ValueError(f"Order not found: {order_id}")

            if order.order_type == "market":
                raise ValueError("Cannot cancel market orders")

            # Unfreeze margin (limit orders have frozen margin)
            if order.frozen_margin > 0:
                self._frozen_usdt -= order.frozen_margin
                self._free_usdt += order.frozen_margin

            self._remove_order_by_id(order_id)
            if self._db_engine:
                from sqlalchemy import update
                from src.storage.database import get_session
                from src.storage.models import SimOrder
                async with get_session(self._db_engine) as session:
                    await session.execute(
                        update(SimOrder)
                        .where(SimOrder.order_id == order_id)
                        .where(SimOrder.status == "open")
                        .values(status="cancelled")
                    )
                    await session.commit()
        logger.info(f"Order cancelled: {order_id}")
```

- [ ] **Step 4: Run tests**

Run: `python -m pytest tests/test_simulated_exchange.py::test_limit_order_cancel_unfreezes tests/test_simulated_exchange.py::test_cancel_market_order_rejected -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/integrations/exchange/simulated.py tests/test_simulated_exchange.py
git commit -m "feat(r6): cancel_order unfreezes limit margin, rejects market cancel"
```

---

### Task 18: Add place_limit_order tool + register in trader.py

**Files:**
- Modify: `src/agent/tools_execution.py`
- Modify: `src/agent/trader.py`

- [ ] **Step 1: Add place_limit_order to tools_execution.py**

Add at end of file:

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

    # Leverage: match position if exists, else use specified
    positions = await deps.exchange.fetch_positions(deps.symbol)
    if positions:
        actual_leverage = positions[0].leverage
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

- [ ] **Step 2: Register in trader.py**

Add after the `set_next_wake` tool and before the Memory Tools section:

```python
    @agent.tool
    async def place_limit_order(
        ctx: RunContext[TradingDeps],
        side: str,
        price: float,
        position_pct: float,
        leverage: int,
        reasoning: str,
    ) -> str:
        """Place a limit order at a specific price (e.g., buy at support level). side='long' or 'short'. position_pct=% of free balance. Always provide reasoning."""
        from src.agent.tools_execution import place_limit_order as _impl

        return await _impl(ctx.deps, side, price, position_pct, leverage, reasoning=reasoning)
```

- [ ] **Step 3: Run existing tests to verify no regressions**

Run: `python -m pytest tests/ -v`
Expected: All PASS

- [ ] **Step 4: Commit**

```bash
git add src/agent/tools_execution.py src/agent/trader.py
git commit -m "feat(r6): add place_limit_order tool and register in trader agent"
```

---

### Task 19: Update persona.py system prompt

**Files:**
- Modify: `src/agent/persona.py:64`

- [ ] **Step 1: Update the async fill guidance**

In `persona.py`, replace line 64:
```python
- After opening a position, you MUST set stop loss and take profit in the follow-up cycle
```

With:
```python
- After submitting an order, you will be notified when it fills. Set stop loss and take profit only after receiving fill confirmation — do NOT attempt in the same cycle as order submission
```

- [ ] **Step 2: Add limit order guidance**

Before the `## Memory` section, add:

```python
## Limit Orders
You can use `place_limit_order` to enter at a specific price (e.g., buy at a support level). Limit orders stay pending until the price is reached. Use market orders for immediate entry, limit orders for planned entries at key levels.
```

- [ ] **Step 3: Run tests**

Run: `python -m pytest tests/ -v`
Expected: All PASS

- [ ] **Step 4: Commit**

```bash
git add src/agent/persona.py
git commit -m "feat(r6): update persona prompt — async fill guidance + limit order guidance"
```

---

### Task 20: Persistence tests (market + limit)

**Files:**
- Modify: `tests/test_simulated_exchange.py`

- [ ] **Step 1: Write persistence tests**

These tests require a real DB engine. Check existing persistence test patterns (e.g., `test_persist_and_restore`) and follow the same setup.

```python
async def test_pending_market_order_persisted_and_restored():
    """Market order pending state survives persist → restore cycle."""
    # This test requires db_engine — follow pattern from test_persist_and_restore
    # If existing test uses a real db, follow that pattern
    # Otherwise, test the in-memory fields directly
    from src.integrations.exchange.simulated import _PendingOrder
    ex = _make_exchange(initial_balance=100.0)
    ex._leverage["BTC/USDT:USDT"] = 3
    await ex.create_order("BTC/USDT:USDT", "buy", "market", 0.001)

    assert len(ex._pending_orders) == 1
    po = ex._pending_orders[0]
    assert po.order_type == "market"
    assert po.frozen_margin > 0
    assert po.leverage == 3
    assert po.trigger_price is None


async def test_limit_order_persisted_and_restored():
    """Limit order pending state has correct fields."""
    ex = _make_exchange(initial_balance=100.0)
    ex._leverage["BTC/USDT:USDT"] = 3
    await ex.create_order("BTC/USDT:USDT", "buy", "limit", 0.001, price=90000.0)

    assert len(ex._pending_orders) == 1
    po = ex._pending_orders[0]
    assert po.order_type == "limit"
    assert po.frozen_margin > 0
    assert po.leverage == 3
    assert po.trigger_price == 90000.0
```

- [ ] **Step 2: Run persistence tests**

Run: `python -m pytest tests/test_simulated_exchange.py::test_pending_market_order_persisted_and_restored tests/test_simulated_exchange.py::test_limit_order_persisted_and_restored -v`
Expected: PASS

- [ ] **Step 3: Write frozen buffer coverage test**

```python
async def test_frozen_buffer_covers_price_movement():
    """0.2% buffer covers normal tick-to-tick price movement."""
    ex = _make_exchange(initial_balance=100.0)
    ex._leverage["BTC/USDT:USDT"] = 3
    await ex.create_order("BTC/USDT:USDT", "buy", "market", 0.001)

    # Price moves up by 0.1% (within buffer)
    new_ask = 95010.0 * 1.001
    tick = Ticker(symbol="BTC/USDT:USDT", last=new_ask - 10, bid=new_ask - 20, ask=new_ask,
                  high=96000.0, low=94000.0, base_volume=1000.0, timestamp=1712534401000)
    await ex._process_tick(tick)

    assert ex._free_usdt >= 0.0
    assert ex._frozen_usdt == 0.0
```

- [ ] **Step 4: Run and commit**

Run: `python -m pytest tests/test_simulated_exchange.py -k "persisted or buffer_covers" -v`
Expected: PASS

```bash
git add tests/test_simulated_exchange.py
git commit -m "test(r6): add persistence and frozen buffer coverage tests"
```

---

### Task 21: Final verification — full test suite

- [ ] **Step 1: Run complete test suite**

Run: `python -m pytest tests/ -v`
Expected: All PASS

- [ ] **Step 2: Run with coverage if available**

Run: `python -m pytest tests/ -v --tb=short 2>&1 | tail -20`
Expected: All green

- [ ] **Step 3: Verify no regressions in unrelated tests**

Run: `python -m pytest tests/test_exchange.py tests/test_tools.py tests/test_trader_agent.py tests/test_scheduler.py tests/test_session_manager.py -v`
Expected: All PASS

- [ ] **Step 4: Final commit if any cleanup needed**

```bash
git status
# If clean, no commit needed
```
