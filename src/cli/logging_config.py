# src/cli/logging_config.py
from __future__ import annotations

import logging
from pathlib import Path

from rich.console import Console
from rich.logging import RichHandler


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
        self._file.close()


def setup_system_logging(debug: bool, log_dir: Path) -> Console:
    """Phase 1 — Create log_dir, configure root logger, return temporary Console."""
    log_dir.mkdir(parents=True, exist_ok=True)

    # Close and clear any existing handlers (important for test isolation)
    root = logging.getLogger()
    for h in root.handlers:
        h.close()
    root.handlers.clear()

    # System log file — all levels
    file_handler = logging.FileHandler(log_dir / "system.log")
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
