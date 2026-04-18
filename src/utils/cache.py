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
        # Caller must not invoke get_or_fetch concurrently for the same key:
        # inflight requests are not de-duplicated, so two concurrent misses
        # will each run fetch_fn. Safe under pydantic-ai's serial tool execution.
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

    def invalidate_stale(self, key: str, max_age: float) -> None:
        """Drop the cached entry for `key` if it is older than `max_age` seconds.

        Callers whose TTL varies across calls (e.g. time-of-day caching) use
        this to prevent a long-TTL write from sticking past a state transition
        that reduces the acceptable age. Example: Alpha Vantage SPY/QQQ write
        on Sunday with TTL=12h would otherwise survive through Monday 9:30-11
        market open, serving Friday close during active trading.

        Tradeoff: after invalidation, a subsequent `get_or_fetch` that hits
        RateLimitHit cannot fall back to the (just-dropped) stale data — the
        exception propagates instead. This is intentional: past a TTL state
        transition, stale data is no longer considered an acceptable answer;
        callers must either re-fetch successfully or degrade to None.
        """
        entry = self._store.get(key)
        if entry is not None and time.monotonic() - entry[1] > max_age:
            self._store.pop(key, None)
