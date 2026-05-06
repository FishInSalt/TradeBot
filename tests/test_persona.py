# tests/test_persona.py
from src.config import PersonaConfig


def test_prompt_contains_layer1_identity():
    """Layer 1 keyword presence — scope limited to Layer 1 only (intent clarity).
    After Iter 4 slim-down Layer 1 only contains: market context (perpetual / one-way)
    + 6 cross-tool bullets (fill / woken trigger responses + wake interval control). timeframe / memory
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


def test_prompt_contains_fill_response_guidance():
    from src.agent.persona import generate_system_prompt
    prompt = generate_system_prompt(PersonaConfig())
    prompt_lower = prompt.lower()
    # Open fill: set SL/TP using chart structure
    assert "open fill" in prompt_lower or "opened a position" in prompt_lower
    assert "stop loss" in prompt_lower and "take profit" in prompt_lower
    # Close fill: review outcome, save memory
    assert "close fill" in prompt_lower or "closed a position" in prompt_lower
    assert "review" in prompt_lower and "outcome" in prompt_lower


def test_prompt_contains_alert_response_guidance():
    from src.agent.persona import generate_system_prompt
    prompt = generate_system_prompt(PersonaConfig())
    prompt_lower = prompt.lower()
    assert "alert response" in prompt_lower
    assert "price level alert" in prompt_lower
    assert "volatility alert" in prompt_lower
    assert "trend" in prompt_lower or "noise" in prompt_lower


def test_prompt_contains_memory_quality_guidance():
    """L28 retained-bullet guard: 'Save actionable lessons to memory.' (spec §2.1)."""
    from src.agent.persona import generate_system_prompt
    prompt = generate_system_prompt(PersonaConfig())
    prompt_lower = prompt.lower()
    assert "actionable" in prompt_lower


def test_prompt_contains_anti_overtrading():
    from src.agent.persona import generate_system_prompt
    prompt = generate_system_prompt(PersonaConfig())
    prompt_lower = prompt.lower()
    assert "according to plan" in prompt_lower
    assert "does not need intervention" in prompt_lower


def test_prompt_contains_layer2_thinking_framework():
    from src.agent.persona import generate_system_prompt
    prompt = generate_system_prompt(PersonaConfig())
    prompt_lower = prompt.lower()
    # Thinking dimensions
    assert "market structure" in prompt_lower
    assert "risk" in prompt_lower and "reward" in prompt_lower
    assert "support" in prompt_lower or "resistance" in prompt_lower
    assert "position" in prompt_lower and ("management" in prompt_lower or "sizing" in prompt_lower)


def test_prompt_no_must_never_constraints():
    from src.agent.persona import generate_system_prompt
    prompt = generate_system_prompt(PersonaConfig())
    # Must not contain MUST/NEVER/ALWAYS as hard imperatives
    assert "You MUST" not in prompt
    assert "MUST NOT" not in prompt
    assert "NEVER go" not in prompt
    assert "NEVER exceed" not in prompt


def test_prompt_no_fixed_step_workflow():
    from src.agent.persona import generate_system_prompt
    prompt = generate_system_prompt(PersonaConfig())
    # Must not have fixed "Step 1: ... Step 2: ..." workflow
    assert "step 1" not in prompt.lower()


def test_prompt_no_numerical_params():
    from src.agent.persona import generate_system_prompt
    config = PersonaConfig(
        max_position_pct=30, preferred_leverage=3,
        stop_loss_pct=3.0, take_profit_pct=6.0,
    )
    prompt = generate_system_prompt(config)
    # Numerical params should NOT appear in prompt — A1 design decision
    # (P3 placeholders; see PersonaConfig docstring in src/config.py
    # and R2-6 wontfix). Relaxing this drift-guard requires revisiting A1.
    assert "30%" not in prompt
    assert "3x" not in prompt
    assert "3.0%" not in prompt
    assert "6.0%" not in prompt


def test_prompt_contains_trading_style_trend():
    from src.agent.persona import generate_system_prompt
    prompt = generate_system_prompt(PersonaConfig(trading_style="trend_following"))
    prompt_lower = prompt.lower()
    assert "trend" in prompt_lower
    assert "confirmation" in prompt_lower or "follow" in prompt_lower


def test_prompt_contains_trading_style_swing():
    from src.agent.persona import generate_system_prompt
    prompt = generate_system_prompt(PersonaConfig(trading_style="swing"))
    prompt_lower = prompt.lower()
    assert "swing" in prompt_lower
    assert "range" in prompt_lower or "pullback" in prompt_lower


def test_prompt_contains_trading_style_breakout():
    from src.agent.persona import generate_system_prompt
    prompt = generate_system_prompt(PersonaConfig(trading_style="breakout"))
    prompt_lower = prompt.lower()
    assert "breakout" in prompt_lower
    assert "consolidation" in prompt_lower or "volume" in prompt_lower


def test_prompt_styles_are_distinct():
    from src.agent.persona import generate_system_prompt
    p1 = generate_system_prompt(PersonaConfig(trading_style="trend_following"))
    p2 = generate_system_prompt(PersonaConfig(trading_style="swing"))
    p3 = generate_system_prompt(PersonaConfig(trading_style="breakout"))
    # Each style should produce meaningfully different content
    assert p1 != p2
    assert p2 != p3


def test_prompt_no_strategy_when_trading_style_none():
    """trading_style=None should not inject strategy section but signal freedom."""
    from src.agent.persona import generate_system_prompt
    prompt = generate_system_prompt(PersonaConfig(personality="moderate"))
    assert "Strategy Preference" not in prompt
    assert "Personality" in prompt
    assert "free to use any trading methodology" in prompt


def test_prompt_has_strategy_when_trading_style_set():
    """trading_style set should inject strategy section."""
    from src.agent.persona import generate_system_prompt
    prompt = generate_system_prompt(PersonaConfig(trading_style="swing"))
    assert "Strategy Preference: Swing" in prompt


def test_prompt_default_config_full_autonomy():
    """Default PersonaConfig (both None) should produce full autonomy prompt."""
    from src.agent.persona import generate_system_prompt
    config = PersonaConfig()
    assert config.personality is None
    assert config.trading_style is None
    prompt = generate_system_prompt(config)
    assert "Personality" not in prompt
    assert "Strategy Preference" not in prompt
    assert "full autonomy" in prompt.lower()


def test_prompt_strategy_only():
    """Strategy without personality — methodology-focused, no personality section."""
    from src.agent.persona import generate_system_prompt
    prompt = generate_system_prompt(PersonaConfig(trading_style="trend_following"))
    assert "Strategy Preference: Trend Following" in prompt
    assert "Personality" not in prompt


def test_prompt_personality_only():
    """Personality without strategy — temperament-focused, free methodology."""
    from src.agent.persona import generate_system_prompt
    prompt = generate_system_prompt(PersonaConfig(personality="aggressive"))
    assert "Personality: Aggressive" in prompt
    assert "Strategy Preference" not in prompt
    assert "free to use any trading methodology" in prompt


def test_prompt_both_configured():
    """Both personality and strategy configured — both sections present."""
    from src.agent.persona import generate_system_prompt
    prompt = generate_system_prompt(PersonaConfig(personality="conservative", trading_style="breakout"))
    assert "Personality: Conservative" in prompt
    assert "Strategy Preference: Breakout" in prompt
    # Should NOT have the "free to use any" text when strategy is set
    assert "free to use any trading methodology" not in prompt


def test_prompt_contains_personality():
    from src.agent.persona import generate_system_prompt
    prompt = generate_system_prompt(PersonaConfig(personality="conservative"))
    prompt_lower = prompt.lower()
    assert "capital preservation" in prompt_lower or "conservative" in prompt_lower


def test_prompt_personalities_are_distinct():
    from src.agent.persona import generate_system_prompt
    p1 = generate_system_prompt(PersonaConfig(personality="conservative"))
    p2 = generate_system_prompt(PersonaConfig(personality="moderate"))
    p3 = generate_system_prompt(PersonaConfig(personality="aggressive"))
    assert p1 != p2
    assert p2 != p3


def test_prompt_persona_describes_temperament():
    """Personality descriptions should describe trader temperament, not just risk rules."""
    from src.agent.persona import generate_system_prompt
    conservative = generate_system_prompt(PersonaConfig(personality="conservative")).lower()
    assert "patient" in conservative
    moderate = generate_system_prompt(PersonaConfig(personality="moderate")).lower()
    assert "balanced" in moderate or "pragmatic" in moderate
    aggressive = generate_system_prompt(PersonaConfig(personality="aggressive")).lower()
    assert "decisive" in aggressive


def test_prompt_style_soft_preference():
    """Strategy descriptions should frame style as a preference, not a rigid rule."""
    from src.agent.persona import generate_system_prompt
    for style in ["trend_following", "swing", "breakout"]:
        prompt = generate_system_prompt(PersonaConfig(trading_style=style)).lower()
        assert "preference" in prompt or "gravitate" in prompt or "not a rigid rule" in prompt


def test_prompt_is_in_english():
    from src.agent.persona import generate_system_prompt
    prompt = generate_system_prompt(PersonaConfig())
    # Should not contain Chinese characters
    import re
    chinese_chars = re.findall(r'[\u4e00-\u9fff]', prompt)
    assert len(chinese_chars) == 0, f"Found Chinese characters: {chinese_chars[:5]}"


def test_prompt_minimum_length():
    from src.agent.persona import generate_system_prompt
    prompt = generate_system_prompt(PersonaConfig())
    # Three-layer prompt should be substantial
    assert len(prompt) > 500


def test_layer1_cross_tool_bullet_count():
    """Layer 1 bullet count drift guard.

    Iter 4 PR #25 reduced Layer 1 from 25 to 5 cross-tool bullets.
    R2-5 PR # added 6th bullet "Wake interval control" (set_next_wake
    × alert/fill/conditional triggers). Bullets are markdown rows
    starting with '\\n- **' — matches `_build_layer1`'s format.
    """
    from src.agent.persona import generate_system_prompt
    from src.config import PersonaConfig
    prompt = generate_system_prompt(PersonaConfig())
    layer1 = prompt.split("## How to Think")[0]
    bullet_count = layer1.count("\n- **")
    assert bullet_count == 6, f"Expected 6 Layer 1 bullets, got {bullet_count}"


def test_layer1_no_tool_invocation_descriptions():
    """After Iter 4, Layer 1 should not contain tool-name invocation patterns —
    tool descriptions belong in docstrings (DRY). The 6 retained bullets describe
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


def test_layer1_contains_wake_interval_control_bullet():
    """R2-5 G8: Layer 1 含 Wake interval control bullet (cross-tool with alert/fill/conditional)."""
    from src.agent.persona import _build_layer1, RuntimeConfig
    layer1 = _build_layer1(RuntimeConfig())
    # bullet 标题
    assert "**Wake interval control**" in layer1, \
        "Layer 1 missing Wake interval control bullet header"
    # cross-tool 关系断言（真正的 Layer 1 价值，比 bound 重要）
    assert "Alerts, fills, and conditional triggers always interrupt sleep regardless of this setting" in layer1, \
        "Layer 1 Wake interval control bullet missing cross-tool interrupt clause"


def test_layer1_renders_dynamic_wake_max():
    """R2-5 G11: _build_layer1 渲染 RuntimeConfig.wake_max_minutes 实际值（非 envelope 1-180）。"""
    from src.agent.persona import _build_layer1, RuntimeConfig
    # sim #4 实证值（30min scheduler 配置下的 wake_max）
    layer1_120 = _build_layer1(RuntimeConfig(wake_max_minutes=120))
    assert "1-120 min for this session" in layer1_120, \
        "wake_max=120 not rendered in bullet"
    assert "1-60 min for this session" not in layer1_120, \
        "default 60 leaked when explicit 120 passed"
    # 默认 60（默认 15min scheduler 配置）
    layer1_60 = _build_layer1(RuntimeConfig(wake_max_minutes=60))
    assert "1-60 min for this session" in layer1_60, \
        "wake_max=60 not rendered in bullet"


def test_generate_system_prompt_default_runtime():
    """R2-5 G9: generate_system_prompt(persona) 单参等价于显式 RuntimeConfig() 默认值。"""
    from src.agent.persona import generate_system_prompt, RuntimeConfig
    from src.config import PersonaConfig
    prompt_default = generate_system_prompt(PersonaConfig())
    prompt_explicit = generate_system_prompt(PersonaConfig(), RuntimeConfig())
    assert prompt_default == prompt_explicit, \
        "Single-arg call must equal explicit RuntimeConfig() — backwards compat broken"
    # 渲染默认 wake_max=60
    assert "1-60 min for this session" in prompt_default, \
        "Default RuntimeConfig() should render 1-60 min"


def test_set_next_wake_no_decision_hints_in_description():
    """R2-5 G10: set_next_wake wrapper docstring fact-only verification.

    Decision hints "shorten when X" / "lengthen when Y" are N5 banned —
    they prescribe agent behavior based on conditions, violating fact-only
    philosophy. This drift guard ensures wrapper docstring (rendered into
    tool_def.description by pydantic-ai 1.78 griffe sniff) stays clean.

    API path: agent._function_toolset.tools[name].tool_def.<attr>
    (matches tests/test_trader_agent.py:210-211 access style; we use
    .description for first-paragraph text vs .parameters_json_schema
    for per-arg Args descriptions — see spec §3.6.1).
    """
    import re
    from src.agent.trader import create_trader_agent
    from src.config import PersonaConfig

    agent = create_trader_agent(model="test", persona_config=PersonaConfig())
    tool = agent._function_toolset.tools["set_next_wake"]
    desc = tool.tool_def.description or ""

    # N5 wordlist verification
    assert not re.search(r"\bshorten when\b", desc, re.IGNORECASE), \
        f"set_next_wake description contains banned 'shorten when': {desc!r}"
    assert not re.search(r"\blengthen when\b", desc, re.IGNORECASE), \
        f"set_next_wake description contains banned 'lengthen when': {desc!r}"
    # Sanity: factual content preserved
    assert "one-shot" in desc.lower(), \
        f"set_next_wake description should preserve 'one-shot' fact: {desc!r}"


def test_set_next_wake_wrapper_layer1_reference_intact():
    """R2-5 PR #34 I-1: wrapper docstring "Wake interval control" reference must point
    to a real Layer 1 bullet by the same name (Single Source of Truth invariant).

    G8 locks Layer 1 bullet header presence; G10 locks wrapper N5 wordlist absence;
    neither catches an orphan reference if Layer 1 bullet is renamed without updating
    wrapper Args.minutes. This test links them — both ends must contain the same string.

    API path: parameters_json_schema for per-arg Args descriptions (vs .description for
    first-paragraph text — see spec §3.6.1 + G10).
    """
    from src.agent.persona import _build_layer1, RuntimeConfig
    from src.agent.trader import create_trader_agent
    from src.config import PersonaConfig

    bullet_name = "Wake interval control"
    layer1 = _build_layer1(RuntimeConfig())
    agent = create_trader_agent(model="test", persona_config=PersonaConfig())
    minutes_desc = (
        agent._function_toolset.tools["set_next_wake"]
        .tool_def.parameters_json_schema["properties"]["minutes"]["description"]
    )

    assert bullet_name in minutes_desc, \
        f"wrapper Args.minutes lost reference to {bullet_name!r}: {minutes_desc!r}"
    assert bullet_name in layer1, \
        f"Layer 1 missing {bullet_name!r} bullet that wrapper points to"


# ─────────── R2-8b: Cycle Closing Summary section drift guards ───────────


def test_layer1_contains_cycle_closing_summary_section():
    """T3.1: section header `## Cycle Closing Summary` is present in Layer 1.
    The new section is independent of `## Cross-Tool Behavior` (different
    semantic dimension; see spec §3.4)."""
    from src.agent.persona import _build_layer1, RuntimeConfig

    layer1 = _build_layer1(RuntimeConfig())
    assert "## Cycle Closing Summary" in layer1, \
        "R2-8b section header missing from Layer 1"


def test_cycle_closing_summary_contains_5_field_anchors():
    """T3.2: all 5 anchor phrases for the trader-native fields are present.
    Anchor wording is the contract — wrappers may reword surroundings, but
    these phrases pin the field identity (see spec §3.2 D2)."""
    from src.agent.persona import _build_layer1, RuntimeConfig

    layer1 = _build_layer1(RuntimeConfig())
    # 5 anchor phrases (case-sensitive; lifted from spec §4.1.1)
    for anchor in (
        "(1) Stance",
        "(2) Active commitments",
        "(3) Thesis & invalidation",
        "(4) This cycle delta",
        "(5) Watch list (optional)",
    ):
        assert anchor in layer1, f"Missing field anchor: {anchor!r}"


def test_cycle_closing_summary_exposes_cap_numbers():
    """T3.3: cap numbers ~600 / ~800 / ~1200 are visible to the agent (D-Q-A fact-only). Phrases anchor to the cap paragraph (not other Layer 1 numerics like wake_max_minutes)."""
    from src.agent.persona import _build_layer1, RuntimeConfig

    layer1 = _build_layer1(RuntimeConfig())
    assert "~600 chars" in layer1
    assert "~800 for critical" in layer1
    assert "~1200" in layer1


def test_cycle_closing_summary_lists_critical_events():
    """T3.4: critical-events list is enumerated so the agent knows when the
    upper soft band (~800) is OK to exceed."""
    from src.agent.persona import _build_layer1, RuntimeConfig

    layer1 = _build_layer1(RuntimeConfig())
    assert "Critical events include:" in layer1
    layer1_lower = layer1.lower()
    # spec §4.1.1 enumerates: just opened/closed, alert triggered with action,
    # SL trail with multiple history points, thesis transition, macro proximity
    assert "just opened" in layer1_lower or "just closed" in layer1_lower
    assert "trail" in layer1_lower
    assert "thesis transition" in layer1_lower
    assert "macro" in layer1_lower


def test_cycle_closing_summary_contains_anti_instruction_guard():
    """T3.5 (review round 2 F1+F3): three key phrases lock the
    observational-not-prescriptive frame in place. Removing any is a drift
    that would re-open the perform-for-audience risk (§3.5)."""
    from src.agent.persona import _build_layer1, RuntimeConfig

    layer1 = _build_layer1(RuntimeConfig())
    assert "observational and descriptive — not prescriptive" in layer1
    assert "Do not include instructions or recommendations for future actions" in layer1
    assert "prefer setting an alert or limit order" in layer1


def test_cycle_closing_summary_does_not_mention_future_self_or_past_self():
    """T3.6 (review round 2 F1): the section must NOT reveal the audience.
    Past wording like "your future self will see this" was deliberately
    deleted to defuse perform-for-audience confirmation bias. This drift
    guard locks against a future PR re-introducing audience-revealing
    framing.
    """
    from src.agent.persona import _build_layer1, RuntimeConfig

    layer1_lower = _build_layer1(RuntimeConfig()).lower()
    assert "future self" not in layer1_lower
    assert "past self" not in layer1_lower
