# tests/test_wizard.py
from __future__ import annotations

import json
import os
import stat
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from rich.console import Console

from src.config import PersonaConfig, Settings, TraderConfig
from src.services.model_manager import ModelConfig


# --- WizardResult ---

def test_wizard_result_construction():
    from src.cli.wizard import WizardResult
    from src.services.model_manager import ModelConfig

    config = ModelConfig(id="test", provider="openai", model="gpt-4o", api_key="k", base_url=None)
    result = WizardResult(
        exchange_type="simulated",
        fee_rate=0.0005,
        initial_balance=100.0,
        api_credentials=None,
        symbol="BTC/USDT:USDT",
        timeframe="15m",
        model_config=config,
        model=object(),
        scheduler_interval_min=15,
        approval_enabled=False,
        token_budget=500000,
        persona=PersonaConfig(),
        session_name="BTC sim",
    )
    assert result.exchange_type == "simulated"
    assert result.fee_rate == 0.0005
    assert result.session_name == "BTC sim"


# --- Credential helpers ---

def test_load_credentials_missing_file(tmp_path):
    from src.cli.wizard import _load_credentials
    assert _load_credentials(tmp_path) == {}


def test_load_credentials_corrupted_json(tmp_path):
    from src.cli.wizard import _load_credentials
    (tmp_path / ".credentials").write_text("{invalid json")
    assert _load_credentials(tmp_path) == {}


def test_save_and_load_credentials(tmp_path):
    from src.cli.wizard import _load_credentials, _save_credentials
    creds = {"api_key": "k1", "secret": "s1", "password": "p1"}
    _save_credentials(tmp_path, "okx", creds)
    loaded = _load_credentials(tmp_path)
    assert loaded["okx"] == creds


def test_save_credentials_file_permissions(tmp_path):
    from src.cli.wizard import _save_credentials
    _save_credentials(tmp_path, "okx", {"api_key": "k"})
    path = tmp_path / ".credentials"
    mode = stat.S_IMODE(os.stat(path).st_mode)
    assert mode == 0o600


def test_save_credentials_preserves_other_exchanges(tmp_path):
    from src.cli.wizard import _load_credentials, _save_credentials
    _save_credentials(tmp_path, "okx", {"api_key": "okx_key"})
    _save_credentials(tmp_path, "binance", {"api_key": "bin_key"})
    loaded = _load_credentials(tmp_path)
    assert loaded["okx"]["api_key"] == "okx_key"
    assert loaded["binance"]["api_key"] == "bin_key"


# --- Step 1: Exchange ---

@patch("src.cli.wizard.FloatPrompt.ask", side_effect=[0.05, 100.0])
@patch("src.cli.wizard.Prompt.ask", return_value="sim")
def test_step_exchange_sim(mock_prompt, mock_float):
    from src.cli.wizard import _step_exchange
    result = _step_exchange(Settings(), Path("/tmp"), Console())
    assert result["exchange_type"] == "simulated"
    assert result["fee_rate"] == 0.0005  # 0.05% → 0.0005
    assert result["initial_balance"] == 100.0
    assert result["api_credentials"] is None


@patch("src.cli.wizard.FloatPrompt.ask", return_value=200.0)
@patch("src.cli.wizard.Prompt.ask", side_effect=["real", "my_key", "my_secret", "my_pass"])
def test_step_exchange_real_new_creds(mock_prompt, mock_float, tmp_path):
    """Settings().exchange.sandbox defaults False → credentials saved under okx_live."""
    from src.cli.wizard import _step_exchange, _load_credentials
    result = _step_exchange(Settings(), tmp_path, Console())
    assert result["exchange_type"] == "okx"
    assert result["api_credentials"]["api_key"] == "my_key"
    assert result["initial_balance"] == 200.0
    saved = _load_credentials(tmp_path)
    assert saved["okx_live"]["api_key"] == "my_key"
    # Legacy "okx" key must not be used — namespace split is the fix point
    assert "okx" not in saved


@patch("src.cli.wizard.FloatPrompt.ask", return_value=100.0)
@patch("src.cli.wizard.Confirm.ask", return_value=True)
@patch("src.cli.wizard.Prompt.ask", return_value="real")
def test_step_exchange_real_reuse_saved(mock_prompt, mock_confirm, mock_float, tmp_path):
    from src.cli.wizard import _save_credentials, _step_exchange
    _save_credentials(tmp_path, "okx_live", {"api_key": "saved_k", "secret": "s", "password": "p"})
    result = _step_exchange(Settings(), tmp_path, Console())
    assert result["api_credentials"]["api_key"] == "saved_k"


@patch("src.cli.wizard.FloatPrompt.ask", return_value=100.0)
@patch("src.cli.wizard.Confirm.ask", return_value=True)
@patch("src.cli.wizard.Prompt.ask", return_value="real")
def test_step_exchange_real_env_fallback(mock_prompt, mock_confirm, mock_float, tmp_path):
    """Tier 2: no .credentials, Settings has env creds → confirm reuse, saved under okx_live."""
    from src.cli.wizard import _step_exchange, _load_credentials
    settings = Settings()
    settings.exchange.api_key = "env_key"
    settings.exchange.secret = "env_secret"
    settings.exchange.password = "env_pass"
    result = _step_exchange(settings, tmp_path, Console())
    assert result["api_credentials"]["api_key"] == "env_key"
    saved = _load_credentials(tmp_path)
    assert saved["okx_live"]["api_key"] == "env_key"


@patch("src.cli.wizard.FloatPrompt.ask", side_effect=["real", "demo_k", "demo_s", "demo_p"])
@patch("src.cli.wizard.Prompt.ask", side_effect=["real", "demo_k", "demo_s", "demo_p"])
@patch("src.cli.wizard.FloatPrompt")
def test_step_exchange_sandbox_saves_under_okx_demo(mock_fp, mock_prompt, mock_fp_ask, tmp_path):
    """sandbox=True → credentials saved under okx_demo namespace (I1 fix)."""
    from src.cli.wizard import _step_exchange, _load_credentials
    from unittest.mock import MagicMock
    mock_fp.ask = MagicMock(return_value=100.0)
    settings = Settings()
    settings.exchange.sandbox = True
    result = _step_exchange(settings, tmp_path, Console())
    assert result["api_credentials"]["api_key"] == "demo_k"
    saved = _load_credentials(tmp_path)
    assert saved["okx_demo"]["api_key"] == "demo_k"
    assert "okx_live" not in saved
    assert "okx" not in saved


@patch("src.cli.wizard.FloatPrompt.ask", return_value=100.0)
@patch("src.cli.wizard.Prompt.ask", side_effect=["real", "fresh_demo_k", "fresh_demo_s", "fresh_demo_p"])
def test_step_exchange_sandbox_ignores_live_saved_creds(mock_prompt, mock_float, tmp_path):
    """.credentials has okx_live but sandbox=True → must not reuse, prompts fresh (I1)."""
    from src.cli.wizard import _save_credentials, _step_exchange, _load_credentials
    _save_credentials(tmp_path, "okx_live", {"api_key": "live_k", "secret": "s", "password": "p"})
    settings = Settings()
    settings.exchange.sandbox = True
    result = _step_exchange(settings, tmp_path, Console())
    # Must NOT use the live credentials — fresh prompts
    assert result["api_credentials"]["api_key"] == "fresh_demo_k"
    saved = _load_credentials(tmp_path)
    assert saved["okx_live"]["api_key"] == "live_k"  # untouched
    assert saved["okx_demo"]["api_key"] == "fresh_demo_k"


# --- Step 2: Trading Pair ---

@patch("src.cli.wizard.Prompt.ask", side_effect=["ETH/USDT:USDT", "1H"])
def test_step_trading_pair_custom(mock_prompt):
    from src.cli.wizard import _step_trading_pair
    result = _step_trading_pair(Settings(), Console())
    assert result["symbol"] == "ETH/USDT:USDT"
    assert result["timeframe"] == "1H"


# --- Step 3: Model ---

def _make_model_manager(models=None, test_ok=True):
    """Create a mock ModelManager with configurable behavior."""
    mm = MagicMock()
    models = models or []
    mm.load_models.return_value = models
    mm.get_model_by_id.side_effect = lambda mid, ms: next((m for m in ms if m.id == mid), None)
    mm.create_model.return_value = MagicMock(name="mock_pydantic_model")
    mm.save_models.return_value = None

    async def _test(*a, **kw):
        return (True, None) if test_ok else (False, "Connection refused")
    mm.test_connectivity = _test
    return mm


_SAMPLE_MODEL = ModelConfig(id="claude", provider="anthropic", model="claude-opus-4-6", api_key="sk-x", base_url=None)


@pytest.mark.asyncio
async def test_step_model_with_flag():
    from src.cli.wizard import _step_model
    mm = _make_model_manager(models=[_SAMPLE_MODEL])
    result = await _step_model(mm, model_id="claude", console=Console())
    assert result["model_config"].id == "claude"


@pytest.mark.asyncio
@patch("src.cli.wizard.IntPrompt.ask", return_value=1)
async def test_step_model_select_existing(mock_int):
    from src.cli.wizard import _step_model
    mm = _make_model_manager(models=[_SAMPLE_MODEL])
    result = await _step_model(mm, model_id=None, console=Console())
    assert result["model_config"].id == "claude"


@pytest.mark.asyncio
@patch("src.cli.wizard.Confirm.ask", return_value=False)
async def test_step_model_connectivity_fail_abort(mock_confirm):
    from src.cli.wizard import _step_model
    mm = _make_model_manager(models=[_SAMPLE_MODEL], test_ok=False)
    result = await _step_model(mm, model_id="claude", console=Console())
    assert result is None


@pytest.mark.asyncio
@patch("src.cli.wizard.Prompt.ask", side_effect=["openai", "gpt-4o", "sk-new", "", "gpt4o"])
@patch("src.cli.wizard.IntPrompt.ask", return_value=2)
async def test_step_model_add_new(mock_int, mock_prompt):
    from src.cli.wizard import _step_model
    mm = _make_model_manager(models=[_SAMPLE_MODEL])
    result = await _step_model(mm, model_id=None, console=Console())
    assert result is not None
    assert result["model_config"].id == "gpt4o"
    mm.save_models.assert_called_once()


# --- Step 4: Risk & Scheduling ---

@patch("src.cli.wizard.IntPrompt.ask", side_effect=[15, 500000])
@patch("src.cli.wizard.Confirm.ask", side_effect=[False])  # approval OFF
def test_step_risk_sim_defaults(mock_confirm, mock_int):
    from src.cli.wizard import _step_risk_scheduling
    result = _step_risk_scheduling(Settings(), "simulated", Console())
    assert result["scheduler_interval_min"] == 15
    assert result["approval_enabled"] is False
    assert result["token_budget"] == 500000


# --- Step 5: Persona ---

@patch("src.cli.wizard.Prompt.ask", side_effect=["auto", "auto"])
def test_step_persona_defaults(mock_prompt):
    from src.cli.wizard import _step_persona
    result = _step_persona(TraderConfig(), Console())
    p = result["persona"]
    assert p.personality is None  # "auto" maps to None
    assert p.trading_style is None  # "auto" maps to None
    # Numerical params use code defaults (not asked in wizard)
    assert p.max_position_pct == 30.0
    assert p.position_sizing == "percentage"


@patch("src.cli.wizard.Prompt.ask", side_effect=["aggressive", "breakout"])
def test_step_persona_custom(mock_prompt):
    from src.cli.wizard import _step_persona
    result = _step_persona(TraderConfig(), Console())
    p = result["persona"]
    assert p.personality == "aggressive"
    assert p.trading_style == "breakout"


@patch("src.cli.wizard.Prompt.ask", side_effect=["conservative", "auto"])
def test_step_persona_personality_only(mock_prompt):
    """Personality set, strategy auto — trading_style should be None."""
    from src.cli.wizard import _step_persona
    result = _step_persona(TraderConfig(), Console())
    p = result["persona"]
    assert p.personality == "conservative"
    assert p.trading_style is None


@patch("src.cli.wizard.Prompt.ask", side_effect=["auto", "swing"])
def test_step_persona_strategy_only(mock_prompt):
    """Personality auto, strategy set — personality should be None."""
    from src.cli.wizard import _step_persona
    result = _step_persona(TraderConfig(), Console())
    p = result["persona"]
    assert p.personality is None
    assert p.trading_style == "swing"


# --- Session naming ---

def test_generate_session_name():
    from src.cli.wizard import _generate_session_name
    assert _generate_session_name("BTC/USDT:USDT", "simulated") == "BTC sim"
    assert _generate_session_name("ETH/USDT:USDT", "okx") == "ETH okx"
    assert _generate_session_name("1000PEPE/USDT:USDT", "simulated") == "1000PEPE sim"


# --- Summary ---

@patch("src.cli.wizard.Confirm.ask", return_value=True)
def test_show_summary_confirm(mock_confirm):
    from src.cli.wizard import _show_summary
    data = {
        "exchange_type": "simulated", "fee_rate": 0.0005, "initial_balance": 100.0,
        "symbol": "BTC/USDT:USDT", "timeframe": "15m",
        "model_config": ModelConfig(id="test", provider="openai", model="gpt-4o", api_key="k", base_url=None),
        "scheduler_interval_min": 15, "approval_enabled": False,
        "token_budget": 500000,
        "persona": PersonaConfig(),
    }
    assert _show_summary(data, Console()) is True


@patch("src.cli.wizard.Confirm.ask", return_value=False)
def test_show_summary_reject(mock_confirm):
    from src.cli.wizard import _show_summary
    data = {
        "exchange_type": "simulated", "fee_rate": 0.0005, "initial_balance": 100.0,
        "symbol": "BTC/USDT:USDT", "timeframe": "15m",
        "model_config": ModelConfig(id="test", provider="openai", model="gpt-4o", api_key="k", base_url=None),
        "scheduler_interval_min": 15, "approval_enabled": False,
        "token_budget": 500000,
        "persona": PersonaConfig(),
    }
    assert _show_summary(data, Console()) is False


# --- Full wizard flow ---

@pytest.mark.asyncio
async def test_run_wizard_full_flow(tmp_path):
    from src.cli.wizard import run_wizard

    mm = _make_model_manager(models=[_SAMPLE_MODEL])
    defaults = Settings()
    trader = TraderConfig()

    with patch("src.cli.wizard.Prompt.ask", side_effect=[
        "sim",               # Step 1: mode
        "BTC/USDT:USDT",    # Step 2: symbol
        "15m",               # Step 2: timeframe
        "moderate",          # Step 5: personality
        "auto",              # Step 5: strategy (auto = no constraint)
        "BTC sim",           # Session name
    ]), patch("src.cli.wizard.FloatPrompt.ask", side_effect=[
        0.05, 100.0,        # Step 1: fee_rate, balance
    ]), patch("src.cli.wizard.IntPrompt.ask", side_effect=[
        1,                   # Step 3: select model #1
        15,                  # Step 4: interval
        500000,              # Step 4: budget
    ]), patch("src.cli.wizard.Confirm.ask", side_effect=[
        False,               # Step 4: approval OFF (sim default)
        True,                # Summary: confirm
    ]):
        result = await run_wizard(
            model_manager=mm, defaults=defaults, trader_defaults=trader,
            config_dir=tmp_path, console=Console(),
        )

    assert result is not None
    assert result.exchange_type == "simulated"
    assert result.symbol == "BTC/USDT:USDT"
    assert result.session_name == "BTC sim"
    assert result.model_config.id == "claude"
    assert result.approval_enabled is False  # sim default
    assert result.persona.personality == "moderate"
    assert result.persona.trading_style is None  # auto = None


@pytest.mark.asyncio
async def test_run_wizard_reject_then_confirm(tmp_path):
    """Summary rejected -> re-run all steps -> confirm second time."""
    from src.cli.wizard import run_wizard

    mm = _make_model_manager(models=[_SAMPLE_MODEL])

    # Two full rounds of prompts: first rejected, second confirmed
    with patch("src.cli.wizard.Prompt.ask", side_effect=[
        # Round 1
        "sim", "BTC/USDT:USDT", "15m", "moderate", "auto",
        # Round 2
        "sim", "ETH/USDT:USDT", "15m", "moderate", "auto",
        "ETH sim",           # Session name (only reached on confirm)
    ]), patch("src.cli.wizard.FloatPrompt.ask", side_effect=[
        # Round 1
        0.05, 100.0,
        # Round 2
        0.05, 200.0,
    ]), patch("src.cli.wizard.IntPrompt.ask", side_effect=[
        # Round 1
        1, 15, 500000,
        # Round 2
        1, 15, 500000,
    ]), patch("src.cli.wizard.Confirm.ask", side_effect=[
        # Round 1
        False, False,        # approval OFF, summary REJECT
        # Round 2
        False, True,         # approval OFF, summary CONFIRM
    ]):
        result = await run_wizard(
            model_manager=mm, defaults=Settings(), trader_defaults=TraderConfig(),
            config_dir=tmp_path, console=Console(),
        )

    assert result is not None
    assert result.symbol == "ETH/USDT:USDT"  # second round values
    assert result.initial_balance == 200.0
    assert result.session_name == "ETH sim"


@pytest.mark.asyncio
async def test_run_wizard_ctrl_c(tmp_path):
    from src.cli.wizard import run_wizard

    mm = _make_model_manager()

    with patch("src.cli.wizard.Prompt.ask", side_effect=KeyboardInterrupt):
        result = await run_wizard(
            model_manager=mm, defaults=Settings(), trader_defaults=TraderConfig(),
            config_dir=tmp_path, console=Console(),
        )

    assert result is None


# --- build_services smoke test ---

def test_build_services_sim_path():
    """Verify sim path returns correct types and wires deps correctly."""
    from unittest.mock import MagicMock, patch
    from src.cli.wizard import WizardResult
    from src.cli.app import build_services

    result = WizardResult(
        exchange_type="simulated", fee_rate=0.0005, initial_balance=100.0,
        api_credentials=None, symbol="BTC/USDT:USDT", timeframe="15m",
        model_config=ModelConfig(id="t", provider="openai", model="gpt-4o", api_key="k", base_url=None),
        model=MagicMock(), scheduler_interval_min=15, approval_enabled=False,
        token_budget=500000, persona=PersonaConfig(),
        session_name="test",
    )
    mock_engine = MagicMock()
    mock_sc = MagicMock()
    mock_settings = MagicMock()
    mock_settings.approval.timeout_seconds = 300

    with patch("src.integrations.exchange.simulated.SimulatedExchange") as MockSim, \
         patch("src.cli.app.MarketDataService"), \
         patch("src.cli.app.MemoryService"), \
         patch("src.cli.app.create_trader_agent") as mock_agent:
        MockSim.return_value = MagicMock()
        mock_agent.return_value = MagicMock()
        exchange, deps, agent, budget, _stats = build_services(
            result, mock_engine, "sid", mock_sc, mock_settings,
        )

    assert deps.symbol == "BTC/USDT:USDT"
    assert deps.timeframe == "15m"
    assert deps.approval_enabled is False
    assert budget.remaining == 500000
    MockSim.assert_called_once()


def _build_news_wiring_result():
    from src.cli.wizard import WizardResult

    return WizardResult(
        exchange_type="simulated", fee_rate=0.0005, initial_balance=100.0,
        api_credentials=None, symbol="BTC/USDT:USDT", timeframe="15m",
        model_config=ModelConfig(id="t", provider="openai", model="gpt-4o", api_key="k", base_url=None),
        model=MagicMock(), scheduler_interval_min=15, approval_enabled=False,
        token_budget=500000, persona=PersonaConfig(),
        session_name="test",
    )


def test_build_services_wires_news_when_enabled():
    """settings.news.enabled=True → deps.news is a NewsService instance."""
    from src.cli.app import build_services

    result = _build_news_wiring_result()
    mock_engine = MagicMock()
    mock_sc = MagicMock()
    mock_settings = MagicMock()
    mock_settings.approval.timeout_seconds = 300
    mock_settings.news.enabled = True

    with patch("src.integrations.exchange.simulated.SimulatedExchange") as MockSim, \
         patch("src.cli.app.MarketDataService"), \
         patch("src.cli.app.MemoryService"), \
         patch("src.cli.app.create_trader_agent") as mock_agent, \
         patch("src.integrations.news.service.NewsService") as MockNewsService:
        MockSim.return_value = MagicMock()
        mock_agent.return_value = MagicMock()
        news_instance = MagicMock()
        MockNewsService.return_value = news_instance
        _, deps, _, _, _stats = build_services(
            result, mock_engine, "sid", mock_sc, mock_settings,
        )

    MockNewsService.assert_called_once_with()
    assert deps.news is news_instance


def test_build_services_omits_news_when_disabled():
    """settings.news.enabled=False → deps.news is None and NewsService not constructed."""
    from src.cli.app import build_services

    result = _build_news_wiring_result()
    mock_engine = MagicMock()
    mock_sc = MagicMock()
    mock_settings = MagicMock()
    mock_settings.approval.timeout_seconds = 300
    mock_settings.news.enabled = False

    with patch("src.integrations.exchange.simulated.SimulatedExchange") as MockSim, \
         patch("src.cli.app.MarketDataService"), \
         patch("src.cli.app.MemoryService"), \
         patch("src.cli.app.create_trader_agent") as mock_agent, \
         patch("src.integrations.news.service.NewsService") as MockNewsService:
        MockSim.return_value = MagicMock()
        mock_agent.return_value = MagicMock()
        _, deps, _, _, _stats = build_services(
            result, mock_engine, "sid", mock_sc, mock_settings,
        )

    MockNewsService.assert_not_called()
    assert deps.news is None


@pytest.mark.asyncio
async def test_run_wizard_uses_name_generator_callback():
    """When name_generator is provided, wizard uses it instead of internal _generate_session_name."""
    from src.cli.wizard import run_wizard, WizardResult

    async def mock_name_gen(symbol: str, exchange_type: str) -> str:
        return f"{symbol.split('/')[0]} sim #42"

    with patch("src.cli.wizard._step_exchange", return_value={
            "exchange_type": "simulated", "fee_rate": 0.0005,
            "initial_balance": 100.0, "api_credentials": None,
        }), \
         patch("src.cli.wizard._step_trading_pair", return_value={
            "symbol": "BTC/USDT:USDT", "timeframe": "15m",
        }), \
         patch("src.cli.wizard._step_model", new_callable=AsyncMock, return_value={
            "model_config": ModelConfig(id="m1", provider="openai", model="gpt-4o", api_key="k", base_url=None),
            "model": MagicMock(),
        }), \
         patch("src.cli.wizard._step_risk_scheduling", return_value={
            "scheduler_interval_min": 15, "approval_enabled": True,
            "token_budget": 500000,
        }), \
         patch("src.cli.wizard._step_persona", return_value={
            "persona": PersonaConfig(),
        }), \
         patch("src.cli.wizard._show_summary", return_value=True), \
         patch("src.cli.wizard.Prompt.ask", return_value="BTC sim #42"):
        result = await run_wizard(
            model_manager=MagicMock(),
            defaults=Settings(),
            trader_defaults=TraderConfig(),
            config_dir=Path("/tmp"),
            console=Console(),
            name_generator=mock_name_gen,
        )

    assert result is not None
    assert result.session_name == "BTC sim #42"


@pytest.mark.asyncio
async def test_run_wizard_without_name_generator_uses_internal():
    """Without name_generator, wizard uses its internal _generate_session_name."""
    from src.cli.wizard import run_wizard

    with patch("src.cli.wizard._step_exchange", return_value={
            "exchange_type": "simulated", "fee_rate": 0.0005,
            "initial_balance": 100.0, "api_credentials": None,
        }), \
         patch("src.cli.wizard._step_trading_pair", return_value={
            "symbol": "BTC/USDT:USDT", "timeframe": "15m",
        }), \
         patch("src.cli.wizard._step_model", new_callable=AsyncMock, return_value={
            "model_config": ModelConfig(id="m1", provider="openai", model="gpt-4o", api_key="k", base_url=None),
            "model": MagicMock(),
        }), \
         patch("src.cli.wizard._step_risk_scheduling", return_value={
            "scheduler_interval_min": 15, "approval_enabled": True,
            "token_budget": 500000,
        }), \
         patch("src.cli.wizard._step_persona", return_value={
            "persona": PersonaConfig(),
        }), \
         patch("src.cli.wizard._show_summary", return_value=True), \
         patch("src.cli.wizard.Prompt.ask", return_value="BTC sim"):
        result = await run_wizard(
            model_manager=MagicMock(),
            defaults=Settings(),
            trader_defaults=TraderConfig(),
            config_dir=Path("/tmp"),
            console=Console(),
        )

    assert result is not None
    assert result.session_name == "BTC sim"


@pytest.mark.asyncio
async def test_run_wizard_existing_names_conflict_reprompts():
    """When user enters a name that exists, wizard re-prompts until unique."""
    from src.cli.wizard import run_wizard

    prompt_returns = iter(["BTC sim #1", "BTC sim #2"])

    with patch("src.cli.wizard._step_exchange", return_value={
            "exchange_type": "simulated", "fee_rate": 0.0005,
            "initial_balance": 100.0, "api_credentials": None,
        }), \
         patch("src.cli.wizard._step_trading_pair", return_value={
            "symbol": "BTC/USDT:USDT", "timeframe": "15m",
        }), \
         patch("src.cli.wizard._step_model", new_callable=AsyncMock, return_value={
            "model_config": ModelConfig(id="m1", provider="openai", model="gpt-4o", api_key="k", base_url=None),
            "model": MagicMock(),
        }), \
         patch("src.cli.wizard._step_risk_scheduling", return_value={
            "scheduler_interval_min": 15, "approval_enabled": True,
            "token_budget": 500000,
        }), \
         patch("src.cli.wizard._step_persona", return_value={
            "persona": PersonaConfig(),
        }), \
         patch("src.cli.wizard._show_summary", return_value=True), \
         patch("src.cli.wizard.Prompt.ask", side_effect=lambda *a, **kw: next(prompt_returns)):
        result = await run_wizard(
            model_manager=MagicMock(),
            defaults=Settings(),
            trader_defaults=TraderConfig(),
            config_dir=Path("/tmp"),
            console=Console(),
            existing_names={"BTC sim #1"},
        )

    assert result is not None
    assert result.session_name == "BTC sim #2"


# --- Task 13: fee_rate full fill ---

def test_wizard_result_fee_rate_type_is_float_not_optional():
    """WizardResult.fee_rate annotation is `float`, not `float | None`."""
    from src.cli.wizard import WizardResult
    import typing
    hints = typing.get_type_hints(WizardResult)
    assert hints["fee_rate"] is float


@patch("src.cli.wizard.FloatPrompt.ask", side_effect=[0.05, 100.0])
@patch("src.cli.wizard.Prompt.ask", return_value="sim")
def test_wizard_simulated_branch_prompt_says_per_side(mock_prompt, mock_float):
    """Simulated fee_rate prompt text says 'Fee rate (% per side)' (was 'Fee rate (%)')."""
    from src.cli.wizard import _step_exchange
    _step_exchange(Settings(), Path("/tmp"), Console())
    # First FloatPrompt.ask call is for fee_rate; check its prompt keyword argument
    first_call = mock_float.call_args_list[0]
    prompt_text = first_call.args[0] if first_call.args else first_call.kwargs.get("prompt", "")
    assert "per side" in prompt_text


@patch("src.cli.wizard.FloatPrompt.ask", side_effect=[200.0, 0.05])
@patch("src.cli.wizard.Prompt.ask", side_effect=["real", "my_key", "my_secret", "my_pass"])
def test_wizard_okx_branch_prompts_for_fee_rate(mock_prompt, mock_float, tmp_path):
    """OKX path collects fee_rate (default 0.05% = OKX BTC perp regular tier taker).

    OKX flow order: balance first, then fee_rate — so side_effect=[balance, fee_pct].
    """
    from src.cli.wizard import _step_exchange
    result = _step_exchange(Settings(), tmp_path, Console())
    assert result["exchange_type"] == "okx"
    assert result["fee_rate"] is not None
    assert result["fee_rate"] == pytest.approx(0.0005)  # 0.05 / 100
    # Verify that FloatPrompt.ask was called with OKX VIP tier mention (second call)
    okx_fee_call = mock_float.call_args_list[1]
    prompt_text = okx_fee_call.args[0] if okx_fee_call.args else okx_fee_call.kwargs.get("prompt", "")
    assert "OKX live" in prompt_text


@patch("src.cli.wizard.Confirm.ask", return_value=True)
def test_wizard_summary_shows_fee_for_okx_path(mock_confirm):
    """_show_summary appends fee% for OKX path (not gated by exchange_type=='simulated')."""
    from src.cli.wizard import _show_summary
    from io import StringIO
    console = Console(file=StringIO(), highlight=False)
    data = {
        "exchange_type": "okx", "fee_rate": 0.0005, "initial_balance": 10000.0,
        "symbol": "BTC/USDT:USDT", "timeframe": "15m",
        "model_config": ModelConfig(id="test", provider="anthropic", model="claude-opus-4-6", api_key="k", base_url=None),
        "scheduler_interval_min": 15, "approval_enabled": True,
        "token_budget": 500000,
        "persona": PersonaConfig(),
    }
    _show_summary(data, console)
    output = console.file.getvalue()
    # .3f matches system prompt Layer 1 rendering (avoids 0.075% → 0.08% drift)
    assert "0.050%" in output
