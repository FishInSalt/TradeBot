"""set_price_volatility_alert — lifecycle facts must reach the LLM-facing channel.

Audit `.working/tool-audits/2026-06-11-set_price_volatility_alert.md` on sim #17
(session 64b4ea1f): the agent set 0.4%/10min, then mis-modelled the alert as
one-shot ("it's consumed") and did a redundant re-set, spending ~3h before
reverse-engineering the singleton/re-arm semantics from observation.

The fix adds the post-fire lifecycle fact (NOT consumed / re-arms / persists
until cancel) to the tool description. This test locks that fact to the SAME
channel the model actually reads — the trader.py @tool wrapper docstring as
griffe reduces it to `tool_def.description` — NOT `tools_execution.__doc__`,
which is dev-facing only and the model never sees.

This is the exact channel-drift bug class already documented for
add_price_level_alert (see test_iter_tool_opt_apla_docstring_return.py:109):
enrichment landing only in tools_execution while the wrapper kept stale text,
so nothing reached the model — yet an impl-__doc__ assertion would still pass.
set_price_volatility_alert had no such guard; this adds it.
"""
from __future__ import annotations


def test_volatility_alert_lifecycle_reaches_llm_channel():
    from pydantic_ai import models
    models.ALLOW_MODEL_REQUESTS = False
    from src.agent.trader import create_trader_agent
    from src.config import PersonaConfig

    agent = create_trader_agent(model="test", persona_config=PersonaConfig())
    tool = agent._function_toolset.tools["set_price_volatility_alert"]
    desc = (tool.tool_def.description or "")
    low = desc.lower()

    # Post-fire lifecycle: the fact the agent had to reverse-engineer in sim #17.
    assert "not consumed" in low, f"missing 'not consumed' in LLM desc: {desc!r}"
    assert "re-arm" in low, f"missing 're-arm' in LLM desc: {desc!r}"
    assert "cancel_price_volatility_alert" in desc, (
        f"missing cancel cross-ref in LLM desc: {desc!r}"
    )
    # Singleton framing (pre-existing) must survive the edit.
    assert "singleton" in low, f"missing 'singleton' in LLM desc: {desc!r}"

    # Per-param descriptions still reach the LLM (channel ②).
    props = (tool.tool_def.parameters_json_schema or {}).get("properties", {})
    assert props.get("threshold_pct", {}).get("description")
    assert props.get("window_minutes", {}).get("description")
    assert props.get("reasoning", {}).get("description")
