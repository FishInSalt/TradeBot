from __future__ import annotations
from src.config import ModelsConfig


class LLMRouter:
    def __init__(self, config: ModelsConfig):
        self._config = config
        self._tier_map = {"strong": config.strong, "weak": config.weak, "default": config.default}
        self._routing = {
            "market_analysis": config.routing.market_analysis,
            "trade_decision": config.routing.trade_decision,
            "news_summary": config.routing.news_summary,
            "review": config.routing.review,
        }

    def resolve(self, task: str) -> str:
        tier = self._routing.get(task, "default")
        return self._tier_map.get(tier, self._config.default)

    def is_strong_model(self, task: str) -> bool:
        return self._routing.get(task, "default") == "strong"
