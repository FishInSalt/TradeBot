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
