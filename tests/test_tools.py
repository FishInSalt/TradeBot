import pytest
from dataclasses import dataclass
from unittest.mock import AsyncMock, MagicMock
import pandas as pd
import numpy as np
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


@pytest.fixture
def deps():
    d = MockDeps(
        symbol="BTC/USDT:USDT",
        timeframe="15m",
        market_data=AsyncMock(),
        exchange=AsyncMock(),
        technical=MagicMock(),
        memory=AsyncMock(),
    )
    d.market_data.get_ticker.return_value = Ticker(
        "BTC/USDT:USDT", 65000.0, 64999.0, 65001.0, 66000.0, 64000.0, 12345.6, 1712534400000
    )
    d.market_data.get_ohlcv_dataframe.return_value = pd.DataFrame(
        {
            "close": np.full(50, 65000.0),
            "open": np.full(50, 65000.0),
            "high": np.full(50, 65500.0),
            "low": np.full(50, 64500.0),
            "volume": np.full(50, 1000.0),
            "timestamp": range(50),
        }
    )
    d.technical.compute_indicators.return_value = {"rsi_14": 55.0}
    d.technical.format_for_llm.return_value = "RSI(14): 55.0"
    d.exchange.fetch_balance.return_value = Balance(10000.0, 8000.0, 2000.0)
    d.exchange.fetch_positions.return_value = [
        Position("BTC/USDT:USDT", "long", 0.01, 64000.0, 10.0, 3, 55000.0)
    ]
    d.exchange.create_order.return_value = Order(
        "o1", "BTC/USDT:USDT", "buy", "market", 0.01, 65000.0, "closed"
    )
    d.exchange.set_leverage = AsyncMock()
    d.exchange.amount_to_precision = MagicMock(side_effect=lambda sym, amt: round(amt, 3))
    d.exchange.fetch_open_orders = AsyncMock(return_value=[])
    d.exchange.cancel_order = AsyncMock()
    d.exchange.has_pending_market_order = MagicMock(return_value=False)
    d.memory.format_for_prompt.return_value = "No memories."
    return d


async def test_get_market_data(deps):
    from src.agent.tools_perception import get_market_data

    result = await get_market_data(deps, "BTC/USDT:USDT", "15m")
    assert "65000" in result
    assert "RSI" in result


async def test_get_position(deps):
    from src.agent.tools_perception import get_position

    result = await get_position(deps, "BTC/USDT:USDT")
    assert "long" in result.lower()
    assert "64000" in result


async def test_get_account_balance(deps):
    from src.agent.tools_perception import get_account_balance

    result = await get_account_balance(deps)
    assert "10000" in result


async def test_get_memories(deps):
    from src.agent.tools_perception import get_memories
    result = await get_memories(deps)
    assert "No memories" in result


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


async def test_get_trade_journal_empty(deps):
    from src.agent.tools_perception import get_trade_journal
    result = await get_trade_journal(deps)
    assert "no trade journal" in result.lower()


async def test_get_trade_journal_with_entries(tmp_path):
    """Test journal formatting with real DB entries and order lookup."""
    from src.storage.database import init_db, get_session
    from src.storage.models import Session, TradeAction
    from src.agent.tools_perception import get_trade_journal
    from unittest.mock import AsyncMock, MagicMock

    engine = await init_db(f"sqlite+aiosqlite:///{tmp_path}/journal_test.db")
    async with get_session(engine) as session:
        session.add(Session(id="s1", name="journal-test", initial_balance=100.0))
        await session.commit()
        session.add(TradeAction(
            session_id="s1", action="open_position", order_id="o1",
            symbol="BTC/USDT:USDT", side="long", reasoning="RSI oversold",
        ))
        session.add(TradeAction(
            session_id="s1", action="order_filled", order_id="o1",
            symbol="BTC/USDT:USDT", side="long", trigger_reason="market",
            reasoning="(exchange: market order filled @ 60200)",
        ))
        await session.commit()

    mock_deps = MagicMock()
    mock_deps.db_engine = engine
    mock_deps.session_id = "s1"
    mock_deps.symbol = "BTC/USDT:USDT"
    mock_deps.exchange = AsyncMock()
    mock_deps.exchange.fetch_order.return_value = Order(
        "o1", "BTC/USDT:USDT", "buy", "market", 0.001, 60200.0, "closed", fee=0.03
    )

    result = await get_trade_journal(mock_deps)
    assert "open_position" in result
    assert "order_filled" in result
    assert "60200" in result
    assert "RSI oversold" in result
    await engine.dispose()


async def test_get_trade_journal_order_fetch_failure(tmp_path):
    """Journal should work even if order fetch fails."""
    from src.storage.database import init_db, get_session
    from src.storage.models import Session, TradeAction
    from src.agent.tools_perception import get_trade_journal
    from unittest.mock import AsyncMock, MagicMock

    engine = await init_db(f"sqlite+aiosqlite:///{tmp_path}/journal_fail.db")
    async with get_session(engine) as session:
        session.add(Session(id="s1", name="fail-test", initial_balance=100.0))
        await session.commit()
        session.add(TradeAction(
            session_id="s1", action="open_position", order_id="o-fail",
            symbol="BTC/USDT:USDT", side="long", reasoning="test",
        ))
        await session.commit()

    mock_deps = MagicMock()
    mock_deps.db_engine = engine
    mock_deps.session_id = "s1"
    mock_deps.symbol = "BTC/USDT:USDT"
    mock_deps.exchange = AsyncMock()
    mock_deps.exchange.fetch_order.side_effect = ValueError("not found")

    result = await get_trade_journal(mock_deps)
    assert "open_position" in result
    assert "test" in result
    await engine.dispose()


async def test_set_price_alert_valid(deps):
    """set_price_alert 参数合法时应调用 exchange.update_alert_params。"""
    from src.agent.tools_execution import set_price_alert
    deps.exchange.update_alert_params = MagicMock()
    result = await set_price_alert(deps, 2.0, 5, reasoning="high volatility")
    assert "updated" in result.lower() or "set" in result.lower()
    deps.exchange.update_alert_params.assert_called_once_with(2.0, 5)


async def test_set_price_alert_threshold_too_low(deps):
    """threshold_pct < 0.5 时应返回错误，不调用 update。"""
    from src.agent.tools_execution import set_price_alert
    deps.exchange.update_alert_params = MagicMock()
    result = await set_price_alert(deps, 0.1, 5, reasoning="test")
    assert "error" in result.lower() or "invalid" in result.lower() or "must be" in result.lower()
    deps.exchange.update_alert_params.assert_not_called()


async def test_set_price_alert_threshold_too_high(deps):
    """threshold_pct > 50 时应返回错误。"""
    from src.agent.tools_execution import set_price_alert
    deps.exchange.update_alert_params = MagicMock()
    result = await set_price_alert(deps, 55.0, 5, reasoning="test")
    assert "error" in result.lower() or "invalid" in result.lower() or "must be" in result.lower()
    deps.exchange.update_alert_params.assert_not_called()


async def test_set_price_alert_window_out_of_range(deps):
    """window_minutes 超出 1-240 范围时应返回错误。"""
    from src.agent.tools_execution import set_price_alert
    deps.exchange.update_alert_params = MagicMock()
    # Lower bound
    result = await set_price_alert(deps, 3.0, 0, reasoning="test")
    assert "error" in result.lower() or "invalid" in result.lower() or "must be" in result.lower()
    deps.exchange.update_alert_params.assert_not_called()
    # Upper bound
    result = await set_price_alert(deps, 3.0, 250, reasoning="test")
    assert "error" in result.lower() or "invalid" in result.lower() or "must be" in result.lower()
    deps.exchange.update_alert_params.assert_not_called()


async def test_add_price_level_alert_success(deps):
    """add_price_level_alert should call exchange and return confirmation."""
    from src.agent.tools_execution import add_price_level_alert
    deps.exchange.add_price_level_alert = MagicMock(return_value="abc123")
    deps.exchange._latest_price = None
    result = await add_price_level_alert(deps, 58000.0, "below", reasoning="support level")
    assert "abc123" in result
    assert "below" in result
    deps.exchange.add_price_level_alert.assert_called_once_with(58000.0, "below", deps.symbol, "support level")


async def test_add_price_level_alert_invalid_direction(deps):
    """Invalid direction should return error without calling exchange."""
    from src.agent.tools_execution import add_price_level_alert
    deps.exchange.add_price_level_alert = MagicMock()
    result = await add_price_level_alert(deps, 58000.0, "sideways", reasoning="test")
    assert "invalid" in result.lower()
    deps.exchange.add_price_level_alert.assert_not_called()


async def test_add_price_level_alert_limit_reached(deps):
    """When exchange returns None (limit), tool returns limit message."""
    from src.agent.tools_execution import add_price_level_alert
    deps.exchange.add_price_level_alert = MagicMock(return_value=None)
    result = await add_price_level_alert(deps, 58000.0, "below", reasoning="test")
    assert "limit" in result.lower()


async def test_add_price_level_alert_immediate_warning(deps):
    """When current price already past target, return warning."""
    from src.agent.tools_execution import add_price_level_alert
    deps.exchange.add_price_level_alert = MagicMock(return_value="abc123")
    deps.exchange._latest_price = 57000.0  # already below 58000
    result = await add_price_level_alert(deps, 58000.0, "below", reasoning="support")
    assert "warning" in result.lower()
    assert "immediately" in result.lower()


async def test_set_next_wake_success(deps):
    """set_next_wake should call setter and return confirmation."""
    from src.agent.tools_execution import set_next_wake
    deps.wake_min_minutes = 1
    deps.wake_max_minutes = 60
    deps.set_next_wake_fn = MagicMock()
    result = await set_next_wake(deps, 10, reasoning="checking position")
    deps.set_next_wake_fn.assert_called_once_with(10)
    assert "10 min" in result


async def test_set_next_wake_clamps_to_max(deps):
    """Minutes above max should be clamped."""
    from src.agent.tools_execution import set_next_wake
    deps.wake_min_minutes = 1
    deps.wake_max_minutes = 60
    deps.set_next_wake_fn = MagicMock()
    result = await set_next_wake(deps, 120, reasoning="test")
    deps.set_next_wake_fn.assert_called_once_with(60)
    assert "clamped" in result.lower()
    assert "60 min" in result


async def test_set_next_wake_clamps_to_min(deps):
    """Minutes below min should be clamped."""
    from src.agent.tools_execution import set_next_wake
    deps.wake_min_minutes = 1
    deps.wake_max_minutes = 60
    deps.set_next_wake_fn = MagicMock()
    result = await set_next_wake(deps, 0, reasoning="test")
    deps.set_next_wake_fn.assert_called_once_with(1)
    assert "clamped" in result.lower()


async def test_set_next_wake_not_available(deps):
    """When set_next_wake_fn is None, return not-available message."""
    from src.agent.tools_execution import set_next_wake
    deps.set_next_wake_fn = None
    result = await set_next_wake(deps, 10, reasoning="test")
    assert "not available" in result.lower()


async def test_open_position_rejects_when_pending(deps):
    """open_position returns rejection message when market order is pending."""
    from src.agent.tools_execution import open_position
    deps.exchange.has_pending_market_order = MagicMock(return_value=True)
    result = await open_position(deps, "long", 20.0, 3, reasoning="test")
    assert "already pending" in result.lower()
    deps.exchange.create_order.assert_not_called()


async def test_open_position_allows_when_no_pending(deps):
    """open_position proceeds normally when no market order is pending."""
    from src.agent.tools_execution import open_position
    deps.exchange.has_pending_market_order = MagicMock(return_value=False)
    result = await open_position(deps, "long", 20.0, 3, reasoning="test")
    assert "submitted" in result.lower()


async def test_close_position_rejects_when_pending(deps):
    """close_position returns rejection message when close order is pending."""
    from src.agent.tools_execution import close_position
    from src.integrations.exchange.base import Position
    deps.exchange.fetch_positions = AsyncMock(return_value=[
        Position("BTC/USDT:USDT", "long", 0.01, 64000.0, 10.0, 3, 55000.0)
    ])
    deps.exchange.has_pending_market_order = MagicMock(return_value=True)
    result = await close_position(deps, reasoning="test")
    assert "already pending" in result.lower()
    deps.exchange.create_order.assert_not_called()


async def test_close_position_allows_when_no_pending(deps):
    """close_position proceeds when no same-direction pending order."""
    from src.agent.tools_execution import close_position
    from src.integrations.exchange.base import Position
    deps.exchange.fetch_positions = AsyncMock(return_value=[
        Position("BTC/USDT:USDT", "long", 0.01, 64000.0, 10.0, 3, 55000.0)
    ])
    deps.exchange.has_pending_market_order = MagicMock(return_value=False)
    result = await close_position(deps, reasoning="test")
    assert "submitted" in result.lower()
