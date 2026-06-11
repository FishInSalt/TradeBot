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


def _type_counts(events: list[tuple[str, Any]]) -> str:
    """Compact `type:count` summary for the drain-cap WARNING, e.g. 'alert:18 conditional:2'."""
    counts: dict[str, int] = {}
    for trigger_type, _ in events:
        counts[trigger_type] = counts.get(trigger_type, 0) + 1
    return " ".join(f"{k}:{v}" for k, v in sorted(counts.items()))


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
        callback: Callable[[list[tuple[str, Any]]], Awaitable[None]],
    ):
        self._interval = interval_seconds
        self._callback = callback
        self._running = False
        self._cycle_running = False
        self._pending_events: list[_TriggerEvent] = []  # heap
        self._sequence_counter = 0
        self._wake_event = asyncio.Event()
        self._next_interval: float | None = None
        self._next_wake_context: str | None = None

    def set_next_interval(self, seconds: float, context: str | None = None) -> None:
        """Set a one-shot interval override for the next sleep.

        `context` (the agent's set_next_wake reasoning) rides the same one-shot
        lifecycle as the interval: it is carried into the timer-driven scheduled
        fire and consumed-and-cleared each cycle, so a preempted wake never leaks
        its context into a later fire (spec 2026-06-11).
        """
        self._next_interval = seconds
        self._next_wake_context = context

    async def trigger(self, trigger_type: str, context: Any | None = None) -> None:
        priority = _PRIORITY_MAP.get(trigger_type, 1)
        self._sequence_counter += 1
        heapq.heappush(
            self._pending_events,
            _TriggerEvent(priority, self._sequence_counter, trigger_type, context),
        )
        self._wake_event.set()

    def drain_pending_events(self) -> list[tuple[str, Any]]:
        """Pop ALL pending events in heap priority order. Used by mid-cycle injection
        (spec 2026-06-11 iter-midcycle-event-injection §1).

        Sync by design: heap ops have no await point (same asyncio loop, no race
        surface), and the injector's failure path must requeue without spawning a
        coroutine. Does NOT touch _wake_event: after an injection drain the heap is
        empty, so _interruptible_sleep's pending check won't fire and clear()
        precedes wait() — a leftover set() never produces a spurious wake.

        The >5 WARNING is a pure observation signal (slightly above sim #17's
        observed mid-cycle batch peak of 3), not a tuning knob; distinct from the
        main-loop drain cap-20 window (historical wake-batch peak 4).
        """
        events: list[tuple[str, Any]] = []
        while self._pending_events:
            ev = heapq.heappop(self._pending_events)
            events.append((ev.trigger_type, ev.context))
        if len(events) > 5:
            logger.warning(
                "mid-cycle drain: %d events in one injection batch (types=%s)",
                len(events), _type_counts(events),
            )
        return events

    def requeue_events(self, events: list[tuple[str, Any]]) -> None:
        """Push events back onto the heap — injection-failure rollback handle
        (spec §1/§2). Delivery degrades to the wake fallback channel, never drops.

        Sequence numbers are re-assigned: same-batch relative order is preserved;
        cross-batch global FIFO is not guaranteed (heap consumption is by priority
        anyway). Sets _wake_event so a main loop already sleeping re-checks the heap.
        """
        for trigger_type, context in events:
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

        await self._run_cycle([("scheduled", None)])

        while self._running:
            interval = self._next_interval if self._next_interval is not None else self._interval
            self._next_interval = None
            wake_ctx = self._next_wake_context   # consume-and-clear, symmetric with _next_interval
            self._next_wake_context = None
            await self._interruptible_sleep(interval)
            if not self._running:
                break

            if self._pending_events:
                events: list[tuple[str, Any]] = []
                while self._pending_events and len(events) < 20:   # cap 20 = guard threshold (5× observed max 4), not a tuning knob; defer+warn, never drop
                    ev = heapq.heappop(self._pending_events)   # heap already priority-ordered
                    events.append((ev.trigger_type, ev.context))
                deferred = len(self._pending_events)           # leftover == post-drain heap depth
                if deferred > 0:                               # ⟺ started with strictly >20
                    logger.warning(
                        "event drain capped: drained=%d deferred=%d total=%d types=%s",
                        len(events), deferred, len(events) + deferred, _type_counts(events),
                    )
                await self._run_cycle(events)                  # ONE cycle consumes the batch
            else:
                await self._run_cycle([("scheduled", wake_ctx)])

    def stop(self) -> None:
        self._running = False
        self._wake_event.set()
        logger.info("Scheduler stopped")

    async def _run_cycle(self, events: list[tuple[str, Any]]) -> None:
        self._cycle_running = True
        try:
            await self._callback(events)
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
