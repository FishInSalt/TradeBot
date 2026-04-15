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
