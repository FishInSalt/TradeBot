# Agent Tool Enhancement Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Enhance Agent tool library — richer perception tools, new tools, bug fixes — to improve trading decision quality.

**Architecture:** Enhance existing tool functions in-place (tools_perception.py, tools_execution.py), expand MetricsService as shared statistics engine, fix indicator bugs in technical.py, consolidate alert methods into BaseExchange. No workflow changes, no new modules.

**Tech Stack:** Python 3.12, pandas/pandas_ta, SQLAlchemy async, pydantic-ai, pytest

**Spec:** `docs/superpowers/specs/2026-04-14-tool-enhancement-design.md`

---

## File Structure

| File | Change Type | Responsibility |
|------|------------|----------------|
| `src/integrations/exchange/base.py` | Modify | Position.created_at; consolidate alert methods (set_alert_service, update_alert_params, get_alert_params, get_price_level_alerts) |
| `src/integrations/exchange/simulated.py` | Modify | Remove alert overrides; fill created_at in fetch_positions |
| `src/integrations/exchange/okx.py` | Modify | Remove alert overrides |
| `src/services/price_alert.py` | Modify | Add get_params() method |
| `src/storage/models.py` | Modify | TradeAction add fee column |
| `src/cli/session_manager.py` | Modify | Add _migrate_trade_actions_table |
| `src/services/metrics.py` | Rewrite | Expand PerformanceMetrics fields; inject engine+session_id in __init__; enhance compute() |
| `src/services/technical.py` | Rewrite | Fix BB/MACD column bugs; add ATR + volume_ratio; rewrite format_for_llm with annotations |
| `src/integrations/market_data.py` | Modify | limit param passthrough |
| `src/agent/trader.py` | Modify | TradingDeps new fields; update tool signatures + docstrings; register 3 new tools |
| `src/agent/tools_perception.py` | Rewrite | Enhance 5 existing tools; add 2 new tools (get_active_alerts, get_performance) |
| `src/agent/tools_execution.py` | Modify | SL/TP distance %; cancel_order; set_price_alert disabled check |
| `src/cli/app.py` | Modify | Wire MetricsService + initial_balance into deps; _record_action_from_fill writes fee |

---

### Task 1: Foundation — BaseExchange & PriceAlertService

**Files:**
- Modify: `src/integrations/exchange/base.py:50-58` (Position), `src/integrations/exchange/base.py:61-149` (BaseExchange)
- Modify: `src/services/price_alert.py:74-80` (add get_params)
- Test: `tests/test_tool_enhancement.py` (new)

- [ ] **Step 1: Create test file with foundation tests**

```python
# tests/test_tool_enhancement.py
"""Tests for tool enhancement (spec: 2026-04-14-tool-enhancement-design)."""
import pytest
from datetime import datetime, timezone
from src.integrations.exchange.base import Ticker


# --- Task 1: Foundation ---

def test_position_created_at_default():
    from src.integrations.exchange.base import Position
    p = Position("BTC/USDT:USDT", "long", 0.01, 65000.0, 10.0, 3, 55000.0)
    assert p.created_at is None  # default None


def test_position_created_at_set():
    from src.integrations.exchange.base import Position
    ts = datetime(2026, 4, 14, tzinfo=timezone.utc)
    p = Position("BTC/USDT:USDT", "long", 0.01, 65000.0, 10.0, 3, 55000.0, created_at=ts)
    assert p.created_at == ts


def test_price_alert_service_get_params():
    from src.services.price_alert import PriceAlertService
    svc = PriceAlertService("BTC/USDT:USDT", window_minutes=30, threshold_pct=3.0)
    assert svc.get_params() == (3.0, 30)


def test_price_alert_service_get_params_after_update():
    from src.services.price_alert import PriceAlertService
    svc = PriceAlertService("BTC/USDT:USDT", window_minutes=30, threshold_pct=3.0)
    svc.update_params(5.0, 60)
    assert svc.get_params() == (5.0, 60)


def test_base_exchange_alert_consolidation():
    """BaseExchange stores alert_service and delegates to it."""
    from unittest.mock import MagicMock
    from src.integrations.exchange.base import BaseExchange

    # Create a concrete subclass for testing
    class _TestExchange(BaseExchange):
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

    ex = _TestExchange()

    # No alert service → get_alert_params returns None
    assert ex.get_alert_params() is None

    # Set alert service
    mock_svc = MagicMock()
    mock_svc.get_params.return_value = (5.0, 60)
    ex.set_alert_service(mock_svc)

    # get_alert_params delegates
    assert ex.get_alert_params() == (5.0, 60)

    # update_alert_params delegates
    ex.update_alert_params(3.0, 30)
    mock_svc.update_params.assert_called_once_with(3.0, 30)


def test_base_exchange_get_price_level_alerts():
    from src.integrations.exchange.base import BaseExchange

    class _TestExchange(BaseExchange):
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

    ex = _TestExchange()
    assert ex.get_price_level_alerts() == []

    ex.add_price_level_alert(75000.0, "above", "BTC/USDT:USDT", "resistance")
    alerts = ex.get_price_level_alerts()
    assert len(alerts) == 1
    assert alerts[0]["price"] == 75000.0

    # Verify it's a copy (mutating returned list doesn't affect internal state)
    alerts.pop()
    assert len(ex.get_price_level_alerts()) == 1
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd /Users/z/Z/TradeBot && python -m pytest tests/test_tool_enhancement.py -x -v 2>&1 | head -40`
Expected: FAIL — Position has no `created_at` kwarg, PriceAlertService has no `get_params`, BaseExchange has no `get_alert_params`/`get_price_level_alerts`

- [ ] **Step 3: Add Position.created_at field**

In `src/integrations/exchange/base.py`, add to Position dataclass:

```python
@dataclass
class Position:
    symbol: str
    side: str
    contracts: float
    entry_price: float
    unrealized_pnl: float
    leverage: int
    liquidation_price: float | None
    created_at: datetime | None = None
```

Add `datetime` import at top:

```python
from __future__ import annotations
import uuid
from abc import ABC, abstractmethod
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import datetime
from typing import Any
```

- [ ] **Step 4: Add PriceAlertService.get_params()**

In `src/services/price_alert.py`, add after `update_params`:

```python
    def get_params(self) -> tuple[float, int]:
        """Return current (threshold_pct, window_minutes)."""
        return (self._threshold_pct, self._window_minutes)
```

- [ ] **Step 5: Consolidate alert methods in BaseExchange**

In `src/integrations/exchange/base.py`, replace the `__init__`, `set_alert_service`, and `update_alert_params` methods and add new methods:

Replace `BaseExchange.__init__`:
```python
class BaseExchange(ABC):
    def __init__(self):
        self._price_level_alerts: list[dict] = []
        self._latest_price: float | None = None
        self._alert_service: Any | None = None
```

Replace `set_alert_service` (was empty pass):
```python
    def set_alert_service(self, service: Any) -> None:
        """Inject PriceAlertService instance."""
        self._alert_service = service
```

Replace `update_alert_params` (was empty pass):
```python
    def update_alert_params(self, threshold_pct: float, window_minutes: int) -> None:
        """Update price alert parameters. Delegates to alert service if set."""
        if self._alert_service:
            self._alert_service.update_params(threshold_pct, window_minutes)
```

Add new methods (after `update_alert_params`):
```python
    def get_alert_params(self) -> tuple[float, int] | None:
        """Return (threshold_pct, window_minutes) or None if alerts disabled."""
        if self._alert_service is not None:
            return self._alert_service.get_params()
        return None

    def get_price_level_alerts(self) -> list[dict]:
        """Return a copy of active price level alerts."""
        return list(self._price_level_alerts)
```

- [ ] **Step 6: Run tests to verify they pass**

Run: `cd /Users/z/Z/TradeBot && python -m pytest tests/test_tool_enhancement.py -x -v 2>&1 | tail -20`
Expected: All Task 1 tests PASS

- [ ] **Step 7: Commit**

```bash
git add tests/test_tool_enhancement.py src/integrations/exchange/base.py src/services/price_alert.py
git commit -m "feat: foundation — Position.created_at, alert consolidation, get_params"
```

---

### Task 2: Exchange Subclass Cleanup

**Files:**
- Modify: `src/integrations/exchange/simulated.py:69-72,138-151,769-774`
- Modify: `src/integrations/exchange/okx.py:91-115`
- Test: `tests/test_tool_enhancement.py` (append)

- [ ] **Step 1: Add tests for SimulatedExchange created_at and alert inheritance**

Append to `tests/test_tool_enhancement.py`:

```python
# --- Task 2: Exchange subclass cleanup ---

async def test_simulated_fetch_positions_has_created_at(tmp_path):
    """SimulatedExchange.fetch_positions fills Position.created_at."""
    from src.integrations.exchange.simulated import SimulatedExchange
    from src.config import ExchangeConfig
    from src.storage.database import init_db

    engine = await init_db(f"sqlite+aiosqlite:///{tmp_path}/t2.db")
    config = ExchangeConfig(name="simulated", fee_rate=0.0005, precision={"BTC/USDT:USDT": 3})
    ex = SimulatedExchange(config=config, db_engine=engine, session_id="t2", symbol="BTC/USDT:USDT")
    # Manually set up state for test (bypass start())
    ex._free_usdt = 10000.0

    from src.integrations.exchange.simulated import _Position
    ex._positions["BTC/USDT:USDT"] = _Position(
        side="long", contracts=0.01, entry_price=65000.0, leverage=3,
    )
    ex._latest_ticker = Ticker("BTC/USDT:USDT", 65500.0, 65499.0, 65501.0, 66000.0, 64000.0, 100.0, 1000)

    positions = await ex.fetch_positions("BTC/USDT:USDT")
    assert len(positions) == 1
    assert positions[0].created_at is not None
    assert isinstance(positions[0].created_at, datetime)
    await engine.dispose()


def test_simulated_exchange_inherits_alert_methods():
    """SimulatedExchange should NOT override set_alert_service/update_alert_params."""
    from src.integrations.exchange.simulated import SimulatedExchange
    from src.integrations.exchange.base import BaseExchange
    # Verify the methods are inherited, not overridden
    assert SimulatedExchange.set_alert_service is BaseExchange.set_alert_service
    assert SimulatedExchange.update_alert_params is BaseExchange.update_alert_params


def test_okx_exchange_inherits_alert_methods():
    """OKXExchange should NOT override set_alert_service/update_alert_params."""
    from src.integrations.exchange.okx import OKXExchange
    from src.integrations.exchange.base import BaseExchange
    assert OKXExchange.set_alert_service is BaseExchange.set_alert_service
    assert OKXExchange.update_alert_params is BaseExchange.update_alert_params
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd /Users/z/Z/TradeBot && python -m pytest tests/test_tool_enhancement.py::test_simulated_fetch_positions_has_created_at tests/test_tool_enhancement.py::test_simulated_exchange_inherits_alert_methods tests/test_tool_enhancement.py::test_okx_exchange_inherits_alert_methods -x -v 2>&1 | tail -20`
Expected: FAIL — fetch_positions doesn't pass created_at; subclasses still override alert methods

- [ ] **Step 3: Update SimulatedExchange — remove alert overrides, fill created_at**

In `src/integrations/exchange/simulated.py`:

**Remove** from `__init__` (line ~72):
```python
        self._alert_service: Any | None = None
```

**Remove** these four methods (lines ~769-774):
```python
    def set_alert_service(self, service: Any) -> None:
        self._alert_service = service

    def update_alert_params(self, threshold_pct: float, window_minutes: int) -> None:
        if self._alert_service:
            self._alert_service.update_params(threshold_pct, window_minutes)
```

**Update** `fetch_positions` to pass `created_at`:

```python
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
            created_at=pos.created_at,
        )]
```

- [ ] **Step 4: Update OKXExchange — remove alert overrides**

In `src/integrations/exchange/okx.py`:

**Remove** from `__init__` (line ~93):
```python
        self._alert_service: Any | None = None
```

**Remove** these four methods (lines ~110-115):
```python
    def set_alert_service(self, service: Any) -> None:
        self._alert_service = service

    def update_alert_params(self, threshold_pct: float, window_minutes: int) -> None:
        if self._alert_service:
            self._alert_service.update_params(threshold_pct, window_minutes)
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `cd /Users/z/Z/TradeBot && python -m pytest tests/test_tool_enhancement.py -x -v 2>&1 | tail -20`
Expected: All tests PASS

- [ ] **Step 6: Run full test suite to check for regressions**

Run: `cd /Users/z/Z/TradeBot && python -m pytest --tb=short 2>&1 | tail -20`
Expected: All existing tests PASS (SimExchange `_alert_service` now lives in BaseExchange.__init__)

- [ ] **Step 7: Commit**

```bash
git add src/integrations/exchange/simulated.py src/integrations/exchange/okx.py tests/test_tool_enhancement.py
git commit -m "refactor: remove alert overrides from exchange subclasses, fill Position.created_at"
```

---

### Task 3: DB Schema — TradeAction.fee Column

**Files:**
- Modify: `src/storage/models.py:47-62`
- Modify: `src/cli/session_manager.py:21-37`
- Modify: `src/cli/app.py:64-78`
- Test: `tests/test_tool_enhancement.py` (append)

- [ ] **Step 1: Add tests**

Append to `tests/test_tool_enhancement.py`:

```python
# --- Task 3: TradeAction.fee column ---

async def test_trade_action_fee_column(tmp_path):
    """TradeAction has optional fee column."""
    from src.storage.database import init_db, get_session
    from src.storage.models import Session, TradeAction

    engine = await init_db(f"sqlite+aiosqlite:///{tmp_path}/t3.db")
    async with get_session(engine) as session:
        session.add(Session(id="t3", name="test-fee", initial_balance=100.0))
        session.add(TradeAction(
            session_id="t3", action="order_filled", symbol="BTC/USDT:USDT",
            pnl=10.0, fee=0.05,
        ))
        await session.commit()

    async with get_session(engine) as session:
        from sqlalchemy import select
        result = await session.execute(select(TradeAction).where(TradeAction.session_id == "t3"))
        action = result.scalar_one()
        assert action.fee == pytest.approx(0.05)
    await engine.dispose()


async def test_trade_action_fee_nullable(tmp_path):
    """TradeAction.fee defaults to None."""
    from src.storage.database import init_db, get_session
    from src.storage.models import Session, TradeAction

    engine = await init_db(f"sqlite+aiosqlite:///{tmp_path}/t3b.db")
    async with get_session(engine) as session:
        session.add(Session(id="t3b", name="test-fee-null", initial_balance=100.0))
        session.add(TradeAction(
            session_id="t3b", action="open_position", symbol="BTC/USDT:USDT",
        ))
        await session.commit()

    async with get_session(engine) as session:
        from sqlalchemy import select
        result = await session.execute(select(TradeAction).where(TradeAction.session_id == "t3b"))
        action = result.scalar_one()
        assert action.fee is None
    await engine.dispose()


async def test_migrate_trade_actions_table(tmp_path):
    """Migration adds fee column to existing trade_actions table."""
    from sqlalchemy import text
    from src.storage.database import init_db, get_session
    from src.cli.session_manager import _migrate_trade_actions_table

    engine = await init_db(f"sqlite+aiosqlite:///{tmp_path}/t3c.db")
    # Verify fee column exists (init_db creates it from model)
    async with get_session(engine) as session:
        result = await session.execute(text("PRAGMA table_info(trade_actions)"))
        columns = {row[1] for row in result}
        assert "fee" in columns

    # Running migration again should be idempotent
    async with engine.begin() as conn:
        await _migrate_trade_actions_table(conn)
    await engine.dispose()
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd /Users/z/Z/TradeBot && python -m pytest tests/test_tool_enhancement.py::test_trade_action_fee_column tests/test_tool_enhancement.py::test_trade_action_fee_nullable tests/test_tool_enhancement.py::test_migrate_trade_actions_table -x -v 2>&1 | tail -20`
Expected: FAIL — TradeAction has no `fee` field, `_migrate_trade_actions_table` doesn't exist

- [ ] **Step 3: Add fee column to TradeAction**

In `src/storage/models.py`, add after line 61 (`reasoning` field):

```python
    fee: Mapped[float | None] = mapped_column(Float, nullable=True)
```

The full TradeAction becomes:
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
    fee: Mapped[float | None] = mapped_column(Float, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)
```

- [ ] **Step 4: Add migration function**

In `src/cli/session_manager.py`, add after `_migrate_session_table`:

```python
async def _migrate_trade_actions_table(conn) -> None:
    """Add fee column to trade_actions table. Idempotent."""
    result = await conn.execute(text("PRAGMA table_info(trade_actions)"))
    existing = {row[1] for row in result}
    if "fee" not in existing:
        await conn.execute(text("ALTER TABLE trade_actions ADD COLUMN fee REAL"))
```

- [ ] **Step 5: Wire migration into select_or_create_session**

In `src/cli/session_manager.py`, find the `select_or_create_session` function. Inside the `async with engine.begin() as conn:` block that calls `_migrate_session_table`, add a call to `_migrate_trade_actions_table`:

```python
    async with engine.begin() as conn:
        await _migrate_session_table(conn)
        await _migrate_trade_actions_table(conn)
        fixed = await _fix_residual_active(conn)
```

- [ ] **Step 6: Update _record_action_from_fill to write fee**

In `src/cli/app.py`, update `_record_action_from_fill`:

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
            price=event.fill_price,
            pnl=event.pnl,
            fee=event.fee,
            reasoning=f"(exchange: {event.trigger_reason} order filled @ {event.fill_price:.2f})",
        ))
        await session.commit()
```

- [ ] **Step 7: Run tests**

Run: `cd /Users/z/Z/TradeBot && python -m pytest tests/test_tool_enhancement.py -x -v 2>&1 | tail -20`
Expected: All tests PASS

- [ ] **Step 8: Commit**

```bash
git add src/storage/models.py src/cli/session_manager.py src/cli/app.py tests/test_tool_enhancement.py
git commit -m "feat: TradeAction.fee column + migration + record fee from fills"
```

---

### Task 4: MetricsService Expansion

**Files:**
- Rewrite: `src/services/metrics.py`
- Test: `tests/test_metrics.py` (rewrite)

- [ ] **Step 1: Rewrite test_metrics.py with new fields**

```python
# tests/test_metrics.py
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


async def _add_fill(engine, pnl, trigger_reason="market", fee=0.5):
    async with get_session(engine) as session:
        session.add(TradeAction(
            session_id="test-session", action="order_filled",
            order_id=f"o-{pnl}", symbol="BTC/USDT:USDT", side="long",
            trigger_reason=trigger_reason, pnl=pnl, fee=fee,
            reasoning=f"(exchange: {trigger_reason} filled)",
        ))
        await session.commit()


async def _add_open_fill(engine, fee=0.5):
    """Add an open-position fill (pnl=None, has fee)."""
    async with get_session(engine) as session:
        session.add(TradeAction(
            session_id="test-session", action="order_filled",
            order_id="o-open", symbol="BTC/USDT:USDT", side="long",
            trigger_reason="market", pnl=None, fee=fee,
            reasoning="(exchange: market filled)",
        ))
        await session.commit()


async def test_compute_metrics(metrics_db):
    from src.services.metrics import MetricsService
    await _add_fill(metrics_db, 30.0, fee=0.5)
    await _add_fill(metrics_db, -15.0, fee=0.3)
    await _add_fill(metrics_db, 180.0, fee=0.8)

    service = MetricsService(engine=metrics_db, session_id="test-session", initial_balance=10000.0)
    metrics = await service.compute()
    assert metrics.total_trades == 3
    assert metrics.win_rate == pytest.approx(2 / 3, abs=0.01)
    assert metrics.total_pnl == pytest.approx(195.0)
    assert metrics.profit_factor > 1.0
    assert metrics.avg_win == pytest.approx(105.0)  # (30+180)/2
    assert metrics.avg_loss == pytest.approx(-15.0)
    assert metrics.best_trade == pytest.approx(180.0)
    assert metrics.worst_trade == pytest.approx(-15.0)
    assert metrics.total_fees == pytest.approx(1.6)  # 0.5+0.3+0.8


async def test_compute_metrics_empty(metrics_db):
    from src.services.metrics import MetricsService
    service = MetricsService(engine=metrics_db, session_id="test-session", initial_balance=10000.0)
    metrics = await service.compute()
    assert metrics.total_trades == 0
    assert metrics.win_rate == 0.0
    assert metrics.total_fees == 0.0
    assert metrics.avg_win == 0.0
    assert metrics.avg_loss == 0.0
    assert metrics.recent_summary == ""


async def test_compute_metrics_with_position(metrics_db):
    from src.services.metrics import MetricsService
    service = MetricsService(engine=metrics_db, session_id="test-session", initial_balance=10000.0)
    metrics = await service.compute(current_position="long 0.001")
    assert metrics.current_position == "long 0.001"


async def test_compute_metrics_recent_summary(metrics_db):
    from src.services.metrics import MetricsService
    await _add_fill(metrics_db, 30.0)
    await _add_fill(metrics_db, -10.0)
    await _add_fill(metrics_db, 50.0)
    await _add_fill(metrics_db, 20.0)

    service = MetricsService(engine=metrics_db, session_id="test-session", initial_balance=10000.0)
    metrics = await service.compute()
    assert "3W 1L" in metrics.recent_summary
    assert "last 4" in metrics.recent_summary


async def test_compute_metrics_total_fees_includes_opens(metrics_db):
    """total_fees includes open fills (pnl=None) that have fee."""
    from src.services.metrics import MetricsService
    await _add_open_fill(metrics_db, fee=0.5)
    await _add_fill(metrics_db, 30.0, fee=0.5)

    service = MetricsService(engine=metrics_db, session_id="test-session", initial_balance=10000.0)
    metrics = await service.compute()
    assert metrics.total_trades == 1  # only close fills count as trades
    assert metrics.total_fees == pytest.approx(1.0)  # both open + close fees


async def test_compute_metrics_max_drawdown(metrics_db):
    from src.services.metrics import MetricsService
    await _add_fill(metrics_db, 100.0, fee=0.0)
    await _add_fill(metrics_db, -50.0, fee=0.0)
    await _add_fill(metrics_db, -30.0, fee=0.0)
    await _add_fill(metrics_db, 200.0, fee=0.0)

    service = MetricsService(engine=metrics_db, session_id="test-session", initial_balance=10000.0)
    metrics = await service.compute()
    # Peak at 100, then drops by 80 → 0.8% of 10000
    assert metrics.max_drawdown_pct == pytest.approx(0.8)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd /Users/z/Z/TradeBot && python -m pytest tests/test_metrics.py -x -v 2>&1 | tail -20`
Expected: FAIL — MetricsService.__init__ signature changed

- [ ] **Step 3: Rewrite metrics.py**

```python
# src/services/metrics.py
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
    avg_win: float = 0.0
    avg_loss: float = 0.0
    best_trade: float = 0.0
    worst_trade: float = 0.0
    recent_summary: str = ""
    total_fees: float = 0.0


class MetricsService:
    def __init__(
        self,
        engine: AsyncEngine,
        session_id: str,
        initial_balance: float = 10000.0,
    ):
        self._engine = engine
        self._session_id = session_id
        self._initial_balance = initial_balance

    async def compute(
        self,
        current_position: str = "none",
    ) -> PerformanceMetrics:
        # Query all fills (including opens with pnl=None) for fee totaling
        async with get_session(self._engine) as session:
            result = await session.execute(
                select(TradeAction)
                .where(TradeAction.session_id == self._session_id)
                .where(TradeAction.action == "order_filled")
                .order_by(TradeAction.created_at)
            )
            all_fills = result.scalars().all()

        # Total fees from ALL fills (open + close)
        total_fees = sum(f.fee for f in all_fills if f.fee is not None)

        # PnL trades: only fills with pnl (close fills)
        pnl_fills = [f for f in all_fills if f.pnl is not None]
        pnls: list[float] = [f.pnl for f in pnl_fills]

        if not pnls:
            return PerformanceMetrics(
                current_position=current_position,
                total_fees=total_fees,
            )

        total_pnl = sum(pnls)
        winning_pnls = [p for p in pnls if p > 0]
        losing_pnls = [p for p in pnls if p <= 0]
        gross_profit = sum(winning_pnls) if winning_pnls else 0.0
        gross_loss = abs(sum(losing_pnls)) if losing_pnls else 0.0

        # Max drawdown
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

        # Recent summary: last N trades
        n = min(5, len(pnls))
        recent_pnls = pnls[-n:]
        recent_wins = sum(1 for p in recent_pnls if p > 0)
        recent_losses = n - recent_wins
        recent_summary = f"{recent_wins}W {recent_losses}L (last {n} trades)"

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
            avg_win=gross_profit / len(winning_pnls) if winning_pnls else 0.0,
            avg_loss=-gross_loss / len(losing_pnls) if losing_pnls else 0.0,
            best_trade=max(pnls),
            worst_trade=min(pnls),
            recent_summary=recent_summary,
            total_fees=total_fees,
        )
```

- [ ] **Step 4: Run metrics tests**

Run: `cd /Users/z/Z/TradeBot && python -m pytest tests/test_metrics.py -x -v 2>&1 | tail -20`
Expected: All PASS

- [ ] **Step 5: Commit**

```bash
git add src/services/metrics.py tests/test_metrics.py
git commit -m "feat: expand MetricsService — avg_win/loss, best/worst, recent_summary, total_fees"
```

---

### Task 5: technical.py Rewrite

**Files:**
- Rewrite: `src/services/technical.py`
- Test: `tests/test_technical.py` (rewrite)

- [ ] **Step 1: Rewrite test_technical.py**

```python
# tests/test_technical.py
import pytest
import pandas as pd
import numpy as np


@pytest.fixture
def sample_ohlcv() -> pd.DataFrame:
    np.random.seed(42)
    n = 100  # need enough rows for MA(50), RSI(14), ATR(14)
    close = 65000 + np.cumsum(np.random.randn(n) * 100)
    return pd.DataFrame({
        "timestamp": range(n),
        "open": close - np.random.rand(n) * 50,
        "high": close + np.random.rand(n) * 100,
        "low": close - np.random.rand(n) * 100,
        "close": close,
        "volume": np.random.rand(n) * 1000 + 500,
    })


@pytest.fixture
def short_ohlcv() -> pd.DataFrame:
    """Only 10 rows — not enough for MA(50) or ATR(14)."""
    n = 10
    close = [65000 + i * 10 for i in range(n)]
    return pd.DataFrame({
        "timestamp": range(n),
        "open": [c - 5 for c in close],
        "high": [c + 50 for c in close],
        "low": [c - 50 for c in close],
        "close": close,
        "volume": [1000.0] * n,
    })


def test_compute_indicators_keys(sample_ohlcv):
    from src.services.technical import TechnicalAnalysisService
    service = TechnicalAnalysisService()
    indicators = service.compute_indicators(sample_ohlcv)
    assert isinstance(indicators, dict)
    # Original keys
    for key in ("rsi_14", "ma_20", "ma_50", "macd", "macd_signal", "macd_histogram",
                "bb_upper", "bb_middle", "bb_lower"):
        assert key in indicators
    # New keys
    assert "atr_14" in indicators
    assert "volume_ratio" in indicators


def test_compute_indicators_bb_order(sample_ohlcv):
    """BB columns must be lower < middle < upper."""
    from src.services.technical import TechnicalAnalysisService
    service = TechnicalAnalysisService()
    indicators = service.compute_indicators(sample_ohlcv)
    if indicators["bb_lower"] is not None:
        assert indicators["bb_lower"] < indicators["bb_middle"] < indicators["bb_upper"]


def test_compute_indicators_macd_histogram_sign(sample_ohlcv):
    """MACD histogram = MACD - signal (verify fields aren't swapped)."""
    from src.services.technical import TechnicalAnalysisService
    service = TechnicalAnalysisService()
    indicators = service.compute_indicators(sample_ohlcv)
    if all(indicators[k] is not None for k in ("macd", "macd_signal", "macd_histogram")):
        expected_hist = indicators["macd"] - indicators["macd_signal"]
        assert indicators["macd_histogram"] == pytest.approx(expected_hist, abs=0.01)


def test_compute_indicators_atr_positive(sample_ohlcv):
    from src.services.technical import TechnicalAnalysisService
    service = TechnicalAnalysisService()
    indicators = service.compute_indicators(sample_ohlcv)
    assert indicators["atr_14"] is not None
    assert indicators["atr_14"] > 0


def test_compute_indicators_volume_ratio(sample_ohlcv):
    from src.services.technical import TechnicalAnalysisService
    service = TechnicalAnalysisService()
    indicators = service.compute_indicators(sample_ohlcv)
    assert indicators["volume_ratio"] is not None
    assert indicators["volume_ratio"] > 0


def test_compute_indicators_short_data(short_ohlcv):
    """Short data returns None for indicators that need more history."""
    from src.services.technical import TechnicalAnalysisService
    service = TechnicalAnalysisService()
    indicators = service.compute_indicators(short_ohlcv)
    assert indicators["ma_50"] is None
    assert indicators["atr_14"] is None


def test_format_for_llm_5m_annotations(sample_ohlcv):
    from src.services.technical import TechnicalAnalysisService
    service = TechnicalAnalysisService()
    indicators = service.compute_indicators(sample_ohlcv)
    text = service.format_for_llm(indicators, current_price=65000.0, timeframe="5m")
    assert "RSI" in text
    assert "MA(20)" in text
    # Should have annotation words
    assert any(word in text.lower() for word in ("neutral", "bullish", "bearish", "overbought", "oversold"))
    # format_for_llm should NOT include ATR or Volume (those are in Market Context)
    assert "ATR" not in text
    assert "Volume" not in text


def test_format_for_llm_none_values(short_ohlcv):
    from src.services.technical import TechnicalAnalysisService
    service = TechnicalAnalysisService()
    indicators = service.compute_indicators(short_ohlcv)
    text = service.format_for_llm(indicators, current_price=65000.0, timeframe="5m")
    assert "N/A" in text
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd /Users/z/Z/TradeBot && python -m pytest tests/test_technical.py -x -v 2>&1 | tail -20`
Expected: FAIL — compute_indicators missing atr_14/volume_ratio, format_for_llm missing timeframe param

- [ ] **Step 3: Rewrite technical.py**

```python
# src/services/technical.py
from __future__ import annotations
import pandas as pd
import pandas_ta as ta  # type: ignore[import-untyped]


class TechnicalAnalysisService:
    def compute_indicators(self, df: pd.DataFrame) -> dict[str, float | None]:
        close = df["close"]
        high = df["high"]
        low = df["low"]
        volume = df["volume"]

        rsi = ta.rsi(close, length=14)  # type: ignore[attr-defined]
        ma_20 = ta.sma(close, length=20)  # type: ignore[attr-defined]
        ma_50 = ta.sma(close, length=50)  # type: ignore[attr-defined]
        macd_df = ta.macd(close)  # type: ignore[attr-defined]
        bb_df = ta.bbands(close, length=20)  # type: ignore[attr-defined]
        atr = ta.atr(high, low, close, length=14)  # type: ignore[attr-defined]
        vol_sma = ta.sma(volume, length=20)  # type: ignore[attr-defined]

        def _last(series: pd.Series | None) -> float | None:
            if series is None or series.empty or pd.isna(series.iloc[-1]):
                return None
            return float(series.iloc[-1])

        def _col(frame: pd.DataFrame | None, like: str) -> pd.Series | None:
            if frame is None:
                return None
            cols = frame.filter(like=like)
            if cols.empty:
                return None
            return cols.iloc[:, 0]

        # Volume ratio: use iloc[-2] (last completed candle) for both numerator and denominator
        volume_ratio: float | None = None
        if vol_sma is not None and len(volume) >= 2 and len(vol_sma) >= 2:
            sma_val = vol_sma.iloc[-2] if not pd.isna(vol_sma.iloc[-2]) else None
            vol_val = volume.iloc[-2]
            if sma_val is not None and sma_val > 0:
                volume_ratio = float(vol_val / sma_val)

        return {
            "rsi_14": _last(rsi),
            "ma_20": _last(ma_20),
            "ma_50": _last(ma_50),
            "macd": _last(_col(macd_df, "MACD_")),
            "macd_signal": _last(_col(macd_df, "MACDs_")),
            "macd_histogram": _last(_col(macd_df, "MACDh_")),
            "bb_upper": _last(_col(bb_df, "BBU_")),
            "bb_middle": _last(_col(bb_df, "BBM_")),
            "bb_lower": _last(_col(bb_df, "BBL_")),
            "atr_14": _last(atr),
            "volume_ratio": volume_ratio,
        }

    def format_for_llm(
        self,
        indicators: dict[str, float | None],
        current_price: float,
        timeframe: str = "5m",
    ) -> str:
        def _fmt(val: float | None, fmt: str = ".2f") -> str:
            return f"{val:{fmt}}" if val is not None else "N/A"

        lines: list[str] = []

        # RSI
        rsi = indicators.get("rsi_14")
        if rsi is not None:
            if rsi < 30:
                label = "oversold"
            elif rsi < 45:
                label = "bearish"
            elif rsi <= 55:
                label = "neutral"
            elif rsi <= 70:
                label = "bullish"
            else:
                label = "overbought"
            lines.append(f"RSI(14): {rsi:.2f} ({label})")
        else:
            lines.append("RSI(14): N/A")

        # MA
        for period in (20, 50):
            ma = indicators.get(f"ma_{period}")
            if ma is not None:
                rel = "price above — bullish" if current_price > ma else "price below — bearish"
                lines.append(f"MA({period}): {ma:.2f} ({rel})")
            else:
                lines.append(f"MA({period}): N/A")

        # MACD
        macd = indicators.get("macd")
        signal = indicators.get("macd_signal")
        hist = indicators.get("macd_histogram")
        if all(v is not None for v in (macd, signal, hist)):
            if hist > 0:
                label = "bullish"
            elif hist < 0:
                label = "bearish"
            else:
                label = "neutral"
            lines.append(f"MACD: {macd:.2f} | Signal: {signal:.2f} | Histogram: {hist:.2f} ({label})")
        else:
            lines.append(f"MACD: {_fmt(macd)} | Signal: {_fmt(signal)} | Histogram: {_fmt(hist)}")

        # Bollinger Bands
        bb_u = indicators.get("bb_upper")
        bb_m = indicators.get("bb_middle")
        bb_l = indicators.get("bb_lower")
        if all(v is not None for v in (bb_u, bb_m, bb_l)):
            if current_price > bb_m:
                pos = "price in upper half"
            else:
                pos = "price in lower half"
            lines.append(f"BB: {bb_u:.0f} / {bb_m:.0f} / {bb_l:.0f} ({pos})")
        else:
            lines.append(f"BB: {_fmt(bb_u)} / {_fmt(bb_m)} / {_fmt(bb_l)}")

        return "\n".join(lines)
```

- [ ] **Step 4: Run tests**

Run: `cd /Users/z/Z/TradeBot && python -m pytest tests/test_technical.py -x -v 2>&1 | tail -20`
Expected: All PASS

- [ ] **Step 5: Run full test suite (format_for_llm signature changed)**

Run: `cd /Users/z/Z/TradeBot && python -m pytest --tb=short 2>&1 | tail -30`
Expected: May need to update callers of `format_for_llm`. The only caller is `tools_perception.py:17` — will be rewritten in Task 7. Check if test_tools.py mocks it (yes — `d.technical.format_for_llm.return_value = "RSI(14): 55.0"` — mocked, so no breakage).

- [ ] **Step 6: Commit**

```bash
git add src/services/technical.py tests/test_technical.py
git commit -m "feat: rewrite technical.py — fix BB/MACD bugs, add ATR + volume_ratio, annotated format_for_llm"
```

---

### Task 6: TradingDeps + app.py Integration

**Files:**
- Modify: `src/agent/trader.py:16-31` (TradingDeps)
- Modify: `src/cli/app.py:178-248,362-368`
- Test: `tests/test_tool_enhancement.py` (append)

- [ ] **Step 1: Add test**

Append to `tests/test_tool_enhancement.py`:

```python
# --- Task 6: TradingDeps expansion ---

def test_trading_deps_new_fields():
    """TradingDeps has initial_balance and metrics fields with defaults."""
    from src.agent.trader import TradingDeps
    from unittest.mock import MagicMock, AsyncMock
    deps = TradingDeps(
        symbol="BTC/USDT:USDT",
        timeframe="15m",
        market_data=MagicMock(),
        exchange=MagicMock(),
        technical=MagicMock(),
        memory=AsyncMock(),
        session_id="test",
    )
    assert deps.initial_balance == 10000.0
    assert deps.metrics is None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd /Users/z/Z/TradeBot && python -m pytest tests/test_tool_enhancement.py::test_trading_deps_new_fields -x -v 2>&1 | tail -10`
Expected: FAIL — TradingDeps has no `initial_balance` or `metrics` fields

- [ ] **Step 3: Add fields to TradingDeps**

In `src/agent/trader.py`, update the TradingDeps dataclass:

```python
@dataclass
class TradingDeps:
    symbol: str
    timeframe: str
    market_data: MarketDataService
    exchange: BaseExchange
    technical: TechnicalAnalysisService
    memory: MemoryService
    session_id: str  # UUID from sessions table, must be explicitly set
    db_engine: object | None = None  # AsyncEngine, typed as object to avoid circular import
    approval_gate: object | None = None  # ApprovalGate instance
    approval_enabled: bool = True
    wake_min_minutes: int = 1
    wake_max_minutes: int = 60
    set_next_wake_fn: Callable[[int], None] | None = None
    initial_balance: float = 10000.0
    metrics: object | None = None  # MetricsService, typed as object to avoid circular import
```

- [ ] **Step 4: Update app.py build_services to wire MetricsService and initial_balance**

In `src/cli/app.py`, add MetricsService creation **before** the `deps = TradingDeps(...)` block, then pass both into the constructor:

Add import at top of `build_services` (or near existing imports):
```python
from src.services.metrics import MetricsService
```

Before `deps = TradingDeps(...)`, create MetricsService:
```python
    metrics_service = MetricsService(
        engine=engine,
        session_id=session_id,
        initial_balance=result.initial_balance,
    )
```

Then update the `TradingDeps(...)` constructor to include the new fields:
```python
    deps = TradingDeps(
        symbol=result.symbol,
        timeframe=result.timeframe,
        market_data=market_data,
        exchange=exchange,
        technical=technical,
        memory=memory,
        session_id=session_id,
        db_engine=engine,
        approval_gate=approval_gate,
        approval_enabled=result.approval_enabled,
        initial_balance=result.initial_balance,
        metrics=metrics_service,
    )
```

Then update `app.py:362-368` — replace the standalone MetricsService creation:

Replace:
```python
    # Initial metrics
    metrics_service = MetricsService(initial_balance=result.initial_balance)
    positions = await exchange.fetch_positions(result.symbol)
    pos_str = f"{positions[0].side} {positions[0].contracts}" if positions else "none"
    metrics = await metrics_service.compute(engine, session_id, current_position=pos_str)
    display_metrics(metrics, console=sc)
```

With:
```python
    # Initial metrics
    positions = await exchange.fetch_positions(result.symbol)
    pos_str = f"{positions[0].side} {positions[0].contracts}" if positions else "none"
    metrics = await deps.metrics.compute(current_position=pos_str)
    display_metrics(metrics, console=sc)
```

- [ ] **Step 5: Run tests**

Run: `cd /Users/z/Z/TradeBot && python -m pytest tests/test_tool_enhancement.py::test_trading_deps_new_fields -x -v 2>&1 | tail -10`
Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add src/agent/trader.py src/cli/app.py tests/test_tool_enhancement.py
git commit -m "feat: TradingDeps initial_balance + metrics; wire MetricsService in build_services"
```

---

### Task 7: get_market_data Enhancement

**Files:**
- Modify: `src/integrations/market_data.py:17-23`
- Rewrite: `src/agent/tools_perception.py:12-23` (get_market_data function)
- Modify: `src/agent/trader.py` (update tool signature + docstring)
- Test: `tests/test_tool_enhancement.py` (append)

- [ ] **Step 1: Add tests**

Append to `tests/test_tool_enhancement.py`:

```python
# --- Task 7+: Shared fixtures ---

import pandas as pd
import numpy as np
from dataclasses import dataclass
from unittest.mock import AsyncMock, MagicMock
from src.integrations.exchange.base import Ticker, Balance, Position, Order


@dataclass
class MockDeps:
    symbol: str
    timeframe: str
    market_data: AsyncMock
    exchange: AsyncMock
    technical: MagicMock
    memory: AsyncMock
    session_id: str = "test-session"
    db_engine: object = None
    approval_gate: object = None
    approval_enabled: bool = False
    wake_min_minutes: int = 1
    wake_max_minutes: int = 60
    set_next_wake_fn: object = None
    initial_balance: float = 10000.0
    metrics: object = None


def _make_deps():
    """Create a MockDeps with all needed fields for enhanced tools."""
    d = MockDeps(
        symbol="BTC/USDT:USDT",
        timeframe="5m",
        market_data=AsyncMock(),
        exchange=AsyncMock(),
        technical=MagicMock(),
        memory=AsyncMock(),
    )
    d.market_data.get_ticker.return_value = Ticker(
        "BTC/USDT:USDT", 74880.0, 74870.0, 74890.0, 75200.0, 73800.0, 12345.6, 1000,
    )
    d.exchange.fetch_balance.return_value = Balance(10000.0, 8000.0, 2000.0)
    d.exchange.fetch_positions.return_value = []
    d.exchange.fetch_open_orders = AsyncMock(return_value=[])
    d.exchange.has_pending_market_order = MagicMock(return_value=False)
    d.exchange.get_alert_params = MagicMock(return_value=(5.0, 60))
    d.exchange.get_price_level_alerts = MagicMock(return_value=[])
    d.exchange.cancel_order = AsyncMock()
    d.exchange.set_leverage = AsyncMock()
    d.exchange.amount_to_precision = MagicMock(side_effect=lambda sym, amt: round(amt, 3))
    d.exchange.create_order = AsyncMock(return_value=Order(
        "o1", "BTC/USDT:USDT", "buy", "market", 0.01, 65000.0, "closed",
    ))
    return d


async def test_get_market_data_four_segments():
    from src.agent.tools_perception import get_market_data

    deps = _make_deps()
    # Create a realistic DataFrame
    n = 100
    deps.market_data.get_ohlcv_dataframe.return_value = pd.DataFrame({
        "timestamp": [1000 + i * 300000 for i in range(n)],
        "open": np.full(n, 74800.0),
        "high": np.full(n, 74900.0),
        "low": np.full(n, 74700.0),
        "close": np.full(n, 74880.0),
        "volume": np.full(n, 125.0),
    })
    deps.technical.compute_indicators.return_value = {
        "rsi_14": 52.88, "ma_20": 74750.0, "ma_50": 74500.0,
        "macd": 12.5, "macd_signal": 8.3, "macd_histogram": 4.2,
        "bb_upper": 75100.0, "bb_middle": 74750.0, "bb_lower": 74400.0,
        "atr_14": 85.2, "volume_ratio": 1.35,
    }
    deps.technical.format_for_llm.return_value = "RSI(14): 52.88 (neutral)\nMA(20): 74750.00"

    result = await get_market_data(deps)
    # Four segment headers
    assert "=== Ticker" in result
    assert "=== Technical Indicators" in result
    assert "=== Market Context ===" in result
    assert "=== Recent Candles" in result
    # Ticker data
    assert "74880" in result
    assert "74870" in result  # bid
    # Market context — ATR and Volume come from indicators dict
    assert "ATR" in result
    assert "Volume" in result
    assert "avg" in result  # volume ratio label


async def test_get_market_data_default_params():
    """get_market_data uses deps.symbol and deps.timeframe when called without args."""
    from src.agent.tools_perception import get_market_data

    deps = _make_deps()
    n = 100
    deps.market_data.get_ohlcv_dataframe.return_value = pd.DataFrame({
        "timestamp": [1000 + i * 300000 for i in range(n)],
        "open": np.full(n, 74800.0), "high": np.full(n, 74900.0),
        "low": np.full(n, 74700.0), "close": np.full(n, 74880.0),
        "volume": np.full(n, 125.0),
    })
    deps.technical.compute_indicators.return_value = {
        "rsi_14": 50.0, "ma_20": 74750.0, "ma_50": 74500.0,
        "macd": 0.0, "macd_signal": 0.0, "macd_histogram": 0.0,
        "bb_upper": 75000.0, "bb_middle": 74750.0, "bb_lower": 74500.0,
        "atr_14": 80.0, "volume_ratio": 1.0,
    }
    deps.technical.format_for_llm.return_value = "RSI(14): 50.00 (neutral)"

    result = await get_market_data(deps)
    # Should have called with deps.symbol and deps.timeframe
    deps.market_data.get_ticker.assert_called_once_with("BTC/USDT:USDT")
    assert "5m" in result  # timeframe in segment headers


async def test_get_market_data_1h_atr_no_qualitative_label():
    """Non-5m timeframes should NOT have ATR qualitative labels (low/moderate/high)."""
    from src.agent.tools_perception import get_market_data

    deps = _make_deps()
    deps.timeframe = "1h"
    n = 100
    deps.market_data.get_ohlcv_dataframe.return_value = pd.DataFrame({
        "timestamp": [1000 + i * 3600000 for i in range(n)],
        "open": np.full(n, 74800.0), "high": np.full(n, 74900.0),
        "low": np.full(n, 74700.0), "close": np.full(n, 74880.0),
        "volume": np.full(n, 125.0),
    })
    deps.technical.compute_indicators.return_value = {
        "rsi_14": 50.0, "ma_20": 74750.0, "ma_50": 74500.0,
        "macd": 0.0, "macd_signal": 0.0, "macd_histogram": 0.0,
        "bb_upper": 75000.0, "bb_middle": 74750.0, "bb_lower": 74500.0,
        "atr_14": 850.0, "volume_ratio": 1.0,
    }
    deps.technical.format_for_llm.return_value = "RSI(14): 50.00 (neutral)"

    result = await get_market_data(deps, timeframe="1h")
    # ATR line should exist with value and percentage
    assert "ATR(14): 850.00" in result
    assert "1h candles" in result
    # Should NOT have qualitative labels
    assert "low volatility" not in result
    assert "moderate" not in result
    assert "high volatility" not in result


async def test_get_market_data_candle_count_clamp():
    """candle_count is clamped to 10-80."""
    from src.agent.tools_perception import get_market_data

    deps = _make_deps()
    n = 100
    deps.market_data.get_ohlcv_dataframe.return_value = pd.DataFrame({
        "timestamp": [1000 + i * 300000 for i in range(n)],
        "open": np.full(n, 74800.0), "high": np.full(n, 74900.0),
        "low": np.full(n, 74700.0), "close": np.full(n, 74880.0),
        "volume": np.full(n, 125.0),
    })
    deps.technical.compute_indicators.return_value = {
        "rsi_14": 50.0, "ma_20": 74750.0, "ma_50": 74500.0,
        "macd": 0.0, "macd_signal": 0.0, "macd_histogram": 0.0,
        "bb_upper": 75000.0, "bb_middle": 74750.0, "bb_lower": 74500.0,
        "atr_14": 80.0, "volume_ratio": 1.0,
    }
    deps.technical.format_for_llm.return_value = "RSI(14): 50.00"

    result = await get_market_data(deps, candle_count=5)
    # candle_count clamped to 10 → fetch_limit = max(10+50, 100) = 100
    assert deps.market_data.get_ohlcv_dataframe.call_args.kwargs["limit"] == 100
    # Output should show "last 10" (clamped from 5)
    assert "last 10" in result
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd /Users/z/Z/TradeBot && python -m pytest tests/test_tool_enhancement.py::test_get_market_data_four_segments -x -v 2>&1 | tail -15`
Expected: FAIL — get_market_data still has old signature `(deps, symbol, timeframe)`

- [ ] **Step 3: Update market_data.py — limit passthrough already works**

No change needed — `get_ohlcv_dataframe` already accepts `limit` parameter and passes it through.

- [ ] **Step 4: Rewrite get_market_data in tools_perception.py**

Replace the `get_market_data` function:

```python
async def get_market_data(
    deps: TradingDeps,
    symbol: str | None = None,
    timeframe: str | None = None,
    candle_count: int = 50,
) -> str:
    """Get market data: ticker, indicators, market context, and recent candles.

    candle_count=20 for quick check or secondary timeframes, 50 for detailed analysis.
    Default 50. Values above 50 may be capped by exchange API limits.
    Total output ~1000-1200 tokens (K-line table ~750-800 + indicators + context).
    """
    symbol = symbol or deps.symbol
    timeframe = timeframe or deps.timeframe
    candle_count = max(10, min(candle_count, 80))

    ticker = await deps.market_data.get_ticker(symbol)
    fetch_limit = max(candle_count + 50, 100)
    df = await deps.market_data.get_ohlcv_dataframe(symbol, timeframe, limit=fetch_limit)
    indicators = deps.technical.compute_indicators(df)
    indicators_text = deps.technical.format_for_llm(
        indicators, current_price=ticker.last, timeframe=timeframe,
    )

    # Determine display count
    available = len(df)
    if available >= candle_count + 50:
        display_count = candle_count
    else:
        display_count = max(10, available - 50)
    display_df = df.tail(display_count)

    sections: list[str] = []

    # === Ticker ===
    sections.append(
        f"=== Ticker ({symbol}) ===\n"
        f"Price: {ticker.last:.2f} | Bid: {ticker.bid:.2f} | Ask: {ticker.ask:.2f}\n"
        f"24h High: {ticker.high:.2f} | Low: {ticker.low:.2f} | Volume: {ticker.base_volume:.2f}"
    )

    # === Technical Indicators ===
    sections.append(
        f"=== Technical Indicators ({timeframe}) ===\n{indicators_text}"
    )

    # === Market Context ===
    ctx_lines = []
    atr = indicators.get("atr_14")
    if atr is not None and ticker.last > 0:
        pct = atr / ticker.last * 100
        if timeframe == "5m":
            if pct < 0.1:
                atr_label = f"{pct:.2f}% of price — low volatility"
            elif pct <= 0.3:
                atr_label = f"{pct:.2f}% of price — moderate"
            else:
                atr_label = f"{pct:.2f}% of price — high volatility"
        else:
            atr_label = f"{pct:.2f}% of price, {timeframe} candles"
        ctx_lines.append(f"ATR(14): {atr:.2f} ({atr_label})")
    else:
        ctx_lines.append("ATR(14): N/A")

    vr = indicators.get("volume_ratio")
    if vr is not None:
        # Show raw volume (iloc[-2], last completed candle) + ratio
        raw_vol = df["volume"].iloc[-2] if len(df) >= 2 else df["volume"].iloc[-1]
        if vr < 0.7:
            vr_label = "low"
        elif vr <= 1.3:
            vr_label = "normal"
        else:
            vr_label = "above normal"
        ctx_lines.append(f"Volume: {raw_vol:.1f} ({vr:.2f}x avg — {vr_label})")
    else:
        ctx_lines.append("Volume: N/A")

    candle_high = display_df["high"].max()
    candle_low = display_df["low"].min()
    ctx_lines.append(f"{display_count}-candle Range: {candle_low:.0f} — {candle_high:.0f}")
    sections.append("=== Market Context ===\n" + "\n".join(ctx_lines))

    # === Recent Candles ===
    from datetime import datetime, timezone
    tf_short = timeframe.lower()
    candle_lines = [f"{'Time':<14} {'Open':>10} {'High':>10} {'Low':>10} {'Close':>10} {'Vol':>10}"]
    for _, row in display_df.iterrows():
        ts = row["timestamp"]
        if isinstance(ts, (int, float)):
            dt = datetime.fromtimestamp(ts / 1000, tz=timezone.utc)
        else:
            dt = ts
        if tf_short in ("1m", "5m", "15m"):
            time_str = dt.strftime("%H:%M")
        elif tf_short in ("1h", "4h"):
            time_str = dt.strftime("%m-%d %H:%M")
        else:
            time_str = dt.strftime("%Y-%m-%d")
        candle_lines.append(
            f"{time_str:<14} {row['open']:>10.2f} {row['high']:>10.2f} "
            f"{row['low']:>10.2f} {row['close']:>10.2f} {row['volume']:>10.1f}"
        )
    sections.append(
        f"=== Recent Candles ({timeframe}, last {display_count}) ===\n"
        + "\n".join(candle_lines)
    )

    return "\n\n".join(sections)
```

- [ ] **Step 5: Update trader.py tool registration**

Replace the `get_market_data` registration:

```python
    @agent.tool
    async def get_market_data(
        ctx: RunContext[TradingDeps],
        symbol: str | None = None,
        timeframe: str | None = None,
        candle_count: int = 50,
    ) -> str:
        """Get market data: ticker, technical indicators, market context, and recent candles.
        candle_count=20 for quick check or secondary timeframes, 50 for detailed analysis.
        Default 50. symbol and timeframe default to session config."""
        from src.agent.tools_perception import get_market_data as _impl

        return await _impl(ctx.deps, symbol, timeframe, candle_count)
```

- [ ] **Step 6: Run tests**

Run: `cd /Users/z/Z/TradeBot && python -m pytest tests/test_tool_enhancement.py::test_get_market_data_four_segments tests/test_tool_enhancement.py::test_get_market_data_default_params tests/test_tool_enhancement.py::test_get_market_data_candle_count_clamp -x -v 2>&1 | tail -20`
Expected: All PASS

- [ ] **Step 7: Run full suite to check for regressions**

Run: `cd /Users/z/Z/TradeBot && python -m pytest --tb=short 2>&1 | tail -20`
Expected: Existing test_tools.py::test_get_market_data may need update (old call signature `get_market_data(deps, "BTC/USDT:USDT", "15m")`). The new function accepts positional `symbol` and `timeframe` so existing test should still work. Verify.

- [ ] **Step 8: Commit**

```bash
git add src/agent/tools_perception.py src/agent/trader.py tests/test_tool_enhancement.py
git commit -m "feat: get_market_data — 4-segment output, candle table, default params"
```

---

### Task 8: get_position + get_account_balance Enhancement

**Files:**
- Modify: `src/agent/tools_perception.py:26-49` (get_position, get_account_balance)
- Modify: `src/agent/trader.py` (get_position signature)
- Test: `tests/test_tool_enhancement.py` (append)

- [ ] **Step 1: Add tests**

Append to `tests/test_tool_enhancement.py`:

```python
# --- Task 8: get_position + get_account_balance enhancement ---

async def test_get_position_enhanced():
    from src.agent.tools_perception import get_position

    deps = _make_deps()
    deps.exchange.fetch_positions.return_value = [
        Position("BTC/USDT:USDT", "long", 0.001, 74761.10, -19.09, 3, 50200.0,
                 created_at=datetime(2026, 4, 14, 12, 0, tzinfo=timezone.utc)),
    ]
    deps.market_data.get_ticker.return_value = Ticker(
        "BTC/USDT:USDT", 74500.0, 74499.0, 74501.0, 75200.0, 73800.0, 100.0, 1000,
    )

    result = await get_position(deps)
    assert "LONG" in result
    assert "74761.10" in result
    # PnL percentage of initial capital
    assert "% of initial capital" in result.lower() or "of initial capital" in result
    # Liquidation distance
    assert "away" in result.lower()
    # Duration
    assert "Duration" in result or "duration" in result.lower() or "min" in result.lower()


async def test_get_position_no_position():
    from src.agent.tools_perception import get_position

    deps = _make_deps()
    deps.exchange.fetch_positions.return_value = []

    result = await get_position(deps)
    assert "No open positions" in result


async def test_get_position_default_symbol():
    """get_position uses deps.symbol when called without args."""
    from src.agent.tools_perception import get_position

    deps = _make_deps()
    result = await get_position(deps)
    deps.exchange.fetch_positions.assert_called_once_with("BTC/USDT:USDT")


async def test_get_account_balance_enhanced():
    from src.agent.tools_perception import get_account_balance

    deps = _make_deps()
    deps.exchange.fetch_balance.return_value = Balance(9981.0, 8981.0, 1000.0)
    deps.initial_balance = 10000.0

    result = await get_account_balance(deps)
    assert "9981.00" in result
    assert "initial" in result.lower()
    assert "Return" in result or "return" in result.lower()
    # Return should be negative
    assert "-0.19%" in result or "-19.00" in result
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd /Users/z/Z/TradeBot && python -m pytest tests/test_tool_enhancement.py::test_get_position_enhanced tests/test_tool_enhancement.py::test_get_account_balance_enhanced -x -v 2>&1 | tail -15`
Expected: FAIL — old output format

- [ ] **Step 3: Rewrite get_position**

Replace in `src/agent/tools_perception.py`:

```python
async def get_position(deps: TradingDeps, symbol: str | None = None) -> str:
    """Get current open position with risk context."""
    symbol = symbol or deps.symbol
    positions = await deps.exchange.fetch_positions(symbol)
    if not positions:
        return "No open positions."

    p = positions[0]
    lines = ["Current Position:"]
    lines.append(f"  {p.side.upper()} {p.contracts} contracts @ {p.entry_price:.2f} | {p.leverage}x leverage")

    # PnL as % of initial capital
    pnl_pct = (p.unrealized_pnl / deps.initial_balance) * 100
    lines.append(f"  PnL: {p.unrealized_pnl:.2f} USDT ({pnl_pct:+.2f}% of initial capital)")

    # Liquidation distance
    if p.liquidation_price:
        ticker = await deps.market_data.get_ticker(symbol)
        liq_dist = abs(ticker.last - p.liquidation_price) / ticker.last * 100
        lines.append(f"  Liquidation: {p.liquidation_price:.2f} ({liq_dist:.1f}% away)")

    # Duration
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

    return "\n".join(lines)
```

- [ ] **Step 4: Rewrite get_account_balance**

Replace in `src/agent/tools_perception.py`:

```python
async def get_account_balance(deps: TradingDeps) -> str:
    """Get account balance with return on initial capital."""
    balance = await deps.exchange.fetch_balance()
    ret_usdt = balance.total_usdt - deps.initial_balance
    ret_pct = (ret_usdt / deps.initial_balance) * 100
    return (
        f"Account Balance:\n"
        f"  Total: {balance.total_usdt:.2f} USDT (initial: {deps.initial_balance:.2f})\n"
        f"  Return: {ret_pct:+.2f}% ({ret_usdt:+.2f} USDT) (incl. unrealized)\n"
        f"  Free: {balance.free_usdt:.2f} USDT\n"
        f"  Used: {balance.used_usdt:.2f} USDT"
    )
```

- [ ] **Step 5: Update trader.py get_position registration**

```python
    @agent.tool
    async def get_position(ctx: RunContext[TradingDeps], symbol: str | None = None) -> str:
        """Get current open position with risk context (PnL %, liquidation distance, duration)."""
        from src.agent.tools_perception import get_position as _impl

        return await _impl(ctx.deps, symbol)
```

- [ ] **Step 6: Run tests**

Run: `cd /Users/z/Z/TradeBot && python -m pytest tests/test_tool_enhancement.py::test_get_position_enhanced tests/test_tool_enhancement.py::test_get_position_no_position tests/test_tool_enhancement.py::test_get_position_default_symbol tests/test_tool_enhancement.py::test_get_account_balance_enhanced -x -v 2>&1 | tail -15`
Expected: All PASS

- [ ] **Step 7: Commit**

```bash
git add src/agent/tools_perception.py src/agent/trader.py tests/test_tool_enhancement.py
git commit -m "feat: get_position risk context + get_account_balance return %"
```

---

### Task 9: get_trade_journal Enhancement + get_open_orders Enhancement

**Files:**
- Modify: `src/agent/tools_perception.py` (get_trade_journal, get_open_orders)
- Test: `tests/test_tool_enhancement.py` (append)

- [ ] **Step 1: Add tests**

Append to `tests/test_tool_enhancement.py`:

```python
# --- Task 9: get_trade_journal + get_open_orders enhancement ---

async def test_get_trade_journal_with_summary(tmp_path):
    from src.agent.tools_perception import get_trade_journal
    from src.storage.database import init_db, get_session
    from src.storage.models import Session, TradeAction
    from src.services.metrics import MetricsService

    engine = await init_db(f"sqlite+aiosqlite:///{tmp_path}/t9.db")
    async with get_session(engine) as session:
        session.add(Session(id="t9", name="test-journal", initial_balance=10000.0))
        session.add(TradeAction(
            session_id="t9", action="order_filled", order_id="o1",
            symbol="BTC/USDT:USDT", side="long", pnl=30.0, fee=0.5,
            reasoning="test fill",
        ))
        session.add(TradeAction(
            session_id="t9", action="order_filled", order_id="o2",
            symbol="BTC/USDT:USDT", side="long", pnl=-10.0, fee=0.3,
            reasoning="test fill 2",
        ))
        await session.commit()

    deps = _make_deps()
    deps.db_engine = engine
    deps.session_id = "t9"
    deps.metrics = MetricsService(engine=engine, session_id="t9", initial_balance=10000.0)
    deps.exchange.fetch_order = AsyncMock(return_value=Order(
        "o1", "BTC/USDT:USDT", "buy", "market", 0.01, 65000.0, "closed", fee=0.5,
    ))

    result = await get_trade_journal(deps)
    # Should have Performance Summary section before Trade Journal
    assert "=== Performance Summary ===" in result
    assert "Win:" in result
    assert "=== Trade Journal ===" in result
    await engine.dispose()


async def test_get_trade_journal_empty(tmp_path):
    from src.agent.tools_perception import get_trade_journal

    deps = _make_deps()
    deps.db_engine = None

    result = await get_trade_journal(deps)
    assert "No trade journal" in result


async def test_get_open_orders_with_distance():
    from src.agent.tools_perception import get_open_orders

    deps = _make_deps()
    deps.market_data.get_ticker.return_value = Ticker(
        "BTC/USDT:USDT", 74761.0, 74760.0, 74762.0, 75200.0, 73800.0, 100.0, 1000,
    )
    deps.exchange.fetch_open_orders.return_value = [
        Order("o1", "BTC/USDT:USDT", "sell", "stop", 0.001, 72500.0, "open"),
        Order("o2", "BTC/USDT:USDT", "sell", "take_profit", 0.001, 79200.0, "open"),
        Order("o3", "BTC/USDT:USDT", "buy", "limit", 0.001, 72000.0, "open"),
        Order("o4", "BTC/USDT:USDT", "buy", "market", 0.001, None, "open"),
    ]

    result = await get_open_orders(deps)
    # Stop and TP should show distance
    assert "% from current" in result or "from current" in result
    # Market order should show "market price" without distance
    assert "market price" in result
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd /Users/z/Z/TradeBot && python -m pytest tests/test_tool_enhancement.py::test_get_trade_journal_with_summary tests/test_tool_enhancement.py::test_get_open_orders_with_distance -x -v 2>&1 | tail -15`
Expected: FAIL — no summary section, no distance percentages

- [ ] **Step 3: Rewrite get_trade_journal**

Replace in `src/agent/tools_perception.py`:

```python
async def get_trade_journal(deps: TradingDeps, limit: int = 20) -> str:
    """Get trade journal — decision timeline with quick stats summary.
    Use for reviewing recent decisions and their outcomes."""
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

    sections: list[str] = []

    # Performance Summary (from MetricsService)
    if deps.metrics is not None:
        metrics = await deps.metrics.compute()
        if metrics.total_trades > 0:
            summary_lines = [
                f"Total Trades: {metrics.total_trades} | Win: {metrics.winning_trades} "
                f"({metrics.win_rate:.1%}) | Loss: {metrics.losing_trades}",
                f"Avg Win: {metrics.avg_win:+.2f} USDT | Avg Loss: {metrics.avg_loss:.2f} USDT",
                f"Profit Factor: {metrics.profit_factor:.2f}",
            ]
            if metrics.recent_summary:
                summary_lines.append(f"Recent: {metrics.recent_summary}")
            sections.append("=== Performance Summary ===\n" + "\n".join(summary_lines))

    # Trade Journal
    order_details = {}
    order_ids = list({a.order_id for a in actions if a.order_id})
    for oid in order_ids:
        try:
            order = await deps.exchange.fetch_order(oid, deps.symbol)
            order_details[oid] = order
        except Exception:
            logger.warning("Failed to fetch order %s", oid, exc_info=True)

    lines = []
    for a in reversed(actions):  # chronological order
        ts = a.created_at.strftime("%m-%d %H:%M")
        line = f"[{ts}] {a.action}"
        if a.side:
            line += f" ({a.side})"
        if a.order_id and a.order_id in order_details:
            od = order_details[a.order_id]
            if od.price:
                line += f" @ {od.price:.2f}"
            if od.fee is not None:
                line += f", fee={od.fee:.4f}"
            line += f" [{od.status}]"
        if a.pnl is not None:
            line += f", pnl={a.pnl:.2f}"
        if a.reasoning:
            line += f"\n  Reasoning: {a.reasoning}"
        lines.append(line)

    sections.append("=== Trade Journal ===\n" + "\n".join(lines))
    return "\n\n".join(sections)
```

- [ ] **Step 4: Rewrite get_open_orders**

Replace in `src/agent/tools_perception.py`:

```python
async def get_open_orders(deps: TradingDeps) -> str:
    """Get all pending orders with distance from current price."""
    orders = await deps.exchange.fetch_open_orders(deps.symbol)
    if not orders:
        return "No pending orders."

    ticker = await deps.market_data.get_ticker(deps.symbol)
    current = ticker.last

    lines = ["Pending Orders:"]
    for o in orders:
        if o.order_type == "market" or o.price is None:
            label = "[PENDING]" if o.order_type == "market" else f"[{o.order_type.upper()}]"
            price_str = "market price"
        else:
            if o.order_type == "limit":
                label = "[LIMIT]"
            else:
                label = f"[{o.order_type.upper()}]"
            dist = (o.price - current) / current * 100
            price_str = f"@ {o.price:.2f} ({dist:+.2f}% from current)"
        lines.append(f"  {label} {o.side} {o.amount} {price_str} | ID: {o.id}")
    return "\n".join(lines)
```

- [ ] **Step 5: Run tests**

Run: `cd /Users/z/Z/TradeBot && python -m pytest tests/test_tool_enhancement.py::test_get_trade_journal_with_summary tests/test_tool_enhancement.py::test_get_trade_journal_empty tests/test_tool_enhancement.py::test_get_open_orders_with_distance -x -v 2>&1 | tail -15`
Expected: All PASS

- [ ] **Step 6: Commit**

```bash
git add src/agent/tools_perception.py tests/test_tool_enhancement.py
git commit -m "feat: get_trade_journal summary header + get_open_orders distance %"
```

---

### Task 10: Execution Tool Enhancements — SL/TP Distance, set_price_alert Disabled, cancel_order

**Files:**
- Modify: `src/agent/tools_execution.py`
- Modify: `src/agent/trader.py`
- Test: `tests/test_tool_enhancement.py` (append)

- [ ] **Step 1: Add tests**

Append to `tests/test_tool_enhancement.py`:

```python
# --- Task 10: Execution tool enhancements ---

async def test_set_stop_loss_distance():
    from src.agent.tools_execution import set_stop_loss

    deps = _make_deps()
    deps.exchange.fetch_positions.return_value = [
        Position("BTC/USDT:USDT", "long", 0.001, 74761.10, -19.09, 3, 50200.0),
    ]
    deps.exchange.create_order.return_value = Order(
        "o1", "BTC/USDT:USDT", "sell", "stop", 0.001, 72500.0, "open",
    )
    deps.market_data.get_ticker.return_value = Ticker(
        "BTC/USDT:USDT", 74761.0, 74760.0, 74762.0, 75200.0, 73800.0, 100.0, 1000,
    )

    result = await set_stop_loss(deps, 72500.0, reasoning="protect capital")
    assert "72500" in result
    assert "% from current" in result or "from current" in result


async def test_set_take_profit_distance():
    from src.agent.tools_execution import set_take_profit

    deps = _make_deps()
    deps.exchange.fetch_positions.return_value = [
        Position("BTC/USDT:USDT", "long", 0.001, 74761.10, -19.09, 3, 50200.0),
    ]
    deps.exchange.create_order.return_value = Order(
        "o1", "BTC/USDT:USDT", "sell", "take_profit", 0.001, 79200.0, "open",
    )
    deps.market_data.get_ticker.return_value = Ticker(
        "BTC/USDT:USDT", 74761.0, 74760.0, 74762.0, 75200.0, 73800.0, 100.0, 1000,
    )

    result = await set_take_profit(deps, 79200.0, reasoning="target resistance")
    assert "79200" in result
    assert "% from current" in result or "from current" in result


async def test_set_price_alert_disabled():
    from src.agent.tools_execution import set_price_alert

    deps = _make_deps()
    deps.exchange.get_alert_params = MagicMock(return_value=None)

    result = await set_price_alert(deps, 5.0, 60, reasoning="test")
    assert "disabled" in result.lower() or "Alerts are disabled" in result


async def test_set_price_alert_enabled():
    from src.agent.tools_execution import set_price_alert

    deps = _make_deps()
    deps.exchange.get_alert_params = MagicMock(return_value=(5.0, 60))
    deps.exchange.update_alert_params = MagicMock()

    result = await set_price_alert(deps, 3.0, 30, reasoning="tighter alert")
    assert "updated" in result.lower() or "3.0%" in result


async def test_cancel_order_success():
    from src.agent.tools_execution import cancel_order

    deps = _make_deps()
    target_order = Order("o1", "BTC/USDT:USDT", "buy", "limit", 0.001, 72000.0, "open")
    deps.exchange.fetch_open_orders.return_value = [target_order]
    deps.exchange.cancel_order = AsyncMock()

    result = await cancel_order(deps, "o1", reasoning="no longer needed")
    assert "cancelled" in result.lower() or "Cancelled" in result or "cancel" in result.lower()
    deps.exchange.cancel_order.assert_called_once_with("o1", "BTC/USDT:USDT")


async def test_cancel_order_not_found():
    from src.agent.tools_execution import cancel_order

    deps = _make_deps()
    deps.exchange.fetch_open_orders.return_value = []

    result = await cancel_order(deps, "nonexistent", reasoning="cleanup")
    assert "not found" in result.lower() or "already filled" in result.lower()


async def test_cancel_order_market_rejected():
    from src.agent.tools_execution import cancel_order

    deps = _make_deps()
    market_order = Order("o1", "BTC/USDT:USDT", "buy", "market", 0.001, None, "open")
    deps.exchange.fetch_open_orders.return_value = [market_order]

    result = await cancel_order(deps, "o1", reasoning="want to cancel")
    assert "Cannot cancel market" in result or "market" in result.lower()
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd /Users/z/Z/TradeBot && python -m pytest tests/test_tool_enhancement.py::test_set_stop_loss_distance tests/test_tool_enhancement.py::test_set_price_alert_disabled tests/test_tool_enhancement.py::test_cancel_order_success -x -v 2>&1 | tail -15`
Expected: FAIL — no distance in SL/TP return, no get_alert_params check, no cancel_order function

- [ ] **Step 3: Update set_stop_loss return with distance**

In `src/agent/tools_execution.py`, replace the return line of `set_stop_loss`:

```python
    ticker = await deps.market_data.get_ticker(deps.symbol)
    dist_pct = (price - ticker.last) / ticker.last * 100
    return f"Stop loss set at {price:.2f} ({dist_pct:+.2f}% from current {ticker.last:.2f}) | Order: {order.id}"
```

Replace the last 2 lines of `set_stop_loss` (the `_record_action` call stays, only change the return):

Full updated end of `set_stop_loss`:
```python
    await _record_action(
        deps, action="set_stop_loss", order_id=order.id,
        side=p.side, price=price, reasoning=reasoning,
    )

    ticker = await deps.market_data.get_ticker(deps.symbol)
    dist_pct = (price - ticker.last) / ticker.last * 100
    return f"Stop loss set at {price:.2f} ({dist_pct:+.2f}% from current {ticker.last:.2f}) | Order: {order.id}"
```

- [ ] **Step 4: Update set_take_profit return with distance**

Same pattern — replace the return line of `set_take_profit`:

```python
    await _record_action(
        deps, action="set_take_profit", order_id=order.id,
        side=p.side, price=price, reasoning=reasoning,
    )

    ticker = await deps.market_data.get_ticker(deps.symbol)
    dist_pct = (price - ticker.last) / ticker.last * 100
    return f"Take profit set at {price:.2f} ({dist_pct:+.2f}% from current {ticker.last:.2f}) | Order: {order.id}"
```

- [ ] **Step 5: Update set_price_alert with disabled check**

Replace the `set_price_alert` function:

```python
async def set_price_alert(
    deps: TradingDeps,
    threshold_pct: float,
    window_minutes: int,
    reasoning: str,
) -> str:
    """Adjust price alert parameters. threshold_pct: 0.5-50%, window_minutes: 1-240."""
    # Check if alerts are enabled
    if deps.exchange.get_alert_params() is None:
        return "Alerts are disabled for this session. Enable alerts in wizard to use this feature."

    # Parameter validation
    if not (0.5 <= threshold_pct <= 50.0):
        return f"Invalid threshold_pct: must be 0.5-50.0, got {threshold_pct}"
    if not (1 <= window_minutes <= 240):
        return f"Invalid window_minutes: must be 1-240, got {window_minutes}"

    deps.exchange.update_alert_params(threshold_pct, window_minutes)

    await _record_action(
        deps, action="set_price_alert",
        reasoning=f"threshold={threshold_pct}%, window={window_minutes}min | {reasoning}",
    )

    return (
        f"Price alert updated: threshold={threshold_pct}%, "
        f"window={window_minutes}min"
    )
```

- [ ] **Step 6: Add cancel_order function**

Add at the end of `src/agent/tools_execution.py`:

```python
async def cancel_order(
    deps: TradingDeps,
    order_id: str,
    reasoning: str,
) -> str:
    """Cancel a pending order (limit, stop, take_profit)."""
    open_orders = await deps.exchange.fetch_open_orders(deps.symbol)
    target = None
    for o in open_orders:
        if o.id == order_id:
            target = o
            break

    if target is None:
        return f"Order not found or already filled: {order_id}"

    if target.order_type == "market":
        return "Cannot cancel market orders"

    await deps.exchange.cancel_order(order_id, deps.symbol)

    await _record_action(
        deps, action="cancel_order", order_id=order_id,
        side=target.side, price=target.price, reasoning=reasoning,
    )

    price_str = f" @ {target.price:.2f}" if target.price else ""
    return f"Order cancelled: {target.order_type} {target.side} {target.amount}{price_str} | ID: {order_id}"
```

- [ ] **Step 7: Register cancel_order in trader.py**

Add in the Execution Tools section of `create_trader_agent`:

```python
    @agent.tool
    async def cancel_order(ctx: RunContext[TradingDeps], order_id: str, reasoning: str) -> str:
        """Cancel a pending order (limit, stop loss, take profit). Always provide reasoning."""
        from src.agent.tools_execution import cancel_order as _impl

        return await _impl(ctx.deps, order_id, reasoning=reasoning)
```

- [ ] **Step 8: Run tests**

Run: `cd /Users/z/Z/TradeBot && python -m pytest tests/test_tool_enhancement.py -k "test_set_stop_loss_distance or test_set_take_profit_distance or test_set_price_alert or test_cancel_order" -x -v 2>&1 | tail -20`
Expected: All PASS

- [ ] **Step 9: Commit**

```bash
git add src/agent/tools_execution.py src/agent/trader.py tests/test_tool_enhancement.py
git commit -m "feat: SL/TP distance %, set_price_alert disabled check, cancel_order tool"
```

---

### Task 11: New Tools — get_active_alerts + get_performance

**Files:**
- Modify: `src/agent/tools_perception.py` (add 2 functions)
- Modify: `src/agent/trader.py` (register 2 tools)
- Test: `tests/test_tool_enhancement.py` (append)

- [ ] **Step 1: Add tests**

Append to `tests/test_tool_enhancement.py`:

```python
# --- Task 11: New tools ---

async def test_get_active_alerts_with_data():
    from src.agent.tools_perception import get_active_alerts

    deps = _make_deps()
    deps.exchange.get_alert_params = MagicMock(return_value=(5.0, 60))
    deps.exchange.get_price_level_alerts = MagicMock(return_value=[
        {"id": "a1", "price": 75000.0, "direction": "above", "reasoning": "key resistance breakout"},
        {"id": "a2", "price": 74000.0, "direction": "below", "reasoning": "support breakdown"},
    ])

    result = await get_active_alerts(deps)
    assert "=== Price Alert Settings ===" in result
    assert "5.0%" in result
    assert "60min" in result
    assert "=== Active Price Level Alerts" in result
    assert "2/20" in result
    assert "75000" in result
    assert "above" in result
    assert "key resistance" in result


async def test_get_active_alerts_disabled():
    from src.agent.tools_perception import get_active_alerts

    deps = _make_deps()
    deps.exchange.get_alert_params = MagicMock(return_value=None)
    deps.exchange.get_price_level_alerts = MagicMock(return_value=[])

    result = await get_active_alerts(deps)
    assert "OFF" in result
    assert "0/20" in result


async def test_get_performance_with_trades(tmp_path):
    from src.agent.tools_perception import get_performance
    from src.storage.database import init_db, get_session
    from src.storage.models import Session, TradeAction
    from src.services.metrics import MetricsService

    engine = await init_db(f"sqlite+aiosqlite:///{tmp_path}/t11.db")
    async with get_session(engine) as session:
        session.add(Session(id="t11", name="test-perf", initial_balance=10000.0))
        session.add(TradeAction(
            session_id="t11", action="order_filled", order_id="o1",
            symbol="BTC/USDT:USDT", side="long", pnl=45.0, fee=0.5,
        ))
        session.add(TradeAction(
            session_id="t11", action="order_filled", order_id="o2",
            symbol="BTC/USDT:USDT", side="long", pnl=-22.0, fee=0.3,
        ))
        await session.commit()

    deps = _make_deps()
    deps.db_engine = engine
    deps.session_id = "t11"
    deps.initial_balance = 10000.0
    deps.metrics = MetricsService(engine=engine, session_id="t11", initial_balance=10000.0)
    deps.exchange.fetch_balance.return_value = Balance(10023.0, 9023.0, 1000.0)

    result = await get_performance(deps)
    assert "=== Trading Performance ===" in result
    assert "Total Trades: 2" in result
    assert "Win: 1" in result
    assert "Profit Factor:" in result
    assert "Max Drawdown:" in result
    assert "Best Trade:" in result
    assert "Total Fees:" in result
    await engine.dispose()


async def test_get_performance_empty(tmp_path):
    from src.agent.tools_perception import get_performance
    from src.storage.database import init_db, get_session
    from src.storage.models import Session
    from src.services.metrics import MetricsService

    engine = await init_db(f"sqlite+aiosqlite:///{tmp_path}/t11b.db")
    async with get_session(engine) as session:
        session.add(Session(id="t11b", name="test-perf-empty", initial_balance=10000.0))
        await session.commit()

    deps = _make_deps()
    deps.db_engine = engine
    deps.session_id = "t11b"
    deps.initial_balance = 10000.0
    deps.metrics = MetricsService(engine=engine, session_id="t11b", initial_balance=10000.0)
    deps.exchange.fetch_balance.return_value = Balance(10000.0, 10000.0, 0.0)

    result = await get_performance(deps)
    assert "No completed trades yet" in result
    await engine.dispose()
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd /Users/z/Z/TradeBot && python -m pytest tests/test_tool_enhancement.py::test_get_active_alerts_with_data tests/test_tool_enhancement.py::test_get_performance_with_trades -x -v 2>&1 | tail -15`
Expected: FAIL — functions don't exist

- [ ] **Step 3: Add get_active_alerts**

Add to `src/agent/tools_perception.py`:

```python
async def get_active_alerts(deps: TradingDeps) -> str:
    """Get current alert configuration: volatility alert params and active price level alerts."""
    sections: list[str] = []

    # Volatility alert settings
    params = deps.exchange.get_alert_params()
    if params is not None:
        threshold, window = params
        sections.append(f"=== Price Alert Settings ===\nVolatility alert: {threshold}% in {window}min window")
    else:
        sections.append("=== Price Alert Settings ===\nVolatility alert: OFF")

    # Price level alerts
    alerts = deps.exchange.get_price_level_alerts()
    count = len(alerts)
    lines = [f"=== Active Price Level Alerts ({count}/20) ==="]
    if alerts:
        for i, a in enumerate(alerts, 1):
            lines.append(f'  #{i} {a["direction"]} {a["price"]:.2f} — "{a["reasoning"]}"')
    else:
        lines.append("  No active alerts.")
    sections.append("\n".join(lines))

    return "\n\n".join(sections)
```

- [ ] **Step 4: Add get_performance**

Add to `src/agent/tools_perception.py`:

```python
async def get_performance(deps: TradingDeps) -> str:
    """Get detailed trading performance statistics.
    Use for reviewing overall results and evaluating strategy effectiveness."""
    balance = await deps.exchange.fetch_balance()
    ret_usdt = balance.total_usdt - deps.initial_balance
    ret_pct = (ret_usdt / deps.initial_balance) * 100

    if deps.metrics is None:
        return (
            f"=== Trading Performance ===\n"
            f"Initial Balance: {deps.initial_balance:.2f} USDT\n"
            f"Current Balance: {balance.total_usdt:.2f} USDT\n"
            f"Return: {ret_pct:+.2f}% ({ret_usdt:+.2f} USDT)\n\n"
            f"No metrics service available."
        )

    metrics = await deps.metrics.compute()

    if metrics.total_trades == 0:
        return (
            f"=== Trading Performance ===\n"
            f"Initial Balance: {deps.initial_balance:.2f} USDT\n"
            f"Current Balance: {balance.total_usdt:.2f} USDT\n"
            f"Return: {ret_pct:+.2f}% ({ret_usdt:+.2f} USDT)\n\n"
            f"No completed trades yet."
        )

    return (
        f"=== Trading Performance ===\n"
        f"Initial Balance: {deps.initial_balance:.2f} USDT\n"
        f"Current Balance: {balance.total_usdt:.2f} USDT\n"
        f"Total Return: {ret_pct:+.2f}% ({ret_usdt:+.2f} USDT) (incl. unrealized)\n"
        f"Realized PnL: {metrics.total_pnl:+.2f} USDT (gross, before fees)\n"
        f"Total Fees: -{metrics.total_fees:.2f} USDT\n\n"
        f"Total Trades: {metrics.total_trades} | Win: {metrics.winning_trades} "
        f"({metrics.win_rate:.1%}) | Loss: {metrics.losing_trades}\n"
        f"Avg Win: {metrics.avg_win:+.2f} USDT | Avg Loss: {metrics.avg_loss:.2f} USDT\n"
        f"Profit Factor: {metrics.profit_factor:.2f}\n"
        f"Max Drawdown: {metrics.max_drawdown_pct:.1f}%\n"
        f"Best Trade: {metrics.best_trade:+.2f} USDT | Worst Trade: {metrics.worst_trade:.2f} USDT"
    )
```

- [ ] **Step 5: Register both tools in trader.py**

Add in the Perception Tools section:

```python
    @agent.tool
    async def get_active_alerts(ctx: RunContext[TradingDeps]) -> str:
        """Get current alert configuration: volatility alert params and active price level alerts."""
        from src.agent.tools_perception import get_active_alerts as _impl

        return await _impl(ctx.deps)

    @agent.tool
    async def get_performance(ctx: RunContext[TradingDeps]) -> str:
        """Get detailed trading performance statistics. Use for reviewing overall results and evaluating strategy effectiveness."""
        from src.agent.tools_perception import get_performance as _impl

        return await _impl(ctx.deps)
```

- [ ] **Step 6: Run tests**

Run: `cd /Users/z/Z/TradeBot && python -m pytest tests/test_tool_enhancement.py -k "test_get_active_alerts or test_get_performance" -x -v 2>&1 | tail -20`
Expected: All PASS

- [ ] **Step 7: Commit**

```bash
git add src/agent/tools_perception.py src/agent/trader.py tests/test_tool_enhancement.py
git commit -m "feat: new tools — get_active_alerts + get_performance"
```

---

### Task 12: Update Existing Tests + Final Regression Check

**Files:**
- Modify: `tests/test_tools.py` (update MockDeps + broken tests)
- Test: Full suite

- [ ] **Step 1: Update MockDeps in test_tools.py**

In `tests/test_tools.py`, update the `MockDeps` dataclass to include new fields:

```python
@dataclass
class MockDeps:
    symbol: str
    timeframe: str
    market_data: AsyncMock
    exchange: AsyncMock
    technical: MagicMock
    memory: AsyncMock
    session_id: str = "test-session"
    db_engine: object = None
    approval_gate: object = None
    approval_enabled: bool = False
    wake_min_minutes: int = 1
    wake_max_minutes: int = 60
    set_next_wake_fn: object = None
    initial_balance: float = 10000.0
    metrics: object = None
```

- [ ] **Step 2: Update deps fixture**

Add any missing mock attributes to the `deps` fixture. The key additions:
- `d.exchange.get_alert_params = MagicMock(return_value=(5.0, 60))`
- `d.exchange.get_price_level_alerts = MagicMock(return_value=[])`

Also add to fixture setup:

```python
    d.market_data.get_ticker.return_value = Ticker(
        "BTC/USDT:USDT", 65000.0, 64999.0, 65001.0, 66000.0, 64000.0, 12345.6, 1712534400000
    )
```

(This is already there, but verify the fixture still works with the new tool implementations.)

- [ ] **Step 3: Fix any broken existing tests**

The main tests likely to break:
- `test_get_market_data` — old signature `get_market_data(deps, "BTC/USDT:USDT", "15m")` still works (positional args), but output format changed
- `test_get_position` — output format changed
- `test_get_account_balance` — output format changed
- `test_set_stop_loss` / `test_set_take_profit` — output format changed
- `test_set_price_alert` — now checks get_alert_params

Update these tests to match new output formats. For example:

```python
async def test_get_market_data(deps):
    from src.agent.tools_perception import get_market_data
    result = await get_market_data(deps, "BTC/USDT:USDT", "15m")
    assert "65000" in result
    assert "=== Ticker" in result

async def test_get_position(deps):
    from src.agent.tools_perception import get_position
    result = await get_position(deps, "BTC/USDT:USDT")
    assert "LONG" in result
    assert "64000" in result

async def test_get_account_balance(deps):
    from src.agent.tools_perception import get_account_balance
    result = await get_account_balance(deps)
    assert "10000" in result
    assert "Return" in result or "return" in result.lower()
```

- [ ] **Step 4: Run full test suite**

Run: `cd /Users/z/Z/TradeBot && python -m pytest --tb=short 2>&1 | tail -30`
Expected: All tests PASS

- [ ] **Step 5: Commit**

```bash
git add tests/test_tools.py
git commit -m "test: update existing tests for new tool output formats"
```

---

### Task 13: Final — Update trader.py Docstrings

**Files:**
- Modify: `src/agent/trader.py` (docstrings for updated tools)

- [ ] **Step 1: Update docstrings for enhanced tools**

In `src/agent/trader.py`, update these tool docstrings:

- `get_account_balance`: `"""Get account balance with return on initial capital."""`
- `get_open_orders`: `"""Get all pending orders with distance from current price."""`
- `get_trade_journal`: `"""Get trade journal — decision timeline with quick stats summary. Use for reviewing recent decisions and their outcomes."""`

- [ ] **Step 2: Run full test suite one final time**

Run: `cd /Users/z/Z/TradeBot && python -m pytest -x -v 2>&1 | tail -40`
Expected: All PASS

- [ ] **Step 3: Commit**

```bash
git add src/agent/trader.py
git commit -m "docs: update tool docstrings for enhanced tools"
```
