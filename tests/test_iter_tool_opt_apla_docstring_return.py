"""iter-tool-opt-apla-docstring-return — return string unification + docstring.

Audit `.working/tool-audits/2026-05-28-add_price_level_alert.md` on sim session
`f0f7b24f` (141 calls / 104 cycles, 2nd most-called Execution Tool):

Issue 2 (P2) — immediate-trigger return string drifts from normal path:
- normal:    `Price level alert set: above 74500.00 (id=...)`
- old warn:  `Alert set (id=...), but WARNING: current price (X) already below Y, may trigger immediately`
- new unified: `Price level alert set: <dir> <price> (id=...) — fires on next tick (current X already <dir> Y)`

Fix unifies the prefix, surfaces the alert direction explicitly (was elided
under `already below Y` condition phrasing), and replaces vague "may trigger
immediately" with precise "fires on next tick" — matching the actual
_check_price_levels behavior in `BaseExchange` (evaluated on every market tick).

Empirical motivation: 7/141 immediate-trigger events in `session_f0f7b24f`
burned ~10K chars of agent reasoning re-deriving "may trigger" semantics
(L82646 single event = 3761 chars including "did the alert fire? ... probably
will trigger as soon as this cycle ends").

Issue 1 (P2) — docstring leanness — verified by docstring-channel grep below.
"""
from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from src.agent.tools_execution import add_price_level_alert


def _make_deps(latest_price: float | None):
    """Minimal deps for APLA tool wrapper test.

    `db_engine=None` skips `_record_action` DB write (early-return at
    tools_execution.py:31). Engine layer behavior is not under test — only
    the tool wrapper's return-string formatting.
    """
    deps = MagicMock()
    deps.symbol = "BTC/USDT:USDT"
    deps.db_engine = None
    deps.exchange._latest_price = latest_price
    # add_price_level_alert (engine method) is sync, returns alert_id str
    deps.exchange.add_price_level_alert = MagicMock(return_value="abcd1234")
    return deps


@pytest.mark.asyncio
async def test_normal_return_format():
    """When condition not yet met (latest below alert price for above-alert),
    return is `Price level alert set: <dir> <price:.2f> (id=<hex>)` — no suffix.
    """
    deps = _make_deps(latest_price=76_000.0)
    out = await add_price_level_alert(deps, price=77_000.0, direction="above",
                                       reasoning="resistance")
    assert out == "Price level alert set: above 77000.00 (id=abcd1234)"


@pytest.mark.asyncio
async def test_immediate_trigger_unified_prefix_and_fires_on_next_tick():
    """Condition already met → success-path suffix `— fires on next tick
    (current X already <dir> Y)`.

    Regression locks against the prior format `Alert set (id=...), but WARNING:
    current price ... already <dir> Y, may trigger immediately`:
      (a) prefix must match normal path (`Price level alert set:`)
      (b) suffix uses precise `fires on next tick` not vague `may trigger`
      (c) alert direction surfaced explicitly (not elided under `already <dir>`)
      (d) no `WARNING` / `Alert set (` (old format) in output
    """
    deps = _make_deps(latest_price=77_050.0)  # already above 77000
    out = await add_price_level_alert(deps, price=77_000.0, direction="above",
                                       reasoning="breakout level")

    # (a) unified prefix matches normal path
    assert out.startswith("Price level alert set: above 77000.00 (id=abcd1234)")
    # (b) precise timing
    assert "fires on next tick" in out
    # (c) direction explicit in suffix
    assert "already above 77000.00" in out
    assert "current 77050.00" in out
    # (d) old format artefacts gone
    assert "WARNING" not in out
    assert "may trigger immediately" not in out
    assert not out.startswith("Alert set (")

    # below-direction symmetry
    deps2 = _make_deps(latest_price=75_950.0)  # already below 76000
    out2 = await add_price_level_alert(deps2, price=76_000.0, direction="below",
                                        reasoning="support break")
    assert out2.startswith("Price level alert set: below 76000.00 (id=abcd1234)")
    assert "fires on next tick" in out2
    assert "already below 76000.00" in out2


@pytest.mark.asyncio
async def test_latest_price_none_returns_normal_format():
    """`_latest_price is None` (no tick observed yet) → skip immediate-trigger
    branch entirely, return normal format. Regression lock for the
    `latest is not None and (...)` guard in tools_execution.py.
    """
    deps = _make_deps(latest_price=None)
    out = await add_price_level_alert(deps, price=77_000.0, direction="above",
                                       reasoning="anything")
    assert out == "Price level alert set: above 77000.00 (id=abcd1234)"
    assert "fires on next tick" not in out


def test_docstring_reaches_llm_channel():
    """Issue 1 (revised) — narrative facts + per-param descriptions must reach the
    LLM-facing channel, i.e. the trader.py @tool wrapper docstring as griffe
    reduces it to `tool_def.description` (channel ①) + `parameters_json_schema`
    (channel ②). NOT `tools_execution.__doc__`, which is dev-facing only and the
    model never sees.

    Regression guard for the channel-drift bug surfaced 2026-05-29: the apla
    enrichment originally landed only in tools_execution while the trader.py
    wrapper kept a short stub, so none of these facts reached the model — yet a
    test asserting `tools_execution.__doc__` passed (guarded the wrong channel).
    """
    from pydantic_ai import models
    models.ALLOW_MODEL_REQUESTS = False
    from src.agent.trader import create_trader_agent
    from src.config import PersonaConfig

    agent = create_trader_agent(model="test", persona_config=PersonaConfig())
    tool = agent._function_toolset.tools["add_price_level_alert"]
    desc = tool.tool_def.description or ""
    props = (tool.tool_def.parameters_json_schema or {}).get("properties", {})

    # ① narrative facts reach the LLM (channel ①)
    assert "fires on next tick" in desc, f"missing in LLM desc: {desc!r}"
    assert "20 active alerts" in desc, f"missing 20-cap in LLM desc: {desc!r}"
    assert "auto-cleared" in desc, f"missing auto-clear in LLM desc: {desc!r}"
    # ② per-param descriptions reach the LLM (channel ②)
    assert "above" in (props.get("direction", {}).get("description") or "")
    assert props.get("price", {}).get("description")
    assert props.get("reasoning", {}).get("description")
