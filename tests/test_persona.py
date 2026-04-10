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


def test_prompt_includes_event_driven_workflow():
    from src.agent.persona import generate_system_prompt
    prompt = generate_system_prompt(PersonaConfig())
    assert "scheduled trigger" in prompt.lower() or "scheduled" in prompt.lower()
    assert "fill event" in prompt.lower() or "fill" in prompt.lower()


def test_prompt_includes_naked_position_check():
    from src.agent.persona import generate_system_prompt
    prompt = generate_system_prompt(PersonaConfig())
    assert "stop loss" in prompt.lower()
    assert "take profit" in prompt.lower() or "protective" in prompt.lower()


def test_prompt_includes_reasoning_instruction():
    from src.agent.persona import generate_system_prompt
    prompt = generate_system_prompt(PersonaConfig())
    assert "reasoning" in prompt.lower()
