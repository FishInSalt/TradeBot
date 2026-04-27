# tests/test_model_manager.py

import json
import os
import pytest
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch


def test_model_config_dataclass():
    """ModelConfig 应正确存储所有字段。"""
    from src.services.model_manager import ModelConfig
    config = ModelConfig(
        id="claude-opus",
        provider="anthropic",
        model="claude-opus-4-6",
        api_key="sk-ant-test",
        base_url=None,
    )
    assert config.id == "claude-opus"
    assert config.provider == "anthropic"
    assert config.model == "claude-opus-4-6"
    assert config.api_key == "sk-ant-test"
    assert config.base_url is None


def test_model_config_with_base_url():
    """ModelConfig 应支持 base_url 字段（OpenRouter 等场景）。"""
    from src.services.model_manager import ModelConfig
    config = ModelConfig(
        id="deepseek-chat",
        provider="openai",
        model="deepseek/deepseek-chat",
        api_key="sk-or-test",
        base_url="https://openrouter.ai/api/v1",
    )
    assert config.base_url == "https://openrouter.ai/api/v1"


def test_load_models_empty(tmp_path: Path):
    """models.json 不存在时应返回空列表。"""
    from src.services.model_manager import ModelManager
    manager = ModelManager(config_path=tmp_path / "models.json")
    models = manager.load_models()
    assert models == []


def test_save_and_load_models(tmp_path: Path):
    """保存后加载应返回相同数据。"""
    from src.services.model_manager import ModelManager, ModelConfig
    manager = ModelManager(config_path=tmp_path / "models.json")

    configs = [
        ModelConfig(id="claude-opus", provider="anthropic", model="claude-opus-4-6",
                    api_key="sk-ant-test", base_url=None),
        ModelConfig(id="deepseek", provider="openai", model="deepseek/deepseek-chat",
                    api_key="sk-or-test", base_url="https://openrouter.ai/api/v1"),
    ]
    manager.save_models(configs)

    loaded = manager.load_models()
    assert len(loaded) == 2
    assert loaded[0].id == "claude-opus"
    assert loaded[0].api_key == "sk-ant-test"
    assert loaded[1].id == "deepseek"
    assert loaded[1].base_url == "https://openrouter.ai/api/v1"


def test_save_models_file_permissions(tmp_path: Path):
    """models.json 应设置 0o600 权限。"""
    from src.services.model_manager import ModelManager, ModelConfig
    manager = ModelManager(config_path=tmp_path / "models.json")

    configs = [
        ModelConfig(id="test", provider="anthropic", model="test-model",
                    api_key="sk-test", base_url=None),
    ]
    manager.save_models(configs)

    file_path = tmp_path / "models.json"
    mode = oct(os.stat(file_path).st_mode & 0o777)
    assert mode == "0o600"


def test_create_model_anthropic():
    """create_model 应为 anthropic provider 返回 AnthropicModel。"""
    from src.services.model_manager import ModelManager, ModelConfig
    from pydantic_ai.models.anthropic import AnthropicModel

    manager = ModelManager(config_path=Path("/dev/null"))
    config = ModelConfig(id="claude", provider="anthropic", model="claude-opus-4-6",
                         api_key="sk-ant-test", base_url=None)
    model = manager.create_model(config)
    assert isinstance(model, AnthropicModel)


def test_create_model_openai():
    """create_model 应为 openai provider 返回 OpenAIChatModel。"""
    from src.services.model_manager import ModelManager, ModelConfig
    from pydantic_ai.models.openai import OpenAIChatModel

    manager = ModelManager(config_path=Path("/dev/null"))
    config = ModelConfig(id="gpt4", provider="openai", model="gpt-4o",
                         api_key="sk-test", base_url=None)
    model = manager.create_model(config)
    assert isinstance(model, OpenAIChatModel)


def test_create_model_openai_with_base_url():
    """create_model 应为 openai provider 传入 base_url。"""
    from src.services.model_manager import ModelManager, ModelConfig
    from pydantic_ai.models.openai import OpenAIChatModel

    manager = ModelManager(config_path=Path("/dev/null"))
    config = ModelConfig(id="deepseek", provider="openai", model="deepseek/deepseek-chat",
                         api_key="sk-or-test", base_url="https://openrouter.ai/api/v1")
    model = manager.create_model(config)
    assert isinstance(model, OpenAIChatModel)


def test_create_model_google():
    """create_model 应为 google-gla provider 返回 GoogleModel。"""
    from src.services.model_manager import ModelManager, ModelConfig
    from pydantic_ai.models.google import GoogleModel

    manager = ModelManager(config_path=Path("/dev/null"))
    config = ModelConfig(id="gemini", provider="google-gla", model="gemini-2.0-flash",
                         api_key="test-key", base_url=None)
    model = manager.create_model(config)
    assert isinstance(model, GoogleModel)


def test_create_model_groq():
    """create_model 应为 groq provider 返回 GroqModel。"""
    from src.services.model_manager import ModelManager, ModelConfig
    from pydantic_ai.models.groq import GroqModel

    manager = ModelManager(config_path=Path("/dev/null"))
    config = ModelConfig(id="llama", provider="groq", model="llama-3.3-70b-versatile",
                         api_key="gsk-test", base_url=None)
    model = manager.create_model(config)
    assert isinstance(model, GroqModel)


def test_create_model_deepseek():
    """create_model 应为 deepseek provider 返回 OpenAIChatModel（带 DeepSeekProvider）。"""
    from src.services.model_manager import ModelManager, ModelConfig
    from pydantic_ai.models.openai import OpenAIChatModel
    from pydantic_ai.providers.deepseek import DeepSeekProvider

    manager = ModelManager(config_path=Path("/dev/null"))
    config = ModelConfig(id="dsv4", provider="deepseek", model="deepseek-v4-pro",
                         api_key="sk-test", base_url=None)
    model = manager.create_model(config)
    assert isinstance(model, OpenAIChatModel)
    # DeepSeekProvider 自带 base_url=https://api.deepseek.com，无需用户传
    assert isinstance(model._provider, DeepSeekProvider)


def test_get_optimal_settings_known_model():
    """已枚举的 model 应返回带 thinking 配置的 ModelSettings。"""
    from src.services.model_manager import get_optimal_settings

    settings = get_optimal_settings("deepseek-v4-pro")
    assert settings.get("thinking") == "high"
    assert settings.get("extra_body") == {"thinking": {"type": "enabled"}}


def test_get_optimal_settings_unknown_model():
    """未列入表的 model 应返回空 dict（不强加 thinking）。"""
    from src.services.model_manager import get_optimal_settings

    assert get_optimal_settings("deepseek-chat") == {}
    assert get_optimal_settings("gpt-4o") == {}


def test_create_model_unsupported_provider():
    """不支持的 provider 应抛出 ValueError。"""
    from src.services.model_manager import ModelManager, ModelConfig

    manager = ModelManager(config_path=Path("/dev/null"))
    config = ModelConfig(id="bad", provider="unsupported", model="test",
                         api_key="test", base_url=None)
    with pytest.raises(ValueError, match="Unsupported provider"):
        manager.create_model(config)


def test_get_model_by_id(tmp_path: Path):
    """get_model_by_id 应从已加载列表中按 id 查找。"""
    from src.services.model_manager import ModelManager, ModelConfig
    manager = ModelManager(config_path=tmp_path / "models.json")

    configs = [
        ModelConfig(id="claude-opus", provider="anthropic", model="claude-opus-4-6",
                    api_key="sk-ant-test", base_url=None),
        ModelConfig(id="deepseek", provider="openai", model="deepseek/deepseek-chat",
                    api_key="sk-or-test", base_url=None),
    ]
    manager.save_models(configs)
    loaded = manager.load_models()

    found = manager.get_model_by_id("deepseek", loaded)
    assert found is not None
    assert found.id == "deepseek"


def test_get_model_by_id_not_found(tmp_path: Path):
    """不存在的 id 应返回 None。"""
    from src.services.model_manager import ModelManager, ModelConfig
    manager = ModelManager(config_path=tmp_path / "models.json")
    found = manager.get_model_by_id("nonexistent", [])
    assert found is None


async def test_test_connectivity_mock():
    """test_connectivity 应调用 agent.run 并返回成功/失败。"""
    from src.services.model_manager import ModelManager
    from unittest.mock import patch, AsyncMock, MagicMock

    manager = ModelManager(config_path=Path("/dev/null"))

    mock_agent_cls = MagicMock()
    mock_agent_instance = MagicMock()
    mock_agent_instance.run = AsyncMock(return_value=MagicMock(output="hi"))
    mock_agent_cls.return_value = mock_agent_instance

    with patch("src.services.model_manager.Agent", mock_agent_cls):
        success, error = await manager.test_connectivity(MagicMock())
        assert success is True
        assert error is None


async def test_test_connectivity_failure():
    """test_connectivity 失败时应返回错误信息。"""
    from src.services.model_manager import ModelManager
    from unittest.mock import patch, AsyncMock, MagicMock

    manager = ModelManager(config_path=Path("/dev/null"))

    mock_agent_cls = MagicMock()
    mock_agent_instance = MagicMock()
    mock_agent_instance.run = AsyncMock(side_effect=Exception("auth failed"))
    mock_agent_cls.return_value = mock_agent_instance

    with patch("src.services.model_manager.Agent", mock_agent_cls):
        success, error = await manager.test_connectivity(MagicMock())
        assert success is False
        assert "auth failed" in error


def test_save_models_duplicate_id_raises(tmp_path: Path):
    """重复 ID 应抛出 ValueError。"""
    from src.services.model_manager import ModelManager, ModelConfig
    manager = ModelManager(config_path=tmp_path / "models.json")

    configs = [
        ModelConfig(id="claude", provider="anthropic", model="claude-opus-4-6",
                    api_key="sk-1", base_url=None),
        ModelConfig(id="claude", provider="anthropic", model="claude-sonnet-4-20250514",
                    api_key="sk-2", base_url=None),
    ]
    with pytest.raises(ValueError, match="Duplicate model IDs"):
        manager.save_models(configs)
