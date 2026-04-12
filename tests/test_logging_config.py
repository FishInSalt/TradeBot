# tests/test_logging_config.py
from __future__ import annotations

import logging
from pathlib import Path

import pytest

from src.cli.logging_config import SessionConsole


@pytest.fixture(autouse=True)
def _restore_root_logger():
    """Restore root logger state after tests that call setup_system_logging."""
    root = logging.getLogger()
    original_handlers = root.handlers[:]
    original_level = root.level
    yield
    root.handlers = original_handlers
    root.setLevel(original_level)


def test_session_console_writes_to_file(tmp_path: Path):
    sc = SessionConsole(session_id="test-123", log_dir=tmp_path)
    sc.print("Hello world")
    sc.close()

    log_file = tmp_path / "session_test-123.log"
    assert log_file.exists()
    content = log_file.read_text()
    assert "Hello world" in content


def test_session_console_appends(tmp_path: Path):
    sc = SessionConsole(session_id="test-456", log_dir=tmp_path)
    sc.print("Line 1")
    sc.print("Line 2")
    sc.close()

    content = (tmp_path / "session_test-456.log").read_text()
    assert "Line 1" in content
    assert "Line 2" in content


def test_session_console_no_ansi_in_file(tmp_path: Path):
    sc = SessionConsole(session_id="test-789", log_dir=tmp_path)
    sc.print("[bold red]Colored text[/]")
    sc.close()

    content = (tmp_path / "session_test-789.log").read_text()
    assert "Colored text" in content
    assert "\x1b[" not in content  # No ANSI escape sequences


def test_session_console_flush_on_print(tmp_path: Path):
    sc = SessionConsole(session_id="test-flush", log_dir=tmp_path)
    sc.print("Flushed line")
    # Read before close — content should be flushed already
    content = (tmp_path / "session_test-flush.log").read_text()
    assert "Flushed line" in content
    sc.close()
