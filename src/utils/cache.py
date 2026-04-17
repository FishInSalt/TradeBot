from __future__ import annotations

import logging
import time
from collections.abc import Awaitable, Callable
from typing import Any

logger = logging.getLogger(__name__)


class RateLimitHit(Exception):
    """Raised when a fetch encounters HTTP 429 or equivalent rate limit."""


class TTLCache:
    """In-memory TTL cache with rate-limit-aware stale fallback.

    On RateLimitHit: if stale data exists, extend TTL to 30 min and return it.
    On other errors: let them propagate — caller decides how to degrade.
    """

    def __init__(self) -> None:
        self._store: dict[str, tuple[Any, float, float]] = {}  # key → (data, created_at, ttl)

    async def get_or_fetch(
        self,
        key: str,
        default_ttl: float,
        fetch_fn: Callable[[], Awaitable[Any]],
    ) -> Any:
        entry = self._store.get(key)
        if entry is not None:
            data, created_at, ttl = entry
            if time.monotonic() - created_at <= ttl:
                return data

        try:
            data = await fetch_fn()
        except RateLimitHit:
            if entry is not None:
                stale_data = entry[0]
                self._store[key] = (stale_data, time.monotonic(), 1800.0)
                logger.warning("Rate limited on key=%s, extending TTL to 30min", key)
                return stale_data
            raise

        self._store[key] = (data, time.monotonic(), default_ttl)
        return data

    def get_stale(self, key: str) -> Any | None:
        """Return cached data ignoring TTL, or None if key was never stored."""
        entry = self._store.get(key)
        return entry[0] if entry is not None else None
