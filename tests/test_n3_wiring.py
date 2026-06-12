"""Integration tests: build_services wires N3 services per settings (spec §8.3).

These tests call build_services with a REAL WizardResult (not MagicMock) so
they stay correct under a future refactor that adds isinstance checks or
dataclass-level validation. The `model` field is set to "test" and
ALLOW_MODEL_REQUESTS is disabled so pydantic-ai does not attempt to resolve
or construct an AnthropicModel for the agent constructed inside
build_services (see tests/test_trader_agent.py:4 for the same guard).
"""
from unittest.mock import MagicMock

import pytest
from pydantic_ai import models

from src.cli.wizard import WizardResult
from src.config import (
    ApprovalConfig, CryptoEtfConfig, DatabaseConfig,
    ExchangeConfig, LLMBudgetConfig, MacroConfig, ModelRouting, ModelsConfig,
    NewsConfig, OnchainConfig, PersonaConfig, SchedulerConfig, Settings,
    TradingConfig,
)
from src.services.model_manager import ModelConfig

models.ALLOW_MODEL_REQUESTS = False


def _make_settings(
    macro_enabled: bool = True,
    etf_enabled: bool = True,
    onchain_enabled: bool = True,
    news_enabled: bool = False,
    coindesk_key: str = "",
) -> Settings:
    return Settings(
        exchange=ExchangeConfig(),
        trading=TradingConfig(),
        models=ModelsConfig(routing=ModelRouting()),
        scheduler=SchedulerConfig(),
        llm_budget=LLMBudgetConfig(),
        database=DatabaseConfig(),
        approval=ApprovalConfig(),
        news=NewsConfig(enabled=news_enabled, coindesk_api_key=coindesk_key),
        macro=MacroConfig(
            enabled=macro_enabled, fred_api_key="k",
            alpha_vantage_api_key="k", coingecko_demo_api_key="k",
        ),
        crypto_etf=CryptoEtfConfig(enabled=etf_enabled, sosovalue_api_key="k"),
        onchain=OnchainConfig(enabled=onchain_enabled),
    )


def _make_result() -> WizardResult:
    """Minimal real WizardResult — all fields explicit so future dataclass
    validation (e.g., __post_init__ checks) surfaces here rather than
    silently passing via MagicMock attribute access."""
    return WizardResult(
        exchange_type="simulated",
        fee_rate=0.001,
        initial_balance=10_000.0,
        api_credentials=None,
        symbol="BTC/USDT:USDT",
        timeframe="15m",
        # ModelConfig is a dataclass with 5 str fields; values don't matter
        # here because build_services only reads result.model (the sentinel
        # below) when constructing the agent.
        model_config=ModelConfig(
            id="test", provider="test", model="test", api_key="", base_url=None,
        ),
        # "test" sentinel lets pydantic-ai build a harness agent without
        # touching Anthropic SDK construction (see ALLOW_MODEL_REQUESTS=False
        # above and tests/test_trader_agent.py:11).
        model="test",
        scheduler_interval_min=15,
        approval_enabled=False,
        token_budget=1_000_000,
        persona=PersonaConfig(),
        session_name="test-session",
    )


async def test_build_services_all_n3_enabled(stub_market_meta):
    from src.cli.app import build_services
    from src.integrations.crypto_etf.service import CryptoEtfService
    from src.integrations.macro.service import MacroService
    from src.integrations.onchain.service import OnchainService

    settings = _make_settings(True, True, True)
    result = _make_result()
    exchange, deps, agent, budget, _stats = await build_services(
        result, MagicMock(), "sid", MagicMock(), settings,
    )
    try:
        assert isinstance(deps.macro, MacroService)
        assert isinstance(deps.crypto_etf, CryptoEtfService)
        assert isinstance(deps.onchain, OnchainService)
    finally:
        # build_services constructs owned httpx clients. Close them so the
        # test does not leak file descriptors between runs.
        await deps.macro.close()
        await deps.crypto_etf.close()
        await deps.onchain.close()


async def test_build_services_wires_coindesk_api_key(stub_market_meta):
    """build_services forwards news.coindesk_api_key into the NewsService's
    CoinDeskNewsClient, so live runs authenticate the (now key-gated) feed."""
    from src.cli.app import build_services

    settings = _make_settings(news_enabled=True, coindesk_key="ck-test")
    exchange, deps, agent, budget, _stats = await build_services(
        _make_result(), MagicMock(), "sid", MagicMock(), settings,
    )
    try:
        assert deps.news is not None
        assert deps.news._news._api_key == "ck-test"
    finally:
        await deps.news.close()
        await deps.macro.close()
        await deps.crypto_etf.close()
        await deps.onchain.close()


async def test_build_services_macro_disabled(stub_market_meta):
    from src.cli.app import build_services

    settings = _make_settings(macro_enabled=False)
    exchange, deps, agent, budget, _stats = await build_services(
        _make_result(), MagicMock(), "sid", MagicMock(), settings,
    )
    try:
        assert deps.macro is None
        # Siblings still on — independent toggles
        assert deps.crypto_etf is not None
        assert deps.onchain is not None
    finally:
        await deps.crypto_etf.close()
        await deps.onchain.close()


async def test_build_services_all_n3_disabled(stub_market_meta):
    from src.cli.app import build_services

    settings = _make_settings(False, False, False)
    exchange, deps, agent, budget, _stats = await build_services(
        _make_result(), MagicMock(), "sid", MagicMock(), settings,
    )
    assert deps.macro is None
    assert deps.crypto_etf is None
    assert deps.onchain is None


async def test_build_services_crypto_etf_disabled_leaves_others_on(stub_market_meta):
    from src.cli.app import build_services

    settings = _make_settings(macro_enabled=True, etf_enabled=False,
                              onchain_enabled=True)
    exchange, deps, agent, budget, _stats = await build_services(
        _make_result(), MagicMock(), "sid", MagicMock(), settings,
    )
    try:
        assert deps.crypto_etf is None
        assert deps.macro is not None
        assert deps.onchain is not None
    finally:
        await deps.macro.close()
        await deps.onchain.close()


async def test_build_services_onchain_disabled_leaves_others_on(stub_market_meta):
    from src.cli.app import build_services

    settings = _make_settings(macro_enabled=True, etf_enabled=True,
                              onchain_enabled=False)
    exchange, deps, agent, budget, _stats = await build_services(
        _make_result(), MagicMock(), "sid", MagicMock(), settings,
    )
    try:
        assert deps.onchain is None
        assert deps.macro is not None
        assert deps.crypto_etf is not None
    finally:
        await deps.macro.close()
        await deps.crypto_etf.close()
