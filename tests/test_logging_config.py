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


def test_write_session_header_contains_metadata_and_resumed_marker(tmp_path: Path):
    from datetime import datetime, timezone
    from src.cli.logging_config import write_session_header

    sc = SessionConsole(session_id="hdr-1", log_dir=tmp_path)
    write_session_header(
        sc,
        name="BTC sim #1",
        session_id="hdr-1",
        symbol="BTC/USDT:USDT",
        mode="simulated",
        timeframe="15m",
        interval_min=15,
        is_new=False,
        started_at=datetime(2026, 6, 9, 14, 32, 7, tzinfo=timezone.utc),
    )
    sc.close()

    content = (tmp_path / "session_hdr-1.log").read_text()
    assert "BTC sim #1" in content
    assert "hdr-1" in content
    assert "BTC/USDT:USDT" in content
    assert "simulated" in content          # Mode full name (not abbreviated "sim")
    assert "15m" in content                # timeframe + interval both formatted with "m"
    assert "(resumed)" in content          # is_new=False
    assert "(new)" not in content
    assert "2026-06-09 14:32:07" in content
    assert "UTC" in content


def test_write_session_header_new_marker_and_interval_formatting(tmp_path: Path):
    from datetime import datetime, timezone
    from src.cli.logging_config import write_session_header

    sc = SessionConsole(session_id="hdr-2", log_dir=tmp_path)
    write_session_header(
        sc,
        name="ETH sim #2",
        session_id="hdr-2",
        symbol="ETH/USDT:USDT",
        mode="okx",
        timeframe="5m",
        interval_min=30,
        is_new=True,
        started_at=datetime(2026, 6, 9, 0, 0, 0, tzinfo=timezone.utc),
    )
    sc.close()

    content = (tmp_path / "session_hdr-2.log").read_text()
    assert "ETH sim #2" in content
    assert "okx" in content
    assert "(new)" in content
    assert "(resumed)" not in content
    assert "30m" in content                # interval_min int → "30m" (not bare "30")


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


def test_setup_system_logging_rotation_ignores_unrelated_files(tmp_path: Path):
    """R2-3 T4: pruning regex filter excludes user-placed files like system.log.bak,
    even when their mtime is older than rotation archives.
    """
    import time
    from src.cli.logging_config import TimestampedRotatingFileHandler

    log_dir = tmp_path / "logs"
    setup_system_logging(debug=False, log_dir=log_dir)

    # Drop a user-placed backup BEFORE any rotation, so its mtime is oldest
    bak = log_dir / "system.log.bak"
    bak.write_text("user manual backup")
    time.sleep(0.01)  # ensure distinct mtime vs upcoming archives

    fh = next(
        h for h in logging.getLogger().handlers
        if isinstance(h, TimestampedRotatingFileHandler)
    )
    fh.backupCount = 2

    test_logger = logging.getLogger("test.r2_3.unrelated")
    # 3 rollovers > backupCount=2, would prune oldest if .bak were eligible
    for msg in ["v1", "v2", "v3"]:
        test_logger.info(msg)
        time.sleep(0.01)
        fh.doRollover()

    # .bak must survive (regex filter excludes non-timestamp suffixes)
    assert bak.exists(), "user-placed system.log.bak was incorrectly pruned"
    assert bak.read_text() == "user manual backup", "bak content corrupted"

    # Timestamped archives still capped at 2
    timestamped = [
        p for p in log_dir.glob("system.log.*")
        if p.name != "system.log.bak"
    ]
    assert len(timestamped) == 2, (
        f"expected 2 timestamped archives, got {[p.name for p in timestamped]}"
    )
