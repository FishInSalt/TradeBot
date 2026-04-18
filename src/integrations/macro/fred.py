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
        if resp.is_error:
            # Don't use raise_for_status — httpx's default HTTPStatusError
            # message includes the full request URL, which here contains the
            # api_key query param. `exc_info=True` in the service layer would
            # then serialize the key into application logs.
            # NOTE (API key leakage boundary): `str(exc)` is sanitized, so
            # Python-stdlib traceback formatting is safe. `exc.request.url`
            # and `exc.response.request.url` still reference the original
            # URL with the api_key — if this project ever integrates Sentry /
            # Datadog / other APM that walks exception attributes, configure
            # their URL/query-string scrubber to redact `api_key=`.
            raise httpx.HTTPStatusError(
                f"FRED returned HTTP {resp.status_code} for series {series_id}",
                request=resp.request,
                response=resp,
            ) from None

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
