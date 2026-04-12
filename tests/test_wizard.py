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
        alert_enabled=True,
        alert_window_min=5,
        alert_threshold_pct=3.0,
        alert_cooldown_min=15,
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
    from src.cli.wizard import _step_exchange, _load_credentials
    result = _step_exchange(Settings(), tmp_path, Console())
    assert result["exchange_type"] == "okx"
    assert result["api_credentials"]["api_key"] == "my_key"
    assert result["initial_balance"] == 200.0
    # Verify credentials were saved
    saved = _load_credentials(tmp_path)
    assert saved["okx"]["api_key"] == "my_key"


@patch("src.cli.wizard.FloatPrompt.ask", return_value=100.0)
@patch("src.cli.wizard.Confirm.ask", return_value=True)
@patch("src.cli.wizard.Prompt.ask", return_value="real")
def test_step_exchange_real_reuse_saved(mock_prompt, mock_confirm, mock_float, tmp_path):
    from src.cli.wizard import _save_credentials, _step_exchange
    _save_credentials(tmp_path, "okx", {"api_key": "saved_k", "secret": "s", "password": "p"})
    result = _step_exchange(Settings(), tmp_path, Console())
    assert result["api_credentials"]["api_key"] == "saved_k"


@patch("src.cli.wizard.FloatPrompt.ask", return_value=100.0)
@patch("src.cli.wizard.Confirm.ask", return_value=True)
@patch("src.cli.wizard.Prompt.ask", return_value="real")
def test_step_exchange_real_env_fallback(mock_prompt, mock_confirm, mock_float, tmp_path):
    """Tier 2: no .credentials, Settings has env creds → confirm reuse."""
    from src.cli.wizard import _step_exchange, _load_credentials
    settings = Settings()
    settings.exchange.api_key = "env_key"
    settings.exchange.secret = "env_secret"
    settings.exchange.password = "env_pass"
    result = _step_exchange(settings, tmp_path, Console())
    assert result["api_credentials"]["api_key"] == "env_key"
    # Verify env creds were saved to .credentials for next time
    saved = _load_credentials(tmp_path)
    assert saved["okx"]["api_key"] == "env_key"


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

@patch("src.cli.wizard.IntPrompt.ask", side_effect=[15, 5, 15, 500000])
@patch("src.cli.wizard.FloatPrompt.ask", return_value=3.0)
@patch("src.cli.wizard.Confirm.ask", side_effect=[False, True])  # approval OFF, alerts ON
def test_step_risk_sim_defaults(mock_confirm, mock_float, mock_int):
    from src.cli.wizard import _step_risk_scheduling
    result = _step_risk_scheduling(Settings(), "simulated", Console())
    assert result["scheduler_interval_min"] == 15
    assert result["approval_enabled"] is False
    assert result["alert_enabled"] is True
    assert result["alert_window_min"] == 5
    assert result["alert_threshold_pct"] == 3.0
    assert result["token_budget"] == 500000


@patch("src.cli.wizard.IntPrompt.ask", side_effect=[30, 500000])
@patch("src.cli.wizard.Confirm.ask", side_effect=[True, False])  # approval ON, alerts OFF
def test_step_risk_alerts_off(mock_confirm, mock_int):
    from src.cli.wizard import _step_risk_scheduling
    result = _step_risk_scheduling(Settings(), "okx", Console())
    assert result["approval_enabled"] is True
    assert result["alert_enabled"] is False
    assert result["alert_window_min"] is None


# --- Step 5: Persona ---

@patch("src.cli.wizard.FloatPrompt.ask", side_effect=[30.0, 3.0, 6.0])
@patch("src.cli.wizard.IntPrompt.ask", return_value=3)
@patch("src.cli.wizard.Prompt.ask", side_effect=["moderate", "trend_following"])
def test_step_persona_defaults(mock_prompt, mock_int, mock_float):
    from src.cli.wizard import _step_persona
    result = _step_persona(TraderConfig(), Console())
    p = result["persona"]
    assert p.risk_tolerance == "moderate"
    assert p.trading_style == "trend_following"
    assert p.max_position_pct == 30.0
    assert p.preferred_leverage == 3
    assert p.stop_loss_pct == 3.0
    assert p.take_profit_pct == 6.0
    # position_sizing uses default, not exposed in wizard
    assert p.position_sizing == "percentage"


@patch("src.cli.wizard.FloatPrompt.ask", side_effect=[50.0, 5.0, 10.0])
@patch("src.cli.wizard.IntPrompt.ask", return_value=10)
@patch("src.cli.wizard.Prompt.ask", side_effect=["aggressive", "breakout"])
def test_step_persona_custom(mock_prompt, mock_int, mock_float):
    from src.cli.wizard import _step_persona
    result = _step_persona(TraderConfig(), Console())
    p = result["persona"]
    assert p.risk_tolerance == "aggressive"
    assert p.trading_style == "breakout"
    assert p.preferred_leverage == 10


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
        "alert_enabled": True, "alert_window_min": 5, "alert_threshold_pct": 3.0, "alert_cooldown_min": 15,
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
        "alert_enabled": False, "alert_window_min": None, "alert_threshold_pct": None, "alert_cooldown_min": None,
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
        "moderate",          # Step 5: risk tolerance
        "trend_following",   # Step 5: trading style
        "BTC sim",           # Session name
    ]), patch("src.cli.wizard.FloatPrompt.ask", side_effect=[
        0.05, 100.0,        # Step 1: fee_rate, balance
        3.0,                 # Step 4: threshold
        30.0, 3.0, 6.0,     # Step 5: max_pos, stop_loss, take_profit
    ]), patch("src.cli.wizard.IntPrompt.ask", side_effect=[
        1,                   # Step 3: select model #1
        15,                  # Step 4: interval
        5, 15,               # Step 4: alert window, cooldown
        500000,              # Step 4: budget
        3,                   # Step 5: leverage
    ]), patch("src.cli.wizard.Confirm.ask", side_effect=[
        False,               # Step 4: approval OFF (sim default)
        True,                # Step 4: alerts ON
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
    assert result.persona.risk_tolerance == "moderate"


@pytest.mark.asyncio
async def test_run_wizard_reject_then_confirm(tmp_path):
    """Summary rejected -> re-run all steps -> confirm second time."""
    from src.cli.wizard import run_wizard

    mm = _make_model_manager(models=[_SAMPLE_MODEL])

    # Two full rounds of prompts: first rejected, second confirmed
    with patch("src.cli.wizard.Prompt.ask", side_effect=[
        # Round 1
        "sim", "BTC/USDT:USDT", "15m", "moderate", "trend_following",
        # Round 2
        "sim", "ETH/USDT:USDT", "15m", "moderate", "trend_following",
        "ETH sim",           # Session name (only reached on confirm)
    ]), patch("src.cli.wizard.FloatPrompt.ask", side_effect=[
        # Round 1
        0.05, 100.0, 3.0, 30.0, 3.0, 6.0,
        # Round 2
        0.05, 200.0, 3.0, 30.0, 3.0, 6.0,
    ]), patch("src.cli.wizard.IntPrompt.ask", side_effect=[
        # Round 1
        1, 15, 5, 15, 500000, 3,
        # Round 2
        1, 15, 5, 15, 500000, 3,
    ]), patch("src.cli.wizard.Confirm.ask", side_effect=[
        # Round 1
        False, True, False,  # approval OFF, alerts ON, summary REJECT
        # Round 2
        False, True, True,   # approval OFF, alerts ON, summary CONFIRM
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
