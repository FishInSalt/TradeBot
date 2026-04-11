# src/services/model_manager.py
from __future__ import annotations

import asyncio
import json
import logging
import os
from dataclasses import dataclass, asdict
from pathlib import Path
from typing import Any

from pydantic_ai import Agent
from pydantic_ai.models.anthropic import AnthropicModel
from pydantic_ai.models.openai import OpenAIChatModel
from pydantic_ai.models.google import GoogleModel
from pydantic_ai.models.groq import GroqModel
from pydantic_ai.providers.anthropic import AnthropicProvider
from pydantic_ai.providers.openai import OpenAIProvider
from pydantic_ai.providers.google import GoogleProvider
from pydantic_ai.providers.groq import GroqProvider

logger = logging.getLogger(__name__)

_PROVIDER_MAP: dict[str, tuple[type, type]] = {
    "anthropic": (AnthropicModel, AnthropicProvider),
    "openai": (OpenAIChatModel, OpenAIProvider),
    "google-gla": (GoogleModel, GoogleProvider),
    "groq": (GroqModel, GroqProvider),
}


@dataclass
class ModelConfig:
    id: str
    provider: str
    model: str
    api_key: str
    base_url: str | None


class ModelManager:
    def __init__(self, config_path: Path = Path("config/models.json")):
        self._config_path = config_path

    def load_models(self) -> list[ModelConfig]:
        """从 models.json 加载模型配置列表。文件不存在时返回空列表。"""
        if not self._config_path.exists():
            return []
        with open(self._config_path) as f:
            data = json.load(f)
        return [
            ModelConfig(
                id=item["id"],
                provider=item["provider"],
                model=item["model"],
                api_key=item["api_key"],
                base_url=item.get("base_url"),
            )
            for item in data
        ]

    def save_models(self, configs: list[ModelConfig]) -> None:
        """保存模型配置列表到 models.json，设置 0o600 权限。"""
        ids = [c.id for c in configs]
        if len(ids) != len(set(ids)):
            dupes = [i for i in ids if ids.count(i) > 1]
            raise ValueError(f"Duplicate model IDs: {set(dupes)}")
        self._config_path.parent.mkdir(parents=True, exist_ok=True)
        data = [asdict(c) for c in configs]
        with open(self._config_path, "w") as f:
            json.dump(data, f, indent=2)
        os.chmod(self._config_path, 0o600)

    def create_model(self, config: ModelConfig) -> Any:
        """根据 ModelConfig 构造 pydantic-ai Model 对象。通过 Provider 传入 api_key 和 base_url。"""
        entry = _PROVIDER_MAP.get(config.provider)
        if entry is None:
            raise ValueError(f"Unsupported provider: {config.provider}")
        model_cls, provider_cls = entry
        provider_kwargs: dict[str, Any] = {"api_key": config.api_key}
        if config.base_url:
            provider_kwargs["base_url"] = config.base_url
        provider = provider_cls(**provider_kwargs)
        return model_cls(config.model, provider=provider)

    def get_model_by_id(self, model_id: str, models: list[ModelConfig]) -> ModelConfig | None:
        """按 id 查找模型配置。"""
        for m in models:
            if m.id == model_id:
                return m
        return None

    async def test_connectivity(self, model: Any, timeout: float = 10.0) -> tuple[bool, str | None]:
        """测试模型 API 连通性。返回 (success, error_message)。"""
        try:
            agent = Agent(model, output_type=str)
            await asyncio.wait_for(
                agent.run("Say 'ok' and nothing else."),
                timeout=timeout,
            )
            return True, None
        except asyncio.TimeoutError:
            return False, "Connection timed out"
        except Exception as e:
            return False, str(e)
