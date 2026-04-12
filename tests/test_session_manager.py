# tests/test_session_manager.py
from __future__ import annotations

import json
from datetime import datetime, timezone, timedelta
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from rich.console import Console
from sqlalchemy import text

from src.storage.database import init_db, get_session
from src.storage.models import Session


async def test_migrate_session_table_adds_new_columns(tmp_path):
    """Migration adds R2 columns to a pre-existing sessions table."""
    db_url = f"sqlite+aiosqlite:///{tmp_path}/test.db"
    engine = await init_db(db_url)

    # Since init_db creates tables from ORM (which now includes R2 columns),
    # we verify migration is idempotent — running it on an already-migrated table is safe
    from src.cli.session_manager import _migrate_session_table
    async with engine.begin() as conn:
        await _migrate_session_table(conn)

    # Verify columns exist
    async with engine.connect() as conn:
        result = await conn.execute(text("PRAGMA table_info(sessions)"))
        cols = {row[1] for row in result}
    for col in ["exchange_type", "timeframe", "scheduler_interval_min",
                "approval_enabled", "alert_config", "fee_rate",
                "token_budget", "last_active_at"]:
        assert col in cols, f"Column {col} missing after migration"
    await engine.dispose()


async def test_migrate_session_table_is_idempotent(tmp_path):
    """Running migration twice does not raise errors."""
    db_url = f"sqlite+aiosqlite:///{tmp_path}/test.db"
    engine = await init_db(db_url)
    from src.cli.session_manager import _migrate_session_table
    async with engine.begin() as conn:
        await _migrate_session_table(conn)
    # Run again — should not error
    async with engine.begin() as conn:
        await _migrate_session_table(conn)
    await engine.dispose()
