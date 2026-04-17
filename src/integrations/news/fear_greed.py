from __future__ import annotations

import httpx

from src.integrations.news.models import InformationEvent
from src.utils.cache import RateLimitHit

_FGI_URL = "https://api.alternative.me/fng/"


class FearGreedClient:
    """Alternative.me Fear & Greed Index client."""

    def __init__(self, http: httpx.AsyncClient) -> None:
        self._http = http

    async def fetch(self) -> InformationEvent | None:
        from datetime import datetime, timezone

        resp = await self._http.get(_FGI_URL)
        if resp.status_code == 429:
            raise RateLimitHit("FGI rate limited")
        resp.raise_for_status()
        items = resp.json().get("data", [])
        if not items:
            return None

        item = items[0]
        # Defensive lookups — if alternative.me ever renames/drops these
        # fields, return None rather than raising a KeyError that makes
        # the whole FGI path appear "temporarily unavailable" to the Agent.
        value = item.get("value")
        classification = item.get("value_classification")
        if not value or not classification:
            return None

        raw_ts = item.get("timestamp")
        try:
            ts = datetime.fromtimestamp(int(raw_ts), tz=timezone.utc)
        except (TypeError, ValueError):
            # Matches coindesk.py's fallback: a bad timestamp shouldn't
            # discard the otherwise-valid FGI reading.
            ts = datetime.now(timezone.utc)
        return InformationEvent(
            timestamp=ts,
            source="alternative_me",
            category="fgi",
            importance="low",
            title=f"{value} / 100 — {classification}",
            content=classification,
        )
