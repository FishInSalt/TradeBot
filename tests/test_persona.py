# tests/test_persona.py
from src.config import PersonaConfig
from src.agent.persona import (
    CYCLE_DECISION_WORD_CAP,
    DEFAULT_TAKER_FEE_RATE,
    RuntimeConfig,
    generate_system_prompt,
)


def test_default_taker_fee_rate_is_okx_btc_perp_regular_tier():
    """DEFAULT_TAKER_FEE_RATE = 0.0005 (OKX BTC perp regular tier taker)."""
    assert DEFAULT_TAKER_FEE_RATE == 0.0005


def test_runtime_config_default_taker_fee_rate():
    """RuntimeConfig 默认 taker_fee_rate 与常量一致 (test/temp 用途)."""
    rc = RuntimeConfig()
    assert rc.taker_fee_rate == DEFAULT_TAKER_FEE_RATE


def test_runtime_config_explicit_taker_fee_rate():
    """RuntimeConfig 接受 explicit override."""
    rc = RuntimeConfig(taker_fee_rate=0.001)
    assert rc.taker_fee_rate == 0.001


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
    # Close fill: review outcome
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


def test_wake_interval_control_states_one_shot_and_rearm():
    """Drift guard: Wake interval control bullet teaches one-shot + re-arm.

    sim #17 wake-forget root cause (spec 2026-06-11-wake-rearm-persona):
    the agent treated wake as a persistent commitment (listed `Wake: HH:MM`
    under Active commitments, reasoned "already set") and skipped re-arming on
    early alert/conditional wakes, silently reverting to the session default.
    The bullet must state the interval is one-shot and that an interrupting
    trigger CANCELS the pending wake, so the agent re-arms each cycle.
    'cancels' (not 'consumes') is load-bearing — 'consumes' wrongly implies
    the wake already fired, the exact wrong inference on an early wake.
    """
    from src.agent.persona import generate_system_prompt
    prompt = generate_system_prompt(PersonaConfig())
    layer1 = prompt.split("## How to Think")[0]
    # Bound the slice to the bullet itself: "Wake interval control" is the last
    # "- **" bullet in Layer 1, so the split element runs to the end of layer1
    # (swallowing the "## Cycle Closing Summary" section) unless we cut at the
    # bullet's trailing blank line. Without this, the "again"/"consumes" asserts
    # would silently span the whole tail rather than the wake bullet.
    wake_bullet = next(
        b for b in layer1.split("\n- **") if b.startswith("Wake interval control")
    ).split("\n\n")[0]
    assert "one-shot" in wake_bullet
    assert "cancels" in wake_bullet
    assert "consumes" not in wake_bullet
    assert "again" in wake_bullet
    # wake_max interpolation must remain intact (no leaked placeholder)
    assert "{runtime" not in prompt


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
    Old phrases ('check the chart', 'do not skip', 'arbitrary ones') deleted.
    The sync-market-fill rewrite replaced the 'use market data' guidance with
    'using the thesis you just formed' (SL/TP set right after the synchronous
    open rather than on a separate trigger wake); softened (non-hard-rule)
    wording remains.
    """
    from src.agent.persona import generate_system_prompt
    prompt = generate_system_prompt(PersonaConfig()).lower()
    # Hard-rule wording must NOT be present
    assert "do not skip market data" not in prompt
    assert "structural support/resistance" not in prompt
    assert "arbitrary ones" not in prompt
    # Softened wording must be present
    assert "using the thesis you just formed" in prompt


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
    assert "alerts, fills, and conditional triggers always interrupt sleep" in layer1, \
        "Layer 1 Wake interval control bullet missing cross-tool interrupt clause"
    assert "scheduled wake-up applies only when no external trigger fires" in layer1, \
        "Layer 1 missing 'scheduled wake-up applies only when no external trigger fires' anchor"


def test_layer1_no_wake_tool_signature_literal():
    """Spec §6.3 T3.2: Layer 1 must not name either wake tool signature literally.

    L3 抽象 (Iter 4 DRY 反转) — 工具描述交 docstring 自承，Layer 1 仅保留
    cross-tool behavior + session-aware bound (per Iter 4 PR #25 pattern).
    """
    from src.agent.persona import _build_layer1, RuntimeConfig
    runtime = RuntimeConfig()
    layer1 = _build_layer1(runtime)
    assert "set_next_wake(minutes)" not in layer1, \
        "Layer 1 must not name set_next_wake signature; description belongs in docstring"
    assert "set_next_wake_at(target_time)" not in layer1, \
        "Layer 1 must not name set_next_wake_at signature; description belongs in docstring"


def test_layer1_renders_dynamic_wake_max():
    """R2-5 G11: _build_layer1 渲染 RuntimeConfig.wake_max_minutes 实际值（非 envelope 1-180）。"""
    from src.agent.persona import _build_layer1, RuntimeConfig
    # sim #4 实证值（30min scheduler 配置下的 wake_max）
    layer1_120 = _build_layer1(RuntimeConfig(wake_max_minutes=120))
    assert "1-120 min from now for this session" in layer1_120, \
        "wake_max=120 not rendered in bullet"
    assert "1-60 min from now for this session" not in layer1_120, \
        "default 60 leaked when explicit 120 passed"
    # 默认 60（默认 15min scheduler 配置）
    layer1_60 = _build_layer1(RuntimeConfig(wake_max_minutes=60))
    assert "1-60 min from now for this session" in layer1_60, \
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
    assert "1-60 min from now for this session" in prompt_default, \
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
        "(3) This cycle delta",
        "(4) Thesis & invalidation",
        "(5) Watch list (optional)",
    ):
        assert anchor in layer1, f"Missing field anchor: {anchor!r}"


def test_cycle_closing_summary_lists_critical_events():
    """T3.4 (R2-8d D5): critical-events enum 仍在长度 ceiling 段内括号形式列出。"""
    from src.agent.persona import _build_layer1, RuntimeConfig

    layer1 = _build_layer1(RuntimeConfig())
    layer1_lower = layer1.lower()
    # D5 重写后 critical events 在词数 ceiling 段内以括号形式 enum
    assert "critical events" in layer1_lower
    assert "open/close" in layer1_lower
    assert "alert with action" in layer1_lower
    assert "thesis transition" in layer1_lower
    assert "macro event proximity" in layer1_lower


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


def test_cycle_closing_summary_lead_uses_cognitive_flow_framing():
    """T-D1: lead 必须用 cognitive flow framing (After your reasoning... record),
    防回滚 summary-centric "The summary IS the final response" 措辞。"""
    from src.agent.persona import _build_layer1, RuntimeConfig
    layer1 = _build_layer1(RuntimeConfig())
    assert "After your reasoning and any tool calls, record" in layer1


def test_cycle_closing_summary_field_order_delta_before_thesis():
    """T-D2: D2 序互换 - (3) This cycle delta 必须在 (4) Thesis & invalidation 之前。
    truncation 兜底序保护反思段。"""
    from src.agent.persona import _build_layer1, RuntimeConfig
    layer1 = _build_layer1(RuntimeConfig())
    pos_delta = layer1.find("(3) This cycle delta")
    pos_thesis = layer1.find("(4) Thesis & invalidation")
    assert pos_delta > 0 and pos_thesis > 0, "anchors missing"
    assert pos_delta < pos_thesis, f"D2 序错: delta@{pos_delta} >= thesis@{pos_thesis}"


def test_cycle_closing_summary_length_guidance_phrases_present():
    """T-D5 (extended R2-Next-A): length guidance phrases include word
    ceilings (400/600) AND the new 700-word system cap (A3)."""
    from src.agent.persona import _build_layer1, RuntimeConfig
    layer1 = _build_layer1(RuntimeConfig())
    assert "400 words" in layer1
    assert "never exceeding 600 words" in layer1
    assert "700 words" in layer1                            # R2-Next-A A3
    assert "single sentence is sufficient" in layer1
    assert "Skip if no relevant observations" in layer1


def test_cycle_closing_summary_no_legacy_fiction_or_system_aware_phrases():
    """T-D4+D5 (R2-Next-A calibrated): persona NOT 含 legacy fiction
    数字 + retired R2-8d-era system-aware phrases that still apply.

    Note (R2-Next-A): "hard-truncates" was removed from this list — A3
    deliberately surfaces system mechanic (3-channel feedback signal
    closes F1 length-loop). Remaining forbidden phrases still defend
    against R2-8d/PR #38 fiction (chars-based ceilings, target framing,
    SKIP fallback, summary-centric priming).
    """
    from src.agent.persona import _build_layer1, RuntimeConfig
    layer1 = _build_layer1(RuntimeConfig())
    forbidden = [
        "~600 chars",  # D4 撤
        "~800",        # D4 撤
        "~1200",       # D4 撤
        "Aim for",     # D4 撤 wishful target framing
        "is typically 1-3 sentences",  # D5 撤 per-field cap fiction
        # "hard-truncates" — retired in R2-Next-A (A3 surfaces system mechanic)
        "## SKIP",                     # D1 不引入 SKIP fallback
        "The summary IS the final response",  # D1 撤 summary-centric priming
        "~4000",       # D5 HARD_CAP 不暴露 (用 "~4000" anchor 而非 bare "4000"
                       # 避免与未来 RuntimeConfig 大数值字段 false-positive 冲突)
    ]
    for phrase in forbidden:
        assert phrase not in layer1, f"forbidden phrase leaked: {phrase!r}"


def test_cycle_closing_summary_explicit_word_cap_anchor():
    """T-A3.1 (R2-Next-A): persona Layer 1 contains the literal "700
    words" anchor — the explicit-cap channel of the 3-channel signal
    stack (paired with D1 marker + D2 header)."""
    from src.agent.persona import _build_layer1, RuntimeConfig
    layer1 = _build_layer1(RuntimeConfig())
    assert "700 words" in layer1


def test_cycle_closing_summary_truncation_consequence_phrase():
    """T-A3.2 (R2-Next-A): persona Layer 1 explicitly states the
    consequence of overflow — "lost from prior-cycle context" or
    similar. The consequence phrase triggers D11 self-reference
    awareness (priors are read 3.07x/cycle, sim #8). Anti-revert
    guard against future drift to mechanism-only wording."""
    from src.agent.persona import _build_layer1, RuntimeConfig
    layer1_lower = _build_layer1(RuntimeConfig()).lower()
    assert "truncated" in layer1_lower or "lost" in layer1_lower, \
        "A3 consequence phrase missing — agent must see that overflow " \
        "loses context to trigger self-correction"


def test_cycle_closing_summary_word_cap_matches_constant():
    """T-A3.3 (R2-Next-A drift guard): the literal "700 words" in
    persona Layer 1 must match CYCLE_DECISION_WORD_CAP. Renaming or
    re-valuing the constant must update the persona text — this test
    catches drift between persona / D1 marker / D2 helper."""
    from src.agent.persona import _build_layer1, RuntimeConfig, CYCLE_DECISION_WORD_CAP

    layer1 = _build_layer1(RuntimeConfig())
    expected = f"{CYCLE_DECISION_WORD_CAP} words"
    assert expected in layer1, \
        f"persona must mention '{expected}' (matching constant)"


def test_word_cap_value_consistent_across_three_channels():
    """T5.1 (R2-Next-A cross-channel drift guard): the literal value of
    CYCLE_DECISION_WORD_CAP must propagate to:
      - persona Layer 1 text "700 words" (A3 channel)
      - _truncate_decision marker "cut at 700 words" (D1 channel)
      - D2 channel: _count_words helper uses the same \\S+ convention
        (asserted indirectly via the helper's behavior; D2 doesn't bake
        the constant value, it just calls _count_words)

    Renaming or re-valuing the constant must keep all three in sync.
    This is the final defense against partial migrations.
    """
    from src.agent.persona import _build_layer1, RuntimeConfig, CYCLE_DECISION_WORD_CAP
    from src.cli.app import _truncate_decision, _count_words

    cap = CYCLE_DECISION_WORD_CAP
    expected_phrase = f"{cap} words"

    # A3 channel
    layer1 = _build_layer1(RuntimeConfig())
    assert expected_phrase in layer1, \
        f"persona missing '{expected_phrase}' — A3 channel out of sync"

    # D1 channel: marker must contain "cut at {cap} words"
    over_cap_text = " ".join(["w"] * (cap + 50))
    truncated = _truncate_decision(over_cap_text)
    assert f"cut at {expected_phrase}" in truncated, \
        f"_truncate_decision marker missing 'cut at {expected_phrase}' " \
        "— D1 channel out of sync"

    # D2 channel: _count_words convention is \\S+ whitespace runs;
    # asserted by checking the helper agrees with the marker's measured
    # count. If the helper used a different convention, the body would
    # have a different word count than the cap.
    body = truncated.rsplit("\n... [truncated", 1)[0]
    assert _count_words(body) == cap, \
        f"_count_words convention diverged: expected exactly {cap} " \
        f"words in truncated body, got {_count_words(body)}"


def test_trading_deps_has_fee_rate_default():
    """TradingDeps default fee_rate matches DEFAULT_TAKER_FEE_RATE."""
    from src.agent.trader import TradingDeps
    from unittest.mock import MagicMock
    deps = TradingDeps(
        symbol="BTC/USDT:USDT", timeframe="5m",
        market_data=MagicMock(), exchange=MagicMock(),
        technical=MagicMock(), memory=MagicMock(),
        session_id="test",
    )
    assert deps.fee_rate == DEFAULT_TAKER_FEE_RATE


def test_layer1_market_context_renders_taker_fee_rate():
    """Market Context segment includes 'Fee: taker X.XXX% per side (set at session start).'"""
    from src.agent.persona import _build_layer1, RuntimeConfig
    rc = RuntimeConfig(taker_fee_rate=0.001)
    text = _build_layer1(rc)
    assert "Fee: taker 0.100% per side (set at session start)." in text
    assert "Round-trip cost on a position = entry_fee + exit_fee" in text
    assert "≈ 2 × fee_rate × notional" in text


def test_market_context_segment_no_evaluation_words():
    """Market Context segment removes 'frequent small trades' / 'erode capital' nudges.

    drift guard scope: only the '## Market Context' segment of _build_layer1
    output. Layer 3 (personality / trading_style) MAY contain compliant
    evaluation descriptors (e.g., 'patient trader') and is not part of this guard.
    """
    from src.agent.persona import _build_layer1, RuntimeConfig
    text = _build_layer1(RuntimeConfig())

    # extract '## Market Context' segment up to next '##' header
    market_ctx_start = text.index("## Market Context")
    next_h2 = text.index("##", market_ctx_start + len("## Market Context"))
    market_ctx_segment = text[market_ctx_start:next_h2]

    forbidden = ["frequent small trades", "erode capital", "friction costs alone"]
    for word in forbidden:
        assert word not in market_ctx_segment, f"'{word}' still present in Market Context"


def test_persona_market_sync_replaces_separate_trigger():
    """市价同步语义改写：不再含 'do not attempt in the same cycle' 类冲突措辞。"""
    from src.agent.persona import generate_system_prompt
    prompt = generate_system_prompt(PersonaConfig())
    low = prompt.lower()
    # 新措辞present
    assert "synchronous" in low
    assert "set stop loss and take profit" in low
    # 旧冲突措辞absent
    assert "do not attempt in the same cycle" not in low
    assert "separate trigger" not in low


def test_layer1_contract_size_line_renders_runtime_values():
    """Layer 1 渲染 contract size 行 + notional 换算规则（spec §3.1）。"""
    runtime = RuntimeConfig(taker_fee_rate=0.001, contract_size=0.01, base_ccy="BTC")
    text = generate_system_prompt(PersonaConfig(), runtime)
    assert ("Contract size: 1 contract = 0.01 BTC. "
            "Notional (USDT) = contracts × contract_size × price.") in text


def test_layer1_contract_size_line_follows_fee_lines():
    """contract size 行紧随 Fee/Round-trip 两行之后、仍在 Market Context 段内（spec §3.1）。"""
    runtime = RuntimeConfig(taker_fee_rate=0.001, contract_size=0.01, base_ccy="BTC")
    text = generate_system_prompt(PersonaConfig(), runtime)
    fee_idx = text.index("Fee: taker")
    roundtrip_idx = text.index("≈ 2 × fee_rate × notional.")  # Round-trip 行稳定子串
    cs_idx = text.index("Contract size: 1 contract")
    # 三行严格顺序：Fee → Round-trip → Contract size
    assert fee_idx < roundtrip_idx < cs_idx
    # contract size 行仍在 Market Context 段内（下一个 ## 段头 "## Cross-Tool Behavior" 之前）
    next_header_idx = text.index("## ", cs_idx)
    assert cs_idx < next_header_idx


def test_layer2_risk_reward_breakeven_question():
    """Layer 2 Risk-Reward 维度含 breakeven 疑问句（spec §3.4）。"""
    text = generate_system_prompt(PersonaConfig())
    assert ("Does the expected move clear the round-trip fee cost — "
            "where is breakeven (entry ± 2 × fee_rate) relative to your stop and target?") in text


def test_persona_carries_injection_delivery_contract():
    """§5 drift guard：persona 文本与 injector header 常量逐字一致（防两处漂移）。

    契约要素切片断言：① NEW EVENTS TRIGGERED 锚（≥2 处：wake bullet 契约句 +
    fill/alert response 通道中性化）；② 注入不 cancel one-shot wake 的边界句；
    ③ 末次工具调用后到达 → 正常唤醒的兜底分支。"""
    from src.agent.persona import generate_system_prompt
    from src.config import PersonaConfig
    from src.services.midcycle_injector import INJECTION_HEADER_PREFIX

    text = generate_system_prompt(PersonaConfig())
    assert text.count(INJECTION_HEADER_PREFIX) >= 2

    wake_bullet = [
        b for b in text.split("\n- **") if b.startswith("Wake interval control")
    ]
    assert len(wake_bullet) == 1
    # Wake bullet 是末位 bullet，split 切片会吞掉 prompt 尾部全部内容——截到段落边界
    # 才真锚定在 bullet 内（同 test_wake_interval_control_states_one_shot_and_rearm）。
    bullet = wake_bullet[0].split("\n\n")[0]
    assert "delivered in your next tool result" in bullet
    assert "does **not** cancel the next-wake interval" in bullet
    assert "still arrives as a normal wake" in bullet
