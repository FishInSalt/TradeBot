from __future__ import annotations

import logging

import httpx

from src.integrations.news.models import InformationEvent
from src.utils.cache import RateLimitHit

logger = logging.getLogger(__name__)

_OKX_ANNOUNCEMENTS_URL = "https://www.okx.com/api/v5/support/announcements"
_ANN_TYPES = ("announcements-delistings", "trading-updates-us-aus")


class OKXAnnouncementsClient:
    """OKX /support/announcements client — delistings + trading rule changes.

    Response schema (verified in Pre-work P4a): items are nested under
    `data[0].details[*]`, not `data[*]`. The flat layer is per-page metadata;
    the actual announcement items live in the `details` array.
    """

    def __init__(self, http: httpx.AsyncClient) -> None:
        self._http = http

    async def fetch(self) -> list[InformationEvent]:
        from datetime import datetime, timezone

        events: list[InformationEvent] = []
        for ann_type in _ANN_TYPES:
            resp = await self._http.get(_OKX_ANNOUNCEMENTS_URL, params={"annType": ann_type})
            if resp.status_code == 429:
                raise RateLimitHit("OKX announcements rate limited")
            resp.raise_for_status()

            data_arr = resp.json().get("data") or []
            if not data_arr or not isinstance(data_arr[0], dict):
                continue
            details = data_arr[0].get("details") or []

            for item in details:
                p_time = int(item.get("pTime") or 0)
                events.append(
                    InformationEvent(
                        timestamp=datetime.fromtimestamp(p_time / 1000, tz=timezone.utc),
                        source="okx_announcement",
                        category="announcement",
                        importance="high",
                        title=item.get("title", ""),
                        url=item.get("url", ""),
                    )
                )
        return events
