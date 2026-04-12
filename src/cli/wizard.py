# src/cli/wizard.py
from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from rich.console import Console
from rich.prompt import Confirm, FloatPrompt, IntPrompt, Prompt
from rich.table import Table

from src.config import PersonaConfig, Settings, TraderConfig
from src.services.model_manager import ModelConfig, ModelManager

_CREDENTIALS_FILE = ".credentials"
_EXCHANGE_DISPLAY = {"simulated": "sim", "okx": "okx"}


@dataclass
class WizardResult:
    # Exchange
    exchange_type: str              # "simulated" / "okx"
    fee_rate: float | None          # simulated only
    initial_balance: float
    api_credentials: dict | None    # real: {api_key, secret, password}
    # Trading pair
    symbol: str
    timeframe: str
    # Model
    model_config: ModelConfig
    model: Any                      # pydantic-ai Model object
    # Risk & scheduling
    scheduler_interval_min: int
    approval_enabled: bool
    alert_enabled: bool
    alert_window_min: int | None
    alert_threshold_pct: float | None
    alert_cooldown_min: int | None
    token_budget: int
    # Persona
    persona: PersonaConfig
    # Session
    session_name: str


def _load_credentials(config_dir: Path) -> dict:
    path = config_dir / _CREDENTIALS_FILE
    if not path.exists():
        return {}
    with open(path) as f:
        return json.load(f)


def _save_credentials(config_dir: Path, exchange: str, creds: dict) -> None:
    path = config_dir / _CREDENTIALS_FILE
    data = _load_credentials(config_dir)
    data[exchange] = creds
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w") as f:
        json.dump(data, f, indent=2)
    os.chmod(path, 0o600)
