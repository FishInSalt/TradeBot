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
