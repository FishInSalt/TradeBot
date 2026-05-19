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
    # 执行类
    assert "open_position" in tool_names
    assert "close_position" in tool_names
    assert "set_stop_loss" in tool_names
    assert "set_take_profit" in tool_names
    assert "adjust_leverage" in tool_names
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
    assert len(REGISTERED_TOOL_NAMES) == 33, (
        f"Expected 33 tools (19+14), got {len(REGISTERED_TOOL_NAMES)}"
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


def test_get_derivatives_data_docstring_includes_oi_anchor_example():
    """W3 R2-Next-G adoption gate (oi_delta_ref_rate 39.1% — 31-50%
    docstring-promo band per spec §6.2): the wrapper docstring must carry a
    fact-only inline example of the rendered OI line with 1h/24h anchors +
    percent delta. Inline narrative form (not an ``Example output:`` block)
    is required because pydantic-ai 1.78 / griffe parses section-like
    headers and strips them from ``tool.tool_def.description`` — only the
    pre-``Args:`` description body reaches the LLM.

    Principle 8 (trust agent + tools first): example is fact-only — no
    guidance verb such as "use X for Y". If the underlying tool output
    format in tools_perception._derive_oi_anchors / get_derivatives_data
    ever drifts away from "Open Interest: $... (1h ago $..., +X.X%;
    24h ago $..., +Y.Y%)" the example becomes stale — this guard fails
    fast so the docstring example stays a truthful spec.
    """
    from src.agent.trader import create_trader_agent
    from src.config import PersonaConfig

    agent = create_trader_agent(model="test", persona_config=PersonaConfig())
    tool = agent._function_toolset.tools["get_derivatives_data"]
    description = tool.tool_def.description

    assert "Open Interest:" in description, (
        f"Example missing OI label in LLM-visible description: {description!r}"
    )
    assert "1h ago $" in description, (
        f"Example missing 1h anchor literal '1h ago $': {description!r}"
    )
    assert "24h ago $" in description, (
        f"Example missing 24h anchor literal '24h ago $': {description!r}"
    )


def test_set_price_volatility_alert_schema_exposes_threshold_range():
    """R2-1 drift guard: set_price_volatility_alert tool schema must expose threshold_pct and
    window_minutes range to LLM via pydantic-ai docstring sniffing.

    First-of-kind drift guard走 .tool_def.<attr> 二级 attr 路径（Iter 5 既有 drift
    guard 仅用一级 attr）。Spec 阶段已实测 pydantic-ai 1.78 verify。
    """
    from src.agent.trader import create_trader_agent
    from src.config import PersonaConfig

    agent = create_trader_agent(model="test", persona_config=PersonaConfig())
    tool = agent._function_toolset.tools["set_price_volatility_alert"]
    schema = tool.tool_def.parameters_json_schema

    threshold_desc = schema["properties"]["threshold_pct"]["description"]
    assert "0.1-50" in threshold_desc, f"threshold range missing from LLM-visible schema: {threshold_desc!r}"

    window_desc = schema["properties"]["window_minutes"]["description"]
    assert "1-240" in window_desc, f"window range missing: {window_desc!r}"


def test_cancel_price_level_alert_schema_exposes_id_format_and_source():
    """R2-2 T1b drift guard: cancel_price_level_alert wrapper docstring 必须
    暴露 alert_id 格式约束 (8-char hex) + id 来源引导 (get_active_alerts)
    给 LLM via pydantic-ai docstring sniffing。

    防 R2-2 修复回退：未来若 docstring 措辞被改弱，drift guard 立即失败。
    """
    from src.agent.trader import create_trader_agent
    from src.config import PersonaConfig

    agent = create_trader_agent(model="test", persona_config=PersonaConfig())
    tool = agent._function_toolset.tools["cancel_price_level_alert"]
    schema = tool.tool_def.parameters_json_schema

    alert_id_desc = schema["properties"]["alert_id"]["description"]
    assert "8-char hex" in alert_id_desc, \
        f"id format constraint missing from LLM-visible schema: {alert_id_desc!r}"
    assert "get_active_alerts" in alert_id_desc, \
        f"id source guidance missing from LLM-visible schema: {alert_id_desc!r}"


def test_dual_mode_tool_wrapper():
    """Foundation drift guard: dual-mode @tool wrapper accepts both
    `@tool` (no override) and `@tool(description=DESC)` (override) forms.

    Override form bypasses griffe section-stripping (see pydantic-ai
    issue #1146 + spec §2.2). Args still parsed from docstring in both
    forms. `require_parameter_descriptions=True` still enforced in
    override mode (missing-Args still fails fast).
    """
    import pytest
    from pydantic_ai import Agent, RunContext
    from pydantic_ai.exceptions import UserError
    from src.agent.trader import _create_dual_mode_tool

    agent = Agent("test", deps_type=type(None), output_type=str)
    tool = _create_dual_mode_tool(agent)

    @tool
    async def t_default(ctx: RunContext[None], x: int) -> str:
        """T1 default mode description.

        Args:
            x: an int.
        """
        return ""

    CUSTOM = "Custom override description.\n\nExamples:\n    t_override(1) → 'ok'\n"

    @tool(description=CUSTOM)
    async def t_override(ctx: RunContext[None], x: int) -> str:
        """Internal docstring — replaced by override.

        Args:
            x: an int.
        """
        return ""

    assert agent._function_toolset.tools["t_default"].tool_def.description == "T1 default mode description."
    assert agent._function_toolset.tools["t_override"].tool_def.description == CUSTOM
    # Args still parsed from docstring in BOTH forms
    assert agent._function_toolset.tools["t_default"].tool_def.parameters_json_schema["properties"]["x"]["description"] == "an int."
    assert agent._function_toolset.tools["t_override"].tool_def.parameters_json_schema["properties"]["x"]["description"] == "an int."

    # Negative control: require_parameter_descriptions=True still fires
    # in override mode if Args section is missing for a parameter.
    fail_agent = Agent("test", deps_type=type(None), output_type=str)
    fail_tool = _create_dual_mode_tool(fail_agent)
    with pytest.raises(UserError, match="Missing parameter descriptions"):
        @fail_tool(description="override desc")
        async def t_missing_args(ctx: RunContext[None], y: int) -> str:
            """Tool with description override but no Args section for y."""
            return ""
