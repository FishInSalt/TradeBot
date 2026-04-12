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
