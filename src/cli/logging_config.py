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
