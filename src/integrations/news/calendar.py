from __future__ import annotations

import logging

import httpx

from src.integrations.news.models import InformationEvent
from src.utils.cache import RateLimitHit

logger = logging.getLogger(__name__)

_FOREXFACTORY_URL = "https://nfs.faireconomy.media/ff_calendar_thisweek.json"


class ForexFactoryClient:
    """ForexFactory economic calendar client (via faireconomy.media feed)."""

    def __init__(self, http: httpx.AsyncClient) -> None:
        self._http = http

    async def fetch_events(self) -> list[InformationEvent]:
        from datetime import datetime, timezone

        resp = await self._http.get(_FOREXFACTORY_URL)
        if resp.status_code == 429:
            raise RateLimitHit("ForexFactory rate limited")
        resp.raise_for_status()

        events: list[InformationEvent] = []
        for item in resp.json():
            if item.get("country") != "USD":
                continue
            impact = item.get("impact", "")
            if impact not in ("High", "Medium"):
                continue

            date_str = item.get("date", "")
            try:
                ts = datetime.fromisoformat(date_str)
                if ts.tzinfo is None:
                    ts = ts.replace(tzinfo=timezone.utc)
            except (ValueError, AttributeError):
                continue

            # spec §3.2 output always shows "Previous: X | Forecast: Y"; use
            # "N/A" when ForexFactory returns an empty string so the macro
            # event section's second line stays consistent for every event.
            previous = item.get("previous") or "N/A"
            forecast = item.get("forecast") or "N/A"
            content = f"Previous: {previous} | Forecast: {forecast}"

            events.append(
                InformationEvent(
                    timestamp=ts,
                    source="forexfactory",
                    category="macro_event",
                    importance="high" if impact == "High" else "medium",
                    title=item.get("title", ""),
                    content=content,
                )
            )
        return events
