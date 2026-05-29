"""iter-tool-opt-cancel-alert-docstring — cancel_price_level_alert docstring polish.

Audit `.working/tool-audits/2026-05-29-cancel_price_level_alert.md` on sim session
`f0f7b24f` (36 calls, all status=ok):

Issue 1 (P1) — auto-clear scope wording too narrow (可读性 / 原则 7):
  LLM-facing docstring (trader.py wrapper, griffe-sniffed → tool_def.description)
  said "alerts **at SL/TP levels** are auto-cleared when a position closes". Actual
  behavior (`BaseExchange.clear_level_alerts_by_symbol`) clears **all** price-level
  alerts for the symbol on full close, regardless of price. The narrow wording made
  the agent doubt the auto-clear covered its custom alerts and issue redundant
  "to be safe" cancels — 6/36 (17%) calls hit the idempotent no-op path post-close
  (session log L43771: "these are my custom alerts. Let me cancel them to be safe").
  Fix aligns the wording with add_price_level_alert's authoritative phrasing
  ("all alerts for a symbol are auto-cleared ... fully closes").

Issue 2a (P3) — idempotent return string drifts from docstring (可读性 / 原则 7):
  Docstring claimed the idempotent path returns a "'Note: Alert {id} no longer
  active' line", but the impl returns `Alert {id} no longer active (already
  triggered or removed)` — no `Note:` prefix. Fix: docstring describes the actual
  return string (no fabricated prefix). The agent reads the real return string, so
  the impl output is locked unchanged; only the docstring is corrected.
"""
from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from src.agent.tools_execution import cancel_price_level_alert
from src.config import PersonaConfig
from src.agent.trader import create_trader_agent


def _llm_description(tool_name: str) -> str:
    """Post-griffe, LLM-visible description for a registered tool.

    This is the trader.py @tool wrapper docstring as griffe strips it down to
    `tool_def.description` — the actual channel ① text the model sees. Asserting
    here (not on tools_execution.__doc__) guarantees we test what reaches the LLM.
    """
    agent = create_trader_agent(model="test", persona_config=PersonaConfig())
    return agent._function_toolset.tools[tool_name].tool_def.description or ""


def test_cancel_auto_clear_scope_is_symbol_wide_not_sltp():
    """Issue 1 — LLM-facing docstring must state auto-clear is symbol-wide, not
    narrowed to "SL/TP levels". Locks against regression to the narrow wording
    that caused 17% redundant post-close cancels.
    """
    desc = _llm_description("cancel_price_level_alert")
    # narrow/misleading wording gone
    assert "at SL/TP levels" not in desc, (
        f"narrow auto-clear scope wording still present: {desc!r}"
    )
    # authoritative symbol-wide scope present (aligned w/ add_price_level_alert)
    assert "all" in desc.lower()
    assert "auto-cleared" in desc
    assert "fully close" in desc or "fully closes" in desc


def test_cancel_docstring_no_fabricated_note_prefix():
    """Issue 2a — docstring must not claim a 'Note:' prefix the impl never emits."""
    desc = _llm_description("cancel_price_level_alert")
    assert "Note: Alert" not in desc, (
        f"docstring fabricates a 'Note:' return prefix the impl does not emit: {desc!r}"
    )
    # still documents the idempotent semantics + reject-on-format-invalid
    assert "no longer active" in desc
    assert "Idempotent" in desc


def _make_deps_no_match():
    """Deps whose alert list never matches → _lookup_alert returns None →
    idempotent path. db_engine=None short-circuits _record_action (not reached
    on idempotent path anyway).
    """
    deps = MagicMock()
    deps.symbol = "BTC/USDT:USDT"
    deps.db_engine = None
    deps.exchange.get_price_level_alerts = MagicMock(return_value=[])
    return deps


@pytest.mark.asyncio
async def test_idempotent_return_string_locked():
    """Issue 2a — actual idempotent return string is the contract the docstring
    must match: bare 'Alert {id} no longer active (already triggered or removed)',
    no 'Note:' prefix.
    """
    deps = _make_deps_no_match()
    out = await cancel_price_level_alert(deps, alert_id="a3f2b8c1",
                                         reasoning="cleanup after close")
    assert out == "Alert a3f2b8c1 no longer active (already triggered or removed)"
    assert not out.startswith("Note:")
