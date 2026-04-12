# R1: Interactive CLI Configuration Wizard — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace scattered `input()` calls in `app.py` with a unified 5-step interactive wizard that produces a `WizardResult` dataclass, consolidating all configuration into one flow.

**Architecture:** New `src/cli/wizard.py` module with a `WizardResult` dataclass (wizard's sole output contract), 5 step functions (exchange, trading pair, model, risk/scheduling, persona), a summary confirmation, and a `run_wizard()` orchestrator. `app.py::run()` is refactored to call the wizard in Phase 3 and use `WizardResult` fields throughout. A new `build_services()` function is extracted from `run()` to construct exchange/deps/agent/budget from `WizardResult`.

**Tech Stack:** Python 3.12+, Rich (Prompt/Confirm/IntPrompt/FloatPrompt/Table), pydantic-ai, pytest + pytest-asyncio

**Spec:** `docs/superpowers/specs/2026-04-12-batch1-r5-r1-r2-design.md` (R1 section, lines 257-448)

---

## File Structure

| File | Action | Responsibility |
|------|--------|---------------|
| `src/cli/wizard.py` | Create | WizardResult dataclass, 5 wizard steps, credential I/O, summary, `run_wizard()` orchestrator |
| `tests/test_wizard.py` | Create | Tests for credential helpers, each wizard step, full wizard flow |
| `src/cli/app.py` | Modify | Delete scattered interaction (model selection, alert config, `_interactive_add_model`), call wizard, extract `build_services()` |
| `.gitignore` | Modify | Add `config/.credentials` |
| `config/settings_sim.yaml` | Modify | Add DEPRECATED header comment |

---

### Task 1: WizardResult dataclass + credential helpers

**Files:**
- Create: `src/cli/wizard.py`
- Create: `tests/test_wizard.py`

- [ ] **Step 1: Write failing tests for WizardResult and credential helpers**

```python
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
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_wizard.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'src.cli.wizard'`

- [ ] **Step 3: Implement WizardResult and credential helpers**

```python
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
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_wizard.py -v`
Expected: 5 passed

- [ ] **Step 5: Commit**

```bash
git add src/cli/wizard.py tests/test_wizard.py
git commit -m "feat(r1): add WizardResult dataclass and credential helpers"
```

---

### Task 2: Wizard Steps 1-2 — Exchange + Trading Pair

**Files:**
- Modify: `src/cli/wizard.py`
- Modify: `tests/test_wizard.py`

- [ ] **Step 1: Write failing tests for exchange and trading pair steps**

Append to `tests/test_wizard.py`:

```python
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


# --- Step 2: Trading Pair ---

@patch("src.cli.wizard.Prompt.ask", side_effect=["ETH/USDT:USDT", "1H"])
def test_step_trading_pair_custom(mock_prompt):
    from src.cli.wizard import _step_trading_pair
    result = _step_trading_pair(Settings(), Console())
    assert result["symbol"] == "ETH/USDT:USDT"
    assert result["timeframe"] == "1H"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_wizard.py::test_step_exchange_sim -v`
Expected: FAIL — `cannot import name '_step_exchange'`

- [ ] **Step 3: Implement _step_exchange and _step_trading_pair**

Add to `src/cli/wizard.py` (after the credential helpers):

```python
def _step_exchange(defaults: Settings, config_dir: Path, console: Console) -> dict:
    """Step 1: Exchange mode. Returns exchange_type, fee_rate, initial_balance, api_credentials."""
    console.print("\n[bold]Step 1: Exchange[/]")
    mode = Prompt.ask("  Mode", choices=["sim", "real"], default="sim", console=console)
    exchange_type = "simulated" if mode == "sim" else "okx"

    if exchange_type == "simulated":
        default_fee_pct = (defaults.exchange.fee_rate or 0.0005) * 100
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
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_wizard.py -v`
Expected: 9 passed

- [ ] **Step 5: Commit**

```bash
git add src/cli/wizard.py tests/test_wizard.py
git commit -m "feat(r1): wizard steps 1-2 (exchange selection + trading pair)"
```

---

### Task 3: Wizard Step 3 — Model Selection

**Files:**
- Modify: `src/cli/wizard.py`
- Modify: `tests/test_wizard.py`

- [ ] **Step 1: Write failing tests for model step**

Append to `tests/test_wizard.py`:

```python
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
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_wizard.py::test_step_model_with_flag -v`
Expected: FAIL — `cannot import name '_step_model'`

- [ ] **Step 3: Implement _step_model and _add_new_model**

Add to `src/cli/wizard.py`:

```python
def _add_new_model(model_manager: ModelManager, console: Console) -> tuple[ModelConfig, Any] | None:
    """Interactive add-new-model sub-flow."""
    console.print("  Supported providers: anthropic, openai, google-gla, groq")
    provider = Prompt.ask("  Provider", console=console)
    model_name = Prompt.ask("  Model name (e.g. claude-opus-4-6, gpt-4o)", console=console)
    api_key = Prompt.ask("  API key", password=True, console=console)
    base_url = Prompt.ask("  Base URL (Enter for default)", default="", console=console) or None
    model_id = Prompt.ask("  Friendly ID (e.g. claude-opus, gpt4o)", console=console)

    if not all([provider, model_name, api_key, model_id]):
        console.print("  [red]All fields except Base URL are required.[/]")
        return None

    config = ModelConfig(
        id=model_id, provider=provider, model=model_name,
        api_key=api_key, base_url=base_url,
    )
    try:
        model = model_manager.create_model(config)
    except ValueError as e:
        console.print(f"  [red]{e}[/]")
        return None
    return config, model


async def _step_model(
    model_manager: ModelManager,
    model_id: str | None,
    console: Console,
) -> dict | None:
    """Step 3: Model selection. Returns dict with model_config + model, or None on cancel."""
    console.print("\n[bold]Step 3: Model[/]")
    existing = model_manager.load_models()
    selected_config = None
    selected_model = None

    # --model flag shortcut
    if model_id:
        selected_config = model_manager.get_model_by_id(model_id, existing)
        if selected_config is None:
            console.print(f"  [yellow]Model '{model_id}' not found, entering selection...[/]")
        else:
            selected_model = model_manager.create_model(selected_config)

    # Interactive selection if needed
    if selected_model is None:
        if existing:
            console.print("  Available models:")
            for i, m in enumerate(existing):
                console.print(f"    {i + 1}. {m.id} ({m.provider}:{m.model})")
            console.print(f"    {len(existing) + 1}. + Add new model")

            choice = IntPrompt.ask("  Select", default=1, console=console)
            idx = choice - 1
            if 0 <= idx < len(existing):
                selected_config = existing[idx]
                selected_model = model_manager.create_model(selected_config)
            else:
                pair = _add_new_model(model_manager, console)
                if pair is None:
                    return None
                selected_config, selected_model = pair
        else:
            console.print("  [yellow]No models configured. Let's add one.[/]")
            pair = _add_new_model(model_manager, console)
            if pair is None:
                return None
            selected_config, selected_model = pair

    # Connectivity test
    # TODO: on failure + user decline, loop back to selection instead of exiting wizard
    console.print(f"  Testing API for {selected_config.id}...")
    success, error = await model_manager.test_connectivity(selected_model)
    if success:
        console.print("  [green]OK[/]")
    else:
        console.print(f"  [red]Failed: {error}[/]")
        if not Confirm.ask("  Continue anyway?", default=False, console=console):
            return None

    # Save new model if not already in list
    if selected_config not in existing:
        existing.append(selected_config)
        model_manager.save_models(existing)
        console.print(f"  [green]Saved '{selected_config.id}' to models.json[/]")

    return {"model_config": selected_config, "model": selected_model}
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_wizard.py -v`
Expected: 13 passed

- [ ] **Step 5: Commit**

```bash
git add src/cli/wizard.py tests/test_wizard.py
git commit -m "feat(r1): wizard step 3 (model selection + connectivity test)"
```

---

### Task 4: Wizard Steps 4-5 — Risk/Scheduling + Persona

**Files:**
- Modify: `src/cli/wizard.py`
- Modify: `tests/test_wizard.py`

- [ ] **Step 1: Write failing tests**

Append to `tests/test_wizard.py`:

```python
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
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_wizard.py::test_step_risk_sim_defaults -v`
Expected: FAIL — `cannot import name '_step_risk_scheduling'`

- [ ] **Step 3: Implement _step_risk_scheduling and _step_persona**

Add to `src/cli/wizard.py`:

```python
def _step_risk_scheduling(defaults: Settings, exchange_type: str, console: Console) -> dict:
    """Step 4: Risk & scheduling config."""
    console.print("\n[bold]Step 4: Risk & Scheduling[/]")
    interval = IntPrompt.ask(
        "  Wake interval (min)", default=defaults.scheduler.interval_minutes, console=console,
    )
    # Approval: sim defaults OFF, real defaults ON
    approval_default = exchange_type != "simulated"
    approval = Confirm.ask("  Approval gate", default=approval_default, console=console)
    alert_enabled = Confirm.ask("  Price alerts", default=defaults.alerts.enabled, console=console)

    alert_window = None
    alert_threshold = None
    alert_cooldown = None
    if alert_enabled:
        alert_window = IntPrompt.ask(
            "    Window (min)", default=defaults.alerts.window_minutes, console=console,
        )
        alert_threshold = FloatPrompt.ask(
            "    Threshold (%)", default=defaults.alerts.threshold_pct, console=console,
        )
        alert_cooldown = IntPrompt.ask(
            "    Cooldown (min)", default=defaults.alerts.cooldown_minutes, console=console,
        )
    budget = IntPrompt.ask(
        "  Token budget (daily)", default=defaults.llm_budget.daily_max_tokens, console=console,
    )
    return {
        "scheduler_interval_min": interval,
        "approval_enabled": approval,
        "alert_enabled": alert_enabled,
        "alert_window_min": alert_window,
        "alert_threshold_pct": alert_threshold,
        "alert_cooldown_min": alert_cooldown,
        "token_budget": budget,
    }


def _step_persona(trader_defaults: TraderConfig, console: Console) -> dict:
    """Step 5: Persona configuration."""
    console.print("\n[bold]Step 5: Persona[/]")
    p = trader_defaults.persona
    risk = Prompt.ask(
        "  Risk tolerance", choices=["conservative", "moderate", "aggressive"],
        default=p.risk_tolerance, console=console,
    )
    style = Prompt.ask(
        "  Trading style", choices=["trend_following", "swing", "breakout"],
        default=p.trading_style, console=console,
    )
    max_pos = FloatPrompt.ask("  Max position (%)", default=p.max_position_pct, console=console)
    leverage = IntPrompt.ask("  Leverage", default=p.preferred_leverage, console=console)
    stop_loss = FloatPrompt.ask("  Stop loss (%)", default=p.stop_loss_pct, console=console)
    take_profit = FloatPrompt.ask("  Take profit (%)", default=p.take_profit_pct, console=console)
    persona = PersonaConfig(
        risk_tolerance=risk,
        trading_style=style,
        max_position_pct=max_pos,
        preferred_leverage=leverage,
        stop_loss_pct=stop_loss,
        take_profit_pct=take_profit,
    )
    return {"persona": persona}
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_wizard.py -v`
Expected: 17 passed

- [ ] **Step 5: Commit**

```bash
git add src/cli/wizard.py tests/test_wizard.py
git commit -m "feat(r1): wizard steps 4-5 (risk/scheduling + persona)"
```

---

### Task 5: Summary, session naming + run_wizard orchestrator

**Files:**
- Modify: `src/cli/wizard.py`
- Modify: `tests/test_wizard.py`

- [ ] **Step 1: Write failing tests**

Append to `tests/test_wizard.py`:

```python
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
    """Summary rejected → re-run all steps → confirm second time."""
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
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_wizard.py::test_generate_session_name -v`
Expected: FAIL — `cannot import name '_generate_session_name'`

- [ ] **Step 3: Implement _generate_session_name, _show_summary, run_wizard**

Add to `src/cli/wizard.py`:

```python
def _generate_session_name(symbol: str, exchange_type: str) -> str:
    """Generate default session name: '{symbol_short} {exchange_display}'.
    R2 will extend with #{N} counter from DB query."""
    symbol_short = symbol.split("/")[0]
    exchange_display = _EXCHANGE_DISPLAY.get(exchange_type, exchange_type)
    return f"{symbol_short} {exchange_display}"


def _show_summary(data: dict, console: Console) -> bool:
    """Show configuration summary. Returns True if user confirms."""
    table = Table(show_header=False, border_style="blue", pad_edge=False)
    table.add_column("Key", style="bold")
    table.add_column("Value")

    ex = data["exchange_type"]
    if ex == "simulated":
        ex += f" (fee: {data['fee_rate'] * 100:.2f}%)"
    table.add_row("Exchange", ex)
    table.add_row("Balance", f"{data['initial_balance']:.0f} USDT")
    table.add_row("Trading", f"{data['symbol']} / {data['timeframe']}")
    table.add_row("Model", f"{data['model_config'].id} ({data['model_config'].provider})")
    table.add_row("Scheduler", f"{data['scheduler_interval_min']} min")
    table.add_row("Approval", "ON" if data["approval_enabled"] else "OFF")

    if data["alert_enabled"]:
        alert_str = (
            f"ON ({data['alert_window_min']}min / {data['alert_threshold_pct']}% "
            f"/ cd {data['alert_cooldown_min']}min)"
        )
    else:
        alert_str = "OFF"
    table.add_row("Alerts", alert_str)

    table.add_row("Budget", f"{data['token_budget']:,} tokens/day")

    p = data["persona"]
    table.add_row("Persona", f"{p.risk_tolerance} / {p.trading_style}")
    table.add_row(
        "Risk Params",
        f"pos {p.max_position_pct:.0f}% / {p.preferred_leverage}x / "
        f"SL {p.stop_loss_pct:.0f}% / TP {p.take_profit_pct:.0f}%",
    )

    console.print()
    console.print(table)
    console.print()
    return Confirm.ask("Confirm?", default=True, console=console)


async def run_wizard(
    model_manager: ModelManager,
    defaults: Settings,
    trader_defaults: TraderConfig,
    config_dir: Path,
    console: Console,
    model_id: str | None = None,
) -> WizardResult | None:
    """Run the interactive configuration wizard. Returns None on Ctrl+C or cancel."""
    try:
        console.print("[bold]Configuration Wizard[/]")

        while True:
            exchange_data = _step_exchange(defaults, config_dir, console)
            trading_data = _step_trading_pair(defaults, console)
            model_data = await _step_model(model_manager, model_id, console)
            if model_data is None:
                console.print("[yellow]Model selection cancelled. Let's try again...[/]\n")
                continue
            risk_data = _step_risk_scheduling(
                defaults, exchange_data["exchange_type"], console,
            )
            persona_data = _step_persona(trader_defaults, console)

            data = {**exchange_data, **trading_data, **model_data, **risk_data, **persona_data}

            if _show_summary(data, console):
                default_name = _generate_session_name(data["symbol"], data["exchange_type"])
                name = Prompt.ask("Session name", default=default_name, console=console)
                data["session_name"] = name
                return WizardResult(**data)

            console.print("[yellow]Let's reconfigure...[/]\n")

    except KeyboardInterrupt:
        return None
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_wizard.py -v`
Expected: All wizard tests pass

- [ ] **Step 5: Commit**

```bash
git add src/cli/wizard.py tests/test_wizard.py
git commit -m "feat(r1): wizard summary, naming, and run_wizard orchestrator"
```

---

### Task 6: Refactor app.py — extract build_services, wire wizard

**Files:**
- Modify: `src/cli/app.py`

This task replaces all scattered interaction in `run()` with the wizard call and extracts `build_services()`. No new tests — existing tests must continue to pass. Done incrementally: add new code → rewrite run() → delete old code, with test runs between each.

- [ ] **Step 1: Add imports, `_DEFAULT_PRECISION`, and `build_services()` to app.py**

Add new imports at the top of `src/cli/app.py` (after existing imports):

```python
from src.cli.wizard import WizardResult, run_wizard
from src.config import ExchangeConfig, load_settings, load_trader_config  # add ExchangeConfig
```

Add `_DEFAULT_PRECISION` and `build_services()` after `run_agent_cycle()` (before `run()`, around line 160):

```python
# --- Phase 5: Service construction ---

_DEFAULT_PRECISION = {
    "BTC/USDT:USDT": 3,
    "ETH/USDT:USDT": 2,
}


def build_services(
    result: WizardResult,
    engine,
    session_id: str,
    sc,
    settings,
):
    """Build exchange, deps, agent, budget from WizardResult."""
    from src.services.price_alert import PriceAlertService

    # Exchange
    if result.exchange_type == "simulated":
        from src.integrations.exchange.simulated import SimulatedExchange
        precision = {result.symbol: _DEFAULT_PRECISION.get(result.symbol, 3)}
        config = ExchangeConfig(
            name="simulated", fee_rate=result.fee_rate, precision=precision,
        )
        exchange = SimulatedExchange(
            config=config, db_engine=engine,
            session_id=session_id, symbol=result.symbol,
        )
        sc.print("Exchange: simulated (local matching)")
    else:
        creds = result.api_credentials
        exchange = OKXExchange(
            api_key=creds["api_key"], secret=creds["secret"],
            password=creds["password"], symbol=result.symbol,
        )
        sc.print("Exchange: okx (REAL account)")

    market_data = MarketDataService(exchange)
    technical = TechnicalAnalysisService()
    memory = MemoryService(engine, session_id=session_id)
    budget = TokenBudget(daily_max=result.token_budget)
    approval_gate = ApprovalGate(
        enabled=result.approval_enabled,
        timeout_seconds=settings.approval.timeout_seconds,
        console=sc,
    )

    agent = create_trader_agent(model=result.model, persona_config=result.persona)

    deps = TradingDeps(
        symbol=result.symbol,
        timeframe=result.timeframe,
        market_data=market_data,
        exchange=exchange,
        technical=technical,
        memory=memory,
        session_id=session_id,
        db_engine=engine,
        approval_gate=approval_gate,
        approval_enabled=result.approval_enabled,
    )

    # Alert service
    if result.alert_enabled:
        alert_service = PriceAlertService(
            symbol=result.symbol,
            window_minutes=result.alert_window_min,
            threshold_pct=result.alert_threshold_pct,
            cooldown_minutes=result.alert_cooldown_min,
        )
        exchange.set_alert_service(alert_service)
        sc.print(
            f"Alerts: ON ({result.alert_window_min}min / "
            f"{result.alert_threshold_pct}% / cd {result.alert_cooldown_min}min)"
        )
    else:
        sc.print("Alerts: OFF")

    return exchange, deps, agent, budget
```

- [ ] **Step 2: Write smoke test for build_services (sim path)**

Append to `tests/test_wizard.py`:

```python
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
        alert_enabled=True, alert_window_min=5, alert_threshold_pct=3.0,
        alert_cooldown_min=15, token_budget=500000, persona=PersonaConfig(),
        session_name="test",
    )
    mock_engine = MagicMock()
    mock_sc = MagicMock()
    mock_settings = MagicMock()
    mock_settings.approval.timeout_seconds = 300

    with patch("src.integrations.exchange.simulated.SimulatedExchange") as MockSim, \
         patch("src.cli.app.MarketDataService"), \
         patch("src.cli.app.MemoryService"), \
         patch("src.cli.app.create_trader_agent") as mock_agent, \
         patch("src.services.price_alert.PriceAlertService"):
        MockSim.return_value = MagicMock()
        mock_agent.return_value = MagicMock()
        exchange, deps, agent, budget = build_services(
            result, mock_engine, "sid", mock_sc, mock_settings,
        )

    assert deps.symbol == "BTC/USDT:USDT"
    assert deps.timeframe == "15m"
    assert deps.approval_enabled is False
    assert budget._daily_max == 500000
    MockSim.assert_called_once()
```

- [ ] **Step 3: Run tests — additive only, nothing should break**

Run: `.venv/bin/python -m pytest tests/ -v`
Expected: All existing tests pass + build_services smoke test passes

- [ ] **Step 4: Rewrite `run()` to use wizard**

Replace the entire `run()` function and delete `_interactive_add_model()`. The new `run()`:

```python
async def run(
    settings_path: Path = Path("config/settings.yaml"),
    trader_path: Path = Path("config/trader.yaml"),
    model_id: str | None = None,
    debug: bool = False,
):
    # ── Phase 1: System logging ──
    log_dir = settings_path.resolve().parent.parent / "logs"
    pre_console = setup_system_logging(debug, log_dir)
    pre_console.print("[bold green]TradeBot — Starting...[/]\n")

    # ── Phase 2: Config + Database ──
    settings = load_settings(settings_path)
    trader_config = load_trader_config(trader_path)

    project_root = settings_path.resolve().parent.parent
    data_dir = project_root / "data"
    data_dir.mkdir(exist_ok=True)
    db_url = settings.database.url
    if db_url.startswith("sqlite") and ":///" in db_url and not db_url.startswith("sqlite+aiosqlite:////"):
        relative_path = db_url.split(":///", 1)[1]
        absolute_path = project_root / relative_path
        db_url = f"sqlite+aiosqlite:///{absolute_path}"
    engine = await init_db(db_url)

    # ── Phase 3: Configuration Wizard ──
    from src.services.model_manager import ModelManager

    config_dir = project_root / "config"
    model_manager = ModelManager(config_path=config_dir / "models.json")

    result = await run_wizard(
        model_manager=model_manager,
        defaults=settings,
        trader_defaults=trader_config,
        config_dir=config_dir,
        console=pre_console,
        model_id=model_id,
    )
    if result is None:
        pre_console.print("Cancelled.")
        return

    # Create session (R2 will add select/restore with #{N} counter)
    # Session.name has unique=True — deduplicate by appending suffix
    async with get_session(engine) as db_sess:
        base_name = result.session_name
        name = base_name
        suffix = 2
        while True:
            existing = await db_sess.execute(
                select(Session).where(Session.name == name)
            )
            if existing.scalar_one_or_none() is None:
                break
            name = f"{base_name} ({suffix})"
            suffix += 1

        trading_session = Session(
            name=name,
            symbol=result.symbol,
            persona_config=json.dumps(result.persona.model_dump()),
            model_config=json.dumps({
                "id": result.model_config.id,
                "provider": result.model_config.provider,
                "model": result.model_config.model,
            }),
            initial_balance=result.initial_balance,
            status="active",
        )
        db_sess.add(trading_session)
        await db_sess.commit()
        await db_sess.refresh(trading_session)
        if name != base_name:
            logger.info(f"Session name '{base_name}' taken, using '{name}'")
    session_id = trading_session.id

    # ── Phase 4: Session logging ──
    sc = setup_session_logging(session_id, log_dir)

    # ── Phase 5: Build services ──
    exchange, deps, agent, budget = build_services(
        result, engine, session_id, sc, settings,
    )

    # ── Phase 6: Main loop ──
    shutdown_event = asyncio.Event()

    def _signal_handler():
        sc.print("\n[yellow]Shutting down gracefully...[/]")
        shutdown_event.set()

    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, _signal_handler)

    handle_fill = None

    async def on_tick(trigger_type: str, context=None):
        if shutdown_event.is_set():
            return
        try:
            await run_agent_cycle(
                agent, deps, trigger_type, budget, engine,
                context, model=result.model, console=sc,
            )
        except Exception:
            logger.exception("Agent cycle failed")
        finally:
            if handle_fill is not None:
                for fill in exchange.drain_pending_fills():
                    try:
                        await handle_fill(fill)
                    except Exception:
                        logger.exception("Fill handler failed for order %s", fill.order_id)

    interval = result.scheduler_interval_min * 60
    scheduler = Scheduler(interval_seconds=interval, callback=on_tick)

    def _create_fill_handler(sched, eng, sid):
        async def handler(event: FillEvent):
            try:
                await _record_action_from_fill(eng, sid, event)
            except Exception:
                logger.warning("Failed to record fill event", exc_info=True)
            finally:
                await sched.trigger("conditional", context=event)
        return handler

    handle_fill = _create_fill_handler(scheduler, engine, session_id)
    exchange.on_fill(handle_fill)

    if result.alert_enabled:
        async def handle_alert(alert_info):
            await scheduler.trigger("alert", context=alert_info)
        exchange.on_alert(handle_alert)

    await exchange.start()

    # Initial metrics
    metrics_service = MetricsService(initial_balance=result.initial_balance)
    positions = await exchange.fetch_positions(result.symbol)
    pos_str = f"{positions[0].side} {positions[0].contracts}" if positions else "none"
    metrics = await metrics_service.compute(engine, session_id, current_position=pos_str)
    display_metrics(metrics, console=sc)

    sc.print(f"\n[bold]Scheduler: every {result.scheduler_interval_min} min[/]")
    sc.print(f"[bold]LLM Budget: {result.token_budget:,} tokens/day[/]")
    sc.print("[dim]Press Ctrl+C to stop[/]\n")

    scheduler_task = asyncio.create_task(scheduler.start())
    await shutdown_event.wait()

    scheduler.stop()
    await scheduler_task
    await exchange.close()
    sc.close()
    pre_console.print("[green]TradeBot stopped.[/]")
```

- [ ] **Step 5: Run all tests to verify nothing is broken**

Run: `.venv/bin/python -m pytest tests/ -v`
Expected: All existing tests + wizard tests pass

- [ ] **Step 6: Commit**

```bash
git add src/cli/app.py tests/test_wizard.py
git commit -m "refactor(r1): wire wizard into app.py, extract build_services, delete scattered interaction"
```

---

### Task 7: Cleanup — .gitignore, settings_sim deprecation, final verification

**Files:**
- Modify: `.gitignore`
- Modify: `config/settings_sim.yaml`

- [ ] **Step 1: Add `config/.credentials` to .gitignore**

Append after the `config/models.json` line in `.gitignore`:

```
config/.credentials
```

- [ ] **Step 2: Add DEPRECATED header to settings_sim.yaml**

Prepend to `config/settings_sim.yaml`:

```yaml
# DEPRECATED — This file is no longer read by TradeBot.
# The interactive wizard (R1) now handles all configuration.
# Kept as reference for simulated-mode defaults.
```

- [ ] **Step 3: Run full test suite**

Run: `.venv/bin/python -m pytest tests/ -v`
Expected: All tests pass

- [ ] **Step 4: Commit**

```bash
git add .gitignore config/settings_sim.yaml
git commit -m "chore(r1): gitignore credentials file, deprecate settings_sim.yaml"
```

---

## Spec Coverage Checklist

| Spec Requirement | Task |
|-----------------|------|
| `WizardResult` dataclass as wizard output | Task 1 |
| Credential storage (`config/.credentials`, 0o600) | Task 1, 7 |
| Step 1: Exchange (sim/real, fee, balance, creds) | Task 2 |
| Step 2: Trading pair (symbol, timeframe) | Task 2 |
| Step 3: Model (existing/new, connectivity test, `--model` flag) | Task 3 |
| Step 4: Risk (scheduler, approval, alerts, budget) | Task 4 |
| Approval defaults (sim=OFF, real=ON) | Task 4 |
| Step 5: Persona (risk/style/leverage/SL/TP) | Task 4 |
| Summary table + confirmation | Task 5 |
| Session name generation | Task 5 |
| `run_wizard()` orchestrator with Ctrl+C + re-run on reject | Task 5 (incl. reject→reconfigure test) |
| Credential read priority (.credentials > .env > manual) | Task 2 |
| Delete `_interactive_add_model()` from app.py | Task 6 |
| Delete scattered `input()` calls from `run()` | Task 6 |
| Extract `build_services()` from `run()` | Task 6 |
| `_DEFAULT_PRECISION` replaces `settings_sim.yaml` precision | Task 6 |
| Use `WizardResult` for exchange/alert/model config | Task 6 |
| `.gitignore`: `config/.credentials` | Task 7 |
| `settings_sim.yaml` deprecated | Task 7 |
| All Rich prompts (no external TUI lib) | Tasks 2-5 |
| YAML files as default value templates only | Task 6 (wizard reads defaults from Settings) |
