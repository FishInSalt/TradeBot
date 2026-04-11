# src/scheduler/scheduler.py
from __future__ import annotations

import asyncio
import logging
from collections import deque
from dataclasses import dataclass
from typing import Any, Awaitable, Callable

logger = logging.getLogger(__name__)


@dataclass
class _TriggerEvent:
    trigger_type: str
    context: Any | None


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
        self._pending_events: deque[_TriggerEvent] = deque()
        self._wake_event = asyncio.Event()

    async def trigger(self, trigger_type: str, context: Any | None = None) -> None:
        self._pending_events.append(_TriggerEvent(trigger_type, context))
        self._wake_event.set()

    async def start(self) -> None:
        self._running = True
        logger.info(f"Scheduler started (interval={self._interval}s)")

        await self._run_cycle("scheduled", None)

        while self._running:
            await self._interruptible_sleep(self._interval)
            if not self._running:
                break

            if self._pending_events:
                # 安全阀：单次最多 drain 10 个事件，防止 cycle 内产生的新事件导致无限循环
                for _ in range(min(len(self._pending_events), 10)):
                    if not self._running or not self._pending_events:
                        break
                    event = self._pending_events.popleft()
                    await self._run_cycle(event.trigger_type, event.context)
            else:
                await self._run_cycle("scheduled", None)

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
        if self._pending_events:
            return
        self._wake_event.clear()
        try:
            await asyncio.wait_for(self._wake_event.wait(), timeout=duration)
        except asyncio.TimeoutError:
            pass
