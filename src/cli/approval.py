from __future__ import annotations

import asyncio
import logging
from concurrent.futures import ThreadPoolExecutor

from rich.console import Console
from rich.panel import Panel

logger = logging.getLogger(__name__)
console = Console()
_executor = ThreadPoolExecutor(max_workers=1)


def format_decision_for_approval(
    action: str,
    reasoning: str,
    position_pct: float,
    leverage: int,
    stop_loss: float | None = None,
    take_profit: float | None = None,
) -> str:
    lines = [
        f"Action: {action.upper()}",
        f"Position: {position_pct}% of balance | Leverage: {leverage}x",
    ]
    if stop_loss:
        lines.append(f"Stop Loss: {stop_loss:.2f}")
    if take_profit:
        lines.append(f"Take Profit: {take_profit:.2f}")
    lines.append(f"\nReasoning: {reasoning}")
    return "\n".join(lines)


class ApprovalGate:
    def __init__(self, enabled: bool = True, timeout_seconds: int = 300):
        self._enabled = enabled
        self._timeout = timeout_seconds

    def check_sync(
        self,
        action: str,
        reasoning: str,
        position_pct: float,
        leverage: int,
        stop_loss: float | None = None,
        take_profit: float | None = None,
    ) -> bool:
        if not self._enabled:
            return True
        text = format_decision_for_approval(
            action, reasoning, position_pct, leverage, stop_loss, take_profit
        )
        console.print(
            Panel(text, title="[bold yellow]Trade Approval Required[/]", border_style="yellow")
        )
        response = input(f"Approve? (y/n, timeout {self._timeout}s): ").strip().lower()
        return response == "y"

    async def check(
        self,
        action: str,
        reasoning: str,
        position_pct: float,
        leverage: int,
        stop_loss: float | None = None,
        take_profit: float | None = None,
    ) -> bool:
        if not self._enabled:
            return True
        loop = asyncio.get_event_loop()
        try:
            result = await asyncio.wait_for(
                loop.run_in_executor(
                    _executor,
                    lambda: self.check_sync(
                        action, reasoning, position_pct, leverage, stop_loss, take_profit
                    ),
                ),
                timeout=self._timeout,
            )
            return result
        except asyncio.TimeoutError:
            logger.warning(f"Approval timed out after {self._timeout}s, skipping trade")
            console.print("[yellow]Approval timed out — trade skipped[/]")
            return False
