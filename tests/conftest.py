import pytest
from src.config import Settings, ExchangeConfig, TradingConfig, TraderConfig, PersonaConfig


@pytest.fixture
def settings() -> Settings:
    return Settings(
        exchange=ExchangeConfig(name="okx", api_key="test", secret="test", password="test"),
        trading=TradingConfig(initial_balance_usdt=10000.0),
    )


@pytest.fixture
def trader_config() -> TraderConfig:
    return TraderConfig(persona=PersonaConfig())
