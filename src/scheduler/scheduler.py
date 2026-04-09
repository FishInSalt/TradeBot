from __future__ import annotations

import asyncio
import logging
from typing import Any, Awaitable, Callable

logger = logging.getLogger(__name__)


class Scheduler:
    def __init__(
        self,
        interval_seconds: float,
        callback: Callable[[str, Any | None], Awaitable[None]],
    ):
        self._interval = interval_seconds
        self._callback = callback
        self._running = False
        self._cycle_running = False
        self._pending_trigger = False
        self._pending_context: Any | None = None
        self._wake_event = asyncio.Event()

    async def trigger(self, trigger_type: str, context: Any | None = None) -> None:
        if self._pending_trigger:
            self._pending_context = None
        else:
            self._pending_trigger = True
            self._pending_context = context
        self._wake_event.set()

    async def start(self) -> None:
        self._running = True
        logger.info(f"Scheduler started (interval={self._interval}s)")

        await self._run_cycle("scheduled", None)

        while self._running:
            await self._interruptible_sleep(self._interval)
            if not self._running:
                break

            if self._pending_trigger:
                self._pending_trigger = False
                ctx = self._pending_context
                self._pending_context = None
                await self._run_cycle("conditional", ctx)
            else:
                await self._run_cycle("scheduled", None)

            if self._pending_trigger:
                self._pending_trigger = False
                self._pending_context = None
                await self._run_cycle("conditional", None)

    def stop(self) -> None:
        self._running = False
        self._wake_event.set()
        logger.info("Scheduler stopped")

    async def _run_cycle(self, trigger_type: str, context: Any | None) -> None:
        self._cycle_running = True
        try:
            await self._callback(trigger_type, context)
        except Exception:
            logger.exception("Agent cycle failed")
        finally:
            self._cycle_running = False

    async def _interruptible_sleep(self, duration: float) -> None:
        if self._pending_trigger:
            return
        self._wake_event.clear()
        try:
            await asyncio.wait_for(self._wake_event.wait(), timeout=duration)
        except asyncio.TimeoutError:
            pass
