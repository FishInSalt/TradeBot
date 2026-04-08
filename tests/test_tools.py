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

    result = await open_position(deps, "long", 20.0, 3)
    assert "o1" in result or "opened" in result.lower()
    deps.exchange.set_leverage.assert_called_once()


async def test_close_position(deps):
    from src.agent.tools_execution import close_position

    result = await close_position(deps)
    assert "close" in result.lower()


async def test_set_stop_loss(deps):
    from src.agent.tools_execution import set_stop_loss

    result = await set_stop_loss(deps, 63000.0)
    assert "63000" in result


async def test_set_take_profit(deps):
    from src.agent.tools_execution import set_take_profit

    result = await set_take_profit(deps, 68000.0)
    assert "68000" in result


async def test_adjust_leverage(deps):
    from src.agent.tools_execution import adjust_leverage

    result = await adjust_leverage(deps, 5)
    assert "5" in result
