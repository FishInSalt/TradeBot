# src/scheduler/scheduler.py
from __future__ import annotations

import asyncio
import heapq
import logging
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable

logger = logging.getLogger(__name__)


# Iter 7 (T2-2 PR-C): conditional > alert > scheduled。未知 trigger_type 同 alert 级。
# pre-next-observation §T2-2 — close fill conditional 不应被 stale alerts 在 FIFO 淹没。
_PRIORITY_MAP = {"conditional": 0, "alert": 1, "scheduled": 2}


@dataclass(order=True)
class _TriggerEvent:
    priority: int
    sequence: int
    trigger_type: str = field(compare=False)
    context: Any | None = field(default=None, compare=False)


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
        self._pending_events: list[_TriggerEvent] = []  # heap
        self._sequence_counter = 0
        self._wake_event = asyncio.Event()
        self._next_interval: float | None = None

    def set_next_interval(self, seconds: float) -> None:
        """Set a one-shot interval override for the next sleep."""
        self._next_interval = seconds

    async def trigger(self, trigger_type: str, context: Any | None = None) -> None:
        priority = _PRIORITY_MAP.get(trigger_type, 1)
        self._sequence_counter += 1
        heapq.heappush(
            self._pending_events,
            _TriggerEvent(priority, self._sequence_counter, trigger_type, context),
        )
        self._wake_event.set()

    async def start(self) -> None:
        self._running = True
        logger.info(f"Scheduler started (interval={self._interval}s)")

        await self._run_cycle("scheduled", None)

        while self._running:
            interval = self._next_interval if self._next_interval is not None else self._interval
            self._next_interval = None
            await self._interruptible_sleep(interval)
            if not self._running:
                break

            if self._pending_events:
                # 安全阀：单次最多 drain 10 个事件，防止 cycle 内产生的新事件导致无限循环
                for _ in range(min(len(self._pending_events), 10)):
                    if not self._running or not self._pending_events:
                        break
                    event = heapq.heappop(self._pending_events)
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
