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
    assert "get_exchange_announcements" in tool_names
    assert "get_macro_calendar" in tool_names
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
    assert len(REGISTERED_TOOL_NAMES) == 32, (
        f"Expected 32 tools (20+11+1), got {len(REGISTERED_TOOL_NAMES)}"
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


def test_trading_deps_no_object_typed_service_fields():
    """T8 drift guard: TradingDeps 6 个 service 字段不能用 object | None。

    限定保护这 6 个特定字段（硬编码列表）；未来加新 deps 字段不会被本测试
    覆盖——是有意的窄化，避免误伤合法 Callable / object 用法。
    """
    from typing import get_args, get_type_hints
    from src.agent.trader import TradingDeps

    expected_typed_fields = {
        "approval_gate", "metrics", "news",
        "macro", "crypto_etf", "onchain",
    }
    hints = get_type_hints(TradingDeps)
    for field_name in expected_typed_fields:
        hint = hints[field_name]
        args = get_args(hint)
        assert object not in args, (
            f"{field_name} still typed with `object` in {args}; "
            f"should be tightened to real service class | None"
        )


def test_all_tools_use_google_docstring_format():
    """T5: 31 个工具全部 docstring_format='google'。

    实测 1.78 toolset 私有 API 可读 Tool.docstring_format 字段。
    若 1.79+ 改名见 spec §6.3 fallback。
    """
    from src.agent.trader import create_trader_agent
    from src.config import PersonaConfig

    agent = create_trader_agent(model="test", persona_config=PersonaConfig())
    for name, tool in agent._function_toolset.tools.items():
        assert tool.docstring_format == "google", (
            f"Tool {name} docstring_format = {tool.docstring_format!r}, expected 'google'"
        )


def test_all_tools_require_parameter_descriptions():
    """T6: 31 个工具全部 require_parameter_descriptions=True。"""
    from src.agent.trader import create_trader_agent
    from src.config import PersonaConfig

    agent = create_trader_agent(model="test", persona_config=PersonaConfig())
    for name, tool in agent._function_toolset.tools.items():
        assert tool.require_parameter_descriptions is True, (
            f"Tool {name} require_parameter_descriptions = "
            f"{tool.require_parameter_descriptions!r}, expected True"
        )


def test_missing_args_with_require_descriptions_triggers_fail():
    """T7: pydantic-ai 1.78 行为契约 — partial(Agent.tool,
    require_parameter_descriptions=True) 装饰缺 Args 段工具时抛异常。

    本测试**不验证 trader.py 实施**（T5/T6 才是 trader.py drift guard）；
    本测试锁定 pydantic-ai 版本行为：若 1.79+ 静默放弃 require 校验，本测试 FAIL 提醒。
    """
    from functools import partial
    import pytest as _pytest
    from pydantic_ai import Agent, RunContext

    agent = Agent("test", deps_type=type(None), output_type=str)
    tool = partial(agent.tool, docstring_format="google", require_parameter_descriptions=True)

    with _pytest.raises(Exception):
        @tool
        async def bad_tool(ctx: RunContext, x: int) -> str:
            """Missing Args section docstring."""
            return str(x)
