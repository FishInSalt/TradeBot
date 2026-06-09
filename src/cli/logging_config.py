# src/cli/logging_config.py
from __future__ import annotations

import glob
import logging
import os
import re
from datetime import datetime
from logging.handlers import RotatingFileHandler
from pathlib import Path

from rich.console import Console
from rich.logging import RichHandler
from rich.rule import Rule


# Module-level constant: archive suffix must match this exact pattern to be
# considered a rotation artifact (vs. user-placed files like system.log.bak).
_ARCHIVE_SUFFIX_RE = re.compile(r"\d{8}-\d{6}-\d{6}$")  # YYYYMMDD-HHMMSS-ffffff


class TimestampedRotatingFileHandler(RotatingFileHandler):
    """RotatingFileHandler whose archive files carry a microsecond-precision
    timestamp suffix instead of stdlib's sequential .1/.2/... numbering.

    Archive name format: ``<baseFilename>.YYYYMMDD-HHMMSS-ffffff``
    (e.g., ``system.log.20260430-160027-747099``).

    The timestamp marks when the file was rotated out (i.e., the END of the
    archive's data window). Microsecond resolution makes intra-process
    collisions practically impossible.

    Pruning keeps the newest ``backupCount`` archives by mtime; only files
    matching the strict timestamp suffix are eligible — user-placed files
    like ``system.log.bak`` are ignored (preserved across rotations).
    """

    def doRollover(self) -> None:
        if self.stream:
            self.stream.close()
            self.stream = None
        ts = datetime.now().strftime("%Y%m%d-%H%M%S-%f")
        dfn = f"{self.baseFilename}.{ts}"
        os.rename(self.baseFilename, dfn)
        if self.backupCount > 0:
            base_prefix_len = len(self.baseFilename) + 1  # +1 for the '.'
            archives = sorted(
                (
                    p for p in glob.glob(f"{self.baseFilename}.*")
                    if _ARCHIVE_SUFFIX_RE.fullmatch(p[base_prefix_len:])
                ),
                key=os.path.getmtime,
            )
            while len(archives) > self.backupCount:
                os.remove(archives.pop(0))
        if not self.delay:
            self.stream = self._open()


class SessionConsole:
    """Dual-write console: terminal (Rich formatted) + session log file (plain text)."""

    def __init__(self, session_id: str, log_dir: Path):
        self._terminal = Console()
        log_file = log_dir / f"session_{session_id}.log"
        self._file = open(log_file, "a", encoding="utf-8")
        self._file_console = Console(file=self._file, no_color=True, width=120)

    def print(self, *args, **kwargs):
        self._terminal.print(*args, **kwargs)
        self._file_console.print(*args, **kwargs)
        self._file.flush()

    def close(self):
        if not self._file.closed:
            self._file.close()


def setup_system_logging(debug: bool, log_dir: Path) -> Console:
    """Phase 1 — Create log_dir, configure root logger, return temporary Console."""
    log_dir.mkdir(parents=True, exist_ok=True)

    # Close and clear any existing handlers (important for test isolation)
    root = logging.getLogger()
    for h in root.handlers:
        h.close()
    root.handlers.clear()

    # System log file — all levels (rotated by size with microsecond-stamped archives)
    file_handler = TimestampedRotatingFileHandler(
        log_dir / "system.log",
        maxBytes=100 * 1024 * 1024,  # 100 MB per file
        backupCount=30,              # ~30 archives → 3 GB cap, ~1 month at sim rate
        encoding="utf-8",
    )
    file_handler.setLevel(logging.DEBUG)
    file_handler.setFormatter(logging.Formatter(
        "%(asctime)s [%(levelname)-7s] %(name)s: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    ))

    # Terminal — WARNING+ by default, DEBUG in debug mode
    terminal_console = Console()
    terminal_handler = RichHandler(
        console=terminal_console,
        rich_tracebacks=True,
        show_path=False,
    )
    terminal_handler.setLevel(logging.DEBUG if debug else logging.WARNING)

    root.setLevel(logging.DEBUG)
    root.addHandler(file_handler)
    root.addHandler(terminal_handler)

    return terminal_console


def setup_session_logging(session_id: str, log_dir: Path) -> SessionConsole:
    """Phase 2 — Create SessionConsole for dual-write session output."""
    return SessionConsole(session_id=session_id, log_dir=log_dir)


def write_session_header(
    sc: SessionConsole,
    *,
    name: str,
    session_id: str,
    symbol: str,
    mode: str,
    timeframe: str,
    interval_min: int,
    is_new: bool,
    started_at: datetime,
) -> None:
    """Write a self-contained session metadata header at the top of each launch.

    Makes the session log answer "which session / when started" on its own (no
    uuid↔DB lookup). Written every startup, so in the append-mode log it also
    delimits run boundaries between successive launches of the same session.

    `mode` is the raw exchange_type (e.g. "simulated") — deliberately the full
    name, not the session-list abbreviation. `interval_min` is minutes (int).
    """
    marker = "new" if is_new else "resumed"
    sc.print(Rule(f"Session: {name} ({marker})"))
    sc.print(f"ID:       {session_id}")
    sc.print(
        f"Symbol:   {symbol}   Mode: {mode}   TF: {timeframe}   "
        f"Interval: {interval_min}m"
    )
    sc.print(f"Started:  {started_at:%Y-%m-%d %H:%M:%S} UTC")
