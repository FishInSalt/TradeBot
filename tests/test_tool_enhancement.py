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


async def test_get_market_data_truncated_data():
    """When exchange returns fewer candles than requested, display_count adapts."""
    from src.agent.tools_perception import get_market_data

    deps = _make_deps()
    # Only 70 rows returned (requested 100 = candle_count 50 + 50 warmup)
    n = 70
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
        "atr_14": 80.0, "volume_ratio": 1.0,
    }
    deps.technical.format_for_llm.return_value = "RSI(14): 50.00"

    result = await get_market_data(deps, candle_count=50)
    # display_count = max(10, 70-50) = 20
    assert "last 20" in result


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
