# iter-tool-opt-dead-example-promote Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Promote 7 tools' dead-Example / dead-trailer docstring content into LLM-visible `tool.tool_def.description` via dual-mode `@tool` wrapper + path A inline / path B description override.

**Architecture:** Two-track promotion. Path A (inline narrative) for `get_order_book` / `get_performance`. Path B (description override constant) for `set_next_wake` / `set_next_wake_at` / `get_market_data` / `get_higher_timeframe_view` / `get_multi_timeframe_snapshot`. Foundation: extract module-level `_create_dual_mode_tool` factory accepting `description=` kwarg. DESC constants centralized in new `src/agent/tools_descriptions.py`.

**Tech Stack:** pydantic-ai 1.78 / pytest

---

## File Structure

- Create: `src/agent/tools_descriptions.py` — 5 `<TOOL>_DESCRIPTION` constants (path B)
- Modify: `src/agent/trader.py` — extract `_create_dual_mode_tool` + apply override + clean docstrings
- Modify: `tests/test_trader_agent.py` — 1 dual-mode wrapper test + 7 per-tool drift guards + 1 module-level audit

---

## Task 1: Refactor `tool()` wrapper to dual-mode + scaffold tools_descriptions.py

**Files:**
- Create: `src/agent/tools_descriptions.py`
- Modify: `src/agent/trader.py:74-85` (extract `tool` factory + adopt in `create_trader_agent`)
- Test: `tests/test_trader_agent.py` (new `test_dual_mode_tool_wrapper`)

- [ ] **Step 1: Write failing test**

Add to `tests/test_trader_agent.py` (after existing imports). Fixture aligns with production trader.py:67-74 (`deps_type=type(None)`, `output_type=str`) — verifies dual-mode contract under the same Agent construction shape:

```python
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

    agent = Agent(model="test", deps_type=type(None), output_type=str)
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
    fail_agent = Agent(model="test", deps_type=type(None), output_type=str)
    fail_tool = _create_dual_mode_tool(fail_agent)
    with pytest.raises(UserError, match="Missing parameter descriptions"):
        @fail_tool(description="override desc")
        async def t_missing_args(ctx: RunContext[None], y: int) -> str:
            """Tool with description override but no Args section for y."""
            return ""
```

- [ ] **Step 2: Run test — verify FAIL**

Run: `.venv/bin/python -m pytest tests/test_trader_agent.py::test_dual_mode_tool_wrapper -xvs`
Expected: FAIL — `ImportError: cannot import name '_create_dual_mode_tool' from 'src.agent.trader'`

- [ ] **Step 3: Create scaffold `src/agent/tools_descriptions.py`**

Create new file with content:

```python
"""LLM-facing tool descriptions for tools where pydantic-ai 1.78 / griffe
strips structured sections (Examples / Example output / inline admonitions)
from `tool.tool_def.description`.

Constants in this module are passed verbatim via `@tool(description=DESC_X)`
to bypass griffe parsing and reach the LLM. Args descriptions remain in the
source docstring (parsed normally into `parameters_json_schema`).

See docs/superpowers/specs/2026-05-19-iter-tool-opt-dead-example-promote-design.md
for the audit (7 tools / 4 loss categories) + design rationale.
"""

# Constants added by subsequent migration tasks (Tasks 2-6).
```

- [ ] **Step 4: Extract `_create_dual_mode_tool` factory + replace inline wrapper**

In `src/agent/trader.py`, find the existing wrapper at line ~80-84:

```python
    # Iter 5 D: 启用 google docstring 显式声明 + 强制 Args 完整性。
    # require_parameter_descriptions=True 在 tool 加载时校验，缺 Args 立即 startup fail。
    # 用 def 而非 functools.partial — partial 丢失 Agent.tool 的 overload 信息，
    # IDE static type checker 会把 @tool 标红；def 让 pyright 看到清晰的装饰器签名。
    def tool(func):
        return agent.tool(
            docstring_format="google",
            require_parameter_descriptions=True,
        )(func)
```

Replace with:

```python
    tool = _create_dual_mode_tool(agent)
```

Add new module-level factory **between `class TradingDeps:` (ends line 49 with `cycle_id: ...`) and `def create_trader_agent(` (line 51)** — these are the only two module-level definitions in the file; insert the factory as the new line 51 (push `create_trader_agent` down):

```python
def _create_dual_mode_tool(agent):
    """Build the project's @tool decorator with two usage modes:

        @tool                       — default: griffe sniffs docstring main_desc + Args
        @tool(description=DESC_X)   — override: pass DESC_X verbatim to LLM,
                                       bypass griffe section-stripping

    Why dual-mode: pydantic-ai 1.78 / griffe strips google section headers
    (Examples:, Example call:, inline admonitions) from tool_def.description.
    Override path B carries multi-outcome Examples / multi-section Example
    output blocks intact. See spec §2.2.

    Backward-compat: 33 existing @tool sites use the no-arg form, unchanged.

    Iter 5 D preserved: docstring_format='google' + require_parameter_descriptions=True
    still enforced on both branches.
    """
    def tool(func=None, *, description=None):
        kwargs = {
            "docstring_format": "google",
            "require_parameter_descriptions": True,
        }
        if description is not None:
            kwargs["description"] = description
        if func is not None and callable(func):
            return agent.tool(**kwargs)(func)
        return lambda f: agent.tool(**kwargs)(f)
    return tool
```

- [ ] **Step 5: Run test — verify PASS**

Run: `.venv/bin/python -m pytest tests/test_trader_agent.py::test_dual_mode_tool_wrapper -xvs`
Expected: PASS

- [ ] **Step 6: Run full trader_agent suite — regression check**

Run: `.venv/bin/python -m pytest tests/test_trader_agent.py -q`
Expected: 13 passed (12 baseline + 1 new) — all 33 tool definitions still match.

- [ ] **Step 7: Commit foundation**

```bash
git add src/agent/trader.py src/agent/tools_descriptions.py tests/test_trader_agent.py
git commit -m "iter-tool-opt-dead-example-promote(1/5): tool() wrapper dual-mode + tools_descriptions scaffold

Extract _create_dual_mode_tool(agent) factory accepting description=
kwarg. Backward-compat for 33 existing @tool sites; foundation for
path B DESC override in Tasks 2-6.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

---

## Task 2: Migrate `set_next_wake` to path B (P0)

**Files:**
- Modify: `src/agent/tools_descriptions.py` (add SET_NEXT_WAKE_DESCRIPTION constant)
- Modify: `src/agent/trader.py` `set_next_wake` wrapper (apply override + strip dead docstring)
- Test: `tests/test_trader_agent.py` (new `test_set_next_wake_description_carries_examples_block`)

- [ ] **Step 1: Write failing drift guard test**

Add to `tests/test_trader_agent.py`:

```python
def test_set_next_wake_description_carries_examples_block():
    """W3 R2-Next-H attribution lever — set_next_wake description must
    carry the 3-outcome Examples block (success + over-max + under-min)
    via path B override, since baseline desc was 69 chars (90% loss).
    """
    from src.agent.trader import create_trader_agent
    from src.config import PersonaConfig

    agent = create_trader_agent(model="test", persona_config=PersonaConfig())
    tool = agent._function_toolset.tools["set_next_wake"]
    desc = tool.tool_def.description

    # Examples block presence
    assert "Examples:" in desc, f"Examples block header missing: {desc!r}"
    assert "consolidation phase" in desc, f"success-outcome example missing: {desc!r}"
    assert "exceeds wake_max" in desc, f"over-max reject outcome missing: {desc!r}"
    assert "below wake_min" in desc, f"under-min reject outcome missing: {desc!r}"
    # Runtime contract
    assert "Alerts, fills" in desc, f"alerts-interrupt-wake contract missing: {desc!r}"
    # Args still parsed (unchanged)
    schema = tool.tool_def.parameters_json_schema
    assert "wake_min_minutes" in schema["properties"]["minutes"]["description"]
```

- [ ] **Step 2: Run test — verify FAIL**

Run: `.venv/bin/python -m pytest tests/test_trader_agent.py::test_set_next_wake_description_carries_examples_block -xvs`
Expected: FAIL — `Examples block header missing` (current desc is 69 chars).

- [ ] **Step 3: Add SET_NEXT_WAKE_DESCRIPTION constant**

Append to `src/agent/tools_descriptions.py`:

```python
SET_NEXT_WAKE_DESCRIPTION = """Schedule the next scheduler wake-up after a relative minute interval.

Returns a confirmation, or a reject message describing the violation. Alerts, fills, and conditional triggers always interrupt scheduled wake.

Examples:
    set_next_wake(15, "consolidation phase, check in 15 min")
    → "Next wake set to 15 min. Reason: ..."

    set_next_wake(90, "...")
    → "Cannot set wake to 90 min: exceeds wake_max=60 min for this session."

    set_next_wake(0, "...")
    → "Cannot set wake to 0 min: below wake_min=1 min."
"""
```

- [ ] **Step 4: Apply override + clean docstring**

In `src/agent/trader.py`, locate the `set_next_wake` wrapper (around line 677). Top of file add import:

```python
from src.agent.tools_descriptions import SET_NEXT_WAKE_DESCRIPTION
```

Replace the `@tool` + docstring block. Before:

```python
    @tool
    async def set_next_wake(
        ctx: RunContext[TradingDeps],
        minutes: int,
        reasoning: str,
    ) -> str:
        """Schedule the next scheduler wake-up after a relative minute interval.

        Args:
            minutes: minutes from now until the next wake-up. Must fall within
                [wake_min_minutes, wake_max_minutes]; rejected otherwise.
            reasoning: brief description of your decision logic.

        Returns a confirmation, or a reject message describing the violation.

        Examples:
            set_next_wake(15, "consolidation phase, check in 15 min")
            → "Next wake set to 15 min. Reason: ..."

            set_next_wake(90, "...")
            → "Cannot set wake to 90 min: exceeds wake_max=60 min for this session."

            set_next_wake(0, "...")
            → "Cannot set wake to 0 min: below wake_min=1 min."

        Alerts, fills, and conditional triggers always interrupt scheduled wake.
        """
        from src.agent.tools_execution import set_next_wake as _impl

        return await _impl(ctx.deps, minutes, reasoning=reasoning)
```

After:

```python
    @tool(description=SET_NEXT_WAKE_DESCRIPTION)
    async def set_next_wake(
        ctx: RunContext[TradingDeps],
        minutes: int,
        reasoning: str,
    ) -> str:
        """Schedule the next scheduler wake-up (relative interval).

        Args:
            minutes: minutes from now until the next wake-up. Must fall within
                [wake_min_minutes, wake_max_minutes]; rejected otherwise.
            reasoning: brief description of your decision logic.
        """
        from src.agent.tools_execution import set_next_wake as _impl

        return await _impl(ctx.deps, minutes, reasoning=reasoning)
```

- [ ] **Step 5: Run test — verify PASS**

Run: `.venv/bin/python -m pytest tests/test_trader_agent.py::test_set_next_wake_description_carries_examples_block -xvs`
Expected: PASS

---

## Task 3: Migrate `set_next_wake_at` to path B (P0)

**Files:**
- Modify: `src/agent/tools_descriptions.py` (add SET_NEXT_WAKE_AT_DESCRIPTION)
- Modify: `src/agent/trader.py` `set_next_wake_at` wrapper
- Test: `tests/test_trader_agent.py` (new `test_set_next_wake_at_description_carries_examples_block`)

- [ ] **Step 1: Write failing drift guard test**

```python
def test_set_next_wake_at_description_carries_examples_block():
    """W3 R2-Next-H attribution lever — set_next_wake_at description must
    carry the 4-outcome Examples block via path B override, since baseline
    desc was 60 chars (95% loss). Adoption W3 only 2.0% (3/147)."""
    from src.agent.trader import create_trader_agent
    from src.config import PersonaConfig

    agent = create_trader_agent(model="test", persona_config=PersonaConfig())
    tool = agent._function_toolset.tools["set_next_wake_at"]
    desc = tool.tool_def.description

    assert "Examples:" in desc
    assert "candle close at 11:00 UTC" in desc, f"success-outcome example missing: {desc!r}"
    assert "nearest future" in desc, f"resolution-semantics literal missing: {desc!r}"
    assert "resolves to tomorrow" in desc, f"tomorrow-resolution outcome missing: {desc!r}"
    assert "Invalid target_time format" in desc, f"format-reject outcome missing: {desc!r}"
    assert "Alerts, fills" in desc, f"alerts-interrupt-wake contract missing: {desc!r}"
```

- [ ] **Step 2: Run test — verify FAIL**

Run: `.venv/bin/python -m pytest tests/test_trader_agent.py::test_set_next_wake_at_description_carries_examples_block -xvs`
Expected: FAIL (desc 60 chars, missing all literals).

- [ ] **Step 3: Add SET_NEXT_WAKE_AT_DESCRIPTION constant**

Append to `src/agent/tools_descriptions.py`:

```python
SET_NEXT_WAKE_AT_DESCRIPTION = """Schedule the next scheduler wake-up at an absolute UTC time.

Returns a confirmation containing the resolved date-time, or a reject message describing the violation. Alerts, fills, and conditional triggers always interrupt scheduled wake.

Examples:
    set_next_wake_at("10:37", "align with 1h candle close at 11:00 UTC")
    → "Next wake set for 2026-05-12 10:37 UTC (in 14 min). Reason: ..."

    set_next_wake_at("12:00", "...")
    → "Cannot wake at 12:00 UTC: nearest future 2026-05-12 12:00 UTC (in 97 min) exceeds wake_max=60 min for this session."

    set_next_wake_at("10:23", "...")  # now=10:23, resolves to tomorrow
    → "Cannot wake at 10:23 UTC: nearest future 2026-05-13 10:23 UTC (in 1440 min) exceeds wake_max=60 min for this session."

    set_next_wake_at("foo", "...")
    → "Invalid target_time format: 'foo'. Expected 'HH:MM' UTC with 2-digit hour and minute (e.g., '10:37' or '03:05')."
"""
```

- [ ] **Step 4: Apply override + clean docstring**

In `src/agent/trader.py`, update the import:

```python
from src.agent.tools_descriptions import (
    SET_NEXT_WAKE_DESCRIPTION,
    SET_NEXT_WAKE_AT_DESCRIPTION,
)
```

Locate `set_next_wake_at` wrapper (around line 708). Replace `@tool` and strip dead docstring sections (Returns 散文 / Examples block / Alerts trailer). The cleaned wrapper:

```python
    @tool(description=SET_NEXT_WAKE_AT_DESCRIPTION)
    async def set_next_wake_at(
        ctx: RunContext[TradingDeps],
        target_time: str,
        reasoning: str,
    ) -> str:
        """Schedule the next scheduler wake-up (absolute UTC time).

        Args:
            target_time: future wake time in 'HH:MM' UTC format (e.g., '10:37').
                Resolves to the nearest future time matching HH:MM (today if
                HH:MM is still ahead in UTC; otherwise tomorrow). Must fall
                within [now+wake_min_minutes, now+wake_max_minutes]; rejected
                otherwise.
            reasoning: brief description of your decision logic.
        """
        from src.agent.tools_execution import set_next_wake_at as _impl

        return await _impl(ctx.deps, target_time, reasoning=reasoning)
```

- [ ] **Step 5: Run test — verify PASS**

Run: `.venv/bin/python -m pytest tests/test_trader_agent.py::test_set_next_wake_at_description_carries_examples_block -xvs`
Expected: PASS

- [ ] **Step 6: Run both P0 tests + regression check**

Run: `.venv/bin/python -m pytest tests/test_trader_agent.py -q`
Expected: 15 passed (12 baseline + 3 new).

- [ ] **Step 7: Commit P0 pair**

```bash
git add src/agent/trader.py src/agent/tools_descriptions.py tests/test_trader_agent.py
git commit -m "iter-tool-opt-dead-example-promote(2/5): set_next_wake + set_next_wake_at path B (W3 R2-Next-H attribution lever)

Before: set_next_wake desc 69 chars (90% loss) / set_next_wake_at desc 60
chars (95% loss). 3-outcome / 4-outcome Examples block + Alerts-interrupt
runtime contract all dead — agent saw essentially nothing.

After: SET_NEXT_WAKE_DESCRIPTION (380 chars) + SET_NEXT_WAKE_AT_DESCRIPTION
(720 chars) via path B override. Args / parameter schema unchanged.

W3 sim #10 adoption baseline:
- set_next_wake_at: 2.0% (3/147)
- set_next_wake reasoning HH:MM UTC: 4% (W3) vs 78% (W2)
W4 verification: any meaningful adoption improvement → R2-Next-H议题反转
attribution 隔离闭环（per spec §2.8）.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

---

## Task 4: Migrate `get_market_data` to path B (P1)

**Files:**
- Modify: `src/agent/tools_descriptions.py` (add GET_MARKET_DATA_DESCRIPTION)
- Modify: `src/agent/trader.py` `get_market_data` wrapper
- Test: `tests/test_trader_agent.py` (new `test_get_market_data_description_carries_example_output`)

- [ ] **Step 1: Write failing drift guard test**

```python
def test_get_market_data_description_carries_example_output():
    """get_market_data description must carry the multi-section Example
    output (Ticker / Recent Candles / Period summary) + OHLCV marker
    semantics via path B override.
    """
    from src.agent.trader import create_trader_agent
    from src.config import PersonaConfig

    agent = create_trader_agent(model="test", persona_config=PersonaConfig())
    tool = agent._function_toolset.tools["get_market_data"]
    desc = tool.tool_def.description

    assert "=== Ticker" in desc, f"Ticker section header missing in example: {desc!r}"
    assert "=== Recent Candles" in desc, f"Candles section header missing: {desc!r}"
    assert "=== Period summary" in desc, f"Period summary section header missing: {desc!r}"
    assert "vol↑" in desc, f"OHLCV vol marker literal missing: {desc!r}"
    assert "range↑" in desc, f"OHLCV range marker literal missing: {desc!r}"
```

- [ ] **Step 2: Run test — verify FAIL**

Expected: FAIL — Example output section currently stripped.

- [ ] **Step 3: Add GET_MARKET_DATA_DESCRIPTION constant**

Append to `src/agent/tools_descriptions.py`:

```python
GET_MARKET_DATA_DESCRIPTION = """Single-timeframe market data: ticker, technical indicators (RSI / MACD / BB / ATR / volume ratio), market context (ATR with percent of price, last-bar volume with average ratio, display-window range), the most recent N closed candles in OHLCV table form with anomaly markers, and a period summary comparing the last 5 vs prior 5 closed candles (avg volume, avg range, net Δclose).

All indicators are computed on the closed-bar series only (excluding the in-progress candle). The OHLCV table also shows closed bars only and is sorted oldest-first by row.

Markers in OHLCV table (upside-only thresholds): `vol↑` for bar volume > 2× SMA(20) of bar volumes; `range↑` for bar range (high - low) > 2× ATR(14); empty for neither threshold tripped. Time column shows candle open in UTC.

Example call:
    get_market_data(timeframe="5m", candle_count=30)

Example output:
    === Ticker (BTC/USDT:USDT @ 14:23:08 UTC) ===
    Last: 81870.50 | Bid: 81870.40 | Ask: 81870.60
    ...
    === Recent Candles (5m, last 30, oldest-first by row) ===
    Time (open UTC)   Open ... Vol     Markers
    14:20         ...         245.3   vol↑
    ...
    === Period summary (last 5 closed candles vs prior 5 closed candles) ===
    Avg vol:            last 5 178.6 / prior 5 132.4 (1.35×)
    Avg range (H-L):    last 5 38.2 / prior 5 24.8 (1.54×)
    Net Δclose:         last 5 -25.0 USDT / prior 5 +120.0 USDT
"""
```

- [ ] **Step 4: Apply override + clean docstring**

In trader.py, add to import list:

```python
from src.agent.tools_descriptions import (
    SET_NEXT_WAKE_DESCRIPTION,
    SET_NEXT_WAKE_AT_DESCRIPTION,
    GET_MARKET_DATA_DESCRIPTION,
)
```

Replace `get_market_data` wrapper:

```python
    @tool(description=GET_MARKET_DATA_DESCRIPTION)
    async def get_market_data(
        ctx: RunContext[TradingDeps],
        symbol: str | None = None,
        timeframe: str | None = None,
        candle_count: int = 30,
    ) -> str:
        """Get single-timeframe market data with indicators + OHLCV.

        Args:
            symbol: Trading symbol. Defaults to session symbol.
            timeframe: CCXT timeframe ("1m", "5m", "1h", etc.). Defaults to session primary timeframe.
            candle_count: Number of closed candles in the OHLCV table. Default 30. Range 10-80 (capped by exchange API).
        """
        from src.agent.tools_perception import get_market_data as _impl

        return await _impl(ctx.deps, symbol, timeframe, candle_count)
```

- [ ] **Step 5: Run test — verify PASS**

---

## Task 5: Migrate `get_higher_timeframe_view` to path B (P1)

**Files:**
- Modify: `src/agent/tools_descriptions.py` (add GET_HIGHER_TIMEFRAME_VIEW_DESCRIPTION)
- Modify: `src/agent/trader.py` `get_higher_timeframe_view` wrapper
- Test: `tests/test_trader_agent.py` (new `test_get_higher_timeframe_view_description_carries_example_and_degradation`)

- [ ] **Step 1: Write failing drift guard test**

```python
def test_get_higher_timeframe_view_description_carries_example_and_degradation():
    """get_higher_timeframe_view description must carry per-tf Example
    output + Degradation trailer via path B override.
    """
    from src.agent.trader import create_trader_agent
    from src.config import PersonaConfig

    agent = create_trader_agent(model="test", persona_config=PersonaConfig())
    tool = agent._function_toolset.tools["get_higher_timeframe_view"]
    desc = tool.tool_def.description

    assert "=== Higher Timeframe View" in desc
    assert "MA stack: MA50 > MA100 > MA200" in desc
    assert "100-period High:" in desc
    assert "insufficient data (need N candles)" in desc, f"Degradation literal missing: {desc!r}"
    assert "MA50 ≈ MA100" in desc, f"MA stack tolerance semantics missing: {desc!r}"
```

- [ ] **Step 2: Run test — verify FAIL**

- [ ] **Step 3: Add GET_HIGHER_TIMEFRAME_VIEW_DESCRIPTION constant**

Append:

```python
GET_HIGHER_TIMEFRAME_VIEW_DESCRIPTION = """Long-term structural view across one or more higher timeframes: ticker (authoritative live price), per-tf MA50/MA100/MA200 with raw value, price-vs-MA percentage, and MA slope (10-bar lookback); MA stack comparison; 100-period high and low with bars-ago and the candle open timestamp; range position within 100-period; 20-period high-low range width; last-bar volume vs 20-period SMA ratio (base volume); ATR(14) raw, percent of price, and ratio vs 20-period ATR average.

All moving averages are simple moving averages (SMA) computed on the closed-bar series only (excluding the in-progress bar). The slope reference and all rolling averages use the closed-candle series.

MA stack comparison uses ">" / "<" / "≈" with 0.1% tolerance: when |MAa - MAb| / MAb < 0.001, the operator collapses to "≈" (e.g., "MA50 ≈ MA100 < MA200").

Per-tf MA periods: 4h / 1d / 1w use (50, 100, 200) — standard moving-average periods. 1M uses (12, 24, 60), corresponding to 1-year / 2-year / 5-year monthly cycles, matching crypto-industry monthly chart conventions; the 1M section header marks the period choice explicitly.

Example call:
    get_higher_timeframe_view(timeframes=["4h", "1d"])

Example output:
    === Higher Timeframe View (BTC/USDT:USDT @ 14:23:08 UTC) ===
    Last: 81870.50

    [4h] (last closed candle: open 2026-05-11 08:00 UTC)
      MA50: 79200.00 (price vs MA: +3.4%; MA slope vs 10 bars ago: +0.8%)
      ...
      MA stack: MA50 > MA100 > MA200
      100-period High: 82800.00 (32 bars ago, candle open 2026-05-06 00:00 UTC)
      ...
      Last bar vol (base): 1521.6 (5.0× SMA(20) avg)
      ATR(14): 1572.30 (1.92% of price; 1.04× vs 20-period ATR(14) avg)
    ...

Per-tf degradation: "insufficient data (need N candles)" if OHLCV history is shorter than the longest MA period; "Error: Temporarily unavailable" if the OHLCV fetch for that tf fails. Overall returns header-only error if the ticker fetch fails.
"""
```

- [ ] **Step 4: Apply override + clean docstring**

Update import list with `GET_HIGHER_TIMEFRAME_VIEW_DESCRIPTION`. Replace wrapper:

```python
    @tool(description=GET_HIGHER_TIMEFRAME_VIEW_DESCRIPTION)
    async def get_higher_timeframe_view(
        ctx: RunContext[TradingDeps],
        timeframes: list[Literal["4h", "1d", "1w", "1M"]] | None = None,
    ) -> str:
        """Higher-timeframe structural view across MA / range / ATR / volume.

        Args:
            timeframes: List of CCXT timeframes from {"4h", "1d", "1w", "1M"}. Default ["4h", "1d"]. Each timeframe rendered as a separate section.
        """
        from src.agent.tools_perception import get_higher_timeframe_view as _impl

        return await _impl(ctx.deps, timeframes)
```

- [ ] **Step 5: Run test — verify PASS**

---

## Task 6: Migrate `get_multi_timeframe_snapshot` to path B (P1, Gate 4 attribution)

**Files:**
- Modify: `src/agent/tools_descriptions.py` (add GET_MULTI_TIMEFRAME_SNAPSHOT_DESCRIPTION)
- Modify: `src/agent/trader.py` `get_multi_timeframe_snapshot` wrapper
- Test: `tests/test_trader_agent.py` (new `test_get_multi_timeframe_snapshot_description_carries_example`)

- [ ] **Step 1: Write failing drift guard test**

```python
def test_get_multi_timeframe_snapshot_description_carries_example():
    """get_multi_timeframe_snapshot description must carry per-TF Example
    output + Degradation trailer via path B override. Gate 4 attribution
    candidate — Gate 4 ⑤ work centers on this tool's MTS structure terms
    adoption.
    """
    from src.agent.trader import create_trader_agent
    from src.config import PersonaConfig

    agent = create_trader_agent(model="test", persona_config=PersonaConfig())
    tool = agent._function_toolset.tools["get_multi_timeframe_snapshot"]
    desc = tool.tool_def.description

    assert "=== Multi-TF Snapshot" in desc
    assert "MA fast-vs-slow per tf" in desc
    assert "Range pos" in desc
    assert "insufficient data" in desc, f"Degradation literal missing: {desc!r}"
```

- [ ] **Step 2: Run test — verify FAIL**

- [ ] **Step 3: Add GET_MULTI_TIMEFRAME_SNAPSHOT_DESCRIPTION constant**

Append:

```python
GET_MULTI_TIMEFRAME_SNAPSHOT_DESCRIPTION = """Multi-timeframe snapshot — single fanout across N timeframes for fast cross-TF structural reads. Per TF: momentum vs MA20 (closed-bar series), MA20 / MA50 fast-vs-slow comparison, ATR percent of price + ratio vs 20-period ATR average, range position within 20-bar high/low. Header row gives ticker + per-tf "MA fast-vs-slow" digest line (e.g. "5m below | 1h above | 4h above | 1d below"). Last 3 closed-candle close-prices per TF for short-momentum read.

All indicators computed on closed-bar series only (excluding the in-progress candle). Algorithm-lock invariant: MTS per-TF outputs match `get_higher_timeframe_view` per-TF (algorithm shared); end-to-end verified by `test_mts_htf_overlap_values_match`.

Example call:
    get_multi_timeframe_snapshot()

Example output:
    === Multi-TF Snapshot (BTC/USDT:USDT) ===
    Last (ticker @ 14:23:08 UTC): 81870.50
    MA fast-vs-slow per tf: 5m below | 1h above | 4h above | 1d below
    Columns: ...
    [5m]  Mom -0.3% (vs MA20) | MA20: 81960 < MA50: 82150 | ATR 0.15% (20p avg 0.18%, 0.83×) | Range pos 65%
          Last 3 closes (closed @ 2026-05-11 14:20 UTC): 81870→81848→81870
    ... (3 more tf rows)

Per-TF degradation: "insufficient data" or "temporarily unavailable" per failed TF. Overall returns header-only error if all TFs fail or the ticker fetch fails.
"""
```

- [ ] **Step 4: Apply override + clean docstring**

Update import list. Replace wrapper:

```python
    @tool(description=GET_MULTI_TIMEFRAME_SNAPSHOT_DESCRIPTION)
    async def get_multi_timeframe_snapshot(
        ctx: RunContext[TradingDeps],
        tfs: list[str] | None = None,
    ) -> str:
        """Multi-TF snapshot — single fanout across N timeframes.

        Args:
            tfs: List of CCXT timeframes. Default ["5m", "1h", "4h", "1d"].
        """
        from src.agent.tools_perception import get_multi_timeframe_snapshot as _impl

        return await _impl(ctx.deps, tfs)
```

- [ ] **Step 5: Run test — verify PASS**

- [ ] **Step 6: Verify cumulative import shape at end of P1 trio**

After Tasks 2-6 all applied, the top of `src/agent/trader.py` should contain exactly this single import block from `tools_descriptions` (5 names, alphabetically ordered for diff stability):

```python
from src.agent.tools_descriptions import (
    GET_HIGHER_TIMEFRAME_VIEW_DESCRIPTION,
    GET_MARKET_DATA_DESCRIPTION,
    GET_MULTI_TIMEFRAME_SNAPSHOT_DESCRIPTION,
    SET_NEXT_WAKE_AT_DESCRIPTION,
    SET_NEXT_WAKE_DESCRIPTION,
)
```

Reconciliation anchor — if a subagent picks up mid-iter and finds fewer or differently-ordered names, this is the canonical end-of-P1 state.

- [ ] **Step 7: Run all P1 trio tests + regression check**

Run: `.venv/bin/python -m pytest tests/test_trader_agent.py -q`
Expected: 18 passed (12 baseline + 6 new through Task 6).

- [ ] **Step 8: Commit P1 trio**

```bash
git add src/agent/trader.py src/agent/tools_descriptions.py tests/test_trader_agent.py
git commit -m "iter-tool-opt-dead-example-promote(3/5): get_market_data + HTF + MTS path B (multi-section Example output)

Three perception tools with multi-section === blocks in Example output
that griffe was stripping. Each had pre-Args text already surviving in
description (~790-1188 chars), but Example output (~600-900 chars) +
Degradation trailer (~150 chars) lost.

Path B move all content (pre-Args prose + Example output + Degradation)
into DESC constants; source docstring keeps a 1-line summary + Args only.

MTS attribution candidate for Gate 4 ⑤ — MTS structure-terms reasoning
adoption baseline W3 71.4%; W4 verify no regression.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

---

## Task 7: Migrate `get_order_book` via path A inline (P2)

**Files:**
- Modify: `src/agent/trader.py` `get_order_book` wrapper (docstring inline rewrite)
- Test: `tests/test_trader_agent.py` (new `test_get_order_book_description_carries_degradation`)

- [ ] **Step 1: Write failing drift guard test**

```python
def test_get_order_book_description_carries_degradation():
    """get_order_book degradation文案 (insufficient / unavailable) must
    reach LLM via path A inline narrative."""
    from src.agent.trader import create_trader_agent
    from src.config import PersonaConfig

    agent = create_trader_agent(model="test", persona_config=PersonaConfig())
    tool = agent._function_toolset.tools["get_order_book"]
    desc = tool.tool_def.description

    assert "insufficient data" in desc, f"insufficient-data degradation literal missing: {desc!r}"
    assert "temporarily unavailable" in desc, f"unavailable degradation literal missing: {desc!r}"
```

- [ ] **Step 2: Run test — verify FAIL**

Expected: FAIL — current docstring has `Degradation:` admonition trailer that's stripped.

- [ ] **Step 3: Inline Degradation as narrative — preserve existing main_desc**

Locate `get_order_book` wrapper in trader.py. Current LLM-visible description (174 chars) ALREADY contains the valuable main_desc + "Reports best bid/ask, cumulative depth, bid/ask share, and concentrated levels (size > 3× same-side median)." sentence — must preserve.

Only the trailing `Degradation: ...` admonition is stripped. Rewrite to ADD the degradation as inline narrative AFTER the existing main_desc (no `<word>:` header — must survive griffe). Replace the wrapper docstring:

```python
        """Return top-N order book depth with concentrated-level breakdown.

        Reports best bid/ask, cumulative depth, bid/ask share, and concentrated
        levels (size > 3× same-side median). If the book is empty or shorter
        than requested depth, the response is `Order book ({symbol}): insufficient
        data (requested depth X, got Y)`. On service failure, the response is
        `Order book ({symbol}): temporarily unavailable`.

        Args:
            depth: levels per side to fetch (default 15).
        """
```

Self-check after edit: `tool.tool_def.description` should now be ~360-400 chars (vs 174 baseline), containing both the original "Reports best bid/ask..." sentence AND the new degradation narrative.

- [ ] **Step 4: Run test — verify PASS**

---

## Task 8: Migrate `get_performance` via path A inline (P3)

**Files:**
- Modify: `src/agent/trader.py` `get_performance` wrapper (inline rewrite of Degradation trailer)
- Test: `tests/test_trader_agent.py` (new `test_get_performance_description_carries_degradation`)

- [ ] **Step 1: Write failing drift guard test**

```python
def test_get_performance_description_carries_degradation():
    """get_performance degradation文案 (zero trades / legacy / no service)
    must reach LLM via path A inline narrative."""
    from src.agent.trader import create_trader_agent
    from src.config import PersonaConfig

    agent = create_trader_agent(model="test", persona_config=PersonaConfig())
    tool = agent._function_toolset.tools["get_performance"]
    desc = tool.tool_def.description

    assert "No completed trades yet" in desc
    assert "Stats unavailable" in desc
    assert "No metrics service available" in desc
```

- [ ] **Step 2: Run test — verify FAIL**

- [ ] **Step 3: Inline Degradation as narrative**

Locate `get_performance` wrapper. The current docstring has `Degradation: 'No completed trades yet.' if zero trades; 'Stats unavailable: ...' if all close fills are legacy; 'No metrics service available.' if metrics service missing.` trailing.

Fold into pre-Args description body (keep `Returns:` block intact since it survives via `<returns>` XML wrap). Add the inline degradation narrative as part of main_desc. Replace docstring:

```python
        """Show session trading performance — balance, return, fees, win rate, drawdown (gross + net dual view).

        When there are no completed trades the response is `No completed trades yet.`. When all close fills are pre-iter legacy (FIFO can't compute), the response is `Stats unavailable: ...`. When the metrics service is unavailable, the response is `No metrics service available.`.

        Returns:
            str: Two sections.

            === Trading Performance === — Initial Balance, Current Balance,
            Total Return (% + USDT, incl. unrealized, net), Realized PnL (gross / net + fees),
            Total Fees (cumulative across all fills).

            === Trade Stats === — Total Trades, Win Rate (gross / net), Avg Win/Loss
            (gross / net), Profit Factor (gross / net), Max Drawdown (net equity),
            Best/Worst Trade (gross / net). Caveats:
            - Pre-iter legacy close fills are skipped (FIFO requires entry_price + amount);
              when present, output adds "Note: net stats based on m/n trades" line.
            - OKX cache-miss close fills are included in algorithm (FIFO uses lot.entry_px
              from open) but flagged in caveat note.

            Related: get_trade_journal (decision timeline).
        """
```

- [ ] **Step 4: Run test — verify PASS**

- [ ] **Step 5: Run both P2+P3 tests + regression check**

Run: `.venv/bin/python -m pytest tests/test_trader_agent.py -q`
Expected: 20 passed (12 baseline + 8 new through Task 8).

- [ ] **Step 6: Commit P2+P3 path A pair**

```bash
git add src/agent/trader.py tests/test_trader_agent.py
git commit -m "iter-tool-opt-dead-example-promote(4/5): get_order_book + get_performance path A (inline Degradation)

Both tools had a single \`Degradation:\` trailer sentence — too small
for path B DESC override overhead. Inline as narrative prose in
pre-Args description body (no \`<word>:\` admonition that griffe would
strip). Single docstring source preserved.

get_performance Returns: block kept (pydantic-ai wraps Returns into
<returns> XML via _griffe.py — already reaches LLM).

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

---

## Task 9: Module-level audit drift guard via source-vs-desc differential (prevent regression)

**Files:**
- Test: `tests/test_trader_agent.py` (new `test_no_block_admonition_lost_to_griffe_stripping`)

**Detection strategy** (revised from initial regex-only design after PoC):

Empirical baseline (2026-05-19 pre-iter) shows:
- `cancel_price_level_alert` source has `Note: alerts at SL/TP levels...` as inline same-line `Note: <continuation>` — griffe **does not** strip; description contains it.
- `get_performance` source has `Degradation: '...' if zero trades` as block-style `Degradation:\n<indented>` — griffe **strips** it.
- griffe's actual trigger is: line ending in `:` + immediately-following indented block. Inline `<Word>: <prose>` on same line is plain prose.

A regex-only audit (matching any `^Note:|^Warning:|...`) would false-positive on the `cancel_price_level_alert` inline case. Instead, the audit uses **source-vs-desc differential**: detect block-style `<Word>:\n[ \t]+\S` patterns in source, then assert the header literal appears in `tool.tool_def.description`. This catches exactly what griffe strips, regardless of the specific header spelling.

- [ ] **Step 1: Write failing-OR-passing audit test (post-Tasks 7-8 expected PASS)**

Add to `tests/test_trader_agent.py`:

```python
def test_no_block_admonition_lost_to_griffe_stripping():
    """Module-level audit: detects when a block-style `<Word>:\\n<indent>`
    admonition in a wrapper's source docstring fails to reach the
    LLM-visible `tool.tool_def.description` (i.e., griffe stripped it).

    Detection is empirical (source-vs-desc differential), not
    regex-pattern-guessing — catches exactly what griffe actually strips
    on the current pydantic-ai / griffe version. Inline `<Word>: <prose>`
    on a single line is NOT detected (it survives griffe as plain prose;
    see `cancel_price_level_alert` for an example).

    Path-B override tools whitelisted — their source docstring is for
    IDE/dev readers only; LLM-facing content lives in DESC constants
    in `src/agent/tools_descriptions.py`.

    Allowed sections: griffe handles Args/Parameters into the
    `parameters_json_schema`, and pydantic-ai wraps Returns into a
    `<returns>` XML segment within description (see
    `pydantic_ai/_griffe.py:doc_descriptions`). So `Args:` / `Returns:` /
    `Yields:` block admonitions are intentional and excluded.
    """
    import re
    import textwrap
    from src.agent.trader import create_trader_agent
    from src.config import PersonaConfig

    agent = create_trader_agent(model="test", persona_config=PersonaConfig())

    # Path-B override sites — docstring is dev-facing, description is in DESC constant.
    PATH_B_OVERRIDE = {
        "set_next_wake",
        "set_next_wake_at",
        "get_market_data",
        "get_higher_timeframe_view",
        "get_multi_timeframe_snapshot",
    }

    # Headers that pydantic-ai handles explicitly — not "dead" even if griffe parses them.
    HANDLED_HEADERS = {"Args", "Arguments", "Parameters", "Returns", "Yields"}

    # Block-style admonition pattern: line ending in `<Word>:` + immediately
    # indented continuation line. Captures multi-word headers like
    # "Example output:" or "Example call:".
    BLOCK_ADMONITION = re.compile(
        r"^[ \t]*([A-Z][A-Za-z]+(?:\s+[a-z]+)?)\s*:\s*\n[ \t]+\S",
        re.MULTILINE,
    )

    offenders = []
    for name, tool in agent._function_toolset.tools.items():
        if name in PATH_B_OVERRIDE:
            continue
        src = textwrap.dedent(tool.function.__doc__ or "")
        desc = tool.tool_def.description or ""
        for match in BLOCK_ADMONITION.finditer(src):
            header = match.group(1)
            if header in HANDLED_HEADERS:
                continue
            # If griffe stripped this block, the header label itself
            # will be absent from `description` — that's the signal.
            if f"{header}:" not in desc:
                offenders.append((name, header))

    assert not offenders, (
        "Found block-style admonitions in @tool docstrings that are stripped\n"
        "from the LLM-visible description by griffe:\n"
        + "\n".join(f"  {n}: {h}:" for n, h in offenders)
        + "\n\nFix: either rewrite as inline narrative (path A — same-line "
        "prose, no indented continuation) OR move content into a DESC constant "
        "with `@tool(description=DESC_X)` (path B — see "
        "src/agent/tools_descriptions.py)."
    )
```

- [ ] **Step 2: Run test — verify PASS**

Run: `.venv/bin/python -m pytest tests/test_trader_agent.py::test_no_block_admonition_lost_to_griffe_stripping -xvs`
Expected: PASS — after Tasks 7-8 cleaned the 2 remaining `Degradation:` admonitions in path-A tools (`get_order_book` / `get_performance`), no stripped admonitions remain in non-PATH_B tools.

Sanity check: the audit should NOT flag `cancel_price_level_alert` (its `Note:` is inline same-line, survives griffe — `Note:` appears in both src and desc, so differential passes).

- [ ] **Step 3: Negative-control sanity (recommended)**

Manually inject a regression in one wrapper temporarily:

```python
# In trader.py, add to any non-PATH_B wrapper docstring:
"""...existing...

Note:
    test line for audit regression check.

Args:
    ...
"""
```

Rerun `pytest tests/test_trader_agent.py::test_no_block_admonition_lost_to_griffe_stripping`.
Expected: FAIL with offender listed.

Revert the injection. Rerun. Expected PASS.

- [ ] **Step 4: Commit module audit**

```bash
git add tests/test_trader_agent.py
git commit -m "iter-tool-opt-dead-example-promote(5/5): module-level audit prevents griffe-stripped admonition regression

Source-vs-description differential audit: detects when a block-style
\`<Word>:\\n<indent>\` admonition in any non-PATH_B wrapper docstring
fails to reach LLM-visible \`tool.tool_def.description\`. Empirical
detection (not regex pattern-guessing) — catches exactly what griffe
actually strips on the current pydantic-ai version.

Whitelist:
- Path-B sites (5 tools) — docstring is dev-only, DESC in tools_descriptions.py
- Args/Returns/Yields headers (pydantic-ai handles)

Inline same-line \`<Word>: <prose>\` patterns (e.g.
cancel_price_level_alert \`Note:\`) survive griffe and are not flagged
— differential mechanism naturally allows them.

Drift guard fires if a future developer adds new dead block-style
admonitions to a wrapper without realizing griffe strips them.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

---

## Task 10: Full verify + push + PR

- [ ] **Step 1: Full test suite**

Run: `.venv/bin/python -m pytest -q`
Expected: 1807 passed / 5 skipped (1798 baseline + 9 new tests; pre-existing `test_v_alert_lifecycle_filters_historical` env-specific failure unchanged).

- [ ] **Step 2: Re-audit via inspection**

Run a quick sanity check:

```bash
.venv/bin/python -c "
from src.agent.trader import create_trader_agent
from src.config import PersonaConfig
agent = create_trader_agent(model='test', persona_config=PersonaConfig())
for name in ['set_next_wake', 'set_next_wake_at', 'get_market_data', 'get_higher_timeframe_view', 'get_multi_timeframe_snapshot', 'get_order_book', 'get_performance']:
    desc = agent._function_toolset.tools[name].tool_def.description
    print(f'{name}: {len(desc)} chars')
"
```

Expected: all 7 tools now have significantly larger descriptions vs baseline.

- [ ] **Step 3: Push branch**

```bash
git push -u origin iter-tool-opt-dead-example-promote
```

- [ ] **Step 4: Open PR**

```bash
gh pr create --title "iter-tool-opt-dead-example-promote: 7 tools dead Examples/trailer → LLM-visible" --body "$(cat <<'EOF'
## Summary

Discovered during PR #58 implementation: pydantic-ai 1.78 / griffe strips structured docstring sections (Examples / Example output / inline admonitions) from `tool.tool_def.description`. Project audit identified 7 tools with ~3940 chars high-density doc content never reaching the LLM.

This PR migrates all 7 via two-track path: **path A inline narrative** (2 tools, single trailer sentence) and **path B description override** (5 tools, multi-outcome / multi-section blocks).

## Audit results (from PR #58 follow-up investigation)

| Tool | Loss % | Pattern | Path |
|---|---|---|---|
| set_next_wake_at | 95.4% | Examples block (4 outcome) + Returns + Alerts contract | B |
| set_next_wake | 90.5% | Examples block (3 outcome) + Returns + Alerts contract | B |
| get_order_book | 57.5% | Degradation trailer | A |
| get_market_data | 52.9% | Example call/output (3 sections) | B |
| get_higher_timeframe_view | 46.8% | Example call/output + Degradation | B |
| get_multi_timeframe_snapshot | 36.1% | Example call/output + Degradation | B |
| get_performance | 14.6% | Degradation trailer (Returns: survived) | A |

## Architecture

- `src/agent/tools_descriptions.py` — new module with 5 DESC constants for path-B tools
- `src/agent/trader.py` — `_create_dual_mode_tool()` factory: `@tool` and `@tool(description=DESC_X)` both supported
- Backward-compat for all 26 unaffected `@tool` sites
- `tests/test_trader_agent.py` — 7 per-tool drift guards + 1 module-level audit + 1 wrapper dual-mode smoke

## Memory anchors

- `[[griffe-example-section-stripped]]` — first实证 PR #58, this PR is the full sweep follow-up
- W3 attribution candidates: ③ R2-Next-H (set_next_wake_at adoption 2.0%) + ⑤ Gate 4 (MTS adoption 71.4%)
- Out-of-scope: 7 项 pre-W4 backlog — this is PR #58-derived independent mini-iter

## W4 verification gates (per spec §2.8)

- set_next_wake_at adoption ≥ 15% (vs W3 2.0%)
- set_next_wake reasoning HH:MM UTC ≥ 20% (vs W3 4%)
- MTS structure-terms adoption no regression (vs W3 71.4%)

## Test plan

- [x] 9 new tests added (1 wrapper + 7 per-tool drift + 1 module audit)
- [x] Full suite: 1807 passed / 5 skipped (was 1798 baseline)
- [x] Pre-existing env failure unchanged (`test_v_alert_lifecycle_filters_historical` — local DB sim #10 state-dependent, CI skip)
- [ ] W4 sim validation (post-merge, user-run per long-walltime memory)

🤖 Generated with [Claude Code](https://claude.com/claude-code)
EOF
)"
```

- [ ] **Step 5: Verify PR opened**

Capture PR URL for status update.

---

## Self-Review Checklist

After plan executes:
- Did all 9 new tests pass?
- Did the 33 unaffected tools' descriptions remain byte-identical?
- Did the 7 affected tools' descriptions grow to expected sizes (300-2000 chars each)?
- Is `src/agent/tools_descriptions.py` cleanly separated (no circular imports)?
- Drift guards cover both per-tool literals + module-level audit?
