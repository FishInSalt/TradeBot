# src/cli/session_manager.py
from __future__ import annotations

import json
import sys
from datetime import datetime, timezone, timedelta
from pathlib import Path

from rich.console import Console
from rich.prompt import Confirm, IntPrompt, Prompt
from rich.table import Table
from sqlalchemy import select, text, update

from src.cli.wizard import WizardResult, run_wizard
from src.config import Settings, TraderConfig
from src.services.model_manager import ModelConfig, ModelManager
from src.storage.database import get_session
from src.storage.models import Session


async def _migrate_session_table(conn) -> None:
    """Check and add R2 columns to sessions table. Idempotent — safe to run repeatedly."""
    result = await conn.execute(text("PRAGMA table_info(sessions)"))
    existing = {row[1] for row in result}
    migrations = [
        ("exchange_type", "TEXT DEFAULT 'simulated'"),
        ("timeframe", "TEXT DEFAULT '15m'"),
        ("scheduler_interval_min", "INTEGER DEFAULT 15"),
        ("approval_enabled", "BOOLEAN DEFAULT 1"),
        ("alert_config", "TEXT"),
        ("fee_rate", "REAL"),
        ("token_budget", "INTEGER DEFAULT 500000"),
        ("last_active_at", "TIMESTAMP"),
    ]
    for col, defn in migrations:
        if col not in existing:
            await conn.execute(text(f"ALTER TABLE sessions ADD COLUMN {col} {defn}"))


async def _restore_session(
    engine,
    session_id: str,
    model_manager: ModelManager,
    model_id: str | None,
    console: Console,
    config_dir: Path,
) -> WizardResult | None:
    """Load session config from DB and reconstruct WizardResult. Returns None on cancel."""
    async with get_session(engine) as db_sess:
        result = await db_sess.execute(select(Session).where(Session.id == session_id))
        s = result.scalar_one()

    # Persona
    from src.config import PersonaConfig
    persona = PersonaConfig(**json.loads(s.persona_config)) if s.persona_config else PersonaConfig()

    # Model — resolve from session record
    saved_model_cfg = json.loads(s.model_config) if s.model_config else None
    saved_model_id = saved_model_cfg.get("id") if saved_model_cfg else None

    all_models = model_manager.load_models()
    selected_config = None
    selected_model = None

    # --model flag takes priority
    if model_id:
        selected_config = model_manager.get_model_by_id(model_id, all_models)
        if selected_config is None:
            console.print(f"[yellow]Model '{model_id}' not found, entering selection...[/]")
        else:
            selected_model = model_manager.create_model(selected_config)

    # No --model flag or --model not found — try session's saved model
    if selected_model is None and saved_model_id:
        selected_config = model_manager.get_model_by_id(saved_model_id, all_models)
        if selected_config:
            if Confirm.ask(
                f"  Continue with model [bold]{selected_config.id}[/]?",
                default=True, console=console,
            ):
                selected_model = model_manager.create_model(selected_config)
        else:
            console.print(f"[yellow]Previous model '{saved_model_id}' no longer available[/]")

    # If still no model, run wizard step 3
    if selected_model is None:
        from src.cli.wizard import _step_model
        model_data = await _step_model(model_manager, None, console)
        if model_data is None:
            return None
        selected_config = model_data["model_config"]
        selected_model = model_data["model"]

    # Alert config
    alert_enabled = False
    alert_window = None
    alert_threshold = None
    alert_cooldown = None
    if s.alert_config:
        alert_data = json.loads(s.alert_config)
        alert_enabled = alert_data.get("enabled", False)
        alert_window = alert_data.get("window")
        alert_threshold = alert_data.get("threshold")
        alert_cooldown = alert_data.get("cooldown")

    # Credentials for real exchange
    api_credentials = None
    if s.exchange_type == "okx":
        from src.cli.wizard import _load_credentials
        saved_creds = _load_credentials(config_dir)
        if "okx" in saved_creds:
            api_credentials = saved_creds["okx"]
        else:
            console.print("[yellow]OKX credentials not found — please re-enter[/]")
            from rich.prompt import Prompt as RichPrompt
            api_key = RichPrompt.ask("  API Key", password=True, console=console)
            secret = RichPrompt.ask("  Secret", password=True, console=console)
            password = RichPrompt.ask("  Password", password=True, console=console)
            api_credentials = {"api_key": api_key, "secret": secret, "password": password}
            from src.cli.wizard import _save_credentials
            _save_credentials(config_dir, "okx", api_credentials)

    # Update status to active
    async with get_session(engine) as db_sess:
        await db_sess.execute(
            update(Session).where(Session.id == session_id).values(status="active")
        )
        await db_sess.commit()

    return WizardResult(
        exchange_type=s.exchange_type,
        fee_rate=s.fee_rate,
        initial_balance=s.initial_balance,
        api_credentials=api_credentials,
        symbol=s.symbol,
        timeframe=s.timeframe,
        model_config=selected_config,
        model=selected_model,
        scheduler_interval_min=s.scheduler_interval_min,
        approval_enabled=s.approval_enabled,
        alert_enabled=alert_enabled,
        alert_window_min=alert_window,
        alert_threshold_pct=alert_threshold,
        alert_cooldown_min=alert_cooldown,
        token_budget=s.token_budget,
        persona=persona,
        session_name=s.name,
    )
