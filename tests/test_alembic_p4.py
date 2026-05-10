"""P4 alembic migration roundtrip — adds 2 nullable Text columns and drops them clean.

Bootstrap via init_db (Path 3) to skip pre-Phase-1 fixture issues — same pattern
as test_alembic_roundtrip_phase1.py.
"""
import os
import subprocess
import sqlite3

import pytest


@pytest.fixture
async def head_db(tmp_path):
    """Bootstrap fresh DB at current head via init_db (Path 3)."""
    from src.storage.database import init_db
    db_path = tmp_path / "p4_roundtrip.db"
    db_url_async = f"sqlite+aiosqlite:///{db_path}"
    engine = await init_db(db_url_async)
    await engine.dispose()
    env = {**os.environ, "TRADEBOT_DB_URL": db_url_async}
    return str(db_path), env


async def test_head_has_p4_columns(head_db):
    """P4 head: sessions.system_prompt + agent_cycles.user_prompt_snapshot exist + nullable."""
    db, _ = head_db
    conn = sqlite3.connect(db)

    sessions_cols = {row[1]: row[3] for row in conn.execute("PRAGMA table_info(sessions)")}
    # PRAGMA table_info row[3] = "notnull" flag (0 = nullable, 1 = NOT NULL)
    assert "system_prompt" in sessions_cols, f"sessions missing system_prompt; cols: {sorted(sessions_cols)}"
    assert sessions_cols["system_prompt"] == 0, "sessions.system_prompt should be nullable"

    ac_cols = {row[1]: row[3] for row in conn.execute("PRAGMA table_info(agent_cycles)")}
    assert "user_prompt_snapshot" in ac_cols, f"agent_cycles missing user_prompt_snapshot; cols: {sorted(ac_cols)}"
    assert ac_cols["user_prompt_snapshot"] == 0, "agent_cycles.user_prompt_snapshot should be nullable"


async def test_downgrade_drops_p4_columns(head_db):
    """P4 downgrade -1: both columns removed; no IntegrityError (fields nullable)."""
    db, env = head_db
    result = subprocess.run(
        ["alembic", "downgrade", "-1"],
        check=True, env=env, capture_output=True, text=True,
    )

    conn = sqlite3.connect(db)
    sessions_cols = {row[1] for row in conn.execute("PRAGMA table_info(sessions)")}
    ac_cols = {row[1] for row in conn.execute("PRAGMA table_info(agent_cycles)")}

    assert "system_prompt" not in sessions_cols, f"system_prompt remained in sessions: {sorted(sessions_cols)}"
    assert "user_prompt_snapshot" not in ac_cols, f"user_prompt_snapshot remained in agent_cycles: {sorted(ac_cols)}"
