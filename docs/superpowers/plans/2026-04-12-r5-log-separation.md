# R5 日志分离 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Split terminal-only logging into dual-stream output: session logs (per-session file + terminal) and system logs (system.log file + terminal WARNING+ only).

**Architecture:** Create `SessionConsole` class that dual-writes to terminal and session log file. Configure Python root logger with FileHandler (system.log, all levels) + RichHandler (terminal, WARNING+ or DEBUG with --debug). Existing `console.print()` calls in app.py/display.py/approval.py migrate to accept a console parameter instead of using module-level globals.

**Tech Stack:** Python logging, Rich Console/RichHandler, pytest

**Spec:** `docs/superpowers/specs/2026-04-12-batch1-r5-r1-r2-design.md` (R5 section)

---

## File Structure

| File | Action | Responsibility |
|------|--------|---------------|
| `src/cli/logging_config.py` | Create | `SessionConsole` class + `setup_system_logging()` + `setup_session_logging()` |
| `tests/test_logging_config.py` | Create | Tests for SessionConsole and logging setup |
| `src/cli/display.py` | Modify | `display_metrics()` takes `console` param; delete `log_trade()`; delete module-level `console` |
| `tests/test_cli.py` | Modify | `test_format_metrics` unchanged (tests `format_metrics`, not `display_metrics`) |
| `src/cli/approval.py` | Modify | `ApprovalGate.__init__` takes `console` param; delete module-level `console` |
| `tests/test_approval.py` | Modify | Pass `console` to `ApprovalGate` in tests |
| `src/integrations/exchange/okx.py` | Modify | L138-139: `Console().print()` → `logger.warning()` |
| `main.py` | Modify | Add `--debug` flag |
| `src/cli/app.py` | Modify | Replace `logging.basicConfig` + module-level `console`; wire `SessionConsole` through `run()` and `run_agent_cycle()` |
| `.gitignore` | Modify | Add `logs/` |
| ~~`config/settings_sim.yaml`~~ | ~~Modify~~ | Deferred to R1 plan (file is only truly deprecated after wizard replaces YAML editing) |

---

### Task 0: Create feature branch

- [ ] **Step 1: Create and switch to feature branch**

```bash
git checkout -b feature/r5-log-separation
```

---

### Task 1: Create SessionConsole class

**Files:**
- Create: `src/cli/logging_config.py`
- Create: `tests/test_logging_config.py`

- [ ] **Step 1: Write failing tests for SessionConsole**

```python
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
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd /Users/z/Z/TradeBot && python -m pytest tests/test_logging_config.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'src.cli.logging_config'`

- [ ] **Step 3: Implement SessionConsole**

```python
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
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd /Users/z/Z/TradeBot && python -m pytest tests/test_logging_config.py -v`
Expected: 4 passed

- [ ] **Step 5: Commit**

```bash
git add src/cli/logging_config.py tests/test_logging_config.py
git commit -m "feat(r5): add SessionConsole with terminal + file dual-write"
```

---

### Task 2: Add setup_system_logging and setup_session_logging

**Files:**
- Modify: `src/cli/logging_config.py`
- Modify: `tests/test_logging_config.py`

- [ ] **Step 1: Write failing tests for logging setup**

Append to `tests/test_logging_config.py`:

```python
from rich.logging import RichHandler

from src.cli.logging_config import setup_system_logging, setup_session_logging


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
```

- [ ] **Step 2: Run tests to verify new tests fail**

Run: `cd /Users/z/Z/TradeBot && python -m pytest tests/test_logging_config.py -v`
Expected: 5 new tests FAIL — `ImportError: cannot import name 'setup_system_logging'`

- [ ] **Step 3: Implement setup functions**

Add to `src/cli/logging_config.py` (after `SessionConsole` class):

```python
def setup_system_logging(debug: bool, log_dir: Path) -> Console:
    """Phase 1 — Create log_dir, configure root logger, return temporary Console."""
    log_dir.mkdir(parents=True, exist_ok=True)

    # Clear any existing handlers (important for test isolation)
    root = logging.getLogger()
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
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd /Users/z/Z/TradeBot && python -m pytest tests/test_logging_config.py -v`
Expected: 9 passed

- [ ] **Step 5: Commit**

```bash
git add src/cli/logging_config.py tests/test_logging_config.py
git commit -m "feat(r5): add setup_system_logging and setup_session_logging"
```

---

### Task 3: Migrate display.py

**Files:**
- Modify: `src/cli/display.py`
- Check: `tests/test_cli.py` (should still pass — tests `format_metrics`, not `display_metrics`)

- [ ] **Step 1: Run existing test to confirm baseline**

Run: `cd /Users/z/Z/TradeBot && python -m pytest tests/test_cli.py -v`
Expected: PASS

- [ ] **Step 2: Modify display.py — add console param, delete log_trade, delete global console**

Replace the entire file `src/cli/display.py` with:

```python
from __future__ import annotations

from rich.panel import Panel

from src.services.metrics import PerformanceMetrics


def format_metrics(metrics: PerformanceMetrics) -> str:
    pos = metrics.current_position.upper() if metrics.current_position != "none" else "FLAT"
    return (
        f"Return: {metrics.total_return_pct:+.2f}% ({metrics.total_pnl:+.2f} USDT)\n"
        f"Win Rate: {metrics.win_rate * 100:.1f}% ({metrics.winning_trades}W / {metrics.losing_trades}L)\n"
        f"Max Drawdown: -{metrics.max_drawdown_pct:.2f}%\n"
        f"Profit Factor: {metrics.profit_factor:.2f}\n"
        f"Total Trades: {metrics.total_trades}\n"
        f"Position: {pos}"
    )


def display_metrics(metrics: PerformanceMetrics, console) -> None:
    color = "green" if metrics.total_pnl >= 0 else "red"
    console.print(Panel(format_metrics(metrics), title="[bold]Performance[/]", border_style=color))
```

Changes:
- Removed `from rich.console import Console` and module-level `console = Console()`
- `display_metrics()` now takes `console` as second parameter
- Deleted `log_trade()` (dead code — never called anywhere)

- [ ] **Step 3: Run existing test to verify no regression**

Run: `cd /Users/z/Z/TradeBot && python -m pytest tests/test_cli.py -v`
Expected: PASS — `test_format_metrics` tests `format_metrics()` which is unchanged

- [ ] **Step 4: Commit**

```bash
git add src/cli/display.py
git commit -m "refactor(r5): display.py takes console param, remove dead log_trade"
```

---

### Task 4: Migrate approval.py

**Files:**
- Modify: `src/cli/approval.py`
- Modify: `tests/test_approval.py`

- [ ] **Step 1: Update tests to pass console to ApprovalGate**

Replace `tests/test_approval.py` with:

```python
from rich.console import Console


def test_format_decision():
    from src.cli.approval import format_decision_for_approval

    text = format_decision_for_approval(
        action="open_long",
        description="Bullish trend",
        position_pct=20.0,
        leverage=3,
    )
    assert "LONG" in text.upper()
    assert "20" in text
    assert "3" in text


def test_auto_approve_when_disabled():
    from src.cli.approval import ApprovalGate

    gate = ApprovalGate(enabled=False, timeout_seconds=300, console=Console())
    result = gate.check_sync("open_long", "Bullish", 20.0, 3)
    assert result is True


def test_approval_accepted(monkeypatch):
    from src.cli.approval import ApprovalGate

    monkeypatch.setattr("builtins.input", lambda _: "y")
    gate = ApprovalGate(enabled=True, timeout_seconds=300, console=Console())
    result = gate.check_sync("open_long", "Bullish trend", 20.0, 3)
    assert result is True


def test_approval_rejected(monkeypatch):
    from src.cli.approval import ApprovalGate

    monkeypatch.setattr("builtins.input", lambda _: "n")
    gate = ApprovalGate(enabled=True, timeout_seconds=300, console=Console())
    result = gate.check_sync("open_long", "Weak signal", 20.0, 3)
    assert result is False
```

- [ ] **Step 2: Run tests — they should fail (constructor doesn't accept console yet)**

Run: `cd /Users/z/Z/TradeBot && python -m pytest tests/test_approval.py -v`
Expected: FAIL — `TypeError: __init__() got an unexpected keyword argument 'console'`

- [ ] **Step 3: Modify approval.py to accept console parameter**

Replace `src/cli/approval.py` with:

```python
from __future__ import annotations

import asyncio
import logging
from concurrent.futures import ThreadPoolExecutor

from rich.panel import Panel

logger = logging.getLogger(__name__)
_executor = ThreadPoolExecutor(max_workers=1)


def format_decision_for_approval(
    action: str,
    description: str,
    position_pct: float,
    leverage: int,
) -> str:
    lines = [
        f"Action: {action.upper()}",
        f"Position: {position_pct}% of balance | Leverage: {leverage}x",
        f"\nDescription: {description}",
    ]
    return "\n".join(lines)


class ApprovalGate:
    def __init__(self, enabled: bool = True, timeout_seconds: int = 300, console=None):
        self._enabled = enabled
        self._timeout = timeout_seconds
        self._console = console

    def check_sync(
        self,
        action: str,
        description: str,
        position_pct: float,
        leverage: int,
    ) -> bool:
        if not self._enabled:
            return True
        text = format_decision_for_approval(
            action, description, position_pct, leverage
        )
        if self._console is not None:
            self._console.print(
                Panel(text, title="[bold yellow]Trade Approval Required[/]", border_style="yellow")
            )
        response = input(f"Approve? (y/n, timeout {self._timeout}s): ").strip().lower()
        return response == "y"

    async def check(
        self,
        action: str,
        description: str,
        position_pct: float,
        leverage: int,
    ) -> bool:
        if not self._enabled:
            return True
        loop = asyncio.get_running_loop()
        try:
            result = await asyncio.wait_for(
                loop.run_in_executor(
                    _executor,
                    lambda: self.check_sync(
                        action, description, position_pct, leverage
                    ),
                ),
                timeout=self._timeout,
            )
            return result
        except asyncio.TimeoutError:
            logger.warning(f"Approval timed out after {self._timeout}s, skipping trade")
            if self._console is not None:
                self._console.print("[yellow]Approval timed out — trade skipped[/]")
            return False
```

Changes:
- Removed `from rich.console import Console` and module-level `console = Console()`
- `ApprovalGate.__init__` takes `console=None` parameter
- Internal `console.print()` calls use `self._console` with None guard

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd /Users/z/Z/TradeBot && python -m pytest tests/test_approval.py -v`
Expected: 4 passed

- [ ] **Step 5: Commit**

```bash
git add src/cli/approval.py tests/test_approval.py
git commit -m "refactor(r5): ApprovalGate takes console param, remove global console"
```

---

### Task 5: Fix okx.py Console usage

**Files:**
- Modify: `src/integrations/exchange/okx.py`

- [ ] **Step 1: Remove redundant Console().print**

In `src/integrations/exchange/okx.py`, replace lines 135-139:

```python
        except Exception:
            self._ws_connected = False
            logger.error("WebSocket connection failed, running in REST-only mode", exc_info=True)
            from rich.console import Console
            Console().print("[yellow]⚠ WebSocket connection failed, running in REST-only mode[/]")
```

with:

```python
        except Exception:
            self._ws_connected = False
            logger.error("WebSocket connection failed, running in REST-only mode", exc_info=True)
```

The `logger.error()` already writes to both `system.log` (all levels) and terminal (WARNING+) under the new logging config. Adding `logger.warning()` would duplicate the message. Just delete the `Console().print()` lines.

- [ ] **Step 2: Run existing OKX tests to verify no regression**

Run: `cd /Users/z/Z/TradeBot && python -m pytest tests/test_okx_websocket.py -v`
Expected: All existing tests PASS

- [ ] **Step 3: Commit**

```bash
git add src/integrations/exchange/okx.py
git commit -m "refactor(r5): okx.py remove redundant Console().print (logger.error suffices)"
```

---

### Task 6: Add --debug flag, update .gitignore

**Files:**
- Modify: `main.py`
- Modify: `.gitignore`

- [ ] **Step 1: Add --debug flag to main.py**

Replace `main.py` with:

```python
# main.py

import argparse
import asyncio

from src.cli.app import run

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="TradeBot")
    parser.add_argument("--model", type=str, default=None, help="Model ID from models.json (skip interactive selection)")
    parser.add_argument("--debug", action="store_true", help="Show all system logs on terminal")
    args = parser.parse_args()
    asyncio.run(run(model_id=args.model, debug=args.debug))
```

- [ ] **Step 2: Add logs/ to .gitignore**

Append to `.gitignore`:

```
logs/
```

- [ ] **Step 3: Commit**

```bash
git add main.py .gitignore
git commit -m "feat(r5): add --debug flag, gitignore logs/"
```

---

### Task 7: Restructure app.py to use new logging

This is the largest task — wiring the new logging into the existing `run()` function and `run_agent_cycle()`.

**Files:**
- Modify: `src/cli/app.py`

- [ ] **Step 1: Replace logging.basicConfig and module-level console**

In `src/cli/app.py`:

Remove the module-level `console` and its import (line 10 and 28):
```python
# DELETE: from rich.console import Console
# DELETE: console = Console()
```

Add import for new logging module:
```python
from src.cli.logging_config import setup_system_logging, setup_session_logging
```

- [ ] **Step 2: Add console parameter to run_agent_cycle**

Change `run_agent_cycle` signature (line 82) from:

```python
async def run_agent_cycle(
    agent,
    deps: TradingDeps,
    trigger_type: str,
    budget: TokenBudget,
    engine,
    context=None,
    model=None,
):
```

to:

```python
async def run_agent_cycle(
    agent,
    deps: TradingDeps,
    trigger_type: str,
    budget: TokenBudget,
    engine,
    context=None,
    model=None,
    console=None,
):
```

And change line 158 from:
```python
    console.print(f"\n[bold cyan]Agent:[/]\n{result.output}\n")
```
to:
```python
    if console is not None:
        console.print(f"\n[bold cyan]Agent:[/]\n{result.output}\n")
```

- [ ] **Step 3: Modify run() to use two-phase logging**

Replace the beginning of `run()` (lines 162-171) from:

```python
async def run(
    settings_path: Path = Path("config/settings.yaml"),
    trader_path: Path = Path("config/trader.yaml"),
    model_id: str | None = None,
):
    logging.basicConfig(
        level=logging.INFO,
        format="%(message)s",
        handlers=[RichHandler(console=console, rich_tracebacks=True)],
    )
```

to:

```python
async def run(
    settings_path: Path = Path("config/settings.yaml"),
    trader_path: Path = Path("config/trader.yaml"),
    model_id: str | None = None,
    debug: bool = False,
):
    # Phase 1: System logging (before session_id is known)
    log_dir = settings_path.resolve().parent.parent / "logs"
    pre_console = setup_system_logging(debug, log_dir)
```

- [ ] **Step 4: Replace all console.print calls in run() and _interactive_add_model**

All `console.print(...)` calls in `run()` (28 places) and `_interactive_add_model()` (3 places) must be migrated. They split into two groups:

**Before session_id is known (use `pre_console`)** — in `run()`:
- L173 (banner), L178-182 (config summary), L197 (model not found error), L200 (model selected), L202-205 (model list + "Add new"), L221 (no models), L227-228 (no model exit), L231-234 (connectivity test), L236-237 (test failed), L244 (model saved), L246 (model info)
Replace each `console.print(...)` with `pre_console.print(...)`.

**Also in `_interactive_add_model()`** — L440, L449, L463:
Add `console` parameter to function signature:
```python
async def _interactive_add_model(model_manager, existing_models, console):
```
Replace each `console.print(...)` inside to use the passed `console` parameter (it's already named `console`, so only the signature needs the parameter added).
Update both call sites in `run()` (L217, L222) to pass `pre_console`:
```python
selected_config, selected_model = await _interactive_add_model(
    model_manager, existing_models, pre_console
)
```

**After session_id is known (use `sc`)** — in `run()`:
Add `sc = setup_session_logging(session_id, log_dir)` after line 279 (where `session_id` is assigned).
- L289 (Exchange: simulated), L297 (Exchange: real), L339-368 (alert config), L421-425 (scheduler/budget info)
Replace each `console.print(...)` after that point with `sc.print(...)`.

- [ ] **Step 5: Wire console through display_metrics and ApprovalGate**

Change `display_metrics(metrics)` call (around line 419) to:
```python
display_metrics(metrics, console=sc)
```

Change `ApprovalGate(...)` construction (around line 303-306) to:
```python
approval_gate = ApprovalGate(
    enabled=settings.approval.enabled,
    timeout_seconds=settings.approval.timeout_seconds,
    console=sc,
)
```

- [ ] **Step 6: Wire console through on_tick callback to run_agent_cycle**

Change `on_tick` (around line 372-378) from:
```python
    async def on_tick(trigger_type: str, context=None):
        if shutdown_event.is_set():
            return
        try:
            await run_agent_cycle(agent, deps, trigger_type, budget, engine, context, model=selected_model)
```

to:
```python
    async def on_tick(trigger_type: str, context=None):
        if shutdown_event.is_set():
            return
        try:
            await run_agent_cycle(agent, deps, trigger_type, budget, engine, context, model=selected_model, console=sc)
```

- [ ] **Step 7: Wire console through _signal_handler**

Change `_signal_handler` (around line 326-328) from:
```python
    def _signal_handler():
        console.print("\n[yellow]Shutting down gracefully...[/]")
        shutdown_event.set()
```

to:
```python
    def _signal_handler():
        sc.print("\n[yellow]Shutting down gracefully...[/]")
        shutdown_event.set()
```

- [ ] **Step 8: Add sc.close() at shutdown**

After `await exchange.close()` (line 432), before the final print, add:
```python
    sc.close()
```

And change the final `console.print(...)` to:
```python
    pre_console.print("[green]TradeBot stopped.[/]")
```

- [ ] **Step 9: Remove unused imports**

Remove from the imports at the top of `app.py`:
```python
# DELETE: from rich.console import Console
# DELETE: from rich.logging import RichHandler
```

(These are now handled by `logging_config.py`. Keep `from rich.console import Console` only if `Console` is still referenced — it shouldn't be after migration.)

- [ ] **Step 10: Run full test suite**

Run: `cd /Users/z/Z/TradeBot && python -m pytest -v`
Expected: All 193+ tests pass (existing tests should not be broken; new `test_logging_config.py` tests also pass)

- [ ] **Step 11: Commit**

```bash
git add src/cli/app.py
git commit -m "refactor(r5): wire SessionConsole through app.py, two-phase logging"
```

---

### Task 8: Verify end-to-end and create PR branch

- [ ] **Step 1: Run full test suite one final time**

Run: `cd /Users/z/Z/TradeBot && python -m pytest -v`
Expected: All tests pass

- [ ] **Step 2: Verify logs/ directory is gitignored**

Run: `mkdir -p logs && touch logs/test.log && git status`
Expected: `logs/` does not appear in untracked files

- [ ] **Step 3: Clean up test file**

Run: `rm -rf logs/`

- [ ] **Step 4: Verify --debug flag is accepted**

Run: `cd /Users/z/Z/TradeBot && python main.py --help`
Expected: Output shows `--debug` option

- [ ] **Step 5: Push branch**

```bash
git push -u origin feature/r5-log-separation
```
