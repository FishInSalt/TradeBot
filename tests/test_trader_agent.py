import pytest
from pydantic_ai import models

models.ALLOW_MODEL_REQUESTS = False


def test_create_trader_agent():
    from src.agent.trader import create_trader_agent
    from src.config import PersonaConfig

    agent = create_trader_agent(model="test", persona_config=PersonaConfig())
    assert agent is not None


def test_trader_agent_has_all_tools():
    from src.agent.trader import create_trader_agent
    from src.config import PersonaConfig

    agent = create_trader_agent(model="test", persona_config=PersonaConfig())
    tool_names = set(agent._function_toolset.tools)
    # 感知类
    assert "get_market_data" in tool_names
    assert "get_position" in tool_names
    assert "get_account_balance" in tool_names
    assert "get_open_orders" in tool_names
    assert "get_trade_journal" in tool_names
    assert "get_memories" in tool_names
    # 执行类
    assert "open_position" in tool_names
    assert "close_position" in tool_names
    assert "set_stop_loss" in tool_names
    assert "set_take_profit" in tool_names
    assert "adjust_leverage" in tool_names
    # 记忆类
    assert "save_memory" in tool_names
    assert "add_price_level_alert" in tool_names
    assert "set_next_wake" in tool_names
    # N2 market intelligence tools
    assert "get_market_news" in tool_names
    assert "get_critical_alerts" in tool_names
    assert "get_derivatives_data" in tool_names
    # N3 perception tools
    assert "get_higher_timeframe_view" in tool_names
    assert "get_macro_context" in tool_names
    assert "get_etf_flows" in tool_names
    assert "get_stablecoin_supply" in tool_names
    # 旧名称不存在
    assert "get_trade_history" not in tool_names


def test_trading_deps_creation():
    from src.agent.trader import TradingDeps
    from unittest.mock import AsyncMock, MagicMock

    deps = TradingDeps(
        symbol="BTC/USDT:USDT",
        timeframe="15m",
        market_data=AsyncMock(),
        exchange=AsyncMock(),
        technical=MagicMock(),
        memory=AsyncMock(),
        session_id="test-session-uuid",
        approval_enabled=True,
    )
    assert deps.symbol == "BTC/USDT:USDT"
