from src.config import ModelsConfig, ModelRouting


def test_resolve_model():
    config = ModelsConfig(
        default="anthropic:claude-sonnet-4-20250514",
        strong="anthropic:claude-opus-4-6",
        weak="anthropic:claude-haiku-4-5-20251001",
        routing=ModelRouting(market_analysis="strong", trade_decision="strong", news_summary="weak", review="weak"),
    )
    from src.services.llm_router import LLMRouter
    router = LLMRouter(config)
    assert router.resolve("market_analysis") == "anthropic:claude-opus-4-6"
    assert router.resolve("news_summary") == "anthropic:claude-haiku-4-5-20251001"
    assert router.resolve("unknown") == "anthropic:claude-sonnet-4-20250514"


def test_is_strong_model():
    config = ModelsConfig()
    from src.services.llm_router import LLMRouter
    router = LLMRouter(config)
    assert router.is_strong_model("market_analysis") is True
    assert router.is_strong_model("news_summary") is False
