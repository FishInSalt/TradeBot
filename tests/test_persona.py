from src.config import PersonaConfig


def test_generate_system_prompt():
    from src.agent.persona import generate_system_prompt
    config = PersonaConfig(risk_tolerance="moderate", trading_style="trend_following",
        max_position_pct=30, preferred_leverage=3, stop_loss_pct=3.0, take_profit_pct=6.0)
    prompt = generate_system_prompt(config)
    assert "moderate" in prompt.lower()
    assert "trend" in prompt.lower()
    assert "30" in prompt
    assert len(prompt) > 100


def test_prompt_includes_soft_constraints():
    from src.agent.persona import generate_system_prompt
    config = PersonaConfig(preferred_leverage=3, max_position_pct=30)
    prompt = generate_system_prompt(config)
    assert "MUST NOT exceed" in prompt or "NEVER" in prompt
    assert "stop loss" in prompt.lower()
    assert "all-in" in prompt.lower()


def test_prompt_includes_trader_role():
    from src.agent.persona import generate_system_prompt
    prompt = generate_system_prompt(PersonaConfig())
    assert "trader" in prompt.lower() or "trading" in prompt.lower()
