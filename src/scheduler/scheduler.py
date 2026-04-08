from __future__ import annotations

import asyncio
import logging
import time
from typing import Awaitable, Callable

logger = logging.getLogger(__name__)


class Scheduler:
    def __init__(
        self,
        interval_seconds: float,
        cooldown_seconds: float,
        callback: Callable[[], Awaitable[None]],
    ):
        self._interval = interval_seconds
        self._cooldown = cooldown_seconds
        self._callback = callback
        self._running = False
        self._last_run: float = 0
        self._stop_event: asyncio.Event | None = None

    async def start(self) -> None:
        self._running = True
        self._stop_event = asyncio.Event()
        logger.info(f"Scheduler started (interval={self._interval}s, cooldown={self._cooldown}s)")
        while self._running:
            now = time.monotonic()
            since_last = now - self._last_run
            if since_last < self._cooldown and self._last_run > 0:
                try:
                    await asyncio.wait_for(self._stop_event.wait(), timeout=self._cooldown - since_last)
                    break
                except asyncio.TimeoutError:
                    pass
                if not self._running:
                    break

            try:
                self._last_run = time.monotonic()
                await self._callback()
            except Exception:
                logger.exception("Scheduler callback error")

            if not self._running:
                break

            try:
                await asyncio.wait_for(self._stop_event.wait(), timeout=self._interval)
                break
            except asyncio.TimeoutError:
                pass

    def stop(self) -> None:
        self._running = False
        if self._stop_event:
            self._stop_event.set()
        logger.info("Scheduler stopped")
