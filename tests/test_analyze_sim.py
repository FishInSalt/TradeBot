"""End-to-end tests for scripts/analyze_sim.py via subprocess."""
from __future__ import annotations
from datetime import datetime, timedelta, timezone

import pytest
import subprocess
import sys

from tests._sim_fixtures import (
    make_session, make_cycle, make_open_lot, make_close_fill, _resolve_db_path,
)
from scripts._sim_metrics import R2_7_MERGED_AT


def _run_analyze(*args, db_path):
    cmd = [sys.executable, "scripts/analyze_sim.py", *args, "--db", str(db_path)]
    return subprocess.run(cmd, capture_output=True, text=True)


async def test_analyze_session_not_found_exit_1(db_engine):
    db_path = _resolve_db_path(db_engine)
    await make_session(db_engine, name="real")
    r = _run_analyze("--session", "typo", db_path=db_path)
    assert r.returncode == 1
    assert "Session 'typo' not found" in r.stderr


async def test_analyze_db_file_missing_exit_1(tmp_path):
    r = _run_analyze("--session", "any", db_path=tmp_path / "nonexistent.db")
    assert r.returncode == 1
    assert "Database file not found" in r.stderr


async def test_analyze_session_by_name_resolves(db_engine):
    db_path = _resolve_db_path(db_engine)
    sid = await make_session(db_engine, name="my_friendly_name")
    await make_cycle(db_engine, sid, "c1")
    r = _run_analyze("--session", "my_friendly_name", db_path=db_path)
    assert r.returncode == 0
    assert "my_friendly_name" in r.stdout


async def test_analyze_out_dir_missing_exit_1(db_engine, tmp_path):
    db_path = _resolve_db_path(db_engine)
    await make_session(db_engine)
    r = _run_analyze("--session", "test_sim",
                     "--out", str(tmp_path / "noexist" / "x.md"), db_path=db_path)
    assert r.returncode == 1
    assert "Output dir" in r.stderr
