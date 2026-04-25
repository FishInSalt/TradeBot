# Iter 4 — Prompt Optimization Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Slim Persona Layer 1 from 25 → 5 bullets; standardize all 31 `@agent.tool` docstrings to Google format; split `get_critical_alerts` into 2 independent tools; soften single-direction wording in L27 / L65; add global fact-only wordlist coverage to ~27 tools.

**Architecture:** pydantic-ai automatically extracts `@agent.tool` function docstrings as `ToolDefinition.description` sent to LLM (griffe sniff Google format). Therefore, tool description bullets in Layer 1 duplicate docstring content (DRY violation). This iter moves descriptions out of Layer 1 into authoritative docstrings, leaving Layer 1 as cross-tool-behavior-only (5 bullets: fill timing / open fill response / close fill response / alert response / OCO atomicity).

**Tech Stack:** Python 3.11+, pydantic-ai 1.78.0 (griffe Google format), pytest, pytest-asyncio, pytest.parametrize.

**Spec:** `docs/superpowers/specs/2026-04-25-iter4-prompt-optimization-design.md` (commit `cf912f1`).

**Branch:** `iter4-prompt-optimization-spec` (already created).

---

## File Structure

| File | Responsibility | Phase |
|---|---|---|
| `src/agent/tools_perception.py` | Add `get_exchange_announcements` + `get_macro_calendar`; delete `get_critical_alerts` | 1 |
| `src/agent/trader.py` | 31 `@agent.tool` docstrings → Google format; replace `get_critical_alerts` registration with 2 new tools; update `REGISTERED_TOOL_NAMES` | 1+2+3 |
| `src/agent/persona.py` | Layer 1 25 → 5 bullets; soften L27; rename `## Tool Usage Notes` → `## Cross-Tool Behavior`; remove L65 single-direction sub-clause | 4+5 |
| `tests/test_news_tools.py` | Reorganize 7 `test_critical_alerts_*` → 10 `test_exchange_announcements_*` + `test_macro_calendar_*` | 1 |
| `tests/test_trader_agent.py` | Update drift assertion `len == 30` → `== 31`; comment `(19+10+1)` → `(20+10+1)` | 1 |
| `tests/test_persona.py` | Delete 4 obsolete tests; rename bullet count 25→5; rewrite `layer1_identity` scope-limited; add 3 new tests | 4+5 |
| `tests/test_fact_only_wordlist.py` | Add 18 new test functions (17 perception/memory single + 1 batch parametrize for 10 execution tools) covering 27 tools | 6 |

---

## Task 1: Split `get_critical_alerts` in `tools_perception.py` (TDD)

**Files:**
- Modify: `src/agent/tools_perception.py:614-694` (replace `get_critical_alerts` with 2 new functions)
- Test: `tests/test_news_tools.py` (add 10 new test functions; remove 7 obsolete `test_critical_alerts_*` in same commit)

This task creates the new implementation functions with TDD AND removes the obsolete tests in the same commit, maintaining clean test state at every commit boundary.

- [ ] **Step 1: Write failing tests for new functions**

Add to `tests/test_news_tools.py` (after the existing `test_critical_alerts_*` block, around L290):

```python
# ===== get_exchange_announcements (Iter 4 split from get_critical_alerts) =====

async def test_exchange_announcements_no_service():
    from src.agent.tools_perception import get_exchange_announcements
    deps = _make_deps(news=None)
    result = await get_exchange_announcements(deps)
    assert "not configured" in result.lower()


async def test_exchange_announcements_format():
    from src.agent.tools_perception import get_exchange_announcements

    news_svc = AsyncMock()
    news_svc.get_announcements.return_value = [
        _event("Delisting XYZ", source="okx_announcement", category="announcement"),
    ]

    deps = _make_deps(news=news_svc)
    result = await get_exchange_announcements(deps)

    assert "Exchange Announcements" in result
    assert "Delisting XYZ" in result
    # Footer is macro-calendar specific — must NOT appear in announcements tool
    assert "macro calendar covers current week only" not in result
    # macro section should NOT appear (this tool is announcements-only)
    assert "Upcoming Macro Events" not in result


async def test_exchange_announcements_empty():
    from src.agent.tools_perception import get_exchange_announcements

    news_svc = AsyncMock()
    news_svc.get_announcements.return_value = []

    deps = _make_deps(news=news_svc)
    result = await get_exchange_announcements(deps)

    assert "No exchange announcements" in result


async def test_exchange_announcements_passes_lookback_hours():
    from src.agent.tools_perception import get_exchange_announcements

    news_svc = AsyncMock()
    news_svc.get_announcements.return_value = []

    deps = _make_deps(news=news_svc)
    await get_exchange_announcements(deps, lookback_hours=48)
    news_svc.get_announcements.assert_called_once_with(48)


async def test_exchange_announcements_unavailable():
    """NewsService returns None → 'temporarily unavailable' rendering."""
    from src.agent.tools_perception import get_exchange_announcements

    news_svc = AsyncMock()
    news_svc.get_announcements.return_value = None

    deps = _make_deps(news=news_svc)
    result = await get_exchange_announcements(deps)

    assert "Exchange announcements service temporarily unavailable" in result


# ===== get_macro_calendar (Iter 4 split from get_critical_alerts) =====

async def test_macro_calendar_no_service():
    from src.agent.tools_perception import get_macro_calendar
    deps = _make_deps(news=None)
    result = await get_macro_calendar(deps)
    assert "not configured" in result.lower()


async def test_macro_calendar_format():
    from src.agent.tools_perception import get_macro_calendar

    news_svc = AsyncMock()
    news_svc.get_macro_events.return_value = [
        _event("FOMC Meeting", source="forexfactory", category="macro_event",
               importance="high", content="Previous: N/A | Forecast: N/A"),
    ]

    deps = _make_deps(news=news_svc)
    result = await get_macro_calendar(deps)

    assert "Upcoming Macro Events" in result
    assert "FOMC Meeting" in result
    assert "Impact: High" in result
    assert "Previous: N/A | Forecast: N/A" in result
    # Footer shows when macro_events is a list (success, even if empty)
    assert "macro calendar covers current week only" in result
    # announcements section should NOT appear
    assert "Exchange Announcements" not in result


async def test_macro_calendar_empty():
    """macro_events=[] → 'no upcoming events' + footer SHOWS (list success)."""
    from src.agent.tools_perception import get_macro_calendar

    news_svc = AsyncMock()
    news_svc.get_macro_events.return_value = []

    deps = _make_deps(news=news_svc)
    result = await get_macro_calendar(deps)

    assert "No upcoming macro events" in result
    # Footer must appear: list (incl. []) is a valid result the scope qualifies
    assert "macro calendar covers current week only" in result


async def test_macro_calendar_passes_lookahead_hours():
    from src.agent.tools_perception import get_macro_calendar

    news_svc = AsyncMock()
    news_svc.get_macro_events.return_value = []

    deps = _make_deps(news=news_svc)
    await get_macro_calendar(deps, lookahead_hours=24)
    news_svc.get_macro_events.assert_called_once_with(24)


async def test_macro_calendar_unavailable():
    """macro_events=None → 'temporarily unavailable' + footer HIDDEN (no result to qualify)."""
    from src.agent.tools_perception import get_macro_calendar

    news_svc = AsyncMock()
    news_svc.get_macro_events.return_value = None

    deps = _make_deps(news=news_svc)
    result = await get_macro_calendar(deps)

    assert "Macro events service temporarily unavailable" in result
    # Footer must be suppressed when macro source is unavailable
    assert "macro calendar covers current week only" not in result
```

- [ ] **Step 2: Run new tests — verify they fail with import error**

Run: `pytest tests/test_news_tools.py::test_exchange_announcements_no_service tests/test_news_tools.py::test_macro_calendar_no_service -v`
Expected: FAIL with `ImportError: cannot import name 'get_exchange_announcements' from 'src.agent.tools_perception'`.

- [ ] **Step 3: Implement both new functions in `tools_perception.py`**

**Strategy: lift-and-shift, not rewrite.** The two new functions reuse the **exact same line-format strings** as the original `get_critical_alerts` (announcements: `f"=== Exchange Announcements (past {lookback_hours}h) ===\n"` + `e.timestamp.strftime("[%Y-%m-%d %H:%M] ") + e.title`; macro: `f"[{ts}] {e.title} — Impact: {impact}"` + `e.content` continuation; footer: `"Note: macro calendar covers current week only..."`). The only changes: drop the `asyncio.gather` orchestrator, drop the cross-section `sections` list (replace with per-tool sections / direct returns), keep the footer tri-state in `get_macro_calendar`. **Verify line-by-line against tools_perception.py:614-694 before committing** — any wording diff would break existing fact-only / news-tool fixtures.

Replace `tools_perception.py:614-694` (the entire `get_critical_alerts` function) with:

```python
async def get_exchange_announcements(
    deps: TradingDeps,
    lookback_hours: int = 24,
) -> str:
    """Get recent exchange announcements (maintenance, delistings, parameter changes)."""
    if deps.news is None:
        return "News service not configured."

    try:
        announcements = await deps.news.get_announcements(lookback_hours)
    except Exception:
        announcements = None

    if announcements is None:
        return (
            f"=== Exchange Announcements (past {lookback_hours}h) ===\n"
            "Exchange announcements service temporarily unavailable."
        )
    if announcements:
        lines = [e.timestamp.strftime("[%Y-%m-%d %H:%M] ") + e.title for e in announcements]
        return (
            f"=== Exchange Announcements (past {lookback_hours}h) ===\n"
            + "\n".join(lines)
        )
    return (
        f"=== Exchange Announcements (past {lookback_hours}h) ===\n"
        "No exchange announcements."
    )


async def get_macro_calendar(
    deps: TradingDeps,
    lookahead_hours: int = 12,
) -> str:
    """Get upcoming macro events (FOMC, CPI, NFP) with impact level.

    Footer rule: shown when macro_events is a list (incl. []) so the scope
    caveat qualifies a real result; suppressed when macro_events is None
    (no result to qualify, per spec §3.4).
    """
    if deps.news is None:
        return "News service not configured."

    try:
        macro_events = await deps.news.get_macro_events(lookahead_hours)
    except Exception:
        macro_events = None

    sections: list[str] = []

    if macro_events is None:
        sections.append(
            f"=== Upcoming Macro Events (next {lookahead_hours}h) ===\n"
            "Macro events service temporarily unavailable."
        )
    elif macro_events:
        lines = []
        for e in macro_events:
            ts = e.timestamp.strftime("%Y-%m-%d %H:%M")
            impact = e.importance.capitalize()
            line = f"[{ts}] {e.title} — Impact: {impact}"
            if e.content:
                line += f"\n  {e.content}"
            lines.append(line)
        sections.append(
            f"=== Upcoming Macro Events (next {lookahead_hours}h) ===\n"
            + "\n".join(lines)
        )
    else:
        sections.append(
            f"=== Upcoming Macro Events (next {lookahead_hours}h) ===\n"
            "No upcoming macro events."
        )

    # Footer: shown when macro_events is a list; suppressed when None.
    if macro_events is not None:
        sections.append(
            "Note: macro calendar covers current week only; "
            "Friday evening / weekend calls may miss next week's early events."
        )

    return "\n\n".join(sections)
```

- [ ] **Step 4: Run new tests — verify they pass**

Run: `pytest tests/test_news_tools.py -k "exchange_announcements or macro_calendar" -v`
Expected: 10 PASS (5 each).

- [ ] **Step 5: Delete the 7 obsolete `test_critical_alerts_*` tests + update file docstring**

Delete `tests/test_news_tools.py:172-289` — the entire `# ===== get_critical_alerts =====` section through the end of `test_critical_alerts_announcements_only_macro_unavailable`.

The 2 mixed-state tests (`_mixed_unavailable_and_empty` / `_announcements_only_macro_unavailable`) decompose into the new single-tool state tests already added in Step 1 (covered by `test_exchange_announcements_unavailable` + `test_macro_calendar_empty` for the first; `test_exchange_announcements_format` + `test_macro_calendar_unavailable` for the second).

Also update file-level docstring at `tests/test_news_tools.py:1`. Old:
```python
"""Tests for get_market_news, get_critical_alerts, get_derivatives_data tools."""
```

New:
```python
"""Tests for get_market_news, get_exchange_announcements, get_macro_calendar, get_derivatives_data tools."""
```

- [ ] **Step 6: Verify no lingering reference to `get_critical_alerts` in `tools_perception.py`**

Run: `grep -n "get_critical_alerts" src/agent/tools_perception.py`
Expected: zero hits (this task only removes the implementation function).

**Other lingering references** are expected and cleaned in later tasks:
- `src/agent/trader.py` L139 / L148 / L395 — cleaned in Task 2 (registration replacement + REGISTERED_TOOL_NAMES)
- `tests/test_trader_agent.py:40` (hardcoded `assert "get_critical_alerts" in tool_names`) — cleaned in Task 2 Step 4
- `src/agent/persona.py:39` (Layer 1 bullet "Use get_critical_alerts before trading") — cleaned in Task 8 (Layer 1 reduction)
- `tests/test_news_tools.py:1` (file-level docstring) — cleaned in Step 5 of this task

A repo-wide grep `grep -rn "get_critical_alerts" src/ tests/` returning zero hits is the **Task 8 Step 2 verification**, not this task's.

- [ ] **Step 7: Run news tools test file to verify clean state**

Run: `pytest tests/test_news_tools.py -v`
Expected: All PASS — only the 10 new `test_exchange_announcements_*` + `test_macro_calendar_*` exist along with any other unrelated tests in the file.

- [ ] **Step 8: Commit**

```bash
git add src/agent/tools_perception.py tests/test_news_tools.py
git commit -m "feat(tools): split get_critical_alerts into get_exchange_announcements + get_macro_calendar

Two independent functions with separate degradation paths.
Footer 'macro calendar covers current week only' tri-state rule preserved
(shown for list incl. [], hidden for None) — see spec §3.4.
Obsolete test_critical_alerts_* removed (10 split-tool tests replace them).

Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>"
```

---

## Task 2: Update `trader.py` `@agent.tool` registration for N8 split

**Files:**
- Modify: `src/agent/trader.py:138-150` (replace `get_critical_alerts` registration)
- Modify: `src/agent/trader.py:380-418` (`REGISTERED_TOOL_NAMES` list)

- [ ] **Step 1: Replace `get_critical_alerts` `@agent.tool` block**

Edit `trader.py:138-150`. Old:

```python
    @agent.tool
    async def get_critical_alerts(
        ctx: RunContext[TradingDeps],
        lookback_hours: int = 24,
        lookahead_hours: int = 12,
    ) -> str:
        """Get critical alerts: exchange announcements and upcoming macro events.
        lookback_hours: how far back to check announcements (default 24h).
        lookahead_hours: how far ahead to check macro events (default 12h).
        Output ~100-400 tokens (often empty when no relevant events are scheduled)."""
        from src.agent.tools_perception import get_critical_alerts as _impl

        return await _impl(ctx.deps, lookback_hours, lookahead_hours)
```

New:

```python
    @agent.tool
    async def get_exchange_announcements(
        ctx: RunContext[TradingDeps],
        lookback_hours: int = 24,
    ) -> str:
        """Get recent exchange announcements (maintenance, delistings, parameter changes).

        Call before trading or when investigating unexpected price moves. Output
        ~50-200 tokens (often empty when no recent announcements).

        Args:
            lookback_hours: how far back to scan for announcements (default 24h).
        """
        from src.agent.tools_perception import get_exchange_announcements as _impl

        return await _impl(ctx.deps, lookback_hours)

    @agent.tool
    async def get_macro_calendar(
        ctx: RunContext[TradingDeps],
        lookahead_hours: int = 12,
    ) -> str:
        """Get upcoming macro events (FOMC, CPI, NFP) with impact level.

        Call before trading or when assessing forward-looking risk. Macro calendar
        covers the current week only — Friday evening / weekend calls may miss
        next week's early events. Output ~50-250 tokens (often empty when no
        scheduled events in window).

        Args:
            lookahead_hours: how far ahead to scan for events (default 12h).
        """
        from src.agent.tools_perception import get_macro_calendar as _impl

        return await _impl(ctx.deps, lookahead_hours)
```

- [ ] **Step 2: Update `REGISTERED_TOOL_NAMES`**

Edit `trader.py:384-405`. Replace the perception block:

Old:
```python
REGISTERED_TOOL_NAMES: list[str] = [
    # --- 感知 (19) ---
    "get_market_data",
    "get_position",
    "get_account_balance",
    "get_open_orders",
    "get_trade_journal",
    "get_memories",
    "get_active_alerts",
    "get_performance",
    "get_market_news",
    "get_critical_alerts",
    "get_derivatives_data",
    ...
```

New:
```python
REGISTERED_TOOL_NAMES: list[str] = [
    # --- 感知 (20) ---
    "get_market_data",
    "get_position",
    "get_account_balance",
    "get_open_orders",
    "get_trade_journal",
    "get_memories",
    "get_active_alerts",
    "get_performance",
    "get_market_news",
    "get_exchange_announcements",
    "get_macro_calendar",
    "get_derivatives_data",
    ...
```

(Replace `"get_critical_alerts"` with the two new lines; update `(19)` → `(20)` in comment.)

- [ ] **Step 3: Run drift test to verify count change is detected**

Run: `pytest tests/test_trader_agent.py::test_registered_tool_names_matches_agent_tools -v`
Expected: FAIL with `Expected 30 tools (19+10+1), got 31` (the assertion in test L84-86).

- [ ] **Step 4: Update drift test count**

Edit `tests/test_trader_agent.py:84-86`. Old:
```python
    assert len(REGISTERED_TOOL_NAMES) == 30, (
        f"Expected 30 tools (19+10+1), got {len(REGISTERED_TOOL_NAMES)}"
    )
```

New:
```python
    assert len(REGISTERED_TOOL_NAMES) == 31, (
        f"Expected 31 tools (20+10+1), got {len(REGISTERED_TOOL_NAMES)}"
    )
```

- [ ] **Step 5: Update `test_trader_agent_has_all_tools` hardcoded assertion**

Edit `tests/test_trader_agent.py:40`. Old:
```python
    assert "get_critical_alerts" in tool_names
```

New:
```python
    assert "get_exchange_announcements" in tool_names
    assert "get_macro_calendar" in tool_names
```

- [ ] **Step 6: Run drift test — verify it passes**

Run: `pytest tests/test_trader_agent.py::test_registered_tool_names_matches_agent_tools -v`
Expected: PASS.

- [ ] **Step 7: Run full trader agent test file**

Run: `pytest tests/test_trader_agent.py -v`
Expected: All PASS (Step 5 fixed the `test_trader_agent_has_all_tools` failure that would otherwise occur).

- [ ] **Step 8: Commit**

```bash
git add src/agent/trader.py tests/test_trader_agent.py
git commit -m "feat(agent): register get_exchange_announcements + get_macro_calendar; update drift assertions

NOTE: persona.py L39 still references 'get_critical_alerts' until Task 8
(Layer 1 reduction). Within this single-PR scope the transient zombie
reference is invisible to users; full repo-wide grep should hit zero
only after Task 8 (verified in Task 14 Step 2).

Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>"
```

**Note on subsequent task line numbers**: this commit adds ~23 net lines to `src/agent/trader.py` (1 old `@agent.tool` block replaced by 2 new blocks with longer Google-format docstrings). All line numbers in Tasks 3-6 below (e.g., `trader.py:269-280` for `open_position`) were captured **before** Task 2 — they are stale by ~23 lines. **Use OLD docstring snippet content as the matching anchor** when running `Edit`; the line numbers serve as a navigation hint only. The Edit tool matches by content, so changes will land correctly even with stale line references.

---

## Task 3: Audit 7 old tools docstrings (Group A — fresh content)

**Files:**
- Modify: `src/agent/trader.py` — 7 `@agent.tool` blocks for: `get_account_balance` (L83-88), `get_open_orders` (L90-95), `open_position` (L269-280), `close_position` (L282-287), `set_stop_loss` (L289-294), `set_take_profit` (L296-301), `adjust_leverage` (L303-308)

Spec §2.3 + §3.1: these 7 tools have skeletal docstrings (single-line, no `Args:`). Migrate to Google format. For the 5 execution tools, migrate `Always provide reasoning.` into the `Args:` section as a parameter description (per spec §3.1 audit checklist item — usage instruction, not call-timing rule).

- [ ] **Step 1: Update `get_account_balance` docstring**

Edit `trader.py:84-85`. Old:
```python
        """Get account balance with return on initial capital."""
```

New:
```python
        """Get account balance with return on initial capital.

        Output reports total equity, free margin, used margin, and percentage
        return on initial capital — useful for sizing decisions and risk checks.
        """
```

- [ ] **Step 2: Update `get_open_orders` docstring**

Edit `trader.py:91-92`. Old:
```python
        """Get all pending orders with distance from current price."""
```

New:
```python
        """Get all pending orders with distance from current price.

        Lists limit orders, stop loss, and take profit orders, each with their
        price level and distance from current. OCO-paired orders (sharing an
        algoId on OKX) render with `[OCO]` tag. Useful before placing new
        orders or when reviewing exposure.
        """
```

- [ ] **Step 3: Update `open_position` docstring**

Edit `trader.py:270-280`. Old:
```python
        side: str,
        position_pct: float,
        leverage: int,
        reasoning: str,
    ) -> str:
        """Open a new position. side='long' or 'short'. position_pct=% of free balance. Always provide reasoning."""
```

New:
```python
        side: str,
        position_pct: float,
        leverage: int,
        reasoning: str,
    ) -> str:
        """Open a new market-order position.

        Position fills via market order; you will receive a fill notification
        when execution completes. Set stop loss and take profit only after the
        fill notification arrives (separate trigger, not in the same cycle).

        Args:
            side: 'long' or 'short'.
            position_pct: percent of free balance to allocate (0-100).
            leverage: leverage multiplier (cannot be changed while holding position).
            reasoning: brief description of your decision logic.
        """
```

- [ ] **Step 4: Update `close_position` docstring**

Edit `trader.py:283-285`. Old:
```python
    async def close_position(ctx: RunContext[TradingDeps], reasoning: str) -> str:
        """Close all open positions. Always provide reasoning."""
```

New:
```python
    async def close_position(ctx: RunContext[TradingDeps], reasoning: str) -> str:
        """Close all open positions via market order.

        Position closure fills via market order; you will receive a fill
        notification when execution completes (separate trigger).

        Args:
            reasoning: brief description of your decision logic (e.g., 'TP target hit', 'thesis invalidated').
        """
```

- [ ] **Step 5: Update `set_stop_loss` docstring**

Edit `trader.py:290-292`. Old:
```python
    async def set_stop_loss(ctx: RunContext[TradingDeps], price: float, reasoning: str) -> str:
        """Set stop loss on current position. Auto-cancels existing stop orders. Always provide reasoning."""
```

New:
```python
    async def set_stop_loss(ctx: RunContext[TradingDeps], price: float, reasoning: str) -> str:
        """Set stop loss on the current position.

        Auto-cancels any existing stop orders before placing the new one.
        On OKX, stop and take_profit orders sharing an algoId render as `[OCO]`
        in get_open_orders and are atomic — cancelling or triggering one leg
        removes both. To replace only one leg, re-create the other leg
        immediately after.

        Args:
            price: trigger price for the stop loss.
            reasoning: brief description of your decision logic.
        """
```

- [ ] **Step 6: Update `set_take_profit` docstring**

Edit `trader.py:297-299`. Old:
```python
    async def set_take_profit(ctx: RunContext[TradingDeps], price: float, reasoning: str) -> str:
        """Set take profit on current position. Auto-cancels existing TP orders. Always provide reasoning."""
```

New:
```python
    async def set_take_profit(ctx: RunContext[TradingDeps], price: float, reasoning: str) -> str:
        """Set take profit on the current position.

        Auto-cancels any existing take_profit orders before placing the new one.
        On OKX, stop and take_profit orders sharing an algoId render as `[OCO]`
        in get_open_orders and are atomic — cancelling or triggering one leg
        removes both. To replace only one leg, re-create the other leg
        immediately after.

        Args:
            price: trigger price for the take profit.
            reasoning: brief description of your decision logic.
        """
```

- [ ] **Step 7: Update `adjust_leverage` docstring**

Edit `trader.py:304-306`. Old:
```python
    async def adjust_leverage(ctx: RunContext[TradingDeps], leverage: int, reasoning: str) -> str:
        """Adjust leverage. Always provide reasoning."""
```

New:
```python
    async def adjust_leverage(ctx: RunContext[TradingDeps], leverage: int, reasoning: str) -> str:
        """Adjust leverage multiplier.

        Cannot be changed while holding a position — close first, then adjust.
        Higher leverage amplifies both gains and losses, including liquidation risk.

        Args:
            leverage: new leverage multiplier.
            reasoning: brief description of your decision logic.
        """
```

- [ ] **Step 8: Run trader agent + persona tests to verify nothing broke**

Run: `pytest tests/test_trader_agent.py tests/test_persona.py -v`
Expected: All PASS (drift tests + persona tests still green; no behavior change yet).

- [ ] **Step 9: Commit**

```bash
git add src/agent/trader.py
git commit -m "refactor(agent): unify 7 old tool docstrings to Google format

5 execution tools migrate 'Always provide reasoning' into Args: section
per spec §3.1 (parameter usage instruction, not call-timing rule).

Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>"
```

---

## Task 4: Audit perception tools batch 1 (8 tools)

**Files:**
- Modify: `src/agent/trader.py` — 8 perception `@agent.tool` blocks: `get_market_data`, `get_position`, `get_market_news`, `get_derivatives_data`, `get_higher_timeframe_view`, `get_macro_context`, `get_etf_flows`, `get_stablecoin_supply`

Each docstring receives content from corresponding §2.2 bullet (see persona.py L29, L48, L38, L40-L44). Migrate to Google format.

**Implementation note**: Steps 2-8 below show only the **new** docstring (not the OLD one) to keep the plan readable. Before each Edit, **Read the current docstring** for the target function to capture the exact `old_string` for the Edit tool. Step 1 (`get_market_data`) shows the full Old + New as the worked example.

- [ ] **Step 1: Update `get_market_data` docstring** (source: persona.py L29)

Edit the `@agent.tool` block for `get_market_data`. Old docstring (matches current trader.py:67-71 — use as Edit anchor; per Task 2 note, line numbers may have shifted):

```python
        """Get market data: ticker, technical indicators, market context, and recent candles.
        candle_count=20 for quick check or secondary timeframes, 50 for detailed analysis.
        Default 50. Values above 50 may be capped by exchange API limits.
        Total output ~1000-1200 tokens (K-line table ~750-800 + indicators + context).
        symbol and timeframe default to session config."""
```

New (preserves `Total output ~1000-1200 tokens` token estimation per spec §3.1 audit checklist; preserves `candle_count=20 for ... 50 for detailed` parameter usage hint; preserves `capped by exchange API limits` operational fact; absorbs L29 multi-timeframe content):

```python
        """Get market data: ticker, technical indicators, market context, and recent candles.

        Use multiple timeframes to build conviction before acting (e.g., "1h" for
        the bigger picture, "5m" for entry timing). Pass candle_count=20 for
        secondary timeframes to save tokens.

        Total output ~1000-1200 tokens (K-line table ~750-800 + indicators + context).

        Args:
            symbol: trading symbol; None defaults to session symbol.
            timeframe: candle timeframe (e.g., '5m', '1h', '4h', '1d'); None defaults to session timeframe.
            candle_count: number of candles to fetch (default 50). Use 20 for quick checks
                or secondary timeframes; 50 for detailed analysis. Values above 50 may be
                capped by exchange API limits.
        """
```

- [ ] **Step 2: Update `get_position` docstring** (source: persona.py L48)

Edit `trader.py:76-81` block. Replace:

```python
        """Get current position details with risk exposure context.

        Includes Risk exposure (notional / margin / liquidation distance in
        ATR(1h) multiples — 1h is the fixed baseline regardless of session
        trading style) and Exit orders section (SL/TP distances from both
        entry and current). Useful both when opening and during ongoing
        position management.

        Args:
            symbol: trading symbol (defaults to session symbol).
        """
```

- [ ] **Step 3: Update `get_market_news` docstring** (source: persona.py L38)

Edit `trader.py:125-136` block. Replace:

```python
        """Get recent crypto news headlines + Fear & Greed Index (0 = max fear, 100 = max greed).

        Returns up to 10 headlines total (up to 5 symbol-specific, remainder
        general crypto); total may be fewer if upstream has limited recent posts.
        Usually call without news_filter; use 'positive' / 'negative' / 'neutral'
        when you want a specific sentiment lens. Output ~500-700 tokens.

        Args:
            news_filter: 'positive', 'negative', 'neutral', or None for latest mix.
        """
```

- [ ] **Step 4: Update `get_derivatives_data` docstring** (source: persona.py L40)

Edit `trader.py:152-162` block. Replace:

```python
        """Get derivatives market data: funding rate, open interest, long/short ratio.

        Positive funding rate means longs pay shorts; negative means shorts pay
        longs (settlement interval varies by contract — see next settlement time
        in output). Open interest is total outstanding contracts. Long/short
        ratio is the ratio of long vs short account positions. Output ~150-250 tokens.

        Args:
            symbol: trading symbol; None uses the currently traded pair.
        """
```

- [ ] **Step 5: Update `get_higher_timeframe_view` docstring** (source: persona.py L41)

Edit `trader.py:164-174` block. Replace:

```python
        """Get long-period structure: MA50/100/200 distances and range position.

        Reports moving averages (MA50/100/200), price position within the recent
        100-period range, and structural highs/lows over a longer window than
        your default trading timeframe. No default — explicitly pick the
        timeframe. Output ~250 tokens.

        Args:
            timeframe: '4h' bridges LTF and 1d; '1d'/'1w'/'1M' for swing/position context.
        """
```

- [ ] **Step 6: Update `get_macro_context` docstring** (source: persona.py L42)

Edit `trader.py:176-183` block. Replace:

```python
        """Get cross-market macro snapshot.

        Includes BTC/ETH dominance, Total Crypto Mcap (CoinGecko), USD
        Trade-Weighted Index (FRED DTWEXBGS — note: the Fed's broad TW index
        across 26 currencies, NOT the ICE DXY across 6 currencies; absolute
        values differ and the two can diverge on single-currency moves, though
        they usually move in the same direction), VIX, 10Y Treasury yield,
        2s10s spread, 10Y inflation expectation (FRED), and SPY/QQQ closing
        quotes (Alpha Vantage). FRED data has daily granularity; SPY/QQQ are
        equity ETFs with NYSE trading-hour quotes. Output ~200 tokens.
        """
```

(No `Args:` section — function takes no parameters beyond `ctx`.)

- [ ] **Step 7: Update `get_etf_flows` docstring** (source: persona.py L43)

Edit `trader.py:185-192` block. Replace:

```python
        """Get US BTC + ETH spot ETF daily net flows + cumulative AUM.

        Today's value may be revised T+1. Output ~300 tokens.

        Args:
            days: lookback days (1-14, default 7).
        """
```

- [ ] **Step 8: Update `get_stablecoin_supply` docstring** (source: persona.py L44)

Edit `trader.py:194-200` block. Replace:

```python
        """Get USDT + USDC current total supply and 7-day changes.

        Data sourced from DefiLlama (on-chain circulating supply). Output ~80 tokens.
        """
```

- [ ] **Step 9: Run full test suite — verify no regression**

Run: `pytest -q 2>&1 | tail -5`
Expected: All PASS; total count unchanged from Task 2 baseline (no test added/removed in this task).

- [ ] **Step 10: Commit**

```bash
git add src/agent/trader.py
git commit -m "refactor(agent): perception batch 1 — 8 tool docstrings to Google format

Sources content from Layer 1 bullets L29 / L38 / L40-L44 / L48 (deleted in
Phase 4). DRY: single source of truth in docstring, removed from Layer 1.

Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>"
```

---

## Task 5: Audit perception tools batch 2 (8 tools)

**Files:**
- Modify: `src/agent/trader.py` — 8 perception `@agent.tool` blocks: `get_memories`, `get_active_alerts`, `get_performance`, `get_trade_journal`, `get_order_book`, `get_recent_trades`, `get_multi_timeframe_snapshot`, `get_price_pivots`

Sources: persona.py L30 (memories side), L35 (alerts side), L37, L45-L47, L50.

**Implementation note**: same "new only" pattern as Task 4 — Read each function's current docstring before each Edit to capture the exact `old_string`.

- [ ] **Step 1: Update `get_memories` docstring** (source: L30 retrieve side)

Edit `trader.py:104-109` block. Replace:

```python
        """Get long-term memories (lessons, patterns, trade reviews).

        Check past memories before making decisions to avoid repeating mistakes
        and apply pattern recognitions that proved correct previously.
        """
```

- [ ] **Step 2: Update `get_active_alerts` docstring** (source: L35 review side)

Edit `trader.py:111-116` block. Replace:

```python
        """Get current alert configuration.

        Reports volatility alert parameters (threshold % + time window) and
        active price level alerts. Useful when reviewing or adjusting your
        alert setup.
        """
```

- [ ] **Step 3: Update `get_performance` docstring** (source: L37 quantitative side)

Edit `trader.py:118-123` block. Replace:

```python
        """Get quantitative trading performance statistics.

        Reports return, win rate, drawdown, profit factor, and other
        quantitative metrics. Use for evaluating strategy effectiveness
        across the session — pair with get_trade_journal for decision
        pattern review.
        """
```

- [ ] **Step 4: Update `get_trade_journal` docstring** (source: L37 decision side)

Edit `trader.py:97-102` block. Replace:

```python
        """Get the trade journal — decision timeline with quick stats summary.

        Use for reviewing recent decisions and their outcomes — pair with
        get_performance for the quantitative view of the same period.
        """
```

- [ ] **Step 5: Update `get_order_book` docstring** (source: L45)

Edit `trader.py:202-216` block. Replace existing docstring (already Google-format-ish, just enhance):

```python
        """Return top-N order book depth with concentrated-level breakdown.

        Reports best bid/ask, cumulative depth, bid/ask share, and concentrated
        levels (size > 3× same-side median). Use to evaluate liquidity, slippage
        risk, or concentrated levels near current price.

        Args:
            depth: levels per side to fetch (default 20).

        Degradation: "Order book ({symbol}): insufficient data (requested depth X, got Y)"
        if book is empty/short; "Order book ({symbol}): temporarily unavailable" on
        service failure.
        """
```

- [ ] **Step 6: Update `get_recent_trades` docstring** (source: L46)

Edit `trader.py:219-235` block (find `get_recent_trades`). Replace docstring:

```python
        """Read taker-flow bias and rhythm over recent minutes.

        Default 300s window across 5 × 60s buckets. Total + trade count + avg
        size shown below buckets.

        Args:
            window_seconds: total scan window (default 300s).
        """
```

- [ ] **Step 7: Update `get_multi_timeframe_snapshot` docstring** (source: L47)

Edit `trader.py:237-252` block. Replace docstring:

```python
        """Scan multi-TF alignment in a single call (default 5m/1h/4h/1d).

        Useful for a once-per-cycle structural overview before committing to
        a direction. Reports 4 columns per TF: momentum / structure / volatility
        / range position.

        Args:
            tfs: list of timeframes; None uses default (5m/1h/4h/1d).
        """
```

- [ ] **Step 8: Update `get_price_pivots` docstring** (source: L50, softened per §3.2)

Edit `trader.py:254-266` block. Replace docstring (delete the L50 trailing "Useful for placing SL/TP at structural levels rather than arbitrary percentages." per spec §3.2):

```python
        """Scan structural price levels.

        Reports swing highs/lows from the last 100 main-TF bars (Williams fractal
        N=5) plus prior daily/weekly/monthly H/L. Levels are grouped above/below
        current price with distance % and bars-ago.
        """
```

- [ ] **Step 9: Run full test suite — verify no regression**

Run: `pytest -q 2>&1 | tail -5`
Expected: All PASS; total count unchanged.

- [ ] **Step 10: Commit**

```bash
git add src/agent/trader.py
git commit -m "refactor(agent): perception batch 2 — 8 tool docstrings to Google format

Sources content from Layer 1 bullets L30 (memories) / L35 (active_alerts) /
L37 (performance + trade_journal) / L45-L47 / L50. L50 softening per spec §3.2:
trailing 'arbitrary percentages' suggestion removed.

Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>"
```

---

## Task 6: Audit execution + memory tools (6 tools)

**Files:**
- Modify: `src/agent/trader.py` — 6 `@agent.tool` blocks: `set_price_alert`, `cancel_order`, `add_price_level_alert`, `set_next_wake`, `place_limit_order`, `save_memory`

Sources: persona.py L30 (save side), L31, L32, L33, L35 (set_price_alert side), L36. Migrate `Always provide reasoning.` into `Args:` section for the 5 execution tools.

**Implementation note**: same "new only" pattern as Task 4 — Read each function's current docstring before each Edit to capture the exact `old_string`.

- [ ] **Step 1: Update `set_price_alert` docstring** (source: L35 adjust side)

Edit `trader.py:310-320` block. Replace:

```python
        """Adjust volatility alert sensitivity.

        Tighten in quiet markets to catch early moves; widen in volatile
        conditions to reduce noise. Pair with get_active_alerts to review
        current configuration.

        Args:
            threshold_pct: alert threshold percent (0.5-50%).
            window_minutes: time window in minutes (1-240).
            reasoning: brief description of your decision logic.
        """
```

- [ ] **Step 2: Update `cancel_order` docstring** (source: L36)

Edit `trader.py:322-327` block. Replace:

```python
        """Cancel a pending order (limit, stop loss, or take profit).

        Use to remove stale limit orders when the market has moved away from
        your intended entry. Leaving outdated orders risks an unintended fill
        at a price that no longer makes sense.

        Args:
            order_id: id of the order to cancel.
            reasoning: brief description of your decision logic.
        """
```

- [ ] **Step 3: Update `add_price_level_alert` docstring** (source: L33)

Edit `trader.py:329-339` block. Replace:

```python
        """Set a one-shot alert at a specific price level.

        Useful for support/resistance levels you want to be notified about.
        Triggers once when reached, then auto-removes. You will be woken up
        when the level is hit.

        Args:
            price: alert price level.
            direction: 'above' (breakout) or 'below' (breakdown).
            reasoning: brief description of your decision logic.
        """
```

- [ ] **Step 4: Update `set_next_wake` docstring** (source: L31)

Edit `trader.py:341-350` block. Replace:

```python
        """Set how soon you want to check the market again.

        One-shot: only affects the next wake, then reverts to the default
        interval. Shorten when you have an open position or expect volatility;
        lengthen when the market is quiet and you have no exposure.

        Args:
            minutes: minutes until next wake.
            reasoning: brief description of your decision logic.
        """
```

- [ ] **Step 5: Update `place_limit_order` docstring** (source: L32)

Edit `trader.py:352-364` block. Replace:

```python
        """Place a limit order at a specific price (e.g., buy at support level).

        Not every entry needs to be a market order — limit orders let you
        target specific levels without paying the spread.

        Args:
            side: 'long' or 'short'.
            price: limit price.
            position_pct: percent of free balance to allocate (0-100).
            leverage: leverage multiplier.
            reasoning: brief description of your decision logic.
        """
```

- [ ] **Step 6: Update `save_memory` docstring** (source: L30 save side)

Edit `trader.py:368-374` block. Replace:

```python
        """Save a learning or observation to long-term memory.

        Save memories that your future self would find actionable — trade
        outcomes, pattern recognitions that proved correct or incorrect, and
        mistakes to avoid. Routine observations like "market is quiet" are
        not worth saving.

        Args:
            category: 'trade_review', 'market_pattern', or 'lesson'.
            content: the memory content to save.
            importance: weight 0-1 (default 0.5).
        """
```

- [ ] **Step 7: Run full test suite — verify no regression**

Run: `pytest -q 2>&1 | tail -5`
Expected: All PASS; total count unchanged.

- [ ] **Step 8: Verify no `@agent.tool` block still has bare "Always provide reasoning."**

Run: `grep -n "Always provide reasoning\." src/agent/trader.py`
Expected: zero hits (all 10 occurrences migrated to `reasoning: ...` in `Args:` sections by Task 3 + Task 6).

- [ ] **Step 9: Commit**

```bash
git add src/agent/trader.py
git commit -m "refactor(agent): execution + memory batch — 6 tool docstrings to Google format

5 execution tools migrate 'Always provide reasoning' into Args: section
(spec §3.1 audit checklist). save_memory pulls L30 save-side guidance.

Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>"
```

---

## Task 7: Update `test_persona.py` drift tests (TDD setup for Phase 4)

**Files:**
- Modify: `tests/test_persona.py` — delete 3 obsolete tests; reduce 1 (memory_quality keep `actionable` only); rename 1; rewrite 1; add 3

This task sets up failing tests that will pass once persona.py Layer 1 is reduced in Task 8.

- [ ] **Step 1: Delete `test_layer1_includes_get_price_pivots` (L279-291)**

Delete the entire function `test_layer1_includes_get_price_pivots` (12 lines). After Layer 1 reduction, Layer 1 no longer contains tool keywords; this test's premise no longer holds.

- [ ] **Step 2: Delete `test_prompt_contains_missing_tool_guidance` (L60-71)**

Delete the entire function. The keywords `"performance"` / `"trade_journal"` / `"get_active_alerts"` come from L37 + L35 bullets, which move into docstrings (Tasks 6-7).

- [ ] **Step 3: Delete `test_prompt_set_next_wake_one_shot` (L74-79)**

Delete the entire function. The keyword `"one-shot"` came from L31 (set_next_wake bullet), which moved into the `set_next_wake` docstring (Task 6).

- [ ] **Step 4: Reduce `test_prompt_contains_memory_quality_guidance` (L44-50) to minimal version**

The keyword `"not worth saving"` came from L30 (deleted), but `"actionable"` is preserved in new L28 (`Save actionable lessons to memory.`) per spec §2.1. Reduce assertions to keep the `actionable` drift guard:

Old:
```python
def test_prompt_contains_memory_quality_guidance():
    from src.agent.persona import generate_system_prompt
    prompt = generate_system_prompt(PersonaConfig())
    prompt_lower = prompt.lower()
    assert "actionable" in prompt_lower
    assert "not worth saving" in prompt_lower or "not worth" in prompt_lower
```

New:
```python
def test_prompt_contains_memory_quality_guidance():
    """L28 retained-bullet guard: 'Save actionable lessons to memory.' (spec §2.1)."""
    from src.agent.persona import generate_system_prompt
    prompt = generate_system_prompt(PersonaConfig())
    prompt_lower = prompt.lower()
    assert "actionable" in prompt_lower
```

- [ ] **Step 5: Rename `test_layer1_bullet_count_25` → `test_layer1_bullet_count_5`**

Edit `test_persona.py:264-276`. Old:
```python
def test_layer1_bullet_count_25():
    """Layer 1 bullet count drift guard (Iter 3: 24 → 25). Bullets are markdown
    rows starting with '\n- **' — matches `_build_layer1`'s tools-section format.
    """
    from src.agent.persona import generate_system_prompt
    config = PersonaConfig()
    prompt = generate_system_prompt(config)
    # Guard: Layer 2 header — protects against silent false-pass if persona.py renames it
    assert "## How to Think" in prompt, \
        "Layer 2 header changed; update split key in this test"
    layer1 = prompt.split("## How to Think")[0]
    bullet_count = layer1.count("\n- **")
    assert bullet_count == 25, f"Expected 25 Layer 1 bullets, got {bullet_count}"
```

New:
```python
def test_layer1_bullet_count_5():
    """Layer 1 bullet count drift guard (Iter 4: 25 → 5 — cross-tool behavior only).
    Bullets are markdown rows starting with '\n- **' — matches `_build_layer1`'s format.
    """
    from src.agent.persona import generate_system_prompt
    config = PersonaConfig()
    prompt = generate_system_prompt(config)
    # Guard: Layer 2 header — protects against silent false-pass if persona.py renames it
    assert "## How to Think" in prompt, \
        "Layer 2 header changed; update split key in this test"
    layer1 = prompt.split("## How to Think")[0]
    bullet_count = layer1.count("\n- **")
    assert bullet_count == 5, f"Expected 5 Layer 1 bullets, got {bullet_count}"
```

- [ ] **Step 6: Rewrite `test_prompt_contains_layer1_identity` (L7-19) with scope limit**

**Motivation** (corrected from earlier draft): the only assertion that actually fails after Iter 4 is `"set_next_wake" in prompt_lower or "wake" in prompt_lower` — `set_next_wake` bullet is removed and `"wake" in "woken"` is False (Python substring). The `timeframe` / `memory` assertions still pass via Layer 2 fallback (`across timeframes` at Layer 2 L59, `memory` at Layer 2 L71). Rewrite is therefore for **intent clarity** (test name says "layer1" but original scoped over the whole prompt) — scope-limit the test to Layer 1 to actually guard Layer 1, and move timeframe/memory coverage to Layer-2-specific tests.

Edit `test_persona.py:5-19`. Old:
```python
def test_prompt_contains_layer1_identity():
    from src.agent.persona import generate_system_prompt
    prompt = generate_system_prompt(PersonaConfig())
    prompt_lower = prompt.lower()
    # Market context
    assert "perpetual" in prompt_lower
    assert "one-way" in prompt_lower or "single direction" in prompt_lower or "close position first" in prompt_lower
    # Fill timing
    assert "fill" in prompt_lower
    # Multi-timeframe (P0)
    assert "timeframe" in prompt_lower
    # Memory
    assert "save_memory" in prompt_lower or "memory" in prompt_lower
    # Dynamic wake
    assert "set_next_wake" in prompt_lower or "wake" in prompt_lower
```

New:
```python
def test_prompt_contains_layer1_identity():
    """Layer 1 keyword presence — scope limited to Layer 1 only (intent clarity).
    After Iter 4 slim-down Layer 1 only contains: market context (perpetual / one-way)
    + 5 cross-tool bullets (fill / woken trigger responses). timeframe / memory
    coverage moves to Layer 2 tests; tool keywords (set_next_wake, save_memory etc.)
    live in docstrings (separate from system prompt).
    """
    from src.agent.persona import generate_system_prompt
    prompt = generate_system_prompt(PersonaConfig())
    assert "## How to Think" in prompt, \
        "Layer 2 header changed; update split key in this test"
    layer1 = prompt.split("## How to Think")[0].lower()
    # Market context (preserved from old L22)
    assert "perpetual" in layer1
    assert "one-way" in layer1 or "single direction" in layer1 or "close position first" in layer1
    # Fill bullets (L26 / L27 / L28) preserved
    assert "fill" in layer1
    # Trigger response keyword: "woken" appears in L27/L28/L34 (trigger response bullets)
    assert "woken" in layer1
```

- [ ] **Step 7: Add `test_layer1_no_tool_invocation_descriptions`**

Add to `test_persona.py` (place after `test_layer1_bullet_count_5`):

```python
def test_layer1_no_tool_invocation_descriptions():
    """After Iter 4, Layer 1 should not contain tool-name invocation patterns —
    tool descriptions belong in docstrings (DRY). The 5 retained bullets describe
    cross-tool behavior, not single-tool invocation.
    """
    import re
    from src.agent.persona import generate_system_prompt
    prompt = generate_system_prompt(PersonaConfig())
    assert "## How to Think" in prompt, \
        "Layer 2 header changed; update split key in this test"
    layer1 = prompt.split("## How to Think")[0]
    # Pattern: "Use get_<tool_name>" or "Use set_<tool_name>" etc. — typical bullet style
    # for tool-invocation descriptions (matches L29-L50 deleted bullets).
    forbidden = re.findall(r"\bUse (get|set|add|cancel|place|save)_\w+", layer1)
    assert forbidden == [], \
        f"Layer 1 should not invoke tools by name (found: {forbidden}); move to docstrings."
```

- [ ] **Step 8: Add `test_prompt_l27_softened`**

Add to `test_persona.py` (place after `test_layer1_no_tool_invocation_descriptions`):

```python
def test_prompt_l27_softened():
    """L27 Open fill response softening (spec §3.2): hard-rule wording removed.
    Old phrases ('check the chart', 'do not skip', 'arbitrary ones') deleted;
    softened wording ('use market data') retained.
    """
    from src.agent.persona import generate_system_prompt
    prompt = generate_system_prompt(PersonaConfig()).lower()
    # Hard-rule wording must NOT be present
    assert "do not skip market data" not in prompt
    assert "structural support/resistance" not in prompt
    assert "arbitrary ones" not in prompt
    # Softened wording must be present
    assert "use market data" in prompt
```

- [ ] **Step 9: Add `test_prompt_l65_softened`**

Add to `test_persona.py` (place after `test_prompt_l27_softened`):

```python
def test_prompt_l65_softened():
    """L65 Layer 2 Risk-Reward single-direction sub-clause removed (spec §3.3).
    The clause '— at a structural level, not an arbitrary percentage' was
    deleted because it imposes a one-way decision rule on stop-loss placement.
    The open question 'Where is the logical stop loss?' is preserved.
    """
    from src.agent.persona import generate_system_prompt
    prompt = generate_system_prompt(PersonaConfig()).lower()
    # Single-direction wording must NOT be present
    assert "arbitrary percentage" not in prompt
    assert "at a structural level" not in prompt
    # Open question preserved
    assert "where is the logical stop loss" in prompt
```

- [ ] **Step 10: Run persona tests — verify expected failures**

Run: `pytest tests/test_persona.py -v`
Expected: 
- `test_layer1_bullet_count_5` FAIL (still 25 bullets)
- `test_layer1_no_tool_invocation_descriptions` FAIL (Layer 1 still has `Use get_market_data` etc.)
- `test_prompt_contains_layer1_identity` PASS (assertions are subsets of current prompt — `woken` already in L27)
- `test_prompt_l27_softened` FAIL (current L27 still has `do not skip market data`)
- `test_prompt_l65_softened` FAIL (current L65 still has `arbitrary percentage`)
- All other tests PASS.

This is the expected TDD red state for Tasks 9-10.

- [ ] **Step 11: Commit**

```bash
git add tests/test_persona.py
git commit -m "test(persona): set up failing tests for Iter 4 Layer 1 + L27/L65 softening

- delete 3 obsolete tests (premise invalid after Layer 1 slim-down)
- reduce memory_quality_guidance to keep 'actionable' drift guard (L28 retained)
- rename bullet_count_25 → _5
- rewrite layer1_identity scoped to Layer 1 (intent clarity)
- add 3 new tests (no_tool_invocation, l27_softened, l65_softened)

Tests fail until persona.py is updated in next tasks (TDD red state).

Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>"
```

---

## Task 8: Reduce `persona.py` Layer 1 (25 → 5 bullets) + soften L27 + rename header

**Files:**
- Modify: `src/agent/persona.py:17-50` (Layer 1 `_build_layer1` body)

- [ ] **Step 1: Replace `_build_layer1()` body**

Edit `src/agent/persona.py:17-50`. Replace the entire `_build_layer1()` function body. Old (L17-50, the function definition + return statement with 25 bullets):

```python
def _build_layer1() -> str:
    return """You are a cryptocurrency trader operating autonomously. ...

## Tool Usage Notes

- **Fill timing**: ...
- **Open fill response**: ...
... (25 bullets total) ...
- **Price pivots**: Use get_price_pivots to scan structural levels..."""
```

New:

```python
def _build_layer1() -> str:
    return """You are a cryptocurrency trader operating autonomously. You analyze markets, manage positions, and make trading decisions using the tools available to you.

## Market Context

You trade USDT-margined perpetual futures (no expiry date). The exchange uses one-way position mode — you cannot hold long and short positions on the same symbol simultaneously. To reverse direction, close your current position first. Leverage cannot be changed while holding a position. Every trade incurs fees on both entry and exit — frequent small trades can erode capital through friction costs alone.

## Cross-Tool Behavior

- **Fill timing**: After submitting a market order, you will be notified when it fills via a separate trigger. Set stop loss and take profit only after receiving fill confirmation — do not attempt in the same cycle as order submission.
- **Open fill response**: When woken by an order fill notification (conditional trigger) that opened a position, identify your stop loss and take profit levels and set them. Use market data to inform these levels.
- **Close fill response**: When woken by a fill that closed a position (stop loss, take profit, or manual close), review the trade outcome: what worked, what didn't, and what you would do differently. Save actionable lessons to memory.
- **Alert response**: When woken by a price alert, assess whether the price move changes your thesis. For a price level alert, evaluate whether the level held or broke and what that implies. For a volatility alert, determine if the move is the start of a trend or just noise before acting.
- **OCO atomicity on OKX**: stop and take_profit orders that share an algoId (rendered as `[OCO]` in get_open_orders) are atomic — cancelling or triggering one leg removes both. If you intend to replace only one leg, re-create the other leg immediately after."""
```

Changes (per spec §2.1 + §2.2 + §3.2):
- Header `## Tool Usage Notes` → `## Cross-Tool Behavior`
- 5 retained bullets (Fill timing / Open fill response / Close fill response / Alert response / OCO atomicity) — original L26/L27/L28/L34/L49
- L27 softened (per spec §3.2):
  - Deleted: "check the chart to identify structural support/resistance levels, then set stop loss and take profit at those levels."
  - Deleted: "Do not skip market data — you need it to place stops at meaningful prices, not arbitrary ones."
  - New: "identify your stop loss and take profit levels and set them. Use market data to inform these levels."
- 20 deleted bullets (L29, L30, L31, L32, L33, L35, L36, L37, L38, L39, L40, L41, L42, L43, L44, L45, L46, L47, L48, L50) — content already migrated to docstrings in Tasks 5-7

- [ ] **Step 2: Run persona tests — verify Phase 4 part 1 passes**

Run: `pytest tests/test_persona.py -v`
Expected:
- `test_layer1_bullet_count_5` PASS
- `test_layer1_no_tool_invocation_descriptions` PASS
- `test_prompt_contains_layer1_identity` PASS
- `test_prompt_l27_softened` PASS
- `test_prompt_l65_softened` FAIL (Task 9 not done yet)
- All other tests PASS

- [ ] **Step 3: Commit**

```bash
git add src/agent/persona.py
git commit -m "refactor(persona): Layer 1 25 → 5 bullets + L27 soften + header rename

20 bullets removed (content moved to docstrings in Tasks 5-7).
Header '## Tool Usage Notes' → '## Cross-Tool Behavior' to reflect new scope.
L27 softening removes 'check the chart' / 'do not skip' / 'arbitrary ones'
hard wording (spec §3.2).

Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>"
```

---

## Task 9: Remove L65 single-direction sub-clause in Layer 2

**Files:**
- Modify: `src/agent/persona.py:65` (Layer 2 Risk-Reward section)

- [ ] **Step 1: Edit `_build_layer2()` Risk-Reward block**

Edit `src/agent/persona.py:65`. Old:
```python
**Risk-Reward**
What is the risk-to-reward ratio of this potential trade? Where is the logical stop loss — at a structural level, not an arbitrary percentage? Is the potential reward worth the risk? Would a better entry improve the ratio?
```

New:
```python
**Risk-Reward**
What is the risk-to-reward ratio of this potential trade? Where is the logical stop loss? Is the potential reward worth the risk? Would a better entry improve the ratio?
```

Only the sub-clause "— at a structural level, not an arbitrary percentage" is deleted. The four open questions remain.

- [ ] **Step 2: Run persona tests — verify all pass**

Run: `pytest tests/test_persona.py -v`
Expected: All PASS, including `test_prompt_l65_softened`.

- [ ] **Step 3: Commit**

```bash
git add src/agent/persona.py
git commit -m "refactor(persona): Layer 2 L65 — remove 'structural level vs arbitrary percentage' single-direction clause

Spec §3.3: the open question 'Where is the logical stop loss?' is preserved;
only the prescriptive sub-clause is removed (one-way decision rule).

Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>"
```

---

## Task 10: Fact-only tests for 7 old perception tools

**Files:**
- Modify: `tests/test_fact_only_wordlist.py` (add 7 new test functions)

**Reference template:** existing `test_get_position_fact_only` (test_fact_only_wordlist.py:163-214) — uses real `Balance(total_usdt=..., free_usdt=..., used_usdt=...)` / `Order(order_type=..., is_algo=False)` / `Ticker(..., high=, low=, base_volume=)` constructors and the actual `deps.market_data.get_ohlcv_dataframe` / `deps.technical.compute_indicators` paths.

**TradingDeps real fields** (src/agent/trader.py:18-39): `symbol` / `timeframe` / `market_data` / `exchange` / `technical` / `memory` / `db_engine` / `metrics` / `news` / `macro` / `crypto_etf` / `onchain` / `set_next_wake_fn`. Plan field names like `journal` / `alert_service` / `etf` / `stablecoin` / `scheduler` **DO NOT EXIST** — use the real ones.

**Strategy:** prefer minimal-mock paths that exercise rendered output (early-return strings or happy-path with verified mocks). The tools below were inspected to determine exact mock paths before this plan was written.

Tools: `get_market_data`, `get_account_balance`, `get_open_orders`, `get_trade_journal`, `get_memories`, `get_active_alerts`, `get_performance`.

**Coverage depth note (per spec §3.5)**: `get_trade_journal` (`db_engine=None` → "No trade journal entries yet.") and `get_performance` (`metrics=None` → minimal balance summary) use **early-return paths** — single-line/few-line templates with near-zero banned-word risk. These are **minimum guards**, not maximum-risk-surface coverage. Rich-text paths (real db rows / populated `MetricsService.compute()` output) are deferred to observation-period follow-up (when real LLM-triggered tool outputs are available as sampling source). Other 5 old perception tools use happy-path mocks (`get_market_data` / `get_account_balance` / `get_open_orders` / `get_memories` / `get_active_alerts`).

- [ ] **Step 1: Extend `MockDeps` with new fields needed by old perception tools**

Edit `tests/test_fact_only_wordlist.py:36-43` (the `MockDeps` dataclass). Add fields:

```python
@dataclass
class MockDeps:
    symbol: str = "BTC/USDT:USDT"
    initial_balance: float = 10000.0
    exchange: AsyncMock = field(default_factory=AsyncMock)
    market_data: AsyncMock = field(default_factory=AsyncMock)
    technical: AsyncMock = field(default_factory=AsyncMock)
    timeframe: str = "5m"
    # Iter 4 fact-only coverage extension (real TradingDeps field names):
    memory: AsyncMock = field(default_factory=AsyncMock)
    db_engine: object | None = None
    metrics: object | None = None
    news: object | None = None
    macro: object | None = None
    crypto_etf: object | None = None
    onchain: object | None = None
    set_next_wake_fn: object | None = None
    wake_min_minutes: int = 1
    wake_max_minutes: int = 60
    session_id: str = "test-session"
    cycle_id: str | None = "test-cycle"  # defense-in-depth: ToolCallRecorder reads this in real flow
    # Execution-tool defaults (Task 12) — skip approval gate by default
    approval_enabled: bool = False
    approval_gate: object | None = None
```

- [ ] **Step 2: Add `get_market_data` fact-only test**

Append to `tests/test_fact_only_wordlist.py`:

```python
@pytest.mark.asyncio
async def test_get_market_data_fact_only(mocker):
    """get_market_data typical-path output must not emit banned subjective words."""
    from src.agent.tools_perception import get_market_data
    deps = MockDeps()
    df = pd.DataFrame([{"timestamp": 0, "open": 64000, "high": 64100, "low": 63900,
                        "close": 64050, "volume": 100.0} for _ in range(250)])
    deps.market_data.get_ohlcv_dataframe = AsyncMock(return_value=df)
    deps.market_data.get_ticker = AsyncMock(return_value=Ticker(
        symbol="BTC/USDT:USDT", last=64050.0, bid=64049.5, ask=64050.5,
        high=65000.0, low=63000.0, base_volume=1000.0, timestamp=0,
    ))
    deps.technical.compute_indicators = mocker.Mock(return_value={
        "rsi_14": 55.0, "macd": 0.5, "macd_signal": 0.3, "macd_hist": 0.2,
        "bb_upper": 65000.0, "bb_middle": 64000.0, "bb_lower": 63000.0, "atr_14": 85.0,
    })
    output = await get_market_data(deps)
    hits = _scan(output)
    assert hits == [], f"get_market_data emitted banned words: {hits}"
```

**Note**: If `get_market_data` calls additional service methods not mocked above, the test will surface them as `AttributeError` on first run — extend mock setup as needed. Inspect `src/agent/tools_perception.py:39` to confirm signature before finalizing.

- [ ] **Step 3: Add `get_account_balance` fact-only test**

```python
@pytest.mark.asyncio
async def test_get_account_balance_fact_only():
    """Happy path: rendered balance lines."""
    from src.agent.tools_perception import get_account_balance
    deps = MockDeps()
    deps.exchange.fetch_balance = AsyncMock(return_value=Balance(
        total_usdt=10500.0, free_usdt=8500.0, used_usdt=2000.0,
    ))
    output = await get_account_balance(deps)
    hits = _scan(output)
    assert hits == [], f"get_account_balance emitted banned words: {hits}"
```

- [ ] **Step 4: Add `get_open_orders` fact-only test**

```python
@pytest.mark.asyncio
async def test_get_open_orders_fact_only():
    """Empty + non-empty rendering paths."""
    from src.agent.tools_perception import get_open_orders
    deps = MockDeps()
    outputs = []

    # Scenario 1: no pending orders → "No pending orders." (early return)
    deps.exchange.fetch_open_orders = AsyncMock(return_value=[])
    outputs.append(await get_open_orders(deps))

    # Scenario 2: limit + OCO pair (covers _render_single_order + OCO branch)
    deps.exchange.fetch_open_orders = AsyncMock(return_value=[
        Order(id="lim1", symbol="BTC/USDT:USDT", side="buy", order_type="limit",
              amount=0.01, price=63000.0, status="open"),
        Order(id="oco1", symbol="BTC/USDT:USDT", side="sell", order_type="stop",
              amount=0.01, price=62000.0, status="open", is_algo=True),
        Order(id="oco1", symbol="BTC/USDT:USDT", side="sell", order_type="take_profit",
              amount=0.01, price=66000.0, status="open", is_algo=True),
    ])
    deps.market_data.get_ticker = AsyncMock(return_value=Ticker(
        symbol="BTC/USDT:USDT", last=64000.0, bid=63999.5, ask=64000.5,
        high=65000.0, low=63000.0, base_volume=1000.0, timestamp=0,
    ))
    outputs.append(await get_open_orders(deps))

    hits = _scan("\n".join(outputs))
    assert hits == [], f"get_open_orders emitted banned words: {hits}"
```

- [ ] **Step 5: Add `get_trade_journal` fact-only test**

```python
@pytest.mark.asyncio
async def test_get_trade_journal_fact_only():
    """Early return path when no db_engine — covers degraded-state output."""
    from src.agent.tools_perception import get_trade_journal
    deps = MockDeps()  # db_engine=None by default
    output = await get_trade_journal(deps)
    hits = _scan(output)
    assert hits == [], f"get_trade_journal emitted banned words: {hits}"
```

(Happy-path with real db engine + decision history is covered by integration tests; here we just verify the early-return string is fact-only clean.)

- [ ] **Step 6: Add `get_memories` fact-only test**

```python
@pytest.mark.asyncio
async def test_get_memories_fact_only():
    """deps.memory.format_for_prompt() returns rendered string."""
    from src.agent.tools_perception import get_memories
    deps = MockDeps()
    deps.memory.format_for_prompt = AsyncMock(return_value="No memories yet.")
    output = await get_memories(deps)
    hits = _scan(output)
    assert hits == [], f"get_memories emitted banned words: {hits}"
```

- [ ] **Step 7: Add `get_active_alerts` fact-only test**

```python
@pytest.mark.asyncio
async def test_get_active_alerts_fact_only(mocker):
    """Volatility config + price level alerts rendering."""
    from src.agent.tools_perception import get_active_alerts
    deps = MockDeps()
    outputs = []

    # Scenario 1: alerts OFF + no price levels
    deps.exchange.get_alert_params = mocker.Mock(return_value=None)
    deps.exchange.get_price_level_alerts = mocker.Mock(return_value=[])
    outputs.append(await get_active_alerts(deps))

    # Scenario 2: alerts ON + 2 price levels
    deps.exchange.get_alert_params = mocker.Mock(return_value=(1.5, 30))
    deps.exchange.get_price_level_alerts = mocker.Mock(return_value=[
        {"direction": "above", "price": 65000.0, "reasoning": "test"},
        {"direction": "below", "price": 62000.0, "reasoning": "test"},
    ])
    outputs.append(await get_active_alerts(deps))

    hits = _scan("\n".join(outputs))
    assert hits == [], f"get_active_alerts emitted banned words: {hits}"
```

- [ ] **Step 8: Add `get_performance` fact-only test**

```python
@pytest.mark.asyncio
async def test_get_performance_fact_only():
    """metrics=None early-return path covers minimal rendering."""
    from src.agent.tools_perception import get_performance
    deps = MockDeps()  # metrics=None by default
    deps.exchange.fetch_balance = AsyncMock(return_value=Balance(
        total_usdt=10100.0, free_usdt=9000.0, used_usdt=1100.0,
    ))
    output = await get_performance(deps)
    hits = _scan(output)
    assert hits == [], f"get_performance emitted banned words: {hits}"
```

(Happy path with real `MetricsService.compute()` returning a populated stats object is covered by integration tests; here we verify the early-return string is fact-only clean.)

- [ ] **Step 9: Run new tests — verify all pass**

Run: `pytest tests/test_fact_only_wordlist.py -k "fact_only" -v 2>&1 | tail -20`
Expected: 7 new tests PASS (assuming fact-only output already clean — if any fails, the tool emitted a banned word and needs investigation).

If a tool's fixture imports fail, fix the MockDeps extension before proceeding.

- [ ] **Step 10: Commit**

```bash
git add tests/test_fact_only_wordlist.py
git commit -m "test(fact-only): add global wordlist coverage for 7 old perception tools

Spec §3.5 (4.5): get_market_data / get_account_balance / get_open_orders /
get_trade_journal / get_memories / get_active_alerts / get_performance.

Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>"
```

---

## Task 11: Fact-only tests for 9 new perception tools

**Files:**
- Modify: `tests/test_fact_only_wordlist.py` (add 9 new test functions)

Tools: `get_market_news`, `get_exchange_announcements`, `get_macro_calendar`, `get_derivatives_data`, `get_higher_timeframe_view`, `get_macro_context`, `get_etf_flows`, `get_stablecoin_supply`, `get_price_pivots` (global wordlist as separate from existing per-tool `PIVOTS_BANNED_RE` test).

**Coverage depth note (per spec §3.5)**: `get_market_news` (`news=None`), `get_macro_context` (`macro=None`), `get_etf_flows` (`crypto_etf=None`), `get_stablecoin_supply` (`onchain=None`) use **early-return paths** ("service not configured." single-line) — minimum guards only. Rich-text paths (full `MacroSnapshot` / news headlines + FGI / `ETFFlowEntry` lists / coin supply rendering) are deferred to observation-period follow-up (real LLM tool output as sampling source — diagnostic value > mock-driven). `get_exchange_announcements` / `get_macro_calendar` / `get_derivatives_data` exercise both list and degraded paths (rich + degraded). `get_higher_timeframe_view` / `get_price_pivots` use 250-bar / 100-bar happy-path fixtures.

**Real deps field names verified** (per tools_perception.py inspection):
- `get_market_news`: `deps.news` (None gives "News service not configured."); happy path uses `deps.news.get_news` + `deps.news.get_fear_greed_index`
- `get_exchange_announcements` / `get_macro_calendar`: `deps.news.get_announcements` / `deps.news.get_macro_events`
- `get_derivatives_data`: `deps.market_data.get_funding_rate` / `get_open_interest` / `get_long_short_ratio`
- `get_higher_timeframe_view`: `deps.market_data.get_ohlcv_dataframe` (NOT `get_ohlcv`)
- `get_macro_context`: `deps.macro.get_snapshot()` (None gives "Macro service not configured.")
- `get_etf_flows`: `deps.crypto_etf.get_etf_flows("BTC", days)` (NOT `deps.etf`; None gives "ETF flows service not configured.")
- `get_stablecoin_supply`: `deps.onchain.get_stablecoin_snapshot()` (NOT `deps.stablecoin`; None gives "Onchain service not configured.")
- `get_price_pivots`: `deps.market_data.get_ohlcv_dataframe` (use 100-row pd.DataFrame with timestamp column; reference test_get_price_pivots_fact_only_5_scenarios at L336)

- [ ] **Step 1: Add 9 fact-only tests**

Append to `tests/test_fact_only_wordlist.py`:

```python
@pytest.mark.asyncio
async def test_get_market_news_fact_only():
    """News service unavailable early-return path."""
    from src.agent.tools_perception import get_market_news
    deps = MockDeps()  # news=None by default
    output = await get_market_news(deps)
    hits = _scan(output)
    assert hits == [], f"get_market_news emitted banned words: {hits}"


@pytest.mark.asyncio
async def test_get_exchange_announcements_fact_only():
    """Empty announcements list (typical) + None (degraded) outputs."""
    from src.agent.tools_perception import get_exchange_announcements
    deps = MockDeps()
    deps.news = AsyncMock()
    outputs = []
    deps.news.get_announcements = AsyncMock(return_value=[])
    outputs.append(await get_exchange_announcements(deps))
    deps.news.get_announcements = AsyncMock(return_value=None)
    outputs.append(await get_exchange_announcements(deps))
    hits = _scan("\n".join(outputs))
    assert hits == [], f"get_exchange_announcements emitted banned words: {hits}"


@pytest.mark.asyncio
async def test_get_macro_calendar_fact_only():
    """Empty (footer shows) + None (footer hidden) per spec §3.4."""
    from src.agent.tools_perception import get_macro_calendar
    deps = MockDeps()
    deps.news = AsyncMock()
    outputs = []
    deps.news.get_macro_events = AsyncMock(return_value=[])
    outputs.append(await get_macro_calendar(deps))
    deps.news.get_macro_events = AsyncMock(return_value=None)
    outputs.append(await get_macro_calendar(deps))
    hits = _scan("\n".join(outputs))
    assert hits == [], f"get_macro_calendar emitted banned words: {hits}"


@pytest.mark.asyncio
async def test_get_derivatives_data_fact_only():
    """All 3 sub-fetches fail → 'temporarily unavailable' rendering path."""
    from src.agent.tools_perception import get_derivatives_data
    deps = MockDeps()
    deps.market_data.get_funding_rate = AsyncMock(side_effect=Exception("down"))
    deps.market_data.get_open_interest = AsyncMock(side_effect=Exception("down"))
    deps.market_data.get_long_short_ratio = AsyncMock(side_effect=Exception("down"))
    output = await get_derivatives_data(deps)
    hits = _scan(output)
    assert hits == [], f"get_derivatives_data emitted banned words: {hits}"


@pytest.mark.asyncio
async def test_get_higher_timeframe_view_fact_only():
    """Typical 250-bar OHLCV → MA + range rendering."""
    from src.agent.tools_perception import get_higher_timeframe_view
    deps = MockDeps()
    df = pd.DataFrame([{"timestamp": i, "open": 64000 + i, "high": 64100 + i,
                        "low": 63900 + i, "close": 64050 + i, "volume": 100.0}
                       for i in range(250)])
    deps.market_data.get_ohlcv_dataframe = AsyncMock(return_value=df)
    output = await get_higher_timeframe_view(deps, "4h")
    hits = _scan(output)
    assert hits == [], f"get_higher_timeframe_view emitted banned words: {hits}"


@pytest.mark.asyncio
async def test_get_macro_context_fact_only():
    """macro=None early-return path."""
    from src.agent.tools_perception import get_macro_context
    deps = MockDeps()  # macro=None by default
    output = await get_macro_context(deps)
    hits = _scan(output)
    assert hits == [], f"get_macro_context emitted banned words: {hits}"


@pytest.mark.asyncio
async def test_get_etf_flows_fact_only():
    """crypto_etf=None early-return path."""
    from src.agent.tools_perception import get_etf_flows
    deps = MockDeps()  # crypto_etf=None by default
    output = await get_etf_flows(deps)
    hits = _scan(output)
    assert hits == [], f"get_etf_flows emitted banned words: {hits}"


@pytest.mark.asyncio
async def test_get_stablecoin_supply_fact_only():
    """onchain=None early-return path."""
    from src.agent.tools_perception import get_stablecoin_supply
    deps = MockDeps()  # onchain=None by default
    output = await get_stablecoin_supply(deps)
    hits = _scan(output)
    assert hits == [], f"get_stablecoin_supply emitted banned words: {hits}"


@pytest.mark.asyncio
async def test_get_price_pivots_global_wordlist_fact_only():
    """Global wordlist coverage — separate from existing PIVOTS_BANNED_WORDS per-tool
    test (test_fact_only_wordlist.py:336+) which guards strong/weak/important/key/major/minor.
    This test ensures price_pivots also passes the global sentiment wordlist.
    """
    from src.agent.tools_perception import get_price_pivots
    deps = MockDeps()
    # Inline 100-row DataFrame with timestamp column (price_pivots reads timestamp).
    # Shape independent from `_pivots_df` helper — that helper's no-timestamp shape
    # is incompatible with this tool, so we build a per-test fixture instead.
    df = pd.DataFrame([{"timestamp": i, "open": 64000, "high": 64100 + (i % 7) * 10,
                        "low": 63900 - (i % 5) * 10, "close": 64050,
                        "volume": 100.0} for i in range(100)])
    deps.market_data.get_ohlcv_dataframe = AsyncMock(return_value=df)
    output = await get_price_pivots(deps)
    hits = _scan(output)
    assert hits == [], f"get_price_pivots emitted banned words: {hits}"
```

- [ ] **Step 2: Run new tests — verify all pass**

Run: `pytest tests/test_fact_only_wordlist.py -k "fact_only and (market_news or exchange_announcements or macro_calendar or derivatives or higher_timeframe or macro_context or etf_flows or stablecoin or price_pivots_global)" -v`
Expected: 9 new tests PASS.

(The `fact_only and (...)` qualifier prevents accidentally matching Task 1's `test_exchange_announcements_*` / `test_macro_calendar_*` tests in `tests/test_news_tools.py`, which are functional tests without the `_fact_only` suffix.)

- [ ] **Step 3: Commit**

```bash
git add tests/test_fact_only_wordlist.py
git commit -m "test(fact-only): add global wordlist coverage for 9 new perception tools

Includes N8 split tools (exchange_announcements + macro_calendar) and
price_pivots global wordlist (separate function from existing per-tool
PIVOTS_BANNED_RE test).

Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>"
```

---

## Task 12: Fact-only batch parametrize for 10 execution tools

**Files:**
- Modify: `tests/test_fact_only_wordlist.py` (add 1 parametrized test function covering 10 execution tools)

Spec §3.5 strategy: most execution tool outputs are fixed templates ("Order placed" / "Insufficient margin" / "Invalid symbol") with low fact-only risk. Use single parametrized function to keep test code DRY.

**Real execution-tool deps paths verified** (per tools_execution.py inspection):
- `open_position`: `exchange.fetch_balance` / `market_data.get_ticker` / `exchange.amount_to_precision` / `exchange.has_pending_market_order` / `_check_approval` (skip via `approval_enabled=False`) / `exchange.set_leverage` / `exchange.create_order` / `_record_action` (skip via `db_engine=None`)
- `close_position`: `exchange.fetch_positions` (returns [] → "No positions to close." early return)
- `set_stop_loss` / `set_take_profit`: `exchange.fetch_positions` (returns [] → "No open position to set..." early return)
- `adjust_leverage`: `exchange.set_leverage` (no-op return); `_record_action` skipped via `db_engine=None`
- `set_price_alert`: `exchange.get_alert_params()` returns None → "Alerts are disabled for this session." early return
- `add_price_level_alert`: invalid `direction` parameter → "Invalid direction: must be 'above' or 'below'..." early return
- `set_next_wake`: `set_next_wake_fn=None` → "Dynamic wake not available" early return
- `place_limit_order`: invalid `side` parameter → "side must be 'long' or 'short'" early return
- `cancel_order`: `exchange.fetch_open_orders` returns [] → "Order not found or already filled" early return

**Strategy**: prefer early-return paths (string templates that exercise the rendered output without complex order/balance/position state). For `open_position` we exercise the happy path with full mocks since its early-return scenarios (precision rounding to 0 / pending order) emit borderline wording worth scanning.

- [ ] **Step 1: Add batch parametrize test**

Append to `tests/test_fact_only_wordlist.py`:

```python
@pytest.mark.asyncio
@pytest.mark.parametrize("invoker", [
    "_invoke_open_position",
    "_invoke_close_position",
    "_invoke_set_stop_loss",
    "_invoke_set_take_profit",
    "_invoke_adjust_leverage",
    "_invoke_set_price_alert",
    "_invoke_cancel_order",
    "_invoke_add_price_level_alert",
    "_invoke_set_next_wake",
    "_invoke_place_limit_order",
])
async def test_execution_tool_fact_only(invoker, mocker):
    """Execution tools — outputs are fixed templates; verify global wordlist clean.
    Each helper exercises a representative path (early-return where minimal,
    happy-path where the early-return is trivially clean).
    MockDeps default `approval_enabled=False` skips the approval gate."""
    deps = MockDeps()
    output = await globals()[invoker](deps, mocker)
    hits = _scan(output)
    assert hits == [], f"{invoker} emitted banned words: {hits}"


# === Execution tool invokers — each sets up minimal mocks for one representative path ===

async def _invoke_open_position(deps, mocker):
    """Happy path: full mock chain through create_order."""
    from src.agent.tools_execution import open_position
    deps.exchange.fetch_balance = AsyncMock(return_value=Balance(
        total_usdt=10000.0, free_usdt=8000.0, used_usdt=2000.0,
    ))
    deps.market_data.get_ticker = AsyncMock(return_value=Ticker(
        symbol="BTC/USDT:USDT", last=64000.0, bid=63999.5, ask=64000.5,
        high=65000.0, low=63000.0, base_volume=1000.0, timestamp=0,
    ))
    deps.exchange.amount_to_precision = mocker.Mock(return_value=0.01)
    deps.exchange.has_pending_market_order = mocker.Mock(return_value=False)
    deps.exchange.set_leverage = AsyncMock(return_value=None)
    deps.exchange.create_order = AsyncMock(return_value=Order(
        id="ord1", symbol="BTC/USDT:USDT", side="buy", order_type="market",
        amount=0.01, price=None, status="open",
    ))
    return await open_position(deps, "long", 10.0, 5, reasoning="test")


async def _invoke_close_position(deps, mocker):
    """Early return: no positions."""
    from src.agent.tools_execution import close_position
    deps.exchange.fetch_positions = AsyncMock(return_value=[])
    return await close_position(deps, reasoning="test")


async def _invoke_set_stop_loss(deps, mocker):
    """Early return: no position."""
    from src.agent.tools_execution import set_stop_loss
    deps.exchange.fetch_positions = AsyncMock(return_value=[])
    return await set_stop_loss(deps, 62000.0, reasoning="test")


async def _invoke_set_take_profit(deps, mocker):
    """Early return: no position."""
    from src.agent.tools_execution import set_take_profit
    deps.exchange.fetch_positions = AsyncMock(return_value=[])
    return await set_take_profit(deps, 66000.0, reasoning="test")


async def _invoke_adjust_leverage(deps, mocker):
    """Happy path: set_leverage no-op + record_action skipped (db_engine=None)."""
    from src.agent.tools_execution import adjust_leverage
    deps.exchange.set_leverage = AsyncMock(return_value=None)
    return await adjust_leverage(deps, 5, reasoning="test")


async def _invoke_set_price_alert(deps, mocker):
    """Early return: alerts disabled."""
    from src.agent.tools_execution import set_price_alert
    deps.exchange.get_alert_params = mocker.Mock(return_value=None)
    return await set_price_alert(deps, 1.5, 30, reasoning="test")


async def _invoke_cancel_order(deps, mocker):
    """Early return: order not found."""
    from src.agent.tools_execution import cancel_order
    deps.exchange.fetch_open_orders = AsyncMock(return_value=[])
    return await cancel_order(deps, "nonexistent-id", reasoning="test")


async def _invoke_add_price_level_alert(deps, mocker):
    """Early return: invalid direction (must be 'above' or 'below')."""
    from src.agent.tools_execution import add_price_level_alert
    return await add_price_level_alert(deps, 64000.0, "sideways", reasoning="test")


async def _invoke_set_next_wake(deps, mocker):
    """Early return: set_next_wake_fn=None."""
    from src.agent.tools_execution import set_next_wake
    return await set_next_wake(deps, 30, reasoning="test")


async def _invoke_place_limit_order(deps, mocker):
    """Early return: invalid side (must be 'long' or 'short')."""
    from src.agent.tools_execution import place_limit_order
    return await place_limit_order(deps, "neutral", 64000.0, 10.0, 5, reasoning="test")
```

- [ ] **Step 2: Run batch test — verify 10 cases pass**

Run: `pytest tests/test_fact_only_wordlist.py::test_execution_tool_fact_only -v`
Expected: 10 PASS (one parametrize case per execution tool).

- [ ] **Step 3: Commit**

```bash
git add tests/test_fact_only_wordlist.py
git commit -m "test(fact-only): batch parametrize coverage for 10 execution tools

Single test function with parametrize fixture — execution tool outputs
are fixed templates with low fact-only risk; DRY over 10 separate functions.

Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>"
```

---

## Task 13: Fact-only test for `save_memory`

**Files:**
- Modify: `tests/test_fact_only_wordlist.py` (add 1 test function)

**Real path verified** (tools_memory.py:9-17): `await deps.memory.save_long_term(category, content, relevance_score=importance)`. Returns string `f"Memory saved [{category}] (importance={importance:.1f}): {content[:80]}"`.

- [ ] **Step 1: Add `save_memory` fact-only test**

Append to `tests/test_fact_only_wordlist.py`:

```python
@pytest.mark.asyncio
async def test_save_memory_fact_only():
    """save_memory output (typical save + neutral content) must not emit banned subjective words."""
    from src.agent.tools_memory import save_memory
    deps = MockDeps()
    deps.memory.save_long_term = AsyncMock(return_value=None)
    output = await save_memory(deps, "lesson", "Reduced position size after observing slippage", 0.5)
    hits = _scan(output)
    assert hits == [], f"save_memory emitted banned words: {hits}"
```

- [ ] **Step 2: Run new test — verify it passes**

Run: `pytest tests/test_fact_only_wordlist.py::test_save_memory_fact_only -v`
Expected: PASS.

- [ ] **Step 3: Commit**

```bash
git add tests/test_fact_only_wordlist.py
git commit -m "test(fact-only): add global wordlist coverage for save_memory

Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>"
```

---

## Task 14: Final verification + push branch

**Files:**
- No code changes; full-suite verification + branch push.

- [ ] **Step 1: Run full test suite**

Run: `pytest -q 2>&1 | tail -10`
Expected: All PASS. Total count ≈ 819 + 30 = 849 (within ~840-860 range per spec §4.3).

(Baseline 819 verified via `pytest --collect-only -q` at iter4 branch start; spec §1.1 / §4.3 still cite 818 — acceptable since spec range 840-860 still covers actual.)

**Recount breakdown** (function net = +21, cases net = +30):
- N8 split: +3 functions (10 new − 7 old)
- Fact-only: +18 functions (17 single + 1 batch parametrize), +27 cases (17 + 10 parametrize)
- Persona drift add: +1 function (`test_layer1_no_tool_invocation_descriptions`)
- Persona delete: −3 functions (`test_layer1_includes_get_price_pivots` / `test_prompt_contains_missing_tool_guidance` / `test_prompt_set_next_wake_one_shot`); `test_prompt_contains_memory_quality_guidance` reduced (function count unchanged)
- L27 + L65 softening: +2 functions
- Functions: +3 + 18 + 1 − 3 + 2 = **+21**; Cases: +3 + 27 + 1 − 3 + 2 = **+30**

If count is outside range: revisit task-specific commit ranges to find the discrepancy.

- [ ] **Step 2: Verify Acceptance Criteria checkpoints**

Manually inspect the 13 acceptance criteria from spec §5:
1. ✅ persona.py Layer 1 = 5 bullets — verify with `grep -c "^- \*\*" src/agent/persona.py` (within Layer 1 section)
2. ✅ Layer 2 L65 deletion — `grep -c "arbitrary percentage" src/agent/persona.py` should be 0
3. ✅ Layer 3 unchanged — `git diff main src/agent/persona.py -- --` should not touch `_build_layer3`
4. ✅ 31 `@agent.tool` Google docstrings — `grep -c "^    @agent.tool" src/agent/trader.py` should be 31
5. ✅ 7 old tools docstring充实 — visually check Task 3 + Task 6 commits
6. ✅ 20 bullets moved to docstrings — verify content (not just deletion) by spot-checking key §2.2 mapping:
   - L29 (multi-tf) → `grep -q "build conviction" src/agent/trader.py` (in get_market_data docstring)
   - L42 (macro DTWEXBGS) → `grep -q "DTWEXBGS" src/agent/trader.py` (in get_macro_context docstring)
   - L31 (set_next_wake) → `grep -q "one-shot" src/agent/trader.py` (in set_next_wake docstring)
   - L30 (memory) → `grep -q "actionable" src/agent/trader.py` (in save_memory docstring)
   - L42 (macro DXY note) → `grep -q "ICE DXY" src/agent/trader.py`
   - L48 (position risk context) → `grep -q "ATR(1h)" src/agent/trader.py` (in get_position docstring)
   - L50 (price pivots fractal) → `grep -q "Williams fractal" src/agent/trader.py` (in get_price_pivots docstring)
   All 7 above must return exit code 0.
7. ✅ L27 + L50 + L65 softening — `pytest tests/test_persona.py::test_prompt_l27_softened tests/test_persona.py::test_prompt_l65_softened -v` PASS
8. ✅ N8 split — `grep -rn "get_critical_alerts" src/ tests/` zero hits; `grep -n "get_exchange_announcements\|get_macro_calendar" src/agent/trader.py` ≥ 4 hits
9. ✅ 31 tools fact-only — Task 10-13 all PASS
10. ✅ test_fact_only_wordlist.py covers all 31 tools
11. ✅ ~848 tests pass, zero regression
12. ✅ single PR — single branch `iter4-prompt-optimization-spec`
13. ✅ header rename — `grep "## Cross-Tool Behavior" src/agent/persona.py` 1 hit; `grep "## Tool Usage Notes" src/agent/persona.py` 0 hits

- [ ] **Step 3: Push branch**

```bash
git push -u origin iter4-prompt-optimization-spec
```

- [ ] **Step 4: Open PR (after user confirmation)**

Wait for user's go-ahead before opening PR. When approved:

```bash
gh pr create --title "Iter 4: prompt optimization — Layer 1 slim, docstring audit, N8 split, fact-only coverage" --body "$(cat <<'EOF'
## Summary

- Layer 1 25 → 5 bullets (cross-tool behavior only); 20 tool descriptions moved to docstrings (DRY)
- 31 `@agent.tool` docstrings unified to Google format (pydantic-ai griffe sniff)
- `get_critical_alerts` split into `get_exchange_announcements` + `get_macro_calendar` (independent degradation; tool count 30 → 31)
- L27 / L65 single-direction wording softened (let agent decide methodology)
- 27 tools fact-only global wordlist coverage (`tests/test_fact_only_wordlist.py`)

## Spec

`docs/superpowers/specs/2026-04-25-iter4-prompt-optimization-design.md` (commit `cf912f1`)

## Test plan

- [x] All 13 Acceptance Criteria verified (spec §5)
- [x] 819 → ~849 tests passing
- [x] N8 split: 7 obsolete `test_critical_alerts_*` removed, 10 `test_exchange_announcements_*` + `test_macro_calendar_*` added
- [x] Footer tri-state rule (list incl. [] → show, None → hide) explicitly tested per §3.4

## Risks & monitoring (observation period)

Per spec §6.1: no real-LLM regression test — Layer 1 slim-down's effect on tool discovery rate is unverifiable until observation period. Heuristic triggers for follow-up:
- any perception tool < 5 calls in first 4 weeks during active trading
- top-3 perception tools > 80% of perception calls
- post-split `get_exchange_announcements` or `get_macro_calendar` 0 calls
- post-fill SL/TP setting rate < 80% (validates `check the chart` removal — §3.2)

🤖 Generated with [Claude Code](https://claude.com/claude-code)
EOF
)"
```

---

## Self-Review Checklist (post-plan)

**Spec coverage:**
- [x] §3.1 (4.1) docstring audit → Tasks 3, 4, 5, 6 (31 tools)
- [x] §3.2 L27 + L50 softening → Task 5 Step 8 (L50 in get_price_pivots) + Task 8 Step 1 (L27)
- [x] §3.3 (4.3) Layer 2 L65 → Task 9
- [x] §3.4 (4.4) N8 split → Tasks 1, 2
- [x] §3.5 (4.5) fact-only coverage → Tasks 10, 11, 12, 13
- [x] §4.2 drift tests → Task 7
- [x] §5 acceptance criteria → Task 14 verification

**Type consistency:**
- `get_exchange_announcements(deps, lookback_hours=24)` and `get_macro_calendar(deps, lookahead_hours=12)` signatures consistent across Task 1 (impl + test cleanup) and Task 2 (registration)
- `MockDeps` extended with real `TradingDeps` fields (`memory`, `db_engine`, `metrics`, `news`, `macro`, `crypto_etf`, `onchain`, `set_next_wake_fn`, `wake_min_minutes`, `wake_max_minutes`, `session_id`, `cycle_id`, `approval_enabled`, `approval_gate`) — see Task 10 Step 1. Field names verified against `src/agent/trader.py:18-39`. `cycle_id` is defense-in-depth: `ToolCallRecorder` reads it in real-flow (`src/services/tool_call_recorder.py:81/93`); fact-only tests bypass the recorder but the field is included to keep the mirror complete.

**`get_critical_alerts` reference cleanup (multi-task):**
- Task 1: tools_perception.py impl removed; obsolete test functions removed; test_news_tools.py file docstring updated
- Task 2: trader.py @agent.tool registration replaced; REGISTERED_TOOL_NAMES updated; test_trader_agent.py:40 hardcoded assertion (`"get_critical_alerts" in tool_names`) replaced with new tool names
- Task 8: persona.py L39 (Layer 1 bullet "Use get_critical_alerts before trading") removed via Layer 1 reduction
- Task 14 Step 2 #8: full repo-wide `grep -rn "get_critical_alerts" src/ tests/` returns zero hits

**Transient zombie reference (Task 2 → Task 8 window)**: persona.py L39 still references `get_critical_alerts` for ~6 commits between Task 2 (where the tool is unregistered) and Task 8 (where Layer 1 is reduced). This is invisible to users since the entire iter ships as a single PR; documented in Task 2 Step 8 commit message.

**Line-number drift after Task 2**: `trader.py` net +23 lines after Task 2 (1 old `@agent.tool` block → 2 new with longer Google docstrings). All line references in Tasks 3-6 captured pre-Task-2 are stale. Edit tool matches by content, so OLD docstring snippets serve as the actual anchors. Documented inline at end of Task 2.

**Token estimation preservation (spec §3.1 audit checklist item)**: each tool docstring keeps its `Output ~XXX tokens` line if present in the original. Verified: `get_market_data` (Task 4 Step 1 — 1000-1200), `get_market_news` (Task 4 Step 3 — 500-700), `get_derivatives_data` (Task 4 Step 4 — 150-250), `get_higher_timeframe_view` (Task 4 Step 5 — 250), `get_macro_context` (Task 4 Step 6 — 200), `get_etf_flows` (Task 4 Step 7 — 300), `get_stablecoin_supply` (Task 4 Step 8 — 80), `get_critical_alerts` split → `get_exchange_announcements` (50-200) + `get_macro_calendar` (50-250) (Task 2 Step 1).

**Placeholder scan:**
- No "TBD" / "TODO" / "implement later" hits
- All test code includes concrete assertions
- All commit messages concrete

---

## Execution Handoff

**Plan complete and saved to `docs/superpowers/plans/2026-04-26-iter4-prompt-optimization.md`. Two execution options:**

**1. Subagent-Driven (recommended)** — fresh subagent per task, review between tasks, fast iteration.

**2. Inline Execution** — execute tasks in this session using executing-plans, batch execution with checkpoints.

**Which approach?**
