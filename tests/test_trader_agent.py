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


def test_registered_tool_names_matches_agent_tools():
    """Drift防护: REGISTERED_TOOL_NAMES 与 create_trader_agent 实际注册的
    tool 一一对应。加 tool 忘更新常量会导致 scripts/tool_call_summary.py
    从'零调用'表静默丢工具 → 本测试立即暴露。"""
    from src.agent.trader import REGISTERED_TOOL_NAMES, create_trader_agent
    from src.config import PersonaConfig

    agent = create_trader_agent(model="test", persona_config=PersonaConfig())
    actual = set(agent._function_toolset.tools)
    declared = set(REGISTERED_TOOL_NAMES)

    assert actual == declared, (
        f"Drift detected:\n"
        f"  In agent but not in REGISTERED_TOOL_NAMES: {actual - declared}\n"
        f"  In REGISTERED_TOOL_NAMES but not in agent: {declared - actual}"
    )
    assert len(REGISTERED_TOOL_NAMES) == 29, (
        f"Expected 29 tools (18+10+1), got {len(REGISTERED_TOOL_NAMES)}"
    )
    # 无重复
    assert len(REGISTERED_TOOL_NAMES) == len(set(REGISTERED_TOOL_NAMES)), \
        "REGISTERED_TOOL_NAMES contains duplicates"


def test_tool_call_recorder_wraps_iter2_tools():
    """Spec §6 integration test: ToolCallRecorder capability is attached to the
    agent AND the 3 Iter 2 perception tools (get_order_book / get_recent_trades /
    get_multi_timeframe_snapshot) are visible on the agent's function toolset.

    Verifies that any @agent.tool added in Iter 2 will be auto-wrapped at runtime
    (pydantic-ai dispatches every tool call through the capability's
    wrap_tool_execute, so visibility in toolset + presence of recorder in the
    capability chain is sufficient proof — no LLM mock needed).
    """
    from src.agent.trader import create_trader_agent
    from src.config import PersonaConfig
    from src.services.tool_call_recorder import ToolCallRecorder

    agent = create_trader_agent(model="test", persona_config=PersonaConfig())

    # 1. ToolCallRecorder is installed as a capability
    root_caps = agent._root_capability.capabilities
    recorder_instances = [c for c in root_caps if isinstance(c, ToolCallRecorder)]
    assert len(recorder_instances) == 1, (
        f"Expected exactly 1 ToolCallRecorder in agent capabilities, "
        f"got {len(recorder_instances)} (all caps: {[type(c).__name__ for c in root_caps]})"
    )

    # 2. All 3 new Iter 2 tools are registered on the toolset — they will be
    #    dispatched through the recorder by pydantic-ai at call time
    registered = set(agent._function_toolset.tools)
    for name in ("get_order_book", "get_recent_trades", "get_multi_timeframe_snapshot"):
        assert name in registered, (
            f"Iter 2 tool '{name}' not registered — ToolCallRecorder cannot wrap it"
        )
