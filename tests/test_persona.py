# tests/test_persona.py
from src.config import PersonaConfig


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
    # Numerical params should NOT appear in prompt
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
