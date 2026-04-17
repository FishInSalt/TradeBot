from __future__ import annotations

import logging

import httpx

from src.integrations.news.models import InformationEvent
from src.utils.cache import RateLimitHit

logger = logging.getLogger(__name__)

_OKX_STATUS_URL = "https://www.okx.com/api/v5/system/status"


class OKXStatusClient:
    """OKX /system/status client — scheduled maintenance + ongoing incidents.

    Pre-work P4b left the response schema unconfirmed (live probe returned
    empty arrays). `_extract_items` handles both layouts so the client stays
    correct regardless of which one P4b ultimately reveals:
      - flat    → `data[*]`                  (current spec assumption)
      - nested  → `data[0].details[*]`       (same shape as /support/announcements)

    If P4b probe (state=completed) confirms one layout, the branch for the
    other stays as cheap defense; no plan update needed.
    """

    def __init__(self, http: httpx.AsyncClient) -> None:
        self._http = http

    async def fetch(self) -> list[InformationEvent]:
        from datetime import datetime, timezone

        # Use fetch time as the observation timestamp so these pass
        # NewsService's lookback filter (past N hours = recently observed).
        # The actual maintenance begin/end goes into the title for display.
        now = datetime.now(timezone.utc)

        events: list[InformationEvent] = []
        for state in ("scheduled", "ongoing"):
            resp = await self._http.get(_OKX_STATUS_URL, params={"state": state})
            if resp.status_code == 429:
                raise RateLimitHit("OKX status rate limited")
            resp.raise_for_status()

            for item in self._extract_items(resp.json()):
                begin_ms = int(item.get("begin", 0))
                end_ms = int(item.get("end", 0))
                begin_dt = datetime.fromtimestamp(begin_ms / 1000, tz=timezone.utc)
                end_dt = datetime.fromtimestamp(end_ms / 1000, tz=timezone.utc)
                title_raw = item.get("title", "")
                title = (
                    f"{title_raw} "
                    f"{begin_dt.strftime('%Y-%m-%d %H:%M')}-"
                    f"{end_dt.strftime('%H:%M')} UTC"
                )
                events.append(
                    InformationEvent(
                        timestamp=now,
                        source="okx_status",
                        category="maintenance",
                        importance="high",
                        title=title,
                    )
                )
        return events

    @staticmethod
    def _extract_items(body: dict) -> list[dict]:
        """Accept both flat `data[*]` and nested `data[0].details[*]` layouts.

        Detection rule: if the first `data` element is a dict whose `details`
        value is a list, treat it as the nested per-page wrapper (same shape
        OKX uses for /support/announcements). Otherwise treat the array as
        flat maintenance items. This keeps the client resilient whether or
        not Pre-work P4b confirms nesting.
        """
        data = body.get("data") or []
        if data and isinstance(data[0], dict) and isinstance(data[0].get("details"), list):
            return data[0]["details"]
        return [item for item in data if isinstance(item, dict)]
