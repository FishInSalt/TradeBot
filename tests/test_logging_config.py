# tests/test_logging_config.py
from __future__ import annotations

import logging
from pathlib import Path

import pytest

from rich.logging import RichHandler

from src.cli.logging_config import SessionConsole, setup_system_logging, setup_session_logging


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



def test_setup_system_logging_creates_log_dir(tmp_path: Path):
    log_dir = tmp_path / "logs"
    console = setup_system_logging(debug=False, log_dir=log_dir)
    assert log_dir.exists()
    assert (log_dir / "system.log").exists()
    assert console is not None


def test_setup_system_logging_writes_to_system_log(tmp_path: Path):
    log_dir = tmp_path / "logs"
    setup_system_logging(debug=False, log_dir=log_dir)

    test_logger = logging.getLogger("test.system.write")
    test_logger.info("system info message")

    content = (log_dir / "system.log").read_text()
    assert "system info message" in content


def test_setup_system_logging_debug_mode(tmp_path: Path):
    log_dir = tmp_path / "logs"
    setup_system_logging(debug=True, log_dir=log_dir)

    root = logging.getLogger()
    # In debug mode, the RichHandler (terminal) should accept DEBUG level
    rich_handlers = [h for h in root.handlers if isinstance(h, RichHandler)]
    assert any(h.level <= logging.DEBUG for h in rich_handlers)


def test_setup_system_logging_non_debug_filters_info(tmp_path: Path):
    log_dir = tmp_path / "logs"
    setup_system_logging(debug=False, log_dir=log_dir)

    root = logging.getLogger()
    rich_handlers = [h for h in root.handlers if isinstance(h, RichHandler)]
    assert any(h.level >= logging.WARNING for h in rich_handlers)


def test_setup_session_logging_returns_session_console(tmp_path: Path):
    log_dir = tmp_path / "logs"
    log_dir.mkdir()
    sc = setup_session_logging(session_id="sid-001", log_dir=log_dir)
    assert isinstance(sc, SessionConsole)
    sc.print("test output")
    sc.close()
    assert (log_dir / "session_sid-001.log").exists()
