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



def test_session_console_double_close(tmp_path: Path):
    sc = SessionConsole(session_id="test-dblclose", log_dir=tmp_path)
    sc.print("before close")
    sc.close()
    sc.close()  # must not raise


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


def test_setup_system_logging_uses_timestamped_rotating_file_handler(tmp_path: Path):
    """R2-3 drift guard: file handler must be TimestampedRotatingFileHandler with
    maxBytes=100MB and backupCount=30.
    """
    from src.cli.logging_config import TimestampedRotatingFileHandler

    log_dir = tmp_path / "logs"
    setup_system_logging(debug=False, log_dir=log_dir)

    root = logging.getLogger()
    file_handlers = [h for h in root.handlers if isinstance(h, TimestampedRotatingFileHandler)]
    assert len(file_handlers) == 1, (
        f"expected exactly 1 TimestampedRotatingFileHandler, got {len(file_handlers)} "
        f"(all handlers: {[type(h).__name__ for h in root.handlers]})"
    )
    fh = file_handlers[0]
    assert fh.maxBytes == 100 * 1024 * 1024, (
        f"expected maxBytes=100MB ({100 * 1024 * 1024}), got {fh.maxBytes}"
    )
    assert fh.backupCount == 30, f"expected backupCount=30, got {fh.backupCount}"


def test_setup_system_logging_rotation_creates_timestamped_archive(tmp_path: Path):
    """R2-3 T2: doRollover() renames active log to a microsecond-stamped archive
    (system.log.YYYYMMDD-HHMMSS-ffffff) and creates fresh system.log.
    """
    import re
    from src.cli.logging_config import TimestampedRotatingFileHandler

    log_dir = tmp_path / "logs"
    setup_system_logging(debug=False, log_dir=log_dir)

    test_logger = logging.getLogger("test.r2_3.rotation")
    test_logger.info("before rollover")

    fh = next(
        h for h in logging.getLogger().handlers
        if isinstance(h, TimestampedRotatingFileHandler)
    )
    fh.doRollover()
    test_logger.info("after rollover")

    # Active file exists, contains only post-rollover content
    active = log_dir / "system.log"
    assert active.exists()
    active_content = active.read_text()
    assert "after rollover" in active_content
    assert "before rollover" not in active_content

    # Exactly 1 archive with timestamped suffix
    archives = sorted(log_dir.glob("system.log.*"))
    assert len(archives) == 1, (
        f"expected 1 archive, got {[a.name for a in archives]}"
    )
    suffix = archives[0].name[len("system.log."):]
    assert re.fullmatch(r"\d{8}-\d{6}-\d{6}", suffix), (
        f"archive suffix {suffix!r} does not match YYYYMMDD-HHMMSS-ffffff"
    )
    assert "before rollover" in archives[0].read_text()


def test_setup_system_logging_rotation_prunes_oldest_beyond_backup_count(tmp_path: Path):
    """R2-3 T3: when archive count exceeds backupCount, oldest (by mtime) is pruned.
    """
    import time
    from src.cli.logging_config import TimestampedRotatingFileHandler

    log_dir = tmp_path / "logs"
    setup_system_logging(debug=False, log_dir=log_dir)

    fh = next(
        h for h in logging.getLogger().handlers
        if isinstance(h, TimestampedRotatingFileHandler)
    )
    # Shrink backupCount for fast test (production is 30)
    fh.backupCount = 2

    test_logger = logging.getLogger("test.r2_3.prune")
    contents = ["v1", "v2", "v3"]
    for msg in contents:
        test_logger.info(msg)
        # Sleep to ensure distinct mtimes on coarse-grained filesystems
        time.sleep(0.01)
        fh.doRollover()

    archives = sorted(log_dir.glob("system.log.*"))
    assert len(archives) == 2, (
        f"expected 2 archives after 3 rollovers with backupCount=2, "
        f"got {[a.name for a in archives]}"
    )
    # Oldest content ("v1") should be pruned; newest two retained
    surviving = "\n".join(a.read_text() for a in archives)
    assert "v1" not in surviving, f"oldest content not pruned: {surviving!r}"
    assert "v2" in surviving and "v3" in surviving
