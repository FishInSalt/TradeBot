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


async def test_get_trade_history(deps):
    from src.agent.tools_perception import get_trade_history

    result = await get_trade_history(deps)
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
