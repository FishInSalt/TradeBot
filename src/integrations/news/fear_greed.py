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
        value = item["value"]
        classification = item["value_classification"]
        raw_ts = item.get("timestamp")
        ts = (
            datetime.fromtimestamp(int(raw_ts), tz=timezone.utc)
            if raw_ts
            else datetime.now(timezone.utc)
        )
        return InformationEvent(
            timestamp=ts,
            source="alternative_me",
            category="fgi",
            importance="low",
            title=f"{value} / 100 — {classification}",
            content=classification,
        )
