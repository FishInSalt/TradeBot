# Toolkit Expansion Iter 2 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Ship 3 new perception tools (`get_order_book` / `get_recent_trades` / `get_multi_timeframe_snapshot`) + in-place enhancement of `get_position` (Risk exposure + Exit orders), with `BaseExchange` extended by 3 abstract methods (`fetch_order_book` / `fetch_trades` / `get_contract_size`).

**Architecture:** Strict data/render layer separation: `BaseExchange` → `MarketDataService` thin wrappers → `tools_perception` renders fact-only output. New tools follow PR C three-state contract (data / insufficient / unavailable). OKX `_parse_order` algo normalization is **out of scope** (Iter 2b).

**Tech Stack:** Python 3.13 / pydantic-ai 1.78.0 / SQLAlchemy 2.0 async / ccxt (OKX, swap) / pytest + pytest-asyncio + pytest-mock.

**Spec reference:** `docs/superpowers/specs/2026-04-20-toolkit-iter2-design.md` (794 lines, commit `554257b`, 14 independent review rounds passed).

---

## Task 0: Pre-flight Validation

**Purpose:** Verify 2 spec assumptions empirically before any code change. Both failures would invalidate design.

**Files:** None (verification only).

### Step 0.1: Confirm Balance.used_usdt field exists

- [ ] **Sub-step 0.1.1: Grep verify**

Run: `grep -n "used_usdt" /Users/z/Z/TradeBot/src/integrations/exchange/base.py`
Expected: `48:    used_usdt: float` (±1 line tolerance).

If missing, abort plan — it expands scope (BaseExchange + all exchanges + all mocks).

### Step 0.2: Confirm CCXT load_markets memoization

- [ ] **Sub-step 0.2.1: Inspect CCXT async version**

Run: `cd /Users/z/Z/TradeBot && uv run python -c "import ccxt.async_support; import inspect; src = inspect.getsource(ccxt.async_support.Exchange.load_markets); print('_markets_loading' in src or 'markets_loading' in src.lower())"`
Expected: `True`.

If `False`, spec §4.2 fallback "self-managed `asyncio.Lock`" becomes required — mark it in Task 3's `get_contract_size` sub-step.

### Step 0.3: Confirm OKX fetch_trades upper limit

- [ ] **Sub-step 0.3.1: Inspect CCXT describe**

Run: `cd /Users/z/Z/TradeBot && uv run python -c "import ccxt.async_support as c; ex = c.okx(); print(ex.describe().get('api', {}).get('public', {}).get('get', {}).get('market/trades', 'n/a'))"`

Alternative if the above path is wrong: `grep -n "market/trades" $(uv run python -c "import ccxt.async_support; print(ccxt.async_support.__file__.replace('__init__.py', 'okx.py'))")`

Expected: Confirm OKX `/market/trades` single-call limit = 500. If CCXT docs mismatches 500, update `RECENT_TRADES_MAX_FETCH` constant in spec before proceeding.

### Step 0.4: Baseline test count

- [ ] **Sub-step 0.4.1: Record baseline**

Run: `cd /Users/z/Z/TradeBot && uv run pytest --collect-only -q 2>/dev/null | tail -5`
Expected: `681 tests collected` (±0). If different, note the actual baseline for later `~725` target adjustment.

---

## Task 1: BaseExchange Extension + All Subclass Stubs (single commit)

**Purpose:** Add 3 dataclasses + 3 abstract methods + stubs to all subclasses (source + test) + skeleton impls on OKX/Sim. Single commit keeps pytest green throughout.

**Files:**
- Modify: `src/integrations/exchange/base.py` (add dataclasses + abstracts)
- Modify: `src/integrations/exchange/okx.py` (skeleton `raise NotImplementedError`)
- Modify: `src/integrations/exchange/simulated.py` (skeleton + `_prev_ticker`)
- Modify: `tests/test_exchange.py` (7 subclass stubs)
- Modify: `tests/test_price_level_alert.py` (1 subclass stub)
- Modify: `tests/test_tool_enhancement.py` (2 subclass stubs)

### Step 1.1: Add dataclasses to base.py

- [ ] **Sub-step 1.1.1: Locate insertion point**

Run: `grep -n "class Balance:\|class Position:" /Users/z/Z/TradeBot/src/integrations/exchange/base.py`
Expected: line ~45 `class Balance`, line ~52 `class Position`. Insert new dataclasses after `Balance`, before `Position`.

- [ ] **Sub-step 1.1.2: Add 3 dataclasses**

Insert after the `Balance` dataclass closing line:

```python
@dataclass
class OrderBookLevel:
    price: float
    amount: float  # base-currency


@dataclass
class OrderBook:
    symbol: str
    bids: list[OrderBookLevel]  # sorted by price DESC (best first)
    asks: list[OrderBookLevel]  # sorted by price ASC (best first)
    timestamp: int | None  # CCXT may return None in some exchanges/conditions


@dataclass
class Trade:
    timestamp: int  # ms
    side: str       # "buy" | "sell" (taker direction per CCXT unified spec)
    price: float
    amount: float   # base-currency
    trade_id: str | None
```

### Step 1.2: Add 3 abstract methods to BaseExchange

- [ ] **Sub-step 1.2.1: Locate abstract method block**

Run: `grep -n "@abstractmethod" /Users/z/Z/TradeBot/src/integrations/exchange/base.py | tail -5`
Expected: list of abstract methods ending around `fetch_long_short_ratio`.

- [ ] **Sub-step 1.2.2: Append 3 new abstract methods**

After the last `@abstractmethod` in `BaseExchange` class body, add:

```python
    @abstractmethod
    async def fetch_order_book(self, symbol: str, depth: int = 20) -> OrderBook: ...

    @abstractmethod
    async def fetch_trades(self, symbol: str, limit: int = 500) -> list[Trade]: ...

    @abstractmethod
    async def get_contract_size(self, symbol: str) -> float:
        """Contract multiplier. OKX BTC swap = 0.01 BTC/contract; Sim = 1.0."""
        ...
```

### Step 1.3: Run tests — expect broken subclass instantiations

- [ ] **Sub-step 1.3.1: Verify abstract enforcement**

Run: `cd /Users/z/Z/TradeBot && uv run pytest tests/test_exchange.py -x 2>&1 | grep -E "abstract|TypeError" | head -5`
Expected: `Can't instantiate abstract class` errors across `DummyExchange` / `_Stub` subclasses. This confirms the abstract methods are enforced.

### Step 1.4: Add stubs to test_exchange.py subclasses

- [ ] **Sub-step 1.4.1: Find all subclasses**

Run: `grep -n "class.*(BaseExchange)" /Users/z/Z/TradeBot/tests/test_exchange.py`
Expected: 7 class definitions (`IncompleteExchange`, 5× `DummyExchange`, `_Stub`).

- [ ] **Sub-step 1.4.2: Add stubs to non-Incomplete subclasses**

For each of the **6** non-`IncompleteExchange` subclasses (lines ~228, 253, 279, 305, 330, 354), add these 3 methods at the end of the class body:

```python
        async def fetch_order_book(self, symbol, depth=20):
            from src.integrations.exchange.base import OrderBook, OrderBookLevel
            return OrderBook(symbol=symbol, bids=[OrderBookLevel(100.0, 1.0)], asks=[OrderBookLevel(101.0, 1.0)], timestamp=0)

        async def fetch_trades(self, symbol, limit=500):
            return []

        async def get_contract_size(self, symbol):
            return 1.0
```

**Do NOT add** to `IncompleteExchange` — it intentionally tests abstract-contract enforcement and relies on being incomplete.

### Step 1.5: Add stubs to test_price_level_alert.py

- [ ] **Sub-step 1.5.1: Find _TestExchange class**

Run: `grep -n "class _TestExchange" /Users/z/Z/TradeBot/tests/test_price_level_alert.py`
Expected: 1 match at line ~13.

- [ ] **Sub-step 1.5.2: Add 3 stubs at class body end**

Same 3-method block as Step 1.4.2.

### Step 1.6: Add stubs to test_tool_enhancement.py

- [ ] **Sub-step 1.6.1: Find _TestExchange classes**

Run: `grep -n "class _TestExchange" /Users/z/Z/TradeBot/tests/test_tool_enhancement.py`
Expected: 2 matches at lines ~42 and ~80.

- [ ] **Sub-step 1.6.2: Add 3 stubs to each**

Same 3-method block as Step 1.4.2, once per class.

### Step 1.7: Add OKX / Sim skeleton implementations

- [ ] **Sub-step 1.7.1: Add okx.py skeletons**

In `src/integrations/exchange/okx.py`, at the end of `OKXExchange` class (before any module-level code), add:

```python
    async def fetch_order_book(self, symbol: str, depth: int = 20) -> OrderBook:
        raise NotImplementedError("Task 3 will implement this")

    async def fetch_trades(self, symbol: str, limit: int = 500) -> list[Trade]:
        raise NotImplementedError("Task 4 will implement this")

    async def get_contract_size(self, symbol: str) -> float:
        raise NotImplementedError("Task 5 will implement this")
```

Also ensure `from src.integrations.exchange.base import OrderBook, Trade` is in the imports block at the top of the file.

- [ ] **Sub-step 1.7.2: Add simulated.py skeletons + _prev_ticker field**

In `src/integrations/exchange/simulated.py`:

1. Find `self._latest_ticker: Ticker | None = None` (line ~72) and add **one line below it**:

```python
        self._prev_ticker: Ticker | None = None
```

2. Find the line updating `_latest_ticker` (grep `self._latest_ticker = ticker`, line ~583). **Immediately before** that line, add:

```python
        self._prev_ticker = self._latest_ticker  # save previous before overwrite (for fetch_trades bias)
```

3. Add skeletons at the end of `SimulatedExchange` class (before module-level code):

```python
    async def fetch_order_book(self, symbol: str, depth: int = 20) -> OrderBook:
        raise NotImplementedError("Task 2 will implement this")

    async def fetch_trades(self, symbol: str, limit: int = 500) -> list[Trade]:
        raise NotImplementedError("Task 2 will implement this")

    async def get_contract_size(self, symbol: str) -> float:
        raise NotImplementedError("Task 2 will implement this")
```

Also ensure `OrderBook, Trade` are imported from `base`.

### Step 1.8: Run full pytest to verify 681 baseline holds

- [ ] **Sub-step 1.8.1: Full test run**

Run: `cd /Users/z/Z/TradeBot && uv run pytest 2>&1 | tail -3`
Expected: `681 passed` (tests don't yet exercise the new abstract methods on OKX/Sim, so `NotImplementedError` is not triggered). If any test fails, it means a subclass stub was missed — grep for the failing class name in test files.

### Step 1.9: Commit

- [ ] **Sub-step 1.9.1: Commit**

```bash
git add src/integrations/exchange/base.py src/integrations/exchange/okx.py src/integrations/exchange/simulated.py tests/test_exchange.py tests/test_price_level_alert.py tests/test_tool_enhancement.py
git commit -m "$(cat <<'EOF'
feat(exchange): extend BaseExchange with order_book / trades / contract_size abstract methods

Adds OrderBookLevel / OrderBook / Trade dataclasses and 3 abstract
methods. OKX / Sim implementations are skeletons raising
NotImplementedError — filled in subsequent tasks. All test subclass
stubs added (test_exchange.py / test_price_level_alert.py /
test_tool_enhancement.py). SimulatedExchange also gains _prev_ticker
field for fetch_trades direction-bias computation (Task 2).

Baseline 681 tests still pass.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 2: SimulatedExchange Implementation

**Purpose:** Fill Sim's 3 methods with synthetic implementations per spec §4.3. Simpler than OKX (no network), so tackle first.

**Files:**
- Modify: `src/integrations/exchange/simulated.py`
- Create: `tests/test_exchange_order_book.py`

### Step 2.1: Create test file with Sim fetch_order_book tests

- [ ] **Sub-step 2.1.1: Write test skeleton**

Create `tests/test_exchange_order_book.py`:

```python
"""Tests for BaseExchange.fetch_order_book / fetch_trades / get_contract_size across OKX and Sim implementations."""
from __future__ import annotations
import pytest
import random
from src.integrations.exchange.base import OrderBook, OrderBookLevel, Trade, Ticker
from src.integrations.exchange.simulated import SimulatedExchange


def _prime_sim_ticker(ex: SimulatedExchange, last: float = 50000.0) -> None:
    """Directly seed SimulatedExchange._latest_ticker without routing through _process_tick.

    Exists because SimulatedExchange has no public set_ticker method —
    real price updates come through the internal tick loop which we don't want
    to exercise in unit tests.
    """
    ex._latest_ticker = Ticker(
        symbol="BTC/USDT:USDT", last=last, bid=last - 0.5, ask=last + 0.5,
        high=last + 100, low=last - 100, base_volume=1000.0, timestamp=0,
    )


@pytest.mark.asyncio
async def test_sim_fetch_order_book_structure():
    """Sim fetch_order_book returns correctly-structured OrderBook synthesized from ticker."""
    ex = SimulatedExchange(symbol="BTC/USDT:USDT", initial_balance=10000.0)
    _prime_sim_ticker(ex, last=50000.0)
    ob = await ex.fetch_order_book("BTC/USDT:USDT", depth=20)
    assert isinstance(ob, OrderBook)
    assert ob.symbol == "BTC/USDT:USDT"
    assert len(ob.bids) == 20
    assert len(ob.asks) == 20
    # Bids descending (best first)
    assert all(ob.bids[i].price >= ob.bids[i+1].price for i in range(len(ob.bids) - 1))
    # Asks ascending
    assert all(ob.asks[i].price <= ob.asks[i+1].price for i in range(len(ob.asks) - 1))
    # Best bid below best ask
    assert ob.bids[0].price < ob.asks[0].price


@pytest.mark.asyncio
async def test_sim_fetch_order_book_custom_depth():
    """Depth parameter respected."""
    ex = SimulatedExchange(symbol="BTC/USDT:USDT", initial_balance=10000.0)
    _prime_sim_ticker(ex, last=50000.0)
    ob = await ex.fetch_order_book("BTC/USDT:USDT", depth=5)
    assert len(ob.bids) == 5
    assert len(ob.asks) == 5
```

- [ ] **Sub-step 2.1.2: Run — expect fail on NotImplementedError**

Run: `cd /Users/z/Z/TradeBot && uv run pytest tests/test_exchange_order_book.py -x 2>&1 | tail -5`
Expected: `NotImplementedError: Task 2 will implement this`.

### Step 2.2: Implement Sim.fetch_order_book

- [ ] **Sub-step 2.2.1: Replace skeleton with real impl**

In `src/integrations/exchange/simulated.py`, replace the `fetch_order_book` skeleton with:

```python
    async def fetch_order_book(self, symbol: str, depth: int = 20) -> OrderBook:
        """Synthesize order book from ticker (best bid/ask + ±0.01% steps)."""
        import time
        if self._latest_ticker is None:
            return OrderBook(symbol=symbol, bids=[], asks=[], timestamp=None)
        bid_price = self._latest_ticker.bid
        ask_price = self._latest_ticker.ask
        bids = [
            OrderBookLevel(
                price=round(bid_price * (1 - 0.0001 * i), 2),
                amount=round(0.01 * (1 + i * 0.1), 4),
            )
            for i in range(depth)
        ]
        asks = [
            OrderBookLevel(
                price=round(ask_price * (1 + 0.0001 * i), 2),
                amount=round(0.01 * (1 + i * 0.1), 4),
            )
            for i in range(depth)
        ]
        return OrderBook(symbol=symbol, bids=bids, asks=asks, timestamp=int(time.time() * 1000))
```

Ensure `OrderBookLevel` is imported.

- [ ] **Sub-step 2.2.2: Run tests again**

Run: `cd /Users/z/Z/TradeBot && uv run pytest tests/test_exchange_order_book.py::test_sim_fetch_order_book_structure tests/test_exchange_order_book.py::test_sim_fetch_order_book_custom_depth -v 2>&1 | tail -5`
Expected: Both pass.

### Step 2.3: Add fetch_trades tests + implementation

- [ ] **Sub-step 2.3.1: Append tests to test_exchange_order_book.py**

```python
@pytest.mark.asyncio
async def test_sim_fetch_trades_structure():
    """Sim fetch_trades returns Trade list with valid fields."""
    ex = SimulatedExchange(symbol="BTC/USDT:USDT", initial_balance=10000.0)
    _prime_sim_ticker(ex, last=50000.0)
    trades = await ex.fetch_trades("BTC/USDT:USDT", limit=500)
    assert isinstance(trades, list)
    assert 20 <= len(trades) <= 50
    for t in trades:
        assert isinstance(t, Trade)
        assert t.side in ("buy", "sell")
        assert t.price > 0
        assert 0.001 <= t.amount <= 0.01
        assert t.timestamp > 0


@pytest.mark.asyncio
async def test_sim_fetch_trades_direction_bias_rising():
    """Over N rounds of rising ticker, cumulative buy volume > sell volume (bias 55%+).

    Manually advances _prev_ticker each round because _prime_sim_ticker bypasses
    _process_tick (which is where prev-save happens in production). Without this,
    _prev_ticker stays None forever → price_change_pct = 0 → buy_prob = 0.5 → flat.
    """
    random.seed(42)
    ex = SimulatedExchange(symbol="BTC/USDT:USDT", initial_balance=10000.0)
    _prime_sim_ticker(ex, last=50000.0)  # seed initial _latest_ticker
    total_buy = 0.0
    total_sell = 0.0
    price = 50000.0
    for _ in range(100):
        ex._prev_ticker = ex._latest_ticker  # manually advance (mimics _process_tick behavior)
        price *= 1.005  # +0.5% each round
        _prime_sim_ticker(ex, last=price)
        trades = await ex.fetch_trades("BTC/USDT:USDT", limit=500)
        total_buy += sum(t.amount for t in trades if t.side == "buy")
        total_sell += sum(t.amount for t in trades if t.side == "sell")
    total = total_buy + total_sell
    buy_share = total_buy / total
    assert buy_share >= 0.55, f"Expected buy bias >= 55% under rising ticker, got {buy_share:.2%}"


@pytest.mark.asyncio
async def test_sim_fetch_trades_direction_bias_falling():
    """Over N rounds of falling ticker, cumulative sell volume > buy volume (bias 55%+)."""
    random.seed(42)
    ex = SimulatedExchange(symbol="BTC/USDT:USDT", initial_balance=10000.0)
    _prime_sim_ticker(ex, last=50000.0)  # seed initial
    total_buy = 0.0
    total_sell = 0.0
    price = 50000.0
    for _ in range(100):
        ex._prev_ticker = ex._latest_ticker  # manually advance
        price *= 0.995  # -0.5% each round
        _prime_sim_ticker(ex, last=price)
        trades = await ex.fetch_trades("BTC/USDT:USDT", limit=500)
        total_buy += sum(t.amount for t in trades if t.side == "buy")
        total_sell += sum(t.amount for t in trades if t.side == "sell")
    total = total_buy + total_sell
    sell_share = total_sell / total
    assert sell_share >= 0.55, f"Expected sell bias >= 55% under falling ticker, got {sell_share:.2%}"
```

- [ ] **Sub-step 2.3.2: Run — expect NotImplementedError**

Run: `cd /Users/z/Z/TradeBot && uv run pytest tests/test_exchange_order_book.py::test_sim_fetch_trades_structure -x 2>&1 | tail -3`
Expected: `NotImplementedError`.

- [ ] **Sub-step 2.3.3: Implement Sim.fetch_trades**

Replace skeleton with:

```python
    async def fetch_trades(self, symbol: str, limit: int = 500) -> list[Trade]:
        """Synthesize ~20-50 trades with direction biased by ticker change."""
        import random
        import time
        if self._latest_ticker is None:
            return []
        # Direction bias based on prev → latest bid change
        if self._prev_ticker is not None and self._prev_ticker.bid > 0:
            price_change_pct = (self._latest_ticker.bid - self._prev_ticker.bid) / self._prev_ticker.bid
        else:
            price_change_pct = 0.0
        buy_prob = 0.5 + max(-0.15, min(0.15, price_change_pct * 20))
        n_trades = random.randint(20, 50)
        mid = (self._latest_ticker.bid + self._latest_ticker.ask) / 2
        now_ms = int(time.time() * 1000)
        window_ms = 300_000  # 5 min, matches RECENT_TRADES_WINDOW_DEFAULT
        trades: list[Trade] = []
        for _ in range(n_trades):
            side = "buy" if random.random() < buy_prob else "sell"
            price = round(mid * (1 + random.uniform(-0.0002, 0.0002)), 2)
            amount = round(random.uniform(0.001, 0.01), 4)
            age_ms = random.randint(0, window_ms - 1)
            trades.append(Trade(
                timestamp=now_ms - age_ms,
                side=side, price=price, amount=amount,
                trade_id=None,
            ))
        return trades
```

- [ ] **Sub-step 2.3.4: Run tests — expect all 3 pass**

Run: `cd /Users/z/Z/TradeBot && uv run pytest tests/test_exchange_order_book.py -v 2>&1 | tail -8`
Expected: 5 tests pass (2 order_book + 3 trades).

### Step 2.4: Implement Sim.get_contract_size

- [ ] **Sub-step 2.4.1: Add test**

```python
@pytest.mark.asyncio
async def test_sim_get_contract_size_always_one():
    """Sim always returns 1.0 (no contract multiplier model)."""
    ex = SimulatedExchange(symbol="BTC/USDT:USDT", initial_balance=10000.0)
    assert await ex.get_contract_size("BTC/USDT:USDT") == 1.0
    assert await ex.get_contract_size("ETH/USDT:USDT") == 1.0
```

- [ ] **Sub-step 2.4.2: Replace skeleton**

```python
    async def get_contract_size(self, symbol: str) -> float:
        return 1.0
```

- [ ] **Sub-step 2.4.3: Run — pass**

Run: `cd /Users/z/Z/TradeBot && uv run pytest tests/test_exchange_order_book.py::test_sim_get_contract_size_always_one -v`
Expected: PASS.

### Step 2.5: Commit

- [ ] **Sub-step 2.5.1: Commit**

```bash
git add src/integrations/exchange/simulated.py tests/test_exchange_order_book.py
git commit -m "$(cat <<'EOF'
feat(sim): implement SimulatedExchange fetch_order_book / fetch_trades / get_contract_size

Synthetic order book: best bid/ask from ticker, depth levels at ±0.01%
steps with ascending amounts. Trades: 20-50 synthetic entries with
taker direction biased by prev→latest bid change (buy_prob = 0.5 +
clip(price_change_pct * 20, ±0.15)). get_contract_size returns 1.0.

Direction bias verified in test with random.seed(42) + 100-round
cumulative assertion (rising → buy_share ≥ 55%; falling → sell_share
≥ 55%).

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 3: OKXExchange fetch_order_book Implementation

**Files:**
- Modify: `src/integrations/exchange/okx.py`
- Modify: `tests/test_exchange_order_book.py`

### Step 3.1: Write failing test (CCXT mock)

- [ ] **Sub-step 3.1.1: Append test**

```python
@pytest.mark.asyncio
async def test_okx_fetch_order_book_parses_ccxt_response(mocker):
    """OKX fetch_order_book parses CCXT raw dict into OrderBook dataclass."""
    from src.integrations.exchange.okx import OKXExchange
    ex = OKXExchange(api_key="k", secret="s", password="p", symbol="BTC/USDT:USDT")
    mock_fetch = mocker.patch.object(
        ex._client, "fetch_order_book",
        return_value={
            "bids": [[50000.0, 1.0], [49999.5, 0.5]],
            "asks": [[50001.0, 0.8], [50001.5, 1.2]],
            "timestamp": 1700000000000,
        }
    )
    ob = await ex.fetch_order_book("BTC/USDT:USDT", depth=2)
    assert ob.symbol == "BTC/USDT:USDT"
    assert ob.timestamp == 1700000000000
    assert len(ob.bids) == 2
    assert ob.bids[0].price == 50000.0
    assert ob.bids[0].amount == 1.0
    assert ob.asks[0].price == 50001.0
    mock_fetch.assert_called_once_with("BTC/USDT:USDT", limit=2)


@pytest.mark.asyncio
async def test_okx_fetch_order_book_timestamp_none_fallback(mocker):
    """If CCXT returns timestamp=None, OKX layer fills with current time."""
    from src.integrations.exchange.okx import OKXExchange
    ex = OKXExchange(api_key="k", secret="s", password="p", symbol="BTC/USDT:USDT")
    mocker.patch.object(ex._client, "fetch_order_book", return_value={
        "bids": [[50000.0, 1.0]], "asks": [[50001.0, 1.0]], "timestamp": None,
    })
    ob = await ex.fetch_order_book("BTC/USDT:USDT", depth=1)
    # Fallback: should be a reasonable ms timestamp (close to now)
    import time
    now_ms = int(time.time() * 1000)
    assert ob.timestamp is not None
    assert abs(ob.timestamp - now_ms) < 10_000  # within 10s
```

- [ ] **Sub-step 3.1.2: Run — expect NotImplementedError**

Run: `cd /Users/z/Z/TradeBot && uv run pytest tests/test_exchange_order_book.py::test_okx_fetch_order_book_parses_ccxt_response -x 2>&1 | tail -3`
Expected: `NotImplementedError` from skeleton.

### Step 3.2: Implement OKX.fetch_order_book

- [ ] **Sub-step 3.2.1: Replace skeleton**

```python
    @_retry(max_retries=2, base_delay=0.5)
    async def fetch_order_book(self, symbol: str, depth: int = 20) -> OrderBook:
        import time
        data = await self._client.fetch_order_book(symbol, limit=depth)
        bids = [OrderBookLevel(price=float(p), amount=float(a)) for p, a in data.get("bids", [])]
        asks = [OrderBookLevel(price=float(p), amount=float(a)) for p, a in data.get("asks", [])]
        ts = data.get("timestamp")
        if ts is None:
            ts = int(time.time() * 1000)
        return OrderBook(symbol=symbol, bids=bids, asks=asks, timestamp=ts)
```

Ensure `OrderBookLevel` is imported from `base`.

- [ ] **Sub-step 3.2.2: Verify `_retry` accepts custom params**

Run: `grep -n "def _retry" /Users/z/Z/TradeBot/src/integrations/exchange/okx.py`
Expected: match showing `_retry(max_retries=3, base_delay=1.0)` signature — verify keyword args work.

If `_retry` signature doesn't support keyword overrides, note this as a spec correction and use existing positional form (or upgrade `_retry` in a small separate commit before this step).

- [ ] **Sub-step 3.2.3: Run tests — both pass**

Run: `cd /Users/z/Z/TradeBot && uv run pytest tests/test_exchange_order_book.py -k "okx_fetch_order_book" -v`
Expected: 2 PASS.

### Step 3.2b: Add @_retry parameter verification test (spec §5.1)

- [ ] **Sub-step 3.2b.1: Append test covering custom retry params**

```python
@pytest.mark.asyncio
async def test_okx_fetch_order_book_retry_params(mocker):
    """@_retry(max_retries=2, base_delay=0.5) — exactly 2 total attempts (max_retries IS total count per okx.py:60 `for attempt in range(max_retries)`), then raises."""
    import ccxt
    from src.integrations.exchange.okx import OKXExchange
    ex = OKXExchange(api_key="k", secret="s", password="p", symbol="BTC/USDT:USDT")
    mock_fetch = mocker.patch.object(
        ex._client, "fetch_order_book",
        side_effect=ccxt.NetworkError("temporary network failure"),
    )
    # Speed up the retry delays for test
    mocker.patch("asyncio.sleep", return_value=None)
    with pytest.raises(ccxt.NetworkError):
        await ex.fetch_order_book("BTC/USDT:USDT", depth=20)
    assert mock_fetch.call_count == 2, (
        f"Expected 2 total attempts for max_retries=2 "
        f"(per okx.py:60 `for attempt in range(max_retries)` → max_retries IS total attempt count, not +1), "
        f"got {mock_fetch.call_count}. "
        "If count=3, @_retry is still using default max_retries=3 — verify fetch_order_book decoration."
    )


@pytest.mark.asyncio
async def test_okx_fetch_trades_retry_params(mocker):
    """Same retry param verification for fetch_trades."""
    import ccxt
    from src.integrations.exchange.okx import OKXExchange
    ex = OKXExchange(api_key="k", secret="s", password="p", symbol="BTC/USDT:USDT")
    mock_fetch = mocker.patch.object(
        ex._client, "fetch_trades",
        side_effect=ccxt.NetworkError("temporary network failure"),
    )
    mocker.patch("asyncio.sleep", return_value=None)
    with pytest.raises(ccxt.NetworkError):
        await ex.fetch_trades("BTC/USDT:USDT", limit=500)
    assert mock_fetch.call_count == 2
```

- [ ] **Sub-step 3.2b.2: Run — may fail if `_retry` doesn't accept kwargs**

Run: `cd /Users/z/Z/TradeBot && uv run pytest tests/test_exchange_order_book.py -k "retry_params" -v`
Expected: PASS (if `_retry` signature already accepts keyword overrides — verified in §3.2.2 grep).

If fails because `_retry` is positional-only: upgrade `_retry` first in a **single tiny prior commit** to accept `max_retries` / `base_delay` as kwargs, then re-run.

### Step 3.3: Commit

- [ ] **Sub-step 3.3.1: Commit**

```bash
git add src/integrations/exchange/okx.py tests/test_exchange_order_book.py
git commit -m "$(cat <<'EOF'
feat(okx): implement fetch_order_book with timestamp fallback

Parses CCXT fetch_order_book response into OrderBook dataclass with
OrderBookLevel entries. Falls back to current ms when CCXT returns
timestamp=None (observed in low-liquidity conditions). Uses
@_retry(max_retries=2, base_delay=0.5) per spec §3.3 for high-time-
sensitivity endpoints (worst-case 1.5s retry vs default 7s).

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 4: OKXExchange fetch_trades Implementation

**Files:**
- Modify: `src/integrations/exchange/okx.py`
- Modify: `tests/test_exchange_order_book.py`

### Step 4.1: Write failing test

- [ ] **Sub-step 4.1.1: Append test**

```python
@pytest.mark.asyncio
async def test_okx_fetch_trades_parses_and_sorts(mocker):
    """OKX fetch_trades parses CCXT response and explicitly sorts ascending by timestamp."""
    from src.integrations.exchange.okx import OKXExchange
    ex = OKXExchange(api_key="k", secret="s", password="p", symbol="BTC/USDT:USDT")
    # Deliberately unordered to test explicit sort
    mocker.patch.object(ex._client, "fetch_trades", return_value=[
        {"timestamp": 1700000030000, "side": "buy", "price": 50001.0, "amount": 0.01, "id": "t3"},
        {"timestamp": 1700000010000, "side": "sell", "price": 50000.0, "amount": 0.02, "id": "t1"},
        {"timestamp": 1700000020000, "side": "buy", "price": 50000.5, "amount": 0.015, "id": None},
    ])
    trades = await ex.fetch_trades("BTC/USDT:USDT", limit=500)
    assert len(trades) == 3
    # Sorted ascending by timestamp
    assert trades[0].timestamp == 1700000010000
    assert trades[1].timestamp == 1700000020000
    assert trades[2].timestamp == 1700000030000
    # trade_id None handling
    assert trades[0].trade_id == "t1"
    assert trades[1].trade_id is None
```

- [ ] **Sub-step 4.1.2: Run — fail**

Expected: `NotImplementedError`.

### Step 4.2: Implement OKX.fetch_trades

- [ ] **Sub-step 4.2.1: Replace skeleton**

```python
    @_retry(max_retries=2, base_delay=0.5)
    async def fetch_trades(self, symbol: str, limit: int = 500) -> list[Trade]:
        data = await self._client.fetch_trades(symbol, limit=limit)
        trades: list[Trade] = []
        for raw in data:
            raw_id = raw.get("id")
            trades.append(Trade(
                timestamp=int(raw["timestamp"]),
                side=str(raw["side"]),
                price=float(raw["price"]),
                amount=float(raw["amount"]),
                trade_id=str(raw_id) if raw_id is not None else None,
            ))
        # Explicit sort — don't rely on CCXT default (unified spec is ascending but not guaranteed)
        trades.sort(key=lambda t: t.timestamp)
        return trades
```

- [ ] **Sub-step 4.2.2: Run — pass**

Run: `cd /Users/z/Z/TradeBot && uv run pytest tests/test_exchange_order_book.py::test_okx_fetch_trades_parses_and_sorts -v`
Expected: PASS.

### Step 4.3: Commit

```bash
git add src/integrations/exchange/okx.py tests/test_exchange_order_book.py
git commit -m "$(cat <<'EOF'
feat(okx): implement fetch_trades with explicit ascending sort + trade_id None handling

Parses CCXT fetch_trades dicts into Trade dataclass. Explicit sort
by timestamp ascending — does not rely on CCXT default ordering
(spec §4.1: unified spec is ascending but per-exchange may differ).
trade_id: None-safe str coercion (avoids str(None)='None' pitfall).
Uses @_retry(max_retries=2, base_delay=0.5) per spec §3.3.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 5: OKXExchange get_contract_size + start() preload

**Files:**
- Modify: `src/integrations/exchange/okx.py`
- Modify: `tests/test_exchange_order_book.py`

### Step 5.1: Write 3 failing tests

- [ ] **Sub-step 5.1.1: Append tests**

```python
@pytest.mark.asyncio
async def test_okx_get_contract_size_loaded(mocker):
    """Markets preloaded: returns contractSize directly from memory."""
    from src.integrations.exchange.okx import OKXExchange
    ex = OKXExchange(api_key="k", secret="s", password="p", symbol="BTC/USDT:USDT")
    ex._client.markets = {"BTC/USDT:USDT": {"contractSize": 0.01}}
    load_mock = mocker.patch.object(ex._client, "load_markets")
    size = await ex.get_contract_size("BTC/USDT:USDT")
    assert size == 0.01
    load_mock.assert_not_called()  # no lazy load needed


@pytest.mark.asyncio
async def test_okx_get_contract_size_lazy_load(mocker):
    """Markets not loaded: triggers lazy load_markets."""
    from src.integrations.exchange.okx import OKXExchange
    ex = OKXExchange(api_key="k", secret="s", password="p", symbol="BTC/USDT:USDT")
    ex._client.markets = {}  # empty → falsy → lazy load triggered
    def _side_effect(*_, **__):
        ex._client.markets = {"BTC/USDT:USDT": {"contractSize": 0.01}}
    load_mock = mocker.patch.object(ex._client, "load_markets", side_effect=_side_effect)
    size = await ex.get_contract_size("BTC/USDT:USDT")
    assert size == 0.01
    load_mock.assert_called_once()


@pytest.mark.asyncio
async def test_okx_get_contract_size_unknown_market_fallback(mocker):
    """Market not in markets dict → returns 1.0 fallback + warning."""
    from src.integrations.exchange.okx import OKXExchange
    ex = OKXExchange(api_key="k", secret="s", password="p", symbol="BTC/USDT:USDT")
    ex._client.markets = {"ETH/USDT:USDT": {"contractSize": 0.01}}
    size = await ex.get_contract_size("BTC/USDT:USDT")
    assert size == 1.0
```

- [ ] **Sub-step 5.1.2: Run — fail**

Expected: `NotImplementedError`.

### Step 5.2: Implement OKX.get_contract_size

- [ ] **Sub-step 5.2.1: Replace skeleton**

```python
    async def get_contract_size(self, symbol: str) -> float:
        if not self._client.markets:
            await self._client.load_markets()
        market = self._client.markets.get(symbol)
        if market is None:
            logger.warning("Market %s not loaded, defaulting contract_size=1.0", symbol)
            return 1.0
        return float(market.get("contractSize", 1.0))
```

- [ ] **Sub-step 5.2.2: Run tests — pass**

Run: `cd /Users/z/Z/TradeBot && uv run pytest tests/test_exchange_order_book.py -k "get_contract_size" -v`
Expected: 3 (OKX) + 1 (Sim) PASS.

### Step 5.3: Add load_markets preload to start()

- [ ] **Sub-step 5.3.1: Locate existing start() method**

Run: `grep -n "async def start" /Users/z/Z/TradeBot/src/integrations/exchange/okx.py`
Expected: line ~115.

- [ ] **Sub-step 5.3.2: Add preload BEFORE the existing try-block**

Based on spec §8.5 decision (fail-fast outside try is preferred — markets failure is fatal for everything downstream), add **immediately after** `async def start(self) -> None:` docstring / first line, **outside** the WebSocket try-block:

```python
        # Preload markets for get_contract_size — fail-fast outside WebSocket try
        # (markets unavailable means all tools relying on contract sizing will be broken;
        # better to fail at startup than silently fall back per-call later)
        await self._client.load_markets()
```

- [ ] **Sub-step 5.3.3: Run full test suite**

Run: `cd /Users/z/Z/TradeBot && uv run pytest 2>&1 | tail -3`
Expected: All passing, +6 new (2 order_book OKX + 2 trades OKX bonus + 3 contract_size OKX + 1 Sim contract_size — ≈ 8-10 new relative to baseline, actual count depends on exact numbering).

### Step 5.4: Commit

```bash
git add src/integrations/exchange/okx.py tests/test_exchange_order_book.py
git commit -m "$(cat <<'EOF'
feat(okx): implement get_contract_size + preload markets in start()

get_contract_size reads market['contractSize'] from in-memory CCXT
markets dict with lazy load_markets fallback when markets not yet
loaded (e.g. test mocks). Unknown market returns 1.0 with warning
log.

start() now preloads markets outside the WebSocket try-block as a
fail-fast: if markets load fails, every contract-sizing-dependent
tool would break anyway, so better fail at startup than silently
fall back per-call (spec §8.5).

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 6: MarketDataService Thin Wrappers

**Files:**
- Modify: `src/integrations/market_data.py`
- Create: `tests/test_market_data_new_wrappers.py` (or append to existing test_market_data.py if exists)

### Step 6.1: Grep current market_data tests

- [ ] **Sub-step 6.1.1: Check existing file**

Run: `ls /Users/z/Z/TradeBot/tests/test_market_data.py 2>/dev/null || echo "not-found"`

If exists: append; if not: create new `tests/test_market_data_new_wrappers.py`.

### Step 6.2: Write failing tests

- [ ] **Sub-step 6.2.1: Write tests**

```python
import pytest
from unittest.mock import AsyncMock
from src.integrations.market_data import MarketDataService
from src.integrations.exchange.base import OrderBook, OrderBookLevel, Trade


@pytest.mark.asyncio
async def test_market_data_get_order_book_delegates_to_exchange():
    """MarketDataService.get_order_book is a thin wrapper with no caching."""
    exchange = AsyncMock()
    exchange.fetch_order_book.return_value = OrderBook(
        symbol="BTC/USDT:USDT",
        bids=[OrderBookLevel(100.0, 1.0)],
        asks=[OrderBookLevel(101.0, 1.0)],
        timestamp=123,
    )
    svc = MarketDataService(exchange)
    ob = await svc.get_order_book("BTC/USDT:USDT", depth=5)
    assert ob.symbol == "BTC/USDT:USDT"
    exchange.fetch_order_book.assert_called_once_with("BTC/USDT:USDT", depth=5)


@pytest.mark.asyncio
async def test_market_data_get_recent_trades_delegates():
    """MarketDataService.get_recent_trades is a thin wrapper."""
    exchange = AsyncMock()
    exchange.fetch_trades.return_value = [Trade(timestamp=1, side="buy", price=100.0, amount=0.01, trade_id="x")]
    svc = MarketDataService(exchange)
    trades = await svc.get_recent_trades("BTC/USDT:USDT", limit=500)
    assert len(trades) == 1
    exchange.fetch_trades.assert_called_once_with("BTC/USDT:USDT", limit=500)
```

- [ ] **Sub-step 6.2.2: Run — fail (method doesn't exist)**

Expected: `AttributeError: 'MarketDataService' object has no attribute 'get_order_book'`.

### Step 6.3: Implement thin wrappers

- [ ] **Sub-step 6.3.1: Add methods to MarketDataService**

In `src/integrations/market_data.py`, add after `get_ohlcv_dataframe`:

```python
    async def get_order_book(self, symbol: str, depth: int = 20) -> OrderBook:
        return await self._exchange.fetch_order_book(symbol, depth=depth)

    async def get_recent_trades(self, symbol: str, limit: int = 500) -> list[Trade]:
        return await self._exchange.fetch_trades(symbol, limit=limit)
```

Add imports at top: `from src.integrations.exchange.base import OrderBook, Trade`.

- [ ] **Sub-step 6.3.2: Run — pass**

Run: `cd /Users/z/Z/TradeBot && uv run pytest tests/test_market_data_new_wrappers.py -v`
Expected: 2 PASS.

### Step 6.4: Commit

```bash
git add src/integrations/market_data.py tests/test_market_data_new_wrappers.py
git commit -m "$(cat <<'EOF'
feat(market_data): thin wrappers for order_book / recent_trades

Delegates to exchange without caching — order book / trades have
high time sensitivity, caching would be actively harmful (spec §0.3).
Keeps the tool-layer → service-layer → exchange-layer split
consistent with existing patterns (get_ohlcv_dataframe).

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 7: tools_perception.get_order_book

**Files:**
- Modify: `src/agent/tools_perception.py`
- Create: `tests/test_toolkit_iter2.py`

### Step 7.1: Define module constants

- [ ] **Sub-step 7.1.1: Add constants at top of tools_perception.py**

After existing imports, add a constants section:

```python
# === Iter 2 toolkit constants ===
# get_order_book
ORDER_BOOK_CONCENTRATION_MULTIPLIER = 3.0
ORDER_BOOK_MAX_CONCENTRATED_LEVELS = 10
ORDER_BOOK_DEPTH_DEFAULT = 20
ORDER_BOOK_BALANCED_THRESHOLD_PCT = 5.0

# get_recent_trades
RECENT_TRADES_WINDOW_DEFAULT = 300
RECENT_TRADES_BUCKET_COUNT = 5
RECENT_TRADES_MAX_FETCH = 500  # OKX /market/trades single-call limit

# get_multi_timeframe_snapshot
MULTI_TF_PRIMARY_MA = {"5m": 20, "1h": 50, "4h": 50, "1d": 50, "1w": 50, "1M": 50}
MULTI_TF_STRUCTURE_MAS = {
    "5m": (20, 50),
    "1h": (50, 200),
    "4h": (50, 200),
    "1d": (50, 200),
    "1w": (20, 50),
    "1M": (20, 50),
}
MULTI_TF_RANGE_PERIODS = 20
MULTI_TF_OHLCV_LIMIT = {"5m": 80, "1h": 250, "4h": 250, "1d": 250, "1w": 60, "1M": 60}
```

### Step 7.2: Write failing tests

- [ ] **Sub-step 7.2.1: Create tests/test_toolkit_iter2.py**

```python
"""Rendering tests for 3 new tools + get_position enhancement (spec §5.2)."""
from __future__ import annotations
import pytest
from dataclasses import dataclass, field
from unittest.mock import AsyncMock
from src.integrations.exchange.base import OrderBook, OrderBookLevel, Trade, Ticker, Balance, Position, Order


@dataclass
class MockDeps:
    symbol: str = "BTC/USDT:USDT"
    initial_balance: float = 10000.0
    exchange: AsyncMock = field(default_factory=AsyncMock)
    market_data: AsyncMock = field(default_factory=AsyncMock)
    technical: AsyncMock = field(default_factory=AsyncMock)


@pytest.mark.asyncio
async def test_order_book_typical_output_format():
    """Typical order book renders best bid/ask, cumulative depth, bid share, concentrated levels."""
    from src.agent.tools_perception import get_order_book
    deps = MockDeps()
    deps.market_data.get_order_book.return_value = OrderBook(
        symbol="BTC/USDT:USDT",
        bids=[
            OrderBookLevel(64190.5, 0.024), OrderBookLevel(64190.0, 0.156),
            *[OrderBookLevel(64190.0 - i * 0.5, 0.1) for i in range(2, 20)],
        ],
        asks=[
            OrderBookLevel(64200.5, 0.032), OrderBookLevel(64201.0, 0.089),
            *[OrderBookLevel(64200.5 + i * 0.5, 0.1) for i in range(2, 20)],
        ],
        timestamp=0,
    )
    result = await get_order_book(deps)
    assert "Order Book" in result
    assert "Best bid:" in result
    assert "Best ask:" in result
    assert "Spread:" in result
    assert "Bid share:" in result
    assert "Depth (top 20 each side)" in result


@pytest.mark.asyncio
async def test_order_book_empty_insufficient():
    """Empty order book returns 'insufficient data' with depth info."""
    from src.agent.tools_perception import get_order_book
    deps = MockDeps()
    deps.market_data.get_order_book.return_value = OrderBook(
        symbol="BTC/USDT:USDT", bids=[], asks=[], timestamp=0,
    )
    result = await get_order_book(deps, depth=20)
    assert "insufficient data" in result
    assert "requested depth 20" in result
    assert "got 0" in result


@pytest.mark.asyncio
async def test_order_book_service_failure():
    """Exception in service layer → 'temporarily unavailable'."""
    from src.agent.tools_perception import get_order_book
    deps = MockDeps()
    deps.market_data.get_order_book.side_effect = Exception("connection reset")
    result = await get_order_book(deps)
    assert "temporarily unavailable" in result


@pytest.mark.asyncio
async def test_order_book_bid_side_heavy():
    """Bid total >> ask total: output shows bid share > 55%."""
    from src.agent.tools_perception import get_order_book
    deps = MockDeps()
    deps.market_data.get_order_book.return_value = OrderBook(
        symbol="BTC/USDT:USDT",
        bids=[OrderBookLevel(100.0 - i * 0.1, 2.0) for i in range(20)],   # total 40
        asks=[OrderBookLevel(100.1 + i * 0.1, 0.5) for i in range(20)],   # total 10
        timestamp=0,
    )
    result = await get_order_book(deps)
    # bids 40 / (40+10) = 80%
    assert "Bid share: 80" in result or "Bid share: 80.0" in result


@pytest.mark.asyncio
async def test_order_book_no_concentrated_levels():
    """All levels have uniform amount → median ≈ amount → no level > 3× median → Concentrated section absent."""
    from src.agent.tools_perception import get_order_book
    deps = MockDeps()
    deps.market_data.get_order_book.return_value = OrderBook(
        symbol="BTC/USDT:USDT",
        bids=[OrderBookLevel(100.0 - i * 0.1, 1.0) for i in range(20)],  # all 1.0
        asks=[OrderBookLevel(100.1 + i * 0.1, 1.0) for i in range(20)],  # all 1.0
        timestamp=0,
    )
    result = await get_order_book(deps)
    # Main sections present
    assert "Best bid:" in result
    assert "Bid share:" in result
    # But no concentrated section when no level exceeds 3× median
    assert "Concentrated levels" not in result


@pytest.mark.asyncio
async def test_order_book_concentrated_truncation_to_10():
    """When > 10 levels exceed 3× median, output truncates to top-10 by amount.

    Data shape: 14 tiny (0.001) + 6 huge (10.0) per side (14+6 = 20 total).
    Sorted → median is between [9th, 10th] which are both tiny → median = 0.001.
    Threshold = 0.001 × 3 = 0.003. All 6 huge levels per side pass → 12 total concentrated.
    12 > 10 → truncation to top-10 kicks in.

    (An earlier version used "10 huge + 10 tiny alternating" which gave median=(0.01+10)/2=5.005
    → threshold=15.015 → zero concentrated levels → test failure. Audit caught it.)
    """
    from src.agent.tools_perception import get_order_book
    deps = MockDeps()
    bids = [OrderBookLevel(100.0 - i * 0.1, 0.001 if i < 14 else 10.0) for i in range(20)]
    asks = [OrderBookLevel(100.1 + i * 0.1, 0.001 if i < 14 else 10.0) for i in range(20)]
    deps.market_data.get_order_book.return_value = OrderBook(
        symbol="BTC/USDT:USDT", bids=bids, asks=asks, timestamp=0,
    )
    result = await get_order_book(deps)
    assert "Concentrated levels" in result
    # Count rendered concentrated rows (each starts with "  Bid  " or "  Ask  ")
    concentrated_lines = [l for l in result.splitlines() if l.startswith("  Bid  ") or l.startswith("  Ask  ")]
    assert len(concentrated_lines) <= 10, f"Expected ≤ 10 truncated rows, got {len(concentrated_lines)}"
```

- [ ] **Sub-step 7.2.2: Run — fail (function doesn't exist)**

Run: `cd /Users/z/Z/TradeBot && uv run pytest tests/test_toolkit_iter2.py::test_order_book_typical_output_format -x 2>&1 | tail -5`
Expected: `ImportError` or `AttributeError`.

### Step 7.3: Implement get_order_book

- [ ] **Sub-step 7.3.1: Add function to tools_perception.py**

At an appropriate location (after existing tools, before N3 helpers), add:

```python
async def get_order_book(deps: TradingDeps, depth: int = ORDER_BOOK_DEPTH_DEFAULT) -> str:
    """Return top-N order book depth with concentrated-level breakdown.

    Args:
        depth: Levels per side to fetch. Default 20.

    Returns:
        str: Multi-line fact-only text (best bid/ask + cumulative depth + bid share + concentrated levels). See spec §2.1.

    Degradation: Returns "Order book ({symbol}): insufficient data (requested depth X, got Y)" if book is empty/short;
    "Order book ({symbol}): temporarily unavailable" on service failure.
    """
    symbol = deps.symbol
    try:
        ob = await deps.market_data.get_order_book(symbol, depth=depth)
    except Exception:
        logger.exception("get_order_book failed for %s", symbol)
        return f"Order book ({symbol}): temporarily unavailable"

    actual = min(len(ob.bids), len(ob.asks))
    if not ob.bids or not ob.asks or actual < depth:
        return f"Order book ({symbol}): insufficient data (requested depth {depth}, got {actual})"

    best_bid = ob.bids[0]
    best_ask = ob.asks[0]
    mid = (best_bid.price + best_ask.price) / 2
    spread = best_ask.price - best_bid.price
    spread_pct = spread / mid * 100

    total_bid = sum(l.amount for l in ob.bids[:depth])
    total_ask = sum(l.amount for l in ob.asks[:depth])
    total_sum = total_bid + total_ask
    bid_deep_pct = (ob.bids[0].price - ob.bids[depth - 1].price) / ob.bids[0].price * 100
    ask_deep_pct = (ob.asks[depth - 1].price - ob.asks[0].price) / ob.asks[0].price * 100

    # Bid share three-state
    if total_bid == 0 and total_ask > 0:
        share_line = "Bid share: 0% (asks only, no bids in top {})".format(depth)
    elif total_ask == 0 and total_bid > 0:
        share_line = "Bid share: 100% (bids only, no asks in top {})".format(depth)
    else:
        bid_share = total_bid / total_sum * 100
        if abs(bid_share - 50) < ORDER_BOOK_BALANCED_THRESHOLD_PCT:
            # Spec §2.1 — fixed '~50%' label when within balanced threshold, not actual value.
            # Actual value on a balanced output creates a conflicting signal
            # ("Bid share: ~47% (balanced)" mixes precise percentage with the approximation marker).
            share_line = "Bid share: ~50% (balanced)"
        else:
            bid_ratio = total_bid / total_ask if total_ask > 0 else float("inf")
            share_line = f"Bid share: {bid_share:.1f}% (bid : ask = {bid_ratio:.2f} : 1)"

    lines = [
        f"=== Order Book ({symbol}) ===",
        f"Best bid: {best_bid.price:.2f} × {best_bid.amount:.4f} BTC  |  Best ask: {best_ask.price:.2f} × {best_ask.amount:.4f} BTC",
        f"Spread: {spread:.2f} ({spread_pct:.3f}%)",
        "",
        f"Depth (top {depth} each side):",
        f"  Bids cumulative: {total_bid:.4f} BTC over {best_bid.price:.2f} - {ob.bids[depth-1].price:.2f} ({bid_deep_pct:.2f}% deep)",
        f"  Asks cumulative: {total_ask:.4f} BTC over {best_ask.price:.2f} - {ob.asks[depth-1].price:.2f} ({ask_deep_pct:.2f}% deep)",
        f"  {share_line}",
    ]

    # Concentrated levels (per-side median)
    import statistics
    bid_amounts = [l.amount for l in ob.bids[:depth]]
    ask_amounts = [l.amount for l in ob.asks[:depth]]
    bid_median = statistics.median(bid_amounts)
    ask_median = statistics.median(ask_amounts)
    threshold_bid = bid_median * ORDER_BOOK_CONCENTRATION_MULTIPLIER
    threshold_ask = ask_median * ORDER_BOOK_CONCENTRATION_MULTIPLIER

    concentrated = []
    for l in ob.bids[:depth]:
        if l.amount > threshold_bid:
            concentrated.append(("Bid", l.price, l.amount, (mid - l.price) / mid * 100, True))
    for l in ob.asks[:depth]:
        if l.amount > threshold_ask:
            concentrated.append(("Ask", l.price, l.amount, (l.price - mid) / mid * 100, False))

    if concentrated:
        # Sort top-10 by amount desc, then restore display order (bids-then-asks, nearest-to-mid first)
        concentrated.sort(key=lambda c: c[2], reverse=True)
        concentrated = concentrated[:ORDER_BOOK_MAX_CONCENTRATED_LEVELS]
        bids_conc = sorted([c for c in concentrated if c[0] == "Bid"], key=lambda c: -c[1])  # price desc
        asks_conc = sorted([c for c in concentrated if c[0] == "Ask"], key=lambda c: c[1])   # price asc
        lines.append("")
        lines.append(f"Concentrated levels (size > {ORDER_BOOK_CONCENTRATION_MULTIPLIER:.0f}× median of top {depth}):")
        for side, price, amount, dist_pct, is_bid in bids_conc + asks_conc:
            direction = "below mid" if is_bid else "above mid"
            lines.append(f"  {side}  {price:.2f}  {amount:.4f} BTC  ({dist_pct:.2f}% {direction})")

    return "\n".join(lines)
```

- [ ] **Sub-step 7.3.2: Run tests — 4 pass**

Run: `cd /Users/z/Z/TradeBot && uv run pytest tests/test_toolkit_iter2.py -k "order_book" -v`
Expected: 4 PASS.

### Step 7.4: Commit

```bash
git add src/agent/tools_perception.py tests/test_toolkit_iter2.py
git commit -m "$(cat <<'EOF'
feat(tools): add get_order_book with concentrated-level breakdown

Implements spec §2.1: best bid/ask + spread + cumulative depth +
bid share (with 0/100% single-side + <±5% balanced three-state) +
concentrated levels (size > 3× same-side median, max 10, sorted by
amount desc then restored to bids-desc / asks-asc nearest-to-mid
order). Fact-only output (no 'wall' / 'strong' labels).

Three-state: data / insufficient data (with depth info) / unavailable.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 8: tools_perception.get_recent_trades

**Files:**
- Modify: `src/agent/tools_perception.py`
- Modify: `tests/test_toolkit_iter2.py`

### Step 8.1: Write failing tests

- [ ] **Sub-step 8.1.1: Append tests**

```python
@pytest.mark.asyncio
async def test_recent_trades_typical(mocker):
    """Typical: 5 buckets, total + count + avg size."""
    from src.agent.tools_perception import get_recent_trades
    import time
    now_ms = int(time.time() * 1000)
    trades = []
    # Distribute trades into known buckets — e.g. 100 trades evenly across 5 minutes
    for i in range(100):
        age = i * 3000  # 0 to 297s
        trades.append(Trade(timestamp=now_ms - age, side="buy" if i % 3 == 0 else "sell",
                            price=64000.0, amount=0.01, trade_id=None))
    deps = MockDeps()
    deps.market_data.get_recent_trades.return_value = trades
    result = await get_recent_trades(deps, window_seconds=300)
    assert "Recent Trades" in result
    assert "last 300s" in result
    assert "5 × 60s buckets" in result
    assert "Total:" in result
    assert "Trade count: 100" in result
    assert "Avg size:" in result


@pytest.mark.asyncio
async def test_recent_trades_empty_cold_market():
    """No trades in window → insufficient data."""
    from src.agent.tools_perception import get_recent_trades
    deps = MockDeps()
    deps.market_data.get_recent_trades.return_value = []
    result = await get_recent_trades(deps, window_seconds=300)
    assert "no trades in last 300s" in result


@pytest.mark.asyncio
async def test_recent_trades_service_failure():
    from src.agent.tools_perception import get_recent_trades
    deps = MockDeps()
    deps.market_data.get_recent_trades.side_effect = Exception("timeout")
    result = await get_recent_trades(deps)
    assert "temporarily unavailable" in result


@pytest.mark.asyncio
async def test_recent_trades_partial_coverage_double_condition():
    """When n>=95% of max AND oldest age < 95% window → partial coverage flagged."""
    from src.agent.tools_perception import get_recent_trades, RECENT_TRADES_MAX_FETCH
    import time
    now_ms = int(time.time() * 1000)
    # Fill up to limit, oldest 200s ago → 200/300 = 67% of window
    trades = [Trade(timestamp=now_ms - int((i / RECENT_TRADES_MAX_FETCH) * 200_000),
                    side="buy", price=64000.0, amount=0.01, trade_id=None)
              for i in range(RECENT_TRADES_MAX_FETCH)]
    deps = MockDeps()
    deps.market_data.get_recent_trades.return_value = trades
    result = await get_recent_trades(deps, window_seconds=300)
    assert "partial coverage" in result


@pytest.mark.asyncio
async def test_recent_trades_all_taker_sell():
    """All trades are taker-sell → 0% taker buy / 100% taker sell / negative net."""
    from src.agent.tools_perception import get_recent_trades
    import time
    now_ms = int(time.time() * 1000)
    trades = [Trade(timestamp=now_ms - i * 3000, side="sell", price=64000.0, amount=0.01, trade_id=None)
              for i in range(50)]
    deps = MockDeps()
    deps.market_data.get_recent_trades.return_value = trades
    result = await get_recent_trades(deps, window_seconds=300)
    assert "0% taker buy" in result
    assert "net -" in result  # negative net (all sells)
```

- [ ] **Sub-step 8.1.2: Run — fail**

Expected: Function doesn't exist.

### Step 8.2: Implement get_recent_trades

- [ ] **Sub-step 8.2.1: Add function**

```python
async def get_recent_trades(deps: TradingDeps, window_seconds: int = RECENT_TRADES_WINDOW_DEFAULT) -> str:
    """Return taker-flow bias and rhythm over a recent time window via 5 time-buckets.

    Args:
        window_seconds: Observation window in seconds. Default 300 (5 min).

    Returns:
        str: 5-bucket breakdown + Total + trade count + avg size. See spec §2.2.

    Degradation: "no trades in last {window_seconds}s" if cold market; "temporarily unavailable" on service failure.
    """
    import time
    symbol = deps.symbol
    try:
        trades = await deps.market_data.get_recent_trades(symbol, limit=RECENT_TRADES_MAX_FETCH)
    except Exception:
        logger.exception("get_recent_trades failed for %s", symbol)
        return f"Recent trades ({symbol}): temporarily unavailable"

    if not trades:
        return f"Recent trades ({symbol}): no trades in last {window_seconds}s"

    now_ms = int(time.time() * 1000)
    window_ms = window_seconds * 1000
    bucket_duration_ms = window_ms // RECENT_TRADES_BUCKET_COUNT

    # Allocate trades to buckets (0 = oldest, 4 = newest); drop over-window
    buckets: list[list[Trade]] = [[] for _ in range(RECENT_TRADES_BUCKET_COUNT)]
    in_window: list[Trade] = []
    for t in trades:
        age_ms = now_ms - t.timestamp
        if age_ms >= window_ms:
            continue  # strict >= to avoid bucket_idx = -1 boundary bug
        bucket_idx = RECENT_TRADES_BUCKET_COUNT - 1 - (age_ms // bucket_duration_ms)
        buckets[bucket_idx].append(t)
        in_window.append(t)

    if not in_window:
        return f"Recent trades ({symbol}): no trades in last {window_seconds}s"

    lines = [f"=== Recent Trades ({symbol}, last {window_seconds}s, {RECENT_TRADES_BUCKET_COUNT} × {bucket_duration_ms // 1000}s buckets) ==="]
    total_buy = 0.0
    total_sell = 0.0
    for i, bucket in enumerate(buckets):
        buy_vol = sum(t.amount for t in bucket if t.side == "buy")
        sell_vol = sum(t.amount for t in bucket if t.side == "sell")
        net = buy_vol - sell_vol
        total_buy += buy_vol
        total_sell += sell_vol
        # Label: for standard 300s/5-bucket → t-5min to t-1min; otherwise bucket {i+1}/N ({start_s}-{end_s}s ago)
        if window_seconds == 300:
            label = f"t-{RECENT_TRADES_BUCKET_COUNT - i}min"
        else:
            start_s = (RECENT_TRADES_BUCKET_COUNT - i - 1) * (bucket_duration_ms // 1000)
            end_s = (RECENT_TRADES_BUCKET_COUNT - i) * (bucket_duration_ms // 1000)
            label = f"bucket {i+1}/{RECENT_TRADES_BUCKET_COUNT} ({start_s}-{end_s}s ago)"
        lines.append(f"  {label}  buy {buy_vol:.4f} / sell {sell_vol:.4f}  (net {net:+.4f})")

    total_vol = total_buy + total_sell
    buy_pct = total_buy / total_vol * 100 if total_vol > 0 else 0.0
    net_total = total_buy - total_sell
    total_label = f"Total: buy {total_buy:.4f} / sell {total_sell:.4f} (net {net_total:+.4f}, {buy_pct:.0f}% taker buy)"

    # Partial coverage double-condition
    fetch_ratio = len(trades) / RECENT_TRADES_MAX_FETCH
    oldest_age_ms = max(now_ms - t.timestamp for t in in_window)
    oldest_age_ratio = oldest_age_ms / window_ms
    if fetch_ratio >= 0.95 and oldest_age_ratio < 0.95:
        total_label = f"Total: buy {total_buy:.4f} / sell {total_sell:.4f} (net {net_total:+.4f}*, {buy_pct:.0f}% taker buy) [* partial coverage: {len(trades)} trades at limit, oldest age {oldest_age_ms//1000}s ({oldest_age_ratio:.0%} of window), window not fully covered]"

    lines.append(total_label)
    lines.append(f"Trade count: {len(in_window)} | Avg size: {total_vol / len(in_window):.4f} BTC")
    return "\n".join(lines)
```

- [ ] **Sub-step 8.2.2: Run tests — pass**

Run: `cd /Users/z/Z/TradeBot && uv run pytest tests/test_toolkit_iter2.py -k "recent_trades" -v`
Expected: 4 PASS.

### Step 8.3: Commit

```bash
git add src/agent/tools_perception.py tests/test_toolkit_iter2.py
git commit -m "$(cat <<'EOF'
feat(tools): add get_recent_trades with 5-bucket taker flow

Implements spec §2.2: 5×60s buckets showing buy/sell/net per window.
Strict >= window boundary prevents bucket_idx=-1 silent-wrong-bucket
bug. Partial coverage double-condition (fetch_ratio >= 0.95 AND
oldest_age_ratio < 0.95) flags genuinely truncated windows without
false-alarming on cold markets.

Three-state: data / no trades in window / unavailable.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 9: tools_perception.get_multi_timeframe_snapshot

**Files:**
- Modify: `src/agent/tools_perception.py`
- Modify: `tests/test_toolkit_iter2.py`

### Step 9.1: Write failing tests

- [ ] **Sub-step 9.1.1: Append tests**

```python
import pandas as pd


def _make_ohlcv_df(n: int, last_close: float = 64200.0) -> pd.DataFrame:
    """Helper: synthetic OHLCV with gentle trend."""
    return pd.DataFrame([
        {"timestamp": 1700000000000 + i * 60_000,
         "open": last_close - (n - i), "high": last_close - (n - i) + 5,
         "low": last_close - (n - i) - 5, "close": last_close - (n - i - 1),
         "volume": 100.0}
        for i in range(n)
    ])


@pytest.mark.asyncio
async def test_multi_tf_snapshot_typical(mocker):
    """Typical: 4 TFs all with sufficient data → 4 formatted rows + Columns header."""
    from src.agent.tools_perception import get_multi_timeframe_snapshot
    deps = MockDeps()
    deps.market_data.get_ohlcv_dataframe = AsyncMock(side_effect=lambda sym, tf, limit: _make_ohlcv_df(limit))
    deps.technical.compute_indicators = mocker.Mock(return_value={"atr_14": 85.0})
    deps.exchange.fetch_ticker = AsyncMock(return_value=Ticker(
        symbol="BTC/USDT:USDT", last=64200.0, bid=64199.5, ask=64200.5,
        high=65000.0, low=63000.0, base_volume=1000.0, timestamp=0,
    ))
    result = await get_multi_timeframe_snapshot(deps)
    assert "Multi-TF Snapshot" in result
    assert "Current price:" in result
    assert "Columns: Momentum" in result
    for tf in ("5m", "1h", "4h", "1d"):
        assert f"{tf}:" in result


@pytest.mark.asyncio
async def test_multi_tf_snapshot_custom_tfs(mocker):
    from src.agent.tools_perception import get_multi_timeframe_snapshot
    deps = MockDeps()
    deps.market_data.get_ohlcv_dataframe = AsyncMock(side_effect=lambda sym, tf, limit: _make_ohlcv_df(limit))
    deps.technical.compute_indicators = mocker.Mock(return_value={"atr_14": 85.0})
    deps.exchange.fetch_ticker = AsyncMock(return_value=Ticker(
        symbol="BTC/USDT:USDT", last=64200.0, bid=64199.5, ask=64200.5,
        high=65000.0, low=63000.0, base_volume=1000.0, timestamp=0,
    ))
    result = await get_multi_timeframe_snapshot(deps, tfs=["1h"])
    assert "1h:" in result
    assert "5m:" not in result


@pytest.mark.asyncio
async def test_multi_tf_snapshot_all_fail(mocker):
    """All TFs raise → overall unavailable."""
    from src.agent.tools_perception import get_multi_timeframe_snapshot
    deps = MockDeps()
    deps.market_data.get_ohlcv_dataframe = AsyncMock(side_effect=Exception("down"))
    result = await get_multi_timeframe_snapshot(deps)
    assert "temporarily unavailable" in result


@pytest.mark.asyncio
async def test_multi_tf_snapshot_per_tf_insufficient(mocker):
    """5m has only 30 candles (< 50 needed): that TF shows insufficient, others OK."""
    from src.agent.tools_perception import get_multi_timeframe_snapshot
    deps = MockDeps()

    def ohlcv_side(sym, tf, limit):
        if tf == "5m":
            return _make_ohlcv_df(30)  # insufficient for MA50
        return _make_ohlcv_df(limit)

    deps.market_data.get_ohlcv_dataframe = AsyncMock(side_effect=ohlcv_side)
    deps.technical.compute_indicators = mocker.Mock(return_value={"atr_14": 85.0})
    deps.exchange.fetch_ticker = AsyncMock(return_value=Ticker(
        symbol="BTC/USDT:USDT", last=64200.0, bid=64199.5, ask=64200.5,
        high=65000.0, low=63000.0, base_volume=1000.0, timestamp=0,
    ))
    result = await get_multi_timeframe_snapshot(deps)
    assert "5m: insufficient data" in result
    assert "1h:" in result  # still rendered


@pytest.mark.asyncio
async def test_multi_tf_snapshot_ma_entangled(mocker):
    """MA fast ≈ MA slow (diff < 0.1%) → 'MA{fast} at MA{slow}' rendering."""
    from src.agent.tools_perception import get_multi_timeframe_snapshot
    import pandas as pd
    deps = MockDeps()
    # Construct a DataFrame where MA50 and MA200 are within 0.1% (tight band of close values)
    tight_df = pd.DataFrame([
        {"timestamp": 1700000000000 + i * 60_000,
         "open": 64000.0, "high": 64001.0, "low": 63999.0,
         "close": 64000.0,  # constant → rolling means are all 64000, diff = 0
         "volume": 100.0}
        for i in range(250)
    ])
    deps.market_data.get_ohlcv_dataframe = AsyncMock(return_value=tight_df)
    deps.technical.compute_indicators = mocker.Mock(return_value={"atr_14": 85.0})
    deps.exchange.fetch_ticker = AsyncMock(return_value=Ticker(
        symbol="BTC/USDT:USDT", last=64000.0, bid=63999.5, ask=64000.5,
        high=64001.0, low=63999.0, base_volume=1000.0, timestamp=0,
    ))
    result = await get_multi_timeframe_snapshot(deps, tfs=["1h"])
    # Expect "MA50 at MA200" (entangled) instead of above/below
    assert "MA50 at MA200" in result
```

- [ ] **Sub-step 9.1.2: Run — fail**

Expected: Function not found.

### Step 9.2: Implement get_multi_timeframe_snapshot

- [ ] **Sub-step 9.2.1: Add function**

```python
async def get_multi_timeframe_snapshot(deps: TradingDeps, tfs: list[str] | None = None) -> str:
    """Quick multi-timeframe scan: momentum | structure | volatility | range position.

    Args:
        tfs: List of CCXT timeframes. Default ["5m", "1h", "4h", "1d"].

    Returns:
        str: 4-column row per TF + Columns header. See spec §2.3.

    Degradation: per-TF "insufficient data" or "temporarily unavailable"; overall unavailable only if ALL TFs fail.
    """
    import asyncio
    symbol = deps.symbol
    if tfs is None:
        tfs = ["5m", "1h", "4h", "1d"]

    # Fetch current price (from ticker, not per-TF close)
    try:
        ticker = await deps.exchange.fetch_ticker(symbol)
        current_price = ticker.last
    except Exception:
        logger.exception("get_multi_timeframe_snapshot ticker fetch failed for %s", symbol)
        return f"Multi-TF snapshot ({symbol}): temporarily unavailable"

    async def _fetch_one(tf: str) -> tuple[str, pd.DataFrame | Exception]:
        try:
            df = await deps.market_data.get_ohlcv_dataframe(symbol, tf, limit=MULTI_TF_OHLCV_LIMIT.get(tf, 250))
            return tf, df
        except Exception as e:
            return tf, e

    results = await asyncio.gather(*[_fetch_one(tf) for tf in tfs], return_exceptions=False)

    # All failed?
    if all(isinstance(r[1], Exception) for r in results):
        return f"Multi-TF snapshot ({symbol}): temporarily unavailable"

    rows: list[str] = []
    for tf, df_or_err in results:
        primary_ma_n = MULTI_TF_PRIMARY_MA.get(tf, 50)
        fast, slow = MULTI_TF_STRUCTURE_MAS.get(tf, (50, 200))
        if isinstance(df_or_err, Exception):
            rows.append(f"{tf}: temporarily unavailable")
            continue
        df = df_or_err
        if df.empty or len(df) < slow:
            rows.append(f"{tf}: insufficient data (need {slow} candles, got {len(df)})")
            continue
        indicators = deps.technical.compute_indicators(df)
        atr = indicators.get("atr_14")
        close = float(df["close"].iloc[-1])

        # Momentum: price vs primary MA
        primary_ma_val = float(df["close"].rolling(primary_ma_n).mean().iloc[-1])
        mom_pct = (current_price - primary_ma_val) / primary_ma_val * 100
        mom_str = f"{mom_pct:+.1f}% vs MA{primary_ma_n}"

        # Structure: MA(fast) vs MA(slow)
        ma_fast = float(df["close"].rolling(fast).mean().iloc[-1])
        ma_slow = float(df["close"].rolling(slow).mean().iloc[-1])
        diff_pct = abs(ma_fast - ma_slow) / ma_slow * 100
        if diff_pct < 0.1:
            struct_str = f"MA{fast} at MA{slow}"
        elif ma_fast > ma_slow:
            struct_str = f"MA{fast} above MA{slow}"
        else:
            struct_str = f"MA{fast} below MA{slow}"
        # (short-structure) marker ONLY for 1w/1M — these are degraded from (50, 200) due to history shortage.
        # 5m's (MA20, MA50) is its native structure, not a degradation → no marker (spec §2.3 example).
        if tf in ("1w", "1M"):
            struct_str += " (short-structure)"

        # Volatility
        atr_pct = (atr / close * 100) if atr is not None else None
        atr_str = f"ATR {atr_pct:.2f}%" if atr_pct is not None else "ATR N/A"

        # Range position: last 20-bar high/low
        last_20 = df.iloc[-MULTI_TF_RANGE_PERIODS:]
        hi = float(last_20["high"].max())
        lo = float(last_20["low"].min())
        range_pct = 0.0 if hi == lo else (close - lo) / (hi - lo) * 100

        rows.append(f"{tf}:  {mom_str:<16} | {struct_str:<40} | {atr_str:<12} | range pos {range_pct:.0f}%")

    header = [
        f"=== Multi-TF Snapshot ({symbol}) ===",
        f"Current price: {current_price:.2f}",
        "Columns: Momentum (price vs primary MA) | Structure (MA alignment) | Volatility (ATR as % of price) | Range pos (position within 20-bar high-low, 0%=low / 100%=high)",
        "",
    ]
    return "\n".join(header + rows)
```

- [ ] **Sub-step 9.2.2: Run tests — pass**

Run: `cd /Users/z/Z/TradeBot && uv run pytest tests/test_toolkit_iter2.py -k "multi_tf" -v`
Expected: 4 PASS.

### Step 9.3: Commit

```bash
git add src/agent/tools_perception.py tests/test_toolkit_iter2.py
git commit -m "$(cat <<'EOF'
feat(tools): add get_multi_timeframe_snapshot (4 TFs, 4 columns)

Implements spec §2.3: Momentum (price vs primary MA) | Structure
(MA alignment with short-structure marker for 1w/1M) | Volatility
(ATR%) | Range pos (20-bar H/L). Default TFs 5m/1h/4h/1d; customizable.
Per-TF independent degradation via asyncio.gather + graceful
exception-per-TF. Inline MA computation (no compute_indicators
MA200 expansion needed).

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 10: get_position Two-Phase Enhancement

**Files:**
- Modify: `src/agent/tools_perception.py`
- Modify: `tests/test_toolkit_iter2.py`
- Modify: `tests/test_tool_enhancement.py` (new field assertions)

### Step 10.1: Write failing tests for enhanced output

- [ ] **Sub-step 10.1.1: Append tests**

```python
from datetime import datetime, timezone


@pytest.mark.asyncio
async def test_get_position_empty_short_circuit(mocker):
    """No open position → early return (1 IO only, no parallel gather)."""
    from src.agent.tools_perception import get_position
    deps = MockDeps()
    deps.exchange.fetch_positions = AsyncMock(return_value=[])
    result = await get_position(deps)
    assert result == "No open positions."
    # Verify other IOs never called
    deps.exchange.fetch_balance.assert_not_called()


@pytest.mark.asyncio
async def test_get_position_enhanced_output(mocker):
    """With position: new Risk exposure + Exit orders sections present."""
    from src.agent.tools_perception import get_position
    deps = MockDeps()
    deps.exchange.fetch_positions = AsyncMock(return_value=[Position(
        symbol="BTC/USDT:USDT", side="long", contracts=0.01,
        entry_price=64000.0, unrealized_pnl=10.0, leverage=3,
        liquidation_price=55000.0, created_at=datetime(2026, 4, 21, 10, 0, tzinfo=timezone.utc),
    )])
    deps.market_data.get_ticker = AsyncMock(return_value=Ticker(
        symbol="BTC/USDT:USDT", last=64100.0, bid=64099.5, ask=64100.5,
        high=65000.0, low=63000.0, base_volume=1000.0, timestamp=0,
    ))
    deps.exchange.fetch_balance = AsyncMock(return_value=Balance(
        total_usdt=10010.0, free_usdt=9796.67, used_usdt=213.33,
    ))
    deps.market_data.get_ohlcv_dataframe = AsyncMock(return_value=_make_ohlcv_df(50, last_close=64100.0))
    deps.technical.compute_indicators = mocker.Mock(return_value={"atr_14": 88.0})
    deps.exchange.fetch_open_orders = AsyncMock(return_value=[
        Order(id="o1", symbol="BTC/USDT:USDT", side="sell", order_type="stop",
              amount=0.01, price=62000.0, status="open"),
        Order(id="o2", symbol="BTC/USDT:USDT", side="sell", order_type="take_profit",
              amount=0.01, price=68000.0, status="open"),
    ])
    deps.exchange.get_contract_size = AsyncMock(return_value=1.0)

    result = await get_position(deps)
    assert "Risk exposure:" in result
    assert "Notional value:" in result
    assert "Margin used:" in result
    # ATR(1h) suffix must be present. Exact multiple is (14.2% liq-dist) / (0.137% ATR%) ≈ 103× ;
    # don't hardcode the number — just verify the suffix structure exists so fixture-number changes don't break.
    assert "ATR(1h)" in result
    assert "× ATR(1h)" in result  # suffix format marker (Liquidation OR Exit orders line has it)
    assert "Exit orders:" in result
    assert "Stop loss:" in result
    assert "Take profit:" in result


@pytest.mark.asyncio
async def test_get_position_no_sl_tp_naked_warning(mocker):
    """Position without SL/TP: explicit 'not set' warnings."""
    from src.agent.tools_perception import get_position
    deps = MockDeps()
    deps.exchange.fetch_positions = AsyncMock(return_value=[Position(
        symbol="BTC/USDT:USDT", side="long", contracts=0.01, entry_price=64000.0,
        unrealized_pnl=10.0, leverage=3, liquidation_price=55000.0,
        created_at=datetime(2026, 4, 21, 10, 0, tzinfo=timezone.utc),
    )])
    deps.market_data.get_ticker = AsyncMock(return_value=Ticker(
        symbol="BTC/USDT:USDT", last=64100.0, bid=64099.5, ask=64100.5,
        high=65000.0, low=63000.0, base_volume=1000.0, timestamp=0,
    ))
    deps.exchange.fetch_balance = AsyncMock(return_value=Balance(total_usdt=10010.0, free_usdt=9796.67, used_usdt=213.33))
    deps.market_data.get_ohlcv_dataframe = AsyncMock(return_value=_make_ohlcv_df(50))
    deps.technical.compute_indicators = mocker.Mock(return_value={"atr_14": 88.0})
    deps.exchange.fetch_open_orders = AsyncMock(return_value=[])
    deps.exchange.get_contract_size = AsyncMock(return_value=1.0)

    result = await get_position(deps)
    assert "Stop loss: not set" in result
    assert "Take profit: not set" in result


@pytest.mark.asyncio
async def test_get_position_atr_unavailable_degrade(mocker):
    """ATR fetch fails: main sections still shown, ATR-multiple suffix omitted."""
    from src.agent.tools_perception import get_position
    deps = MockDeps()
    deps.exchange.fetch_positions = AsyncMock(return_value=[Position(
        symbol="BTC/USDT:USDT", side="long", contracts=0.01, entry_price=64000.0,
        unrealized_pnl=10.0, leverage=3, liquidation_price=55000.0,
        created_at=datetime(2026, 4, 21, 10, 0, tzinfo=timezone.utc),
    )])
    deps.market_data.get_ticker = AsyncMock(return_value=Ticker(
        symbol="BTC/USDT:USDT", last=64100.0, bid=64099.5, ask=64100.5,
        high=65000.0, low=63000.0, base_volume=1000.0, timestamp=0,
    ))
    deps.exchange.fetch_balance = AsyncMock(return_value=Balance(total_usdt=10010.0, free_usdt=9796.67, used_usdt=213.33))
    deps.market_data.get_ohlcv_dataframe = AsyncMock(side_effect=Exception("no OHLCV"))
    deps.exchange.fetch_open_orders = AsyncMock(return_value=[])
    deps.exchange.get_contract_size = AsyncMock(return_value=1.0)

    result = await get_position(deps)
    assert "Risk exposure:" in result
    assert "ATR(1h)" not in result  # suffix omitted on ATR failure
```

- [ ] **Sub-step 10.1.2: Run — fail on shape mismatch**

Expected: Missing Risk exposure / Exit orders strings.

### Step 10.2: Rewrite get_position

- [ ] **Sub-step 10.2.1: Replace existing get_position body**

Find existing `async def get_position(...)` in tools_perception.py (line ~112) and replace with:

```python
async def get_position(deps: TradingDeps, symbol: str | None = None) -> str:
    """Show current position with risk exposure and SL/TP distances.

    Args:
        symbol: Optional override of deps.symbol.

    Returns:
        str: Multi-section position view (position line + PnL + Duration + Risk exposure + Exit orders). See spec §2.4.

    Degradation: 'No open positions.' if empty. ATR(1h) unavailable → ATR-multiple suffixes omitted (other sections intact).
    """
    import asyncio
    symbol = symbol or deps.symbol

    # Phase 1: positions only — early return if empty
    positions = await deps.exchange.fetch_positions(symbol)
    if not positions:
        return "No open positions."

    p = positions[0]

    # Phase 2: gather remaining IO in parallel. OHLCV has per-call soft-fail (ATR suffix omission
    # is spec §2.4 three-state). Ticker / balance / orders / contract_size failures are hard —
    # wrap the whole gather in a try/except that degrades the enhanced sections, keeping the
    # original position+PnL+Duration lines intact.
    #
    # NOTE: spec §3.3 suggests `return_exceptions=True`. We use `False + outer try/except` instead
    # for these reasons: (1) simpler to reason about — any hard failure collapses to a single
    # degradation path rather than 5 per-IO isinstance checks; (2) Risk exposure and Exit orders
    # both need coherent ticker + balance + contract_size; partial success gives misleading
    # numbers (e.g. "Notional X USDT" without a valid ticker → stale). The spec's preference
    # is a recommendation, not a hard constraint; the audit flagged this as P3 (non-critical).
    async def _safe_ohlcv():
        try:
            return await deps.market_data.get_ohlcv_dataframe(symbol, "1h", limit=50)
        except Exception:
            logger.exception("get_position: 1h OHLCV fetch failed")
            return None

    try:
        ticker, balance, ohlcv_df, open_orders, contract_size = await asyncio.gather(
            deps.market_data.get_ticker(symbol),
            deps.exchange.fetch_balance(),
            _safe_ohlcv(),
            deps.exchange.fetch_open_orders(symbol),
            deps.exchange.get_contract_size(symbol),
            return_exceptions=False,
        )
    except Exception:
        logger.exception("get_position: one of ticker/balance/orders/contract_size failed")
        # Emit position+PnL+Duration only, add a degradation footer. This matches the spirit
        # of spec §2.4 (don't lose core info on enhancement failure).
        lines = ["Current Position:"]
        lines.append(f"  {p.side.upper()} {p.contracts} contracts @ {p.entry_price:.2f} | {p.leverage}x leverage")
        if deps.initial_balance > 0:
            pnl_pct = (p.unrealized_pnl / deps.initial_balance) * 100
            lines.append(f"  PnL: {p.unrealized_pnl:.2f} USDT ({pnl_pct:+.2f}% of initial capital)")
        else:
            lines.append(f"  PnL: {p.unrealized_pnl:.2f} USDT")
        lines.append("")
        lines.append("Risk exposure + Exit orders: temporarily unavailable")
        return "\n".join(lines)

    # ATR(1h) — may be None if OHLCV failed
    atr_1h = None
    if ohlcv_df is not None and not ohlcv_df.empty:
        indicators = deps.technical.compute_indicators(ohlcv_df)
        atr_1h = indicators.get("atr_14")
    current_price = ticker.last

    lines = ["Current Position:"]
    lines.append(f"  {p.side.upper()} {p.contracts} contracts @ {p.entry_price:.2f} | {p.leverage}x leverage")

    if deps.initial_balance > 0:
        pnl_pct = (p.unrealized_pnl / deps.initial_balance) * 100
        lines.append(f"  PnL: {p.unrealized_pnl:.2f} USDT ({pnl_pct:+.2f}% of initial capital)")
    else:
        lines.append(f"  PnL: {p.unrealized_pnl:.2f} USDT")

    # Duration (existing logic)
    if p.created_at is not None:
        from datetime import datetime, timezone
        now = datetime.now(timezone.utc)
        delta = now - p.created_at
        total_minutes = int(delta.total_seconds() / 60)
        if total_minutes < 60:
            dur_str = f"{total_minutes} min"
        elif total_minutes < 1440:
            dur_str = f"{total_minutes // 60}h {total_minutes % 60}m"
        else:
            dur_str = f"{total_minutes // 1440}d {(total_minutes % 1440) // 60}h"
        lines.append(f"  Duration: {dur_str}")
    else:
        lines.append("  Duration: N/A")

    # === Risk exposure ===
    notional = p.contracts * p.entry_price * contract_size
    equity = balance.total_usdt
    exp_pct = notional / equity * 100 if equity > 0 else 0.0
    margin_used = balance.used_usdt
    margin_pct = margin_used / equity * 100 if equity > 0 else 0.0
    atr_pct_1h = atr_1h / current_price * 100 if atr_1h is not None and current_price > 0 else None

    lines.append("")
    lines.append("Risk exposure:")
    lines.append(f"  Notional value: {notional:.2f} USDT ({exp_pct:.1f}% of equity {equity:.2f})")
    lines.append(f"  Margin used: {margin_used:.2f} USDT ({margin_pct:.1f}% of equity, from balance.used_usdt)")
    if p.liquidation_price is not None and current_price > 0:
        liq_dist_pct = abs(current_price - p.liquidation_price) / current_price * 100
        if atr_pct_1h is not None and atr_pct_1h > 0:
            atr_mult = liq_dist_pct / atr_pct_1h
            lines.append(f"  Liquidation: {p.liquidation_price:.2f} ({liq_dist_pct:.1f}% away = {atr_mult:.1f}× ATR(1h))")
        else:
            lines.append(f"  Liquidation: {p.liquidation_price:.2f} ({liq_dist_pct:.1f}% away)")

    # === Exit orders ===
    sl_orders = sorted([o for o in open_orders if o.order_type == "stop" and o.symbol == symbol], key=lambda o: o.price or 0)
    tp_orders = sorted([o for o in open_orders if o.order_type == "take_profit" and o.symbol == symbol], key=lambda o: o.price or 0)
    lines.append("")
    lines.append("Exit orders:")

    def _fmt_exit(o, kind: str) -> str:
        dist_entry_pct = (o.price - p.entry_price) / p.entry_price * 100 if o.price else 0.0
        dist_curr_pct = (o.price - current_price) / current_price * 100 if o.price and current_price > 0 else 0.0
        direction_entry = "above" if dist_entry_pct > 0 else "below"
        direction_curr = "above" if dist_curr_pct > 0 else "below"
        suffix = ""
        if atr_pct_1h is not None and atr_pct_1h > 0:
            atr_mult = abs(dist_curr_pct) / atr_pct_1h
            suffix = f" = {atr_mult:.1f}× ATR(1h)"
        return f"  {kind}: {o.price:.2f} ({abs(dist_entry_pct):.1f}% {direction_entry} entry, {abs(dist_curr_pct):.1f}% {direction_curr} current{suffix})  [{o.amount} contracts]"

    if sl_orders:
        for o in sl_orders:
            lines.append(_fmt_exit(o, "Stop loss"))
    else:
        lines.append("  Stop loss: not set")

    if tp_orders:
        for o in tp_orders:
            lines.append(_fmt_exit(o, "Take profit"))
    else:
        lines.append("  Take profit: not set")

    return "\n".join(lines)
```

- [ ] **Sub-step 10.2.2: Run tests — pass**

Run: `cd /Users/z/Z/TradeBot && uv run pytest tests/test_toolkit_iter2.py -k "get_position" -v`
Expected: 4 PASS.

### Step 10.3: Update test_tool_enhancement.py — _make_deps + new field assertions

- [ ] **Sub-step 10.3.1: Patch `_make_deps()` first (prevents regression across 3 existing tests)**

Enhanced `get_position` calls `deps.exchange.get_contract_size` + `deps.market_data.get_ohlcv_dataframe` via `asyncio.gather`. If these aren't mocked, AsyncMock auto-attributes return nested mocks that blow up in arithmetic (`p.contracts * entry_price * MagicMock()` → TypeError on `{:.2f}` formatting).

Run: `grep -n "def _make_deps" /Users/z/Z/TradeBot/tests/test_tool_enhancement.py`
Expected: line ~265.

Add **2 lines** inside `_make_deps()` body (before `return d`, anywhere after `d = MockDeps(...)`):

```python
    d.exchange.get_contract_size = AsyncMock(return_value=1.0)
    # Default: OHLCV fetch fails → _safe_ohlcv returns None → atr_1h stays None → ATR-multiple suffix omitted.
    # Tests that want to exercise the ATR path should override this per-test.
    d.market_data.get_ohlcv_dataframe = AsyncMock(side_effect=Exception("default: no OHLCV in _make_deps"))
```

This default keeps existing tests passing (they only assert `"away"`, which appears whether or not ATR suffix is present).

- [ ] **Sub-step 10.3.2: Find test_get_position_enhanced**

Run: `grep -n "def test_get_position_enhanced" /Users/z/Z/TradeBot/tests/test_tool_enhancement.py`
Expected: line ~481.

- [ ] **Sub-step 10.3.3: Augment main test (not replace)**

At line ~499 after the existing `"away" in result.lower()` assertion, append:

```python
    # New Iter 2 fields
    assert "Risk exposure:" in result
    assert "Notional" in result or "notional" in result.lower()
    assert "Exit orders:" in result
```

- [ ] **Sub-step 10.3.4: Run full test_tool_enhancement.py suite**

Run: `cd /Users/z/Z/TradeBot && uv run pytest tests/test_tool_enhancement.py -v 2>&1 | tail -10`
Expected: all pass (including the 3 new assertions + 2 other get_position tests that rely on _make_deps defaults).

### Step 10.4: Commit

```bash
git add src/agent/tools_perception.py tests/test_toolkit_iter2.py tests/test_tool_enhancement.py
git commit -m "$(cat <<'EOF'
feat(tools): enhance get_position with Risk exposure + Exit orders

Two-phase IO: fetch_positions first → early return on empty (no
wasted IO); with position, asyncio.gather the remaining 4 IOs
(ticker / balance / ohlcv / open_orders / contract_size). Adds
Risk exposure section (notional, margin used from balance.used_usdt,
liquidation in ATR(1h) multiples) and Exit orders section (SL/TP
distances from both entry and current, plus ATR multiples). ATR(1h)
failure → suffix omitted but core info retained. Explicit "not set"
for naked positions.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 11: Register Tools + Layer 1 Prompt + REGISTERED_TOOL_NAMES

**Files:**
- Modify: `src/agent/trader.py`
- Modify: `src/agent/persona.py`
- Modify: `tests/test_trader_agent.py`
- Modify: `tests/test_display_cycle.py` (non-blocker)

### Step 11.1: Add 3 @agent.tool wrappers in trader.py

- [ ] **Sub-step 11.1.1: Locate existing wrappers**

Run: `grep -n "@agent.tool" /Users/z/Z/TradeBot/src/agent/trader.py | tail -5`
Expected: Match list of existing `@agent.tool` decorators.

- [ ] **Sub-step 11.1.2: Add 3 new wrappers**

After the last perception `@agent.tool` (likely after `get_stablecoin_supply`), add:

```python
    @agent.tool
    async def get_order_book(ctx: RunContext[TradingDeps], depth: int = 20) -> str:
        """Return top-N order book depth with concentrated-level breakdown.

        Args:
            depth: Levels per side to fetch. Default 20.

        Returns:
            str: Multi-line fact-only text (best bid/ask + cumulative depth + bid share + concentrated levels).

        Degradation: "Order book ({symbol}): insufficient data (requested depth X, got Y)" if book is empty/short;
        "Order book ({symbol}): temporarily unavailable" on service failure.
        """
        from src.agent.tools_perception import get_order_book as _impl
        return await _impl(ctx.deps, depth=depth)

    @agent.tool
    async def get_recent_trades(ctx: RunContext[TradingDeps], window_seconds: int = 300) -> str:
        """Return taker-flow bias and rhythm over a recent time window via 5 time-buckets.

        Args:
            window_seconds: Observation window in seconds. Default 300 (5 min).

        Returns:
            str: 5-bucket breakdown + Total + trade count + avg size.

        Degradation: "Recent trades ({symbol}): no trades in last {window_seconds}s" if cold market;
        "Recent trades ({symbol}): temporarily unavailable" on service failure. Heavy windows
        may annotate Total with "partial coverage" footnote.
        """
        from src.agent.tools_perception import get_recent_trades as _impl
        return await _impl(ctx.deps, window_seconds=window_seconds)

    @agent.tool
    async def get_multi_timeframe_snapshot(ctx: RunContext[TradingDeps], tfs: list[str] | None = None) -> str:
        """Quick multi-timeframe scan: momentum | structure | volatility | range position.

        Args:
            tfs: List of CCXT timeframes. Default ["5m", "1h", "4h", "1d"]. 1w/1M are supported but non-default.

        Returns:
            str: 4-column row per TF + Columns header.

        Degradation: per-TF "insufficient data (need N candles, got M)" or "temporarily unavailable";
        overall "Multi-TF snapshot ({symbol}): temporarily unavailable" only if ALL TFs fail or ticker fetch fails.
        """
        from src.agent.tools_perception import get_multi_timeframe_snapshot as _impl
        return await _impl(ctx.deps, tfs=tfs)
```

### Step 11.2: Update REGISTERED_TOOL_NAMES

- [ ] **Sub-step 11.2.1: Find constant**

Run: `grep -n "REGISTERED_TOOL_NAMES" /Users/z/Z/TradeBot/src/agent/trader.py`
Expected: line ~319 start.

- [ ] **Sub-step 11.2.2: Append 3 new names**

Add `"get_order_book"`, `"get_recent_trades"`, `"get_multi_timeframe_snapshot"` at the end of the perception block (before execution tools), maintaining alphabetical / grouping order.

### Step 11.3: Update test_trader_agent.py hardcoded count

- [ ] **Sub-step 11.3.1: Fix assertion**

In `tests/test_trader_agent.py:84-85`, change:

```python
    assert len(REGISTERED_TOOL_NAMES) == 26, (
        f"Expected 26 tools (15+10+1), got {len(REGISTERED_TOOL_NAMES)}"
```

to:

```python
    assert len(REGISTERED_TOOL_NAMES) == 29, (
        f"Expected 29 tools (18+10+1), got {len(REGISTERED_TOOL_NAMES)}"
```

### Step 11.4: Add Layer 1 bullets to persona.py

- [ ] **Sub-step 11.4.1: Find anchor**

Run: `grep -n "Stablecoin supply" /Users/z/Z/TradeBot/src/agent/persona.py`
Expected: line ~44 (last bullet of Tool Usage Notes).

- [ ] **Sub-step 11.4.2: Append 4 bullets**

After the line ending with `"...sourced from on-chain data via DefiLlama."""` (end of the triple-quoted string), insert (before the closing `"""`):

```
- **Order book**: Use get_order_book for top-N depth with cumulative volume + bid/ask share + concentrated levels (size > 3× same-side median). Evaluate liquidity, slippage risk, or concentrated levels near current price.
- **Recent trades**: Use get_recent_trades to read taker-flow bias and rhythm over recent minutes (default 300s, 5 × 60s buckets). Total + trade count + avg size shown below buckets.
- **Multi-timeframe snapshot**: Use get_multi_timeframe_snapshot once per cycle to scan multi-TF alignment (default 5m/1h/4h/1d) before committing to a direction. 4 columns per TF: momentum / structure / volatility / range position.
- **Position risk context**: get_position now includes Risk exposure (notional / margin / liquidation in ATR(1h) multiples — 1h is the fixed baseline regardless of session trading style) and Exit orders section (SL/TP distances from both entry and current). Useful both when opening and during ongoing position management.
```

### Step 11.5: Update test_display_cycle.py mock (non-blocker)

- [ ] **Sub-step 11.5.1: Update mock content at line ~39**

Replace the existing mock string with an expanded version mirroring new format:

```python
    content = (
        "Current Position:\n"
        "  LONG 0.500 contracts @ 83100.00 | 3x leverage\n"
        "  PnL: 5.50 USDT (+1.32% of initial capital)\n"
        "  Duration: 2h 30m\n"
        "\n"
        "Risk exposure:\n"
        "  Notional value: 41550.00 USDT (4.2% of equity 100000.00)\n"
        "  Margin used: 13850.00 USDT (13.9% of equity, from balance.used_usdt)\n"
        "  Liquidation: 55000.00 (34.7% away = 5.8× ATR(1h))\n"
        "\n"
        "Exit orders:\n"
        "  Stop loss: not set\n"
        "  Take profit: not set"
    )
```

### Step 11.6: Run full test suite

- [ ] **Sub-step 11.6.1: Pytest full**

Run: `cd /Users/z/Z/TradeBot && uv run pytest 2>&1 | tail -5`
Expected: All pass, ~720-725 total.

### Step 11.7: Commit

```bash
git add src/agent/trader.py src/agent/persona.py tests/test_trader_agent.py tests/test_display_cycle.py
git commit -m "$(cat <<'EOF'
feat(agent): register 3 new tools + Layer 1 prompt + drift guard 29

Wires get_order_book / get_recent_trades / get_multi_timeframe_snapshot
as @agent.tool. Appends 4 Layer 1 bullets in persona.py (3 new tools
+ get_position enhancement note). REGISTERED_TOOL_NAMES = 29 and
test_trader_agent.py hardcoded assertion synchronized (was 26).
test_display_cycle.py mock content updated to new get_position format
for representational accuracy.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 12: Fact-only Regression

**Files:**
- Create: `tests/test_fact_only_wordlist.py`

### Step 12.1: Write fact-only scan tests

- [ ] **Sub-step 12.1.1: Create test file**

```python
"""Fact-only regression: ensure new/enhanced tools don't emit banned subjective words (spec §3.5)."""
from __future__ import annotations
import re
import pytest
from unittest.mock import AsyncMock
from dataclasses import dataclass, field
from src.integrations.exchange.base import OrderBook, OrderBookLevel, Trade, Ticker, Balance, Position, Order

FACT_ONLY_BANNED_WORDS_RE = [
    r"\bwall\b", r"\baggressive\b", r"\bbullish\b", r"\bbearish\b",
    r"\boverbought\b", r"\boversold\b", r"\bdry powder\b",
    r"\brisk[- ]on\b", r"\brisk[- ]off\b",
    r"\bbull market\b", r"\bbear market\b",
    r"\bpressure\b", r"\brally\b", r"\bplunge\b",
    r"\bsurge\b", r"\bcrash\b", r"\bpump\b", r"\bdump\b",
]
FACT_ONLY_BANNED_PHRASES_RE = [
    r"\bstrong support\b", r"\bstrong resistance\b",
    r"\bweak support\b", r"\bweak resistance\b",
    r"\btrend\s+(up|down|flat)\b",
]


def _scan(output: str) -> list[str]:
    """Return list of banned pattern hits after stripping Columns: header lines."""
    # Strip header lines
    lines = [l for l in output.splitlines() if not l.startswith("Columns:")]
    scrubbed = "\n".join(lines)
    hits = []
    for pat in FACT_ONLY_BANNED_WORDS_RE + FACT_ONLY_BANNED_PHRASES_RE:
        if re.search(pat, scrubbed, re.IGNORECASE):
            hits.append(pat)
    return hits


@dataclass
class MockDeps:
    symbol: str = "BTC/USDT:USDT"
    initial_balance: float = 10000.0
    exchange: AsyncMock = field(default_factory=AsyncMock)
    market_data: AsyncMock = field(default_factory=AsyncMock)
    technical: AsyncMock = field(default_factory=AsyncMock)


@pytest.mark.asyncio
async def test_order_book_fact_only_4_scenarios():
    """Typical / bid-heavy / asks-only / service-failure all fact-only."""
    from src.agent.tools_perception import get_order_book
    outputs = []
    deps = MockDeps()

    # Scenario 1: typical
    deps.market_data.get_order_book = AsyncMock(return_value=OrderBook(
        symbol="BTC/USDT:USDT",
        bids=[OrderBookLevel(100 - i * 0.1, 1.0) for i in range(20)],
        asks=[OrderBookLevel(101 + i * 0.1, 1.0) for i in range(20)],
        timestamp=0,
    ))
    outputs.append(await get_order_book(deps))

    # Scenario 2: bid-heavy (extreme)
    deps.market_data.get_order_book = AsyncMock(return_value=OrderBook(
        symbol="BTC/USDT:USDT",
        bids=[OrderBookLevel(100 - i * 0.1, 5.0) for i in range(20)],
        asks=[OrderBookLevel(101 + i * 0.1, 0.1) for i in range(20)],
        timestamp=0,
    ))
    outputs.append(await get_order_book(deps))

    # Scenario 3: asks only
    deps.market_data.get_order_book = AsyncMock(return_value=OrderBook(
        symbol="BTC/USDT:USDT", bids=[], asks=[], timestamp=0,
    ))
    outputs.append(await get_order_book(deps))

    # Scenario 4: failure
    deps.market_data.get_order_book = AsyncMock(side_effect=Exception("down"))
    outputs.append(await get_order_book(deps))

    combined = "\n".join(outputs)
    hits = _scan(combined)
    assert not hits, f"Banned words in get_order_book outputs: {hits}\n{combined}"


@pytest.mark.asyncio
async def test_recent_trades_fact_only_4_scenarios():
    from src.agent.tools_perception import get_recent_trades
    import time
    now_ms = int(time.time() * 1000)
    deps = MockDeps()
    outputs = []

    # S1: typical
    deps.market_data.get_recent_trades = AsyncMock(return_value=[
        Trade(timestamp=now_ms - i * 3000, side="buy" if i % 2 == 0 else "sell",
              price=64000.0, amount=0.01, trade_id=None) for i in range(50)
    ])
    outputs.append(await get_recent_trades(deps))

    # S2: all buy
    deps.market_data.get_recent_trades = AsyncMock(return_value=[
        Trade(timestamp=now_ms - i * 3000, side="buy", price=64000.0, amount=0.01, trade_id=None)
        for i in range(50)
    ])
    outputs.append(await get_recent_trades(deps))

    # S3: cold
    deps.market_data.get_recent_trades = AsyncMock(return_value=[])
    outputs.append(await get_recent_trades(deps))

    # S4: fail
    deps.market_data.get_recent_trades = AsyncMock(side_effect=Exception("x"))
    outputs.append(await get_recent_trades(deps))

    hits = _scan("\n".join(outputs))
    assert not hits, f"Banned words in get_recent_trades outputs: {hits}"


@pytest.mark.asyncio
async def test_multi_tf_snapshot_fact_only(mocker):
    """Spec §5.3 clause: 4 scenarios — typical / MA entangled (at) / per-TF insufficient / all-fail."""
    from src.agent.tools_perception import get_multi_timeframe_snapshot
    import pandas as pd
    deps = MockDeps()
    outputs = []

    # Scenario 1: typical (above/below MA)
    df = pd.DataFrame([{"timestamp": 0, "open": 64000, "high": 64100, "low": 63900,
                        "close": 64050, "volume": 100.0} for _ in range(250)])
    deps.market_data.get_ohlcv_dataframe = AsyncMock(return_value=df)
    deps.technical.compute_indicators = mocker.Mock(return_value={"atr_14": 85.0})
    deps.exchange.fetch_ticker = AsyncMock(return_value=Ticker(
        symbol="BTC/USDT:USDT", last=64050.0, bid=64049.5, ask=64050.5,
        high=65000.0, low=63000.0, base_volume=1000.0, timestamp=0,
    ))
    outputs.append(await get_multi_timeframe_snapshot(deps))

    # Scenario 2: MA entangled — flat close → all rolling MAs equal → diff_pct<0.1 → "MA at MA"
    flat_df = pd.DataFrame([{"timestamp": 0, "open": 64000, "high": 64001, "low": 63999,
                             "close": 64000, "volume": 100.0} for _ in range(250)])
    deps.market_data.get_ohlcv_dataframe = AsyncMock(return_value=flat_df)
    outputs.append(await get_multi_timeframe_snapshot(deps, tfs=["1h"]))

    # Scenario 3: per-TF insufficient — 5m returns only 30 candles (< 50 needed)
    def _partial_side(sym, tf, limit):
        if tf == "5m":
            return pd.DataFrame([{"timestamp": 0, "open": 64000, "high": 64100, "low": 63900,
                                  "close": 64050, "volume": 100.0} for _ in range(30)])
        return df
    deps.market_data.get_ohlcv_dataframe = AsyncMock(side_effect=_partial_side)
    outputs.append(await get_multi_timeframe_snapshot(deps))

    # Scenario 4: all TF fail
    deps.market_data.get_ohlcv_dataframe = AsyncMock(side_effect=Exception("x"))
    outputs.append(await get_multi_timeframe_snapshot(deps))

    hits = _scan("\n".join(outputs))
    assert not hits, f"Banned words in get_multi_timeframe_snapshot outputs: {hits}"


@pytest.mark.asyncio
async def test_get_position_fact_only(mocker):
    from src.agent.tools_perception import get_position
    import pandas as pd
    from datetime import datetime, timezone
    deps = MockDeps()
    outputs = []

    # Typical with SL/TP
    df = pd.DataFrame([{"timestamp": 0, "open": 64000, "high": 64100, "low": 63900,
                        "close": 64050, "volume": 100.0} for _ in range(50)])
    deps.exchange.fetch_positions = AsyncMock(return_value=[Position(
        symbol="BTC/USDT:USDT", side="long", contracts=0.01, entry_price=64000.0,
        unrealized_pnl=10.0, leverage=3, liquidation_price=55000.0,
        created_at=datetime(2026, 4, 21, 10, 0, tzinfo=timezone.utc),
    )])
    deps.market_data.get_ticker = AsyncMock(return_value=Ticker(
        symbol="BTC/USDT:USDT", last=64100.0, bid=64099.5, ask=64100.5,
        high=65000.0, low=63000.0, base_volume=1000.0, timestamp=0,
    ))
    deps.exchange.fetch_balance = AsyncMock(return_value=Balance(total_usdt=10010, free_usdt=9796, used_usdt=213))
    deps.market_data.get_ohlcv_dataframe = AsyncMock(return_value=df)
    deps.technical.compute_indicators = mocker.Mock(return_value={"atr_14": 88.0})
    deps.exchange.fetch_open_orders = AsyncMock(return_value=[
        Order(id="o1", symbol="BTC/USDT:USDT", side="sell", order_type="stop",
              amount=0.01, price=62000.0, status="open"),
    ])
    deps.exchange.get_contract_size = AsyncMock(return_value=1.0)
    outputs.append(await get_position(deps))

    # No SL/TP
    deps.exchange.fetch_open_orders = AsyncMock(return_value=[])
    outputs.append(await get_position(deps))

    hits = _scan("\n".join(outputs))
    assert not hits, f"Banned words in get_position outputs: {hits}"
```

- [ ] **Sub-step 12.1.2: Run — all pass (if any hit, investigate)**

Run: `cd /Users/z/Z/TradeBot && uv run pytest tests/test_fact_only_wordlist.py -v`
Expected: 4 PASS.

### Step 12.2: Commit

```bash
git add tests/test_fact_only_wordlist.py
git commit -m "$(cat <<'EOF'
test(toolkit): fact-only regression for 3 new tools + get_position

Regex-based scan of banned subjective words (spec §3.5). Each tool
tested across 2-4 scenarios (typical / extreme / edge / failure) to
cover different format branches. Strips 'Columns:' header lines
(Momentum trade jargon is allowed in headers only). Extends PR #18's
N5 fact-only cleanup to new surface.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 13: Final Verification

**Files:** None (verification only).

### Step 13.1: Full test suite

- [ ] **Sub-step 13.1.1: Run all tests**

Run: `cd /Users/z/Z/TradeBot && uv run pytest 2>&1 | tail -5`
Expected: All pass. Count target: 681 + ~45 = ~725.

### Step 13.2: Acceptance criteria checklist

Walk through spec §6 acceptance criteria one at a time:

- [ ] BaseExchange has 3 new abstract methods (grep confirms)
- [ ] OKX & Sim both implement all 3 (pytest test_exchange_order_book passes)
- [ ] OKXExchange.start() preloads markets (grep confirms)
- [ ] `_parse_order` untouched (diff check: `git log -p src/integrations/exchange/okx.py | grep _parse_order`)
- [ ] 3 new `@agent.tool` registered (grep confirms)
- [ ] REGISTERED_TOOL_NAMES = 29 + test_trader_agent.py drift guard green
- [ ] `get_position` has Risk exposure + Exit orders sections (test assertions)
- [ ] `get_position` Notional numbers consistent between OKX & Sim (spec §4.2 consistency)
- [ ] Sim SL/TP works; OKX "Stop loss: not set" is known limitation (documented in spec §2.4)
- [ ] ToolCallRecorder wraps 3 new tools automatically (verify integration test if available)
- [ ] Three-state contract in all new tools (test coverage)
- [ ] Fact-only regression 3-4 scenarios per tool pass (Task 12)
- [ ] Layer 1 prompt +4 bullets; test_persona.py green
- [ ] Total test count ~725

### Step 13.3: Branch & PR

- [ ] **Sub-step 13.3.1: Review git log**

Run: `cd /Users/z/Z/TradeBot && git log --oneline main..HEAD`
Expected: 12-14 commits (Task 1-12) + spec commit.

- [ ] **Sub-step 13.3.2: Prepare PR body**

At user's request, open PR with title `feat(toolkit): Iter 2 — order_book / recent_trades / multi_tf_snapshot + get_position enhancement (#TBD)` and body summarizing §6 acceptance.

**Do NOT push / create PR without explicit user approval.**

---

## Appendix: Known Limitations Carried to Iter 2b

- OKX `_parse_order` algo normalization: untouched this round. Real OKX account SL/TP orders show as `order_type="conditional"` (or equivalent raw OKX type), not `"stop"`/`"take_profit"`, so `get_position`'s Exit orders section will render `"Stop loss: not set"` on a real OKX account even when SL is set. This is **a known and accepted limitation** for Iter 2. Details and unblock plan in memory `project_iter2b_okx_algo_normalization`.
- `sandboxMode` configuration: no `OKX_SANDBOX` env var support yet — demo-account testing requires ccxt manual wiring. Also Iter 2b.
- `get_open_orders` OCO merged display: part of Iter 2b.
