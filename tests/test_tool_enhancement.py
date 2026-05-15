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
    """BaseExchange stores alert_service via set_volatility_alert lazy create
    and clears it via cancel_volatility_alert."""
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
        async def cancel_order(self, order_id, symbol, is_algo: bool = False): ...  # noqa: ARG002
        async def fetch_funding_rate(self, symbol): ...
        async def fetch_open_interest_history(self, symbol, period="1h", limit=26): ...
        async def fetch_long_short_ratio(self, symbol): ...

        async def fetch_order_book(self, symbol, depth=20):
            from src.integrations.exchange.base import OrderBook, OrderBookLevel
            return OrderBook(symbol=symbol, bids=[OrderBookLevel(100.0, 1.0)], asks=[OrderBookLevel(101.0, 1.0)], timestamp=0)

        async def fetch_trades(self, symbol, limit=500):
            return []

        async def get_contract_size(self, symbol):
            return 1.0

        async def get_mark_price(self, symbol):
            return 0.0

    ex = _TestExchange()

    # No alert service → get_alert_params returns None
    assert ex.get_alert_params() is None

    # set_volatility_alert lazy-creates → get_alert_params returns the configured tuple
    ex.set_volatility_alert(threshold_pct=5.0, window_minutes=60, symbol="BTC/USDT:USDT")
    assert ex.get_alert_params() == (5.0, 60)

    # Second set updates in place
    ex.set_volatility_alert(threshold_pct=3.0, window_minutes=30, symbol="BTC/USDT:USDT")
    assert ex.get_alert_params() == (3.0, 30)

    # cancel returns to None
    ex.cancel_volatility_alert()
    assert ex.get_alert_params() is None


def test_base_set_volatility_alert_lazy_creates_when_none():
    """First call constructs PriceAlertService with the passed args."""
    from src.integrations.exchange.base import BaseExchange
    from src.services.price_alert import PriceAlertService

    class _TestExchange(BaseExchange):
        async def fetch_ticker(self, symbol): ...
        async def fetch_ohlcv(self, symbol, timeframe, limit=100): ...
        async def create_order(self, symbol, side, order_type, amount, price=None, params=None): ...
        async def fetch_balance(self): ...
        async def fetch_positions(self, symbol): ...
        async def set_leverage(self, symbol, leverage): ...
        def amount_to_precision(self, symbol, amount): ...
        async def close(self): ...
        async def fetch_order(self, order_id, symbol=None): ...
        async def fetch_open_orders(self, symbol): ...
        async def fetch_closed_orders(self, symbol, limit=20): ...
        async def cancel_order(self, order_id, symbol, is_algo: bool = False): ...  # noqa: ARG002
        async def fetch_funding_rate(self, symbol): ...
        async def fetch_open_interest_history(self, symbol, period="1h", limit=26): ...
        async def fetch_long_short_ratio(self, symbol): ...
        async def fetch_order_book(self, symbol, depth=20): ...
        async def fetch_trades(self, symbol, limit=500): return []
        async def get_contract_size(self, symbol): return 1.0
        async def get_mark_price(self, symbol): return 0.0

    ex = _TestExchange()
    assert ex._alert_service is None
    assert ex.get_alert_params() is None

    ex.set_volatility_alert(threshold_pct=2.0, window_minutes=30, symbol="BTC/USDT:USDT")

    assert isinstance(ex._alert_service, PriceAlertService)
    assert ex.get_alert_params() == (2.0, 30)


def test_base_set_volatility_alert_updates_when_exists():
    """Second call invokes update_params on the same instance and clears _ticks."""
    from src.integrations.exchange.base import BaseExchange

    class _TestExchange(BaseExchange):
        async def fetch_ticker(self, symbol): ...
        async def fetch_ohlcv(self, symbol, timeframe, limit=100): ...
        async def create_order(self, symbol, side, order_type, amount, price=None, params=None): ...
        async def fetch_balance(self): ...
        async def fetch_positions(self, symbol): ...
        async def set_leverage(self, symbol, leverage): ...
        def amount_to_precision(self, symbol, amount): ...
        async def close(self): ...
        async def fetch_order(self, order_id, symbol=None): ...
        async def fetch_open_orders(self, symbol): ...
        async def fetch_closed_orders(self, symbol, limit=20): ...
        async def cancel_order(self, order_id, symbol, is_algo: bool = False): ...  # noqa: ARG002
        async def fetch_funding_rate(self, symbol): ...
        async def fetch_open_interest_history(self, symbol, period="1h", limit=26): ...
        async def fetch_long_short_ratio(self, symbol): ...
        async def fetch_order_book(self, symbol, depth=20): ...
        async def fetch_trades(self, symbol, limit=500): return []
        async def get_contract_size(self, symbol): return 1.0
        async def get_mark_price(self, symbol): return 0.0

    ex = _TestExchange()
    ex.set_volatility_alert(threshold_pct=5.0, window_minutes=60, symbol="BTC/USDT:USDT")
    first_instance = ex._alert_service

    # Feed a tick to populate the rolling window
    ex._alert_service.check(50000.0, 1700000000000)
    assert len(ex._alert_service._ticks) == 1

    # Second call must update in place AND clear ticks
    ex.set_volatility_alert(threshold_pct=2.0, window_minutes=30, symbol="BTC/USDT:USDT")

    assert ex._alert_service is first_instance  # same instance
    assert ex.get_alert_params() == (2.0, 30)
    assert len(ex._alert_service._ticks) == 0   # window reset


def test_base_cancel_volatility_alert_clears_to_none():
    """cancel_volatility_alert sets _alert_service back to None."""
    from src.integrations.exchange.base import BaseExchange

    class _TestExchange(BaseExchange):
        async def fetch_ticker(self, symbol): ...
        async def fetch_ohlcv(self, symbol, timeframe, limit=100): ...
        async def create_order(self, symbol, side, order_type, amount, price=None, params=None): ...
        async def fetch_balance(self): ...
        async def fetch_positions(self, symbol): ...
        async def set_leverage(self, symbol, leverage): ...
        def amount_to_precision(self, symbol, amount): ...
        async def close(self): ...
        async def fetch_order(self, order_id, symbol=None): ...
        async def fetch_open_orders(self, symbol): ...
        async def fetch_closed_orders(self, symbol, limit=20): ...
        async def cancel_order(self, order_id, symbol, is_algo: bool = False): ...  # noqa: ARG002
        async def fetch_funding_rate(self, symbol): ...
        async def fetch_open_interest_history(self, symbol, period="1h", limit=26): ...
        async def fetch_long_short_ratio(self, symbol): ...
        async def fetch_order_book(self, symbol, depth=20): ...
        async def fetch_trades(self, symbol, limit=500): return []
        async def get_contract_size(self, symbol): return 1.0
        async def get_mark_price(self, symbol): return 0.0

    ex = _TestExchange()
    ex.set_volatility_alert(threshold_pct=3.0, window_minutes=15, symbol="BTC/USDT:USDT")
    assert ex._alert_service is not None

    ex.cancel_volatility_alert()
    assert ex._alert_service is None
    assert ex.get_alert_params() is None


def test_base_cancel_volatility_alert_idempotent_when_already_none():
    """cancel_volatility_alert is a no-op when _alert_service is already None."""
    from src.integrations.exchange.base import BaseExchange

    class _TestExchange(BaseExchange):
        async def fetch_ticker(self, symbol): ...
        async def fetch_ohlcv(self, symbol, timeframe, limit=100): ...
        async def create_order(self, symbol, side, order_type, amount, price=None, params=None): ...
        async def fetch_balance(self): ...
        async def fetch_positions(self, symbol): ...
        async def set_leverage(self, symbol, leverage): ...
        def amount_to_precision(self, symbol, amount): ...
        async def close(self): ...
        async def fetch_order(self, order_id, symbol=None): ...
        async def fetch_open_orders(self, symbol): ...
        async def fetch_closed_orders(self, symbol, limit=20): ...
        async def cancel_order(self, order_id, symbol, is_algo: bool = False): ...  # noqa: ARG002
        async def fetch_funding_rate(self, symbol): ...
        async def fetch_open_interest_history(self, symbol, period="1h", limit=26): ...
        async def fetch_long_short_ratio(self, symbol): ...
        async def fetch_order_book(self, symbol, depth=20): ...
        async def fetch_trades(self, symbol, limit=500): return []
        async def get_contract_size(self, symbol): return 1.0
        async def get_mark_price(self, symbol): return 0.0

    ex = _TestExchange()
    assert ex._alert_service is None
    ex.cancel_volatility_alert()  # must not raise
    assert ex._alert_service is None


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
        async def cancel_order(self, order_id, symbol, is_algo: bool = False): ...  # noqa: ARG002
        async def fetch_funding_rate(self, symbol): ...
        async def fetch_open_interest_history(self, symbol, period="1h", limit=26): ...
        async def fetch_long_short_ratio(self, symbol): ...

        async def fetch_order_book(self, symbol, depth=20):
            from src.integrations.exchange.base import OrderBook, OrderBookLevel
            return OrderBook(symbol=symbol, bids=[OrderBookLevel(100.0, 1.0)], asks=[OrderBookLevel(101.0, 1.0)], timestamp=0)

        async def fetch_trades(self, symbol, limit=500):
            return []

        async def get_contract_size(self, symbol):
            return 1.0

        async def get_mark_price(self, symbol):
            return 0.0

    ex = _TestExchange()
    assert ex.get_price_level_alerts() == []

    ex.add_price_level_alert(75000.0, "above", "BTC/USDT:USDT", "resistance")
    alerts = ex.get_price_level_alerts()
    assert len(alerts) == 1
    assert alerts[0]["price"] == 75000.0

    # Verify it's a copy (mutating returned list doesn't affect internal state)
    alerts.pop()
    assert len(ex.get_price_level_alerts()) == 1


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


def test_simulated_exchange_inherits_volatility_alert_methods():
    """SimulatedExchange should NOT override set_volatility_alert / cancel_volatility_alert."""
    from src.integrations.exchange.simulated import SimulatedExchange
    from src.integrations.exchange.base import BaseExchange
    assert SimulatedExchange.set_volatility_alert is BaseExchange.set_volatility_alert
    assert SimulatedExchange.cancel_volatility_alert is BaseExchange.cancel_volatility_alert


def test_okx_exchange_inherits_volatility_alert_methods():
    """OKXExchange should NOT override set_volatility_alert / cancel_volatility_alert."""
    from src.integrations.exchange.okx import OKXExchange
    from src.integrations.exchange.base import BaseExchange
    assert OKXExchange.set_volatility_alert is BaseExchange.set_volatility_alert
    assert OKXExchange.cancel_volatility_alert is BaseExchange.cancel_volatility_alert


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
    fee_rate: float = 0.0005
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
    d.exchange.register_close_order_entry = MagicMock()
    d.exchange.get_alert_params = MagicMock(return_value=(5.0, 60))
    d.exchange.get_price_level_alerts = MagicMock(return_value=[])
    d.exchange.cancel_order = AsyncMock()
    d.exchange.set_leverage = AsyncMock()
    d.exchange.amount_to_precision = MagicMock(side_effect=lambda sym, amt: round(amt, 3))
    d.exchange.create_order = AsyncMock(return_value=Order(
        "o1", "BTC/USDT:USDT", "buy", "market", 0.01, 65000.0, "closed",
    ))
    d.exchange.get_contract_size = AsyncMock(return_value=1.0)
    d.exchange.get_mark_price = AsyncMock(return_value=0.0)
    d.exchange.algo_trigger_reference = "last"
    # Default: OHLCV fetch fails → _safe_ohlcv returns None → atr_1h stays None → ATR-multiple suffix omitted.
    # Tests that want to exercise the ATR path should override this per-test.
    d.market_data.get_ohlcv_dataframe = AsyncMock(side_effect=Exception("default: no OHLCV in _make_deps"))
    return d


async def test_get_market_data_four_segments():
    from src.agent.tools_perception import get_market_data

    deps = _make_deps()
    # Create a realistic DataFrame
    n = 100
    deps.market_data.get_ohlcv_dataframe = AsyncMock()
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
        "atr_14": 85.2,
    }
    deps.technical.format_for_llm.return_value = "RSI(14): 52.88\nMA(20): 74750.00 (price vs MA: +0.2%)"

    result = await get_market_data(deps)
    # Four segment headers
    assert "=== Ticker" in result
    assert "=== Technical Indicators" in result
    assert "=== Market Context ===" in result
    assert "=== Recent Candles" in result
    # Ticker data
    assert "74880" in result
    assert "74870" in result  # bid
    # Market context — ATR and last-bar volume present (iter w2r2-next-d Task 4
    # replaced the old "Volume:" label with "Last bar vol: X (Y× SMA(20) avg)").
    assert "ATR" in result
    assert "Last bar vol:" in result
    assert "SMA(20) avg" in result  # volume ratio label


async def test_get_market_data_default_params():
    """get_market_data uses deps.symbol and deps.timeframe when called without args."""
    from src.agent.tools_perception import get_market_data

    deps = _make_deps()
    n = 100
    deps.market_data.get_ohlcv_dataframe = AsyncMock()
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
        "atr_14": 80.0,
    }
    deps.technical.format_for_llm.return_value = "RSI(14): 50.00"

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
    deps.market_data.get_ohlcv_dataframe = AsyncMock()
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
        "atr_14": 850.0,
    }
    deps.technical.format_for_llm.return_value = "RSI(14): 50.00"

    result = await get_market_data(deps, timeframe="1h")
    # ATR line should exist with value and percentage
    assert "ATR(14): 850.00" in result
    assert "1h candles" in result
    # Should NOT have qualitative labels
    assert "low volatility" not in result
    assert "moderate" not in result
    assert "high volatility" not in result


async def test_get_market_data_5m_atr_no_qualitative_label():
    """5m timeframe must NOT emit ATR qualitative labels — symmetric with 1h.

    Regression guard: previously the 5m branch rendered
    "low volatility / moderate / high volatility" based on pct thresholds.
    N5 cleanup removes this; this test prevents label regrowth.
    """
    from src.agent.tools_perception import get_market_data

    deps = _make_deps()
    deps.timeframe = "5m"
    n = 100
    deps.market_data.get_ohlcv_dataframe = AsyncMock()
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
        "atr_14": 85.2,
    }
    deps.technical.format_for_llm.return_value = "RSI(14): 50.00"

    result = await get_market_data(deps, timeframe="5m")
    assert "ATR(14): 85.20" in result
    assert "5m candles" in result
    # NO qualitative labels
    assert "low volatility" not in result
    assert "moderate" not in result
    assert "high volatility" not in result
    # Volume label also gone (Task 6 removes it)
    assert "above normal" not in result
    # "normal" alone is too common to grep safely; use "— normal" marker
    assert "— normal" not in result


async def test_get_market_data_truncated_data():
    """When exchange returns fewer candles than requested, display_count adapts.

    iter w2r2-next-d Task 4 changed two things:
      - _closed_bars now strips the in-progress bar before display: with n=70
        raw rows, available_closed = 69 (not 70).
      - display_count formula unchanged: max(10, available_closed - 50).
        With available_closed = 69, display_count = 19.
    """
    from src.agent.tools_perception import get_market_data

    deps = _make_deps()
    # Only 70 rows returned (requested 100 = candle_count 50 + 50 warmup)
    n = 70
    deps.market_data.get_ohlcv_dataframe = AsyncMock()
    deps.market_data.get_ohlcv_dataframe.return_value = pd.DataFrame({
        "timestamp": [1000 + i * 300000 for i in range(n)],
        "open": np.full(n, 74800.0), "high": np.full(n, 74900.0),
        "low": np.full(n, 74700.0), "close": np.full(n, 74880.0),
        "volume": np.full(n, 125.0),
    })
    deps.technical.compute_indicators.return_value = {
        "rsi_14": 50.0, "ma_20": 74750.0, "ma_50": None,
        "macd": 0.0, "macd_signal": 0.0, "macd_histogram": 0.0,
        "bb_upper": 75000.0, "bb_middle": 74750.0, "bb_lower": 74500.0,
        "atr_14": 80.0,
    }
    deps.technical.format_for_llm.return_value = "RSI(14): 50.00"

    result = await get_market_data(deps, candle_count=50)
    # available_closed = 70 - 1 (in-progress strip) = 69
    # 69 < 50 + 50 = 100 → display_count = max(10, 69-50) = 19
    assert "last 19" in result


async def test_get_market_data_candle_count_clamp():
    """candle_count is clamped to 10-80."""
    from src.agent.tools_perception import get_market_data

    deps = _make_deps()
    n = 100
    deps.market_data.get_ohlcv_dataframe = AsyncMock()
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
        "atr_14": 80.0,
    }
    deps.technical.format_for_llm.return_value = "RSI(14): 50.00"

    result = await get_market_data(deps, candle_count=5)
    # candle_count clamped to 10 → fetch_limit = max(10+50, 100) = 100
    assert deps.market_data.get_ohlcv_dataframe.call_args.kwargs["limit"] == 100
    # Output should show "last 10" (clamped from 5)
    assert "last 10" in result


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
    deps.exchange.get_mark_price = AsyncMock(return_value=74500.0)

    result = await get_position(deps)
    # iter-tool-opt-as-of-header: position header includes inline fetch timestamp
    import re as _re
    assert _re.search(
        r"=== Position \(BTC/USDT:USDT @ \d{2}:\d{2}:\d{2} UTC\) ===",
        result,
    ), result[:200]
    assert "Side: Long" in result
    assert "74761.10" in result or "74,761.10" in result
    # PnL percentage of initial capital
    assert "% of initial capital" in result.lower() or "of initial capital" in result
    # Liquidation distance
    assert "away" in result.lower()
    # Duration
    assert "Duration" in result or "duration" in result.lower() or "min" in result.lower()
    # New Iter 2 fields (Task 10 enhancement) — R2-8c sectioned form
    assert "=== Risk Exposure ===" in result
    assert "Notional" in result or "notional" in result.lower()
    assert "=== Exit Orders ===" in result


async def test_get_position_created_at_none():
    """OKX mode: created_at is None → Duration shows N/A."""
    from src.agent.tools_perception import get_position

    deps = _make_deps()
    deps.exchange.fetch_positions.return_value = [
        Position("BTC/USDT:USDT", "long", 0.001, 74761.10, -19.09, 3, 50200.0,
                 created_at=None),
    ]
    deps.market_data.get_ticker.return_value = Ticker(
        "BTC/USDT:USDT", 74500.0, 74499.0, 74501.0, 75200.0, 73800.0, 100.0, 1000,
    )

    result = await get_position(deps)
    assert "Duration: N/A" in result


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
    # iter-tool-opt-as-of-header: Trade Journal header now includes "@ HH:MM:SS UTC"
    # (Performance Summary stays plain — only the per-task target sections are tagged).
    assert "=== Performance Summary ===" in result
    assert "Win:" in result
    import re as _re
    assert _re.search(
        r"=== Trade Journal \(@ \d{2}:\d{2}:\d{2} UTC\) ===",
        result,
    ), result[:300]
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
    assert "% from last price" in result or "from last price" in result
    # Market order should show "market price" without distance
    assert "market price" in result


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
    assert "% from last price" in result or "from last price" in result


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
    assert "% from last price" in result or "from last price" in result


async def test_set_price_volatility_alert_enabled():
    from src.agent.tools_execution import set_price_volatility_alert

    deps = _make_deps()
    deps.exchange.get_alert_params = MagicMock(return_value=(5.0, 60))
    deps.exchange.set_volatility_alert = MagicMock()

    result = await set_price_volatility_alert(deps, 3.0, 30, reasoning="tighter alert")
    assert "replaced:" in result.lower() or "3.0%" in result


async def test_set_price_volatility_alert_accepts_threshold_0_1():
    """R2-1 T4: tool layer accepts threshold_pct=0.1 (new lower bound)."""
    from src.agent.tools_execution import set_price_volatility_alert
    deps = _make_deps()
    deps.exchange.get_alert_params = MagicMock(return_value=None)
    deps.exchange.set_volatility_alert = MagicMock()
    result = await set_price_volatility_alert(deps, threshold_pct=0.1, window_minutes=15, reasoning="test")
    assert "Price volatility alert set" in result
    assert "threshold=0.1%" in result  # `%` 锁尾防 0.15 子串误命中（spec P2-1）


async def test_set_price_volatility_alert_rejects_threshold_below_0_1():
    """R2-1 T5: tool layer rejects threshold_pct=0.05 with new error message."""
    from src.agent.tools_execution import set_price_volatility_alert
    deps = _make_deps()
    deps.exchange.set_volatility_alert = MagicMock()
    result = await set_price_volatility_alert(deps, threshold_pct=0.05, window_minutes=15, reasoning="test")
    assert "Invalid threshold_pct: must be 0.1-50.0" in result


async def test_cancel_order_success():
    from src.agent.tools_execution import cancel_order

    deps = _make_deps()
    target_order = Order("o1", "BTC/USDT:USDT", "buy", "limit", 0.001, 72000.0, "open")
    deps.exchange.fetch_open_orders.return_value = [target_order]
    deps.exchange.cancel_order = AsyncMock()

    result = await cancel_order(deps, "o1", reasoning="no longer needed")
    assert "cancelled" in result.lower() or "Cancelled" in result or "cancel" in result.lower()
    deps.exchange.cancel_order.assert_called_once_with("o1", "BTC/USDT:USDT", is_algo=False)


async def test_cancel_order_not_found():
    from src.agent.tools_execution import cancel_order

    deps = _make_deps()
    deps.exchange.fetch_open_orders.return_value = []

    result = await cancel_order(deps, "nonexistent", reasoning="cleanup")
    # iter-tool-opt-cancel-order-idempotent: new ok-with-note format
    # aligned with cancel_price_level_alert (R2-Next-E PR #47).
    assert "no longer active" in result
    assert "already filled or cancelled" in result


async def test_cancel_order_idempotent_when_already_filled():
    """iter-tool-opt-cancel-order-idempotent: R2-Next-E (PR #47) alignment —
    cancel_order returns ok-with-note when order no longer in book
    (filled / cancelled by another path), matching cancel_price_level_alert.
    """
    from src.agent.tools_execution import cancel_order

    deps = _make_deps()
    # fetch_open_orders returns empty (target order already filled / cancelled).
    deps.exchange.fetch_open_orders.return_value = []

    result = await cancel_order(deps, "filled_order_id", reasoning="cleanup")

    # New ok-with-note format (idempotent, principle 6 state-not-exist).
    assert "Order filled_order_id no longer active" in result
    assert "(already filled or cancelled)" in result
    # Old reject-like format must be gone.
    assert "Order not found or already filled:" not in result


async def test_cancel_order_market_rejected():
    from src.agent.tools_execution import cancel_order

    deps = _make_deps()
    market_order = Order("o1", "BTC/USDT:USDT", "buy", "market", 0.001, None, "open")
    deps.exchange.fetch_open_orders.return_value = [market_order]

    result = await cancel_order(deps, "o1", reasoning="want to cancel")
    assert "Cannot cancel market" in result or "market" in result.lower()


# --- Task 11: New tools ---

async def test_get_active_alerts_with_data(monkeypatch):
    from src.agent.tools_perception import get_active_alerts

    monkeypatch.setattr("src.integrations.exchange.base.time.time", lambda: 1700000000.0)
    monkeypatch.setattr("src.agent.tools_perception.time.time", lambda: 1700000000.0)

    deps = _make_deps()
    deps.exchange.get_alert_params = MagicMock(return_value=(5.0, 60))
    deps.exchange.get_price_level_alerts = MagicMock(return_value=[
        {"id": "a1", "price": 75000.0, "direction": "above", "reasoning": "key resistance breakout",
         "created_at": 1700000000.0},
        {"id": "a2", "price": 74000.0, "direction": "below", "reasoning": "support breakdown",
         "created_at": 1700000000.0},
    ])

    result = await get_active_alerts(deps)
    # iter-tool-opt-as-of-header: first-section header now carries inline fetch timestamp
    import re as _re
    assert _re.search(
        r"=== Price Volatility Alert \(@ \d{2}:\d{2}:\d{2} UTC\) ===",
        result,
    ), result[:200]
    assert "5.0%" in result
    assert "60min" in result
    assert "=== Price Level Alerts" in result
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
    assert "Not set" in result
    assert "0/20" in result


def test_set_price_volatility_alert_in_registered_tool_names():
    """Drift guard (iter-10): set_price_volatility_alert renamed from set_price_alert.
    Hard rename — old name must be absent."""
    from src.agent.trader import REGISTERED_TOOL_NAMES

    assert "set_price_volatility_alert" in REGISTERED_TOOL_NAMES
    assert "set_price_alert" not in REGISTERED_TOOL_NAMES


async def test_get_active_alerts_section_headers_renamed(monkeypatch):
    """Drift guard (iter-10): section headers renamed from
    Price Alert Settings / Active Price Level Alerts
    to Price Volatility Alert / Price Level Alerts."""
    from src.agent.tools_perception import get_active_alerts

    monkeypatch.setattr("src.integrations.exchange.base.time.time", lambda: 1700000000.0)
    monkeypatch.setattr("src.agent.tools_perception.time.time", lambda: 1700000000.0)

    deps = _make_deps()
    deps.exchange.get_alert_params = MagicMock(return_value=(2.5, 30))
    deps.exchange.get_price_level_alerts = MagicMock(return_value=[
        {"id": "abc12345", "price": 75000.0, "direction": "above",
         "reasoning": "drift-guard fixture", "created_at": 1700000000.0},
    ])

    output = await get_active_alerts(deps)

    assert "=== Price Volatility Alert (@" in output
    assert "=== Price Level Alerts (1/20) (@" in output
    assert "Price Alert Settings" not in output
    assert "Active Price Level Alerts" not in output


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
    # iter-tool-opt-as-of-header: first-section header now carries inline fetch timestamp
    import re as _re
    assert _re.search(
        r"=== Trading Performance \(@ \d{2}:\d{2}:\d{2} UTC\) ===",
        result,
    ), result[:200]
    assert "Total Trades: 2" in result
    assert "Win: 1" in result
    assert "Profit Factor:" in result
    assert "Max Drawdown:" in result
    assert "Best Trade:" in result
    assert "Total Fees:" in result
    await engine.dispose()


async def test_get_performance_no_metrics_service():
    """get_performance handles deps.metrics=None gracefully."""
    from src.agent.tools_perception import get_performance

    deps = _make_deps()
    deps.metrics = None
    deps.exchange.fetch_balance.return_value = Balance(10000.0, 10000.0, 0.0)

    result = await get_performance(deps)
    assert "No metrics service available" in result


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


# --- Iter 2b T4: get_open_orders OCO merge rendering ---

from unittest.mock import AsyncMock, MagicMock

import pytest

from src.integrations.exchange.base import Order, Ticker


def _make_oco_deps(orders: list[Order], ticker_last: float = 70000.0):
    """OCO merge rendering deps factory (distinct from existing file helpers)."""
    deps = MagicMock()
    deps.symbol = "BTC/USDT:USDT"
    deps.exchange = MagicMock()
    deps.exchange.fetch_open_orders = AsyncMock(return_value=orders)
    deps.exchange.algo_trigger_reference = "last"
    deps.market_data = MagicMock()
    deps.market_data.get_ticker = AsyncMock(return_value=Ticker(
        symbol="BTC/USDT:USDT", last=ticker_last, bid=ticker_last - 1,
        ask=ticker_last + 1, high=ticker_last, low=ticker_last,
        base_volume=0.0, timestamp=0,
    ))
    return deps


@pytest.mark.asyncio
async def test_get_open_orders_merges_oco_into_single_line():
    from src.agent.tools_perception import get_open_orders
    oco_id = "algo_123"
    orders = [
        Order(id=oco_id, symbol="BTC/USDT:USDT", side="sell",
              order_type="stop", amount=1.0, price=60000.0,
              status="open", fee=None, is_algo=True),
        Order(id=oco_id, symbol="BTC/USDT:USDT", side="sell",
              order_type="take_profit", amount=1.0, price=80000.0,
              status="open", fee=None, is_algo=True),
    ]
    out = await get_open_orders(_make_oco_deps(orders))
    lines = out.splitlines()
    # Only one rendered row besides the header (R2-8c explicit section header)
    # iter-tool-opt-as-of-header: header now includes inline "@ HH:MM:SS UTC"
    import re as _re
    assert _re.fullmatch(
        r"=== Pending Orders \(@ \d{2}:\d{2}:\d{2} UTC\) ===",
        lines[0],
    ), lines[0]
    data_lines = [l for l in lines[1:] if l.strip()]
    assert len(data_lines) == 1, f"expected 1 merged OCO line, got {data_lines}"
    row = data_lines[0]
    assert "[OCO]" in row
    assert "stop" in row.lower()
    assert "tp" in row.lower()
    assert "algoId:" in row
    assert "cancel removes both legs" in row
    assert "60000.00" in row
    assert "80000.00" in row


@pytest.mark.asyncio
async def test_get_open_orders_non_oco_single_orders_separate_lines():
    from src.agent.tools_perception import get_open_orders
    orders = [
        Order(id="p1", symbol="BTC/USDT:USDT", side="buy",
              order_type="limit", amount=0.5, price=65000.0,
              status="open", is_algo=False),
        Order(id="s1", symbol="BTC/USDT:USDT", side="sell",
              order_type="stop", amount=0.5, price=60000.0,
              status="open", is_algo=True),  # single-leg conditional SL
    ]
    out = await get_open_orders(_make_oco_deps(orders))
    data_lines = [l for l in out.splitlines()[1:] if l.strip()]
    assert len(data_lines) == 2
    assert "[LIMIT]" in data_lines[0]
    assert "[STOP]" in data_lines[1]
    # no OCO merge tag
    assert "[OCO]" not in out


@pytest.mark.asyncio
async def test_get_open_orders_fact_only_no_banned_words():
    """N5 fact-only compliance regression — OCO merge line must not contain evaluative words."""
    from src.agent.tools_perception import get_open_orders
    orders = [
        Order(id="oco_1", symbol="BTC/USDT:USDT", side="sell",
              order_type="stop", amount=1.0, price=60000.0, status="open",
              is_algo=True),
        Order(id="oco_1", symbol="BTC/USDT:USDT", side="sell",
              order_type="take_profit", amount=1.0, price=80000.0, status="open",
              is_algo=True),
    ]
    out = await get_open_orders(_make_oco_deps(orders))
    banned = ("protective", "tight", "wide", "safe", "aggressive")
    for word in banned:
        assert word not in out.lower(), f"banned word '{word}' in output:\n{out}"


@pytest.mark.asyncio
async def test_get_open_orders_oco_handles_zero_ticker_without_dist_suffix():
    """ticker.last == 0 (abnormal fallback) must not ZeroDivisionError;
    dist suffix should be omitted but other fields kept. spec §2.4.1 regression."""
    from src.agent.tools_perception import get_open_orders
    orders = [
        Order(id="oco_z", symbol="BTC/USDT:USDT", side="sell",
              order_type="stop", amount=1.0, price=60000.0, status="open",
              is_algo=True),
        Order(id="oco_z", symbol="BTC/USDT:USDT", side="sell",
              order_type="take_profit", amount=1.0, price=80000.0, status="open",
              is_algo=True),
    ]
    out = await get_open_orders(_make_oco_deps(orders, ticker_last=0.0))
    # no crash + OCO structure preserved
    data_lines = [l for l in out.splitlines()[1:] if l.strip()]
    assert len(data_lines) == 1
    row = data_lines[0]
    assert "[OCO]" in row
    assert "60000.00" in row and "80000.00" in row
    # dist suffix must not appear
    assert "% from last price" not in row


# --- Task 7 (Iter 2b): tools_execution forwards is_algo to cancel_order ---

from src.integrations.exchange.base import Position


def _make_exec_deps(positions=None, open_orders=None, ticker_last=70000.0):
    deps = MagicMock()
    deps.symbol = "BTC/USDT:USDT"
    deps.session_id = "s1"
    deps.db_engine = None
    deps.approval_enabled = False
    deps.approval_gate = None
    deps.exchange = MagicMock()
    deps.exchange.fetch_positions = AsyncMock(return_value=positions or [])
    deps.exchange.fetch_open_orders = AsyncMock(return_value=open_orders or [])
    deps.exchange.cancel_order = AsyncMock(return_value=None)
    deps.exchange.create_order = AsyncMock(return_value=Order(
        id="new_order", symbol="BTC/USDT:USDT", side="sell",
        order_type="stop", amount=1.0, price=60000.0, status="open", is_algo=True,
    ))
    deps.market_data = MagicMock()
    deps.market_data.get_ticker = AsyncMock(return_value=Ticker(
        symbol="BTC/USDT:USDT", last=ticker_last, bid=ticker_last - 1,
        ask=ticker_last + 1, high=ticker_last, low=ticker_last,
        base_volume=0.0, timestamp=0,
    ))
    return deps


def _pos(side="long", contracts=1.0):
    return Position(symbol="BTC/USDT:USDT", side=side, contracts=contracts,
                    entry_price=70000.0, unrealized_pnl=0.0, leverage=10,
                    liquidation_price=None)


@pytest.mark.asyncio
async def test_set_stop_loss_forwards_is_algo_true_for_algo_sl():
    from src.agent.tools_execution import set_stop_loss
    old_sl = Order(id="algo_old", symbol="BTC/USDT:USDT", side="sell",
                   order_type="stop", amount=1.0, price=59000.0,
                   status="open", is_algo=True)
    deps = _make_exec_deps(positions=[_pos()], open_orders=[old_sl])
    await set_stop_loss(deps, price=60000.0, reasoning="tighten")
    call = deps.exchange.cancel_order.call_args
    is_algo_actual = (
        call.kwargs.get("is_algo") if "is_algo" in call.kwargs
        else (call.args[2] if len(call.args) > 2 else False)
    )
    assert is_algo_actual is True


@pytest.mark.asyncio
async def test_set_stop_loss_forwards_is_algo_false_for_sim_sl():
    from src.agent.tools_execution import set_stop_loss
    old_sl = Order(id="sim_old", symbol="BTC/USDT:USDT", side="sell",
                   order_type="stop", amount=1.0, price=59000.0,
                   status="open", is_algo=False)
    deps = _make_exec_deps(positions=[_pos()], open_orders=[old_sl])
    await set_stop_loss(deps, price=60000.0, reasoning="tighten")
    call = deps.exchange.cancel_order.call_args
    is_algo_actual = (
        call.kwargs.get("is_algo") if "is_algo" in call.kwargs
        else (call.args[2] if len(call.args) > 2 else False)
    )
    assert is_algo_actual is False


@pytest.mark.asyncio
async def test_set_take_profit_forwards_is_algo_true_for_algo_tp():
    from src.agent.tools_execution import set_take_profit
    old_tp = Order(id="algo_tp", symbol="BTC/USDT:USDT", side="sell",
                   order_type="take_profit", amount=1.0, price=80000.0,
                   status="open", is_algo=True)
    deps = _make_exec_deps(positions=[_pos()], open_orders=[old_tp])
    await set_take_profit(deps, price=81000.0, reasoning="bump")
    call = deps.exchange.cancel_order.call_args
    is_algo_actual = (
        call.kwargs.get("is_algo") if "is_algo" in call.kwargs
        else (call.args[2] if len(call.args) > 2 else False)
    )
    assert is_algo_actual is True


@pytest.mark.asyncio
async def test_set_take_profit_forwards_is_algo_false_for_sim_tp():
    from src.agent.tools_execution import set_take_profit
    old_tp = Order(id="sim_tp", symbol="BTC/USDT:USDT", side="sell",
                   order_type="take_profit", amount=1.0, price=80000.0,
                   status="open", is_algo=False)
    deps = _make_exec_deps(positions=[_pos()], open_orders=[old_tp])
    await set_take_profit(deps, price=81000.0, reasoning="bump")
    call = deps.exchange.cancel_order.call_args
    is_algo_actual = (
        call.kwargs.get("is_algo") if "is_algo" in call.kwargs
        else (call.args[2] if len(call.args) > 2 else False)
    )
    assert is_algo_actual is False


@pytest.mark.asyncio
async def test_cancel_order_tool_routes_is_algo_true_for_algo_order():
    from src.agent.tools_execution import cancel_order
    target = Order(id="algo_xyz", symbol="BTC/USDT:USDT", side="sell",
                   order_type="stop", amount=1.0, price=60000.0,
                   status="open", is_algo=True)
    deps = _make_exec_deps(open_orders=[target])
    await cancel_order(deps, order_id="algo_xyz", reasoning="stale")
    call = deps.exchange.cancel_order.call_args
    is_algo_actual = (
        call.kwargs.get("is_algo") if "is_algo" in call.kwargs
        else (call.args[2] if len(call.args) > 2 else False)
    )
    assert is_algo_actual is True


@pytest.mark.asyncio
async def test_cancel_order_tool_routes_is_algo_false_for_plain_order():
    from src.agent.tools_execution import cancel_order
    target = Order(id="plain_abc", symbol="BTC/USDT:USDT", side="buy",
                   order_type="limit", amount=0.5, price=65000.0,
                   status="open", is_algo=False)
    deps = _make_exec_deps(open_orders=[target])
    await cancel_order(deps, order_id="plain_abc", reasoning="stale")
    call = deps.exchange.cancel_order.call_args
    is_algo_actual = (
        call.kwargs.get("is_algo") if "is_algo" in call.kwargs
        else (call.args[2] if len(call.args) > 2 else False)
    )
    assert is_algo_actual is False


# --- iter-tool-opt-open-orders-distance-pts: distance shows % + pts ---
# First principle-2 implicit-fact promote per
# docs/superpowers/principles/tool-design-principles.md §2 — narrative ≥3
# hand-calc threshold met (sim #8 ≥7 cycles compute "X pts from current").


@pytest.mark.asyncio
async def test_get_open_orders_distance_shows_percent_and_pts():
    """Single (non-OCO) order: distance suffix shows both % and pts.

    iter-tool-opt-open-orders-distance-pts: D promote per principle 2
    narrative ≥7 hand-calc (e.g. cycle dc9e15a7 "SL at 81,920 is only
    ~105 pts from current").

    current = 82175.0; limit buy @ 82750.00
    expected dist = (82750-82175)/82175*100 = +0.6996... → +0.70%
    expected pts  = 82750 - 82175 = +575.0
    """
    from src.agent.tools_perception import get_open_orders
    orders = [
        Order(id="p1", symbol="BTC/USDT:USDT", side="buy",
              order_type="limit", amount=0.001, price=82750.0,
              status="open", is_algo=False),
    ]
    out = await get_open_orders(_make_oco_deps(orders, ticker_last=82175.0))
    assert "@ 82750.00 (+0.70% / +575.0 pts from last price)" in out, (
        f"distance suffix missing or wrong format:\n{out}"
    )


@pytest.mark.asyncio
async def test_get_open_orders_single_order_negative_pts():
    """Single stop order below current: pts must be negative (signed)."""
    from src.agent.tools_perception import get_open_orders
    # current = 82175.0; sell stop @ 81600.00
    # dist = (81600-82175)/82175*100 = -575/82175*100 = -0.6996... → -0.70%
    # pts  = -575.0
    orders = [
        Order(id="s1", symbol="BTC/USDT:USDT", side="sell",
              order_type="stop", amount=0.001, price=81600.0,
              status="open", is_algo=True),
    ]
    out = await get_open_orders(_make_oco_deps(orders, ticker_last=82175.0))
    assert "@ 81600.00 (-0.70% / -575.0 pts from last price)" in out, (
        f"signed pts suffix missing:\n{out}"
    )


@pytest.mark.asyncio
async def test_get_open_orders_oco_shows_percent_and_pts():
    """OCO 双 leg 各自显示 % + pts (signed both sides).

    current = 82200.0
    SL @ 81920 → dist = -0.3406...% → -0.34%, pts = -280.0
    TP @ 83000 → dist = +0.9732...% → +0.97%, pts = +800.0
    """
    from src.agent.tools_perception import get_open_orders
    oco_id = "algo_pts_1"
    orders = [
        Order(id=oco_id, symbol="BTC/USDT:USDT", side="sell",
              order_type="stop", amount=1.0, price=81920.0,
              status="open", fee=None, is_algo=True),
        Order(id=oco_id, symbol="BTC/USDT:USDT", side="sell",
              order_type="take_profit", amount=1.0, price=83000.0,
              status="open", fee=None, is_algo=True),
    ]
    out = await get_open_orders(_make_oco_deps(orders, ticker_last=82200.0))
    assert "stop 81920.00 (-0.34% / -280.0 pts from last price)" in out, (
        f"OCO SL leg suffix missing:\n{out}"
    )
    assert "tp 83000.00 (+0.97% / +800.0 pts from last price)" in out, (
        f"OCO TP leg suffix missing:\n{out}"
    )
