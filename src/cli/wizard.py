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


def _step_exchange(defaults: Settings, config_dir: Path, console: Console) -> dict:
    """Step 1: Exchange mode. Returns exchange_type, fee_rate, initial_balance, api_credentials."""
    console.print("\n[bold]Step 1: Exchange[/]")
    mode = Prompt.ask("  Mode", choices=["sim", "real"], default="sim", console=console)
    exchange_type = "simulated" if mode == "sim" else "okx"

    if exchange_type == "simulated":
        fee = defaults.exchange.fee_rate if defaults.exchange.fee_rate is not None else 0.0005
        default_fee_pct = fee * 100
        fee_pct = FloatPrompt.ask("  Fee rate (%)", default=default_fee_pct, console=console)
        balance = FloatPrompt.ask(
            "  Initial balance (USDT)",
            default=defaults.trading.initial_balance_usdt,
            console=console,
        )
        return {
            "exchange_type": "simulated",
            "fee_rate": fee_pct / 100,
            "initial_balance": balance,
            "api_credentials": None,
        }

    # Real exchange — try saved credentials first
    api_credentials = None
    saved = _load_credentials(config_dir)
    if "okx" in saved:
        console.print("  [dim]Saved OKX credentials found[/]")
        if Confirm.ask("  Use saved credentials?", default=True, console=console):
            api_credentials = saved["okx"]

    # Spec defines 3-tier priority: .credentials > .env > manual input.
    # For .env tier, spec says "pre-fill as defaults". We intentionally simplify
    # to confirm-or-reject (not per-field pre-fill) because Rich's Prompt displays
    # default values in plain text even with password=True, which would leak secrets.
    if api_credentials is None:
        env_key = defaults.exchange.api_key
        env_secret = defaults.exchange.secret
        env_pass = defaults.exchange.password
        if env_key and env_secret and env_pass:
            console.print("  [dim]Credentials found in environment[/]")
            if Confirm.ask("  Use environment credentials?", default=True, console=console):
                api_credentials = {"api_key": env_key, "secret": env_secret, "password": env_pass}
                _save_credentials(config_dir, "okx", api_credentials)

    if api_credentials is None:
        api_key = Prompt.ask("  API Key", console=console)
        secret = Prompt.ask("  Secret", password=True, console=console)
        password = Prompt.ask("  Password", password=True, console=console)
        api_credentials = {"api_key": api_key, "secret": secret, "password": password}
        _save_credentials(config_dir, "okx", api_credentials)

    balance = FloatPrompt.ask(
        "  Initial balance (USDT)",
        default=defaults.trading.initial_balance_usdt,
        console=console,
    )
    return {
        "exchange_type": "okx",
        "fee_rate": None,
        "initial_balance": balance,
        "api_credentials": api_credentials,
    }


def _step_trading_pair(defaults: Settings, console: Console) -> dict:
    """Step 2: Trading pair. Returns symbol, timeframe."""
    console.print("\n[bold]Step 2: Trading Pair[/]")
    symbol = Prompt.ask("  Symbol", default=defaults.trading.symbol, console=console)
    timeframe = Prompt.ask(
        "  Timeframe",
        choices=["1m", "5m", "15m", "1H", "4H"],
        default=defaults.trading.timeframe,
        console=console,
    )
    return {"symbol": symbol, "timeframe": timeframe}
