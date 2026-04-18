"""FRED (Federal Reserve Economic Data) API client."""
from __future__ import annotations

import httpx

from src.integrations.macro.models import FREDObservation
from src.utils.cache import RateLimitHit

_FRED_URL = "https://api.stlouisfed.org/fred/series/observations"


class FREDClient:
    """Fetches the latest non-missing observation for a single FRED series.

    FRED returns "." for missing readings (e.g. holidays). We scan up to
    `limit=3` rows to find the first real value, so that DTWEXBGS's ~1-week
    report delay (spec §2.2) still yields a usable observation.
    """

    def __init__(self, http: httpx.AsyncClient, api_key: str) -> None:
        self._http = http
        self._api_key = api_key

    async def fetch_latest(self, series_id: str) -> FREDObservation | None:
        params = {
            "series_id": series_id,
            "api_key": self._api_key,
            "file_type": "json",
            "limit": 3,
            "sort_order": "desc",
        }
        resp = await self._http.get(_FRED_URL, params=params)
        if resp.status_code == 429:
            raise RateLimitHit(f"FRED rate limited for {series_id}")
        resp.raise_for_status()

        observations = resp.json().get("observations", [])
        for obs in observations:
            raw = obs.get("value")
            if raw in (None, "", "."):
                continue
            try:
                value = float(raw)
            except (TypeError, ValueError):
                continue
            date = obs.get("date", "")
            if not date:
                continue
            return FREDObservation(series_id=series_id, date=date, value=value)
        return None
