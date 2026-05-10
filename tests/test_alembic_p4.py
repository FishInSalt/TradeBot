"""P4 alembic migration roundtrip — adds 2 nullable Text columns and drops them clean.

Bootstrap via init_db (Path 3) to skip pre-Phase-1 fixture issues — same pattern
as test_alembic_roundtrip_phase1.py.
"""
import os
import subprocess
import sqlite3

import pytest


PHASE1_REV = "61ac4841a55d"
EXPECTED_VIEWS = {"v_cycle_metrics", "v_alert_lifecycle", "v_order_lifecycle"}


def _query_views(db_path: str) -> set[str]:
    conn = sqlite3.connect(db_path)
    return {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='view'")}


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


@pytest.fixture
async def phase1_head_db(tmp_path):
    """Bootstrap fresh DB then explicitly downgrade to Phase 1 head — forward
    upgrade tests start from Phase 1 to exercise P4 migration's upgrade() path."""
    from src.storage.database import init_db
    db_path = tmp_path / "p4_forward.db"
    db_url_async = f"sqlite+aiosqlite:///{db_path}"
    engine = await init_db(db_url_async)
    await engine.dispose()
    env = {**os.environ, "TRADEBOT_DB_URL": db_url_async}
    subprocess.run(
        ["alembic", "downgrade", PHASE1_REV],
        check=True, env=env, capture_output=True,
    )
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

    # I2: views must be recreated by downgrade — protects against future
    # accidental removal of the view drop/recreate dance in downgrade().
    views = _query_views(db)
    assert EXPECTED_VIEWS.issubset(views), (
        f"Views missing after P4 downgrade: {EXPECTED_VIEWS - views}. "
        f"downgrade() must drop views, alter table, then recreate them — "
        f"recreate step appears to be missing or broken."
    )


async def test_upgrade_from_phase1_to_p4_preserves_views(phase1_head_db):
    """I1: real `alembic upgrade head` executes P4 migration's upgrade() —
    asserts both P4 columns added AND 3 Phase 1 views still functional after upgrade.

    Bypassing init_db Path 3 stamping ensures upgrade() is actually run, catching
    future alembic / SQLAlchemy behavior shifts (e.g. batch_alter_table interacting
    badly with views) that would silently break the migration without this test.
    """
    db, env = phase1_head_db

    # Sanity: confirm we're at Phase 1 head and P4 columns are NOT yet present.
    conn = sqlite3.connect(db)
    sessions_cols = {row[1] for row in conn.execute("PRAGMA table_info(sessions)")}
    ac_cols = {row[1] for row in conn.execute("PRAGMA table_info(agent_cycles)")}
    assert "system_prompt" not in sessions_cols, "Pre-condition: P4 column should not exist at Phase 1 head"
    assert "user_prompt_snapshot" not in ac_cols, "Pre-condition: P4 column should not exist at Phase 1 head"
    conn.close()

    # Forward upgrade — this exercises P4 migration's upgrade() function.
    subprocess.run(
        ["alembic", "upgrade", "head"],
        check=True, env=env, capture_output=True,
    )

    # P4 columns must now be present.
    conn = sqlite3.connect(db)
    sessions_cols = {row[1]: row[3] for row in conn.execute("PRAGMA table_info(sessions)")}
    ac_cols = {row[1]: row[3] for row in conn.execute("PRAGMA table_info(agent_cycles)")}
    assert "system_prompt" in sessions_cols, "P4 upgrade did not add sessions.system_prompt"
    assert sessions_cols["system_prompt"] == 0, "sessions.system_prompt should be nullable post-upgrade"
    assert "user_prompt_snapshot" in ac_cols, "P4 upgrade did not add agent_cycles.user_prompt_snapshot"
    assert ac_cols["user_prompt_snapshot"] == 0, "agent_cycles.user_prompt_snapshot should be nullable post-upgrade"

    # 3 Phase 1 views must still be functional.
    views = _query_views(db)
    assert EXPECTED_VIEWS.issubset(views), (
        f"Views missing after P4 upgrade: {EXPECTED_VIEWS - views}. "
        f"upgrade() may have inadvertently dropped views without recreating them."
    )
