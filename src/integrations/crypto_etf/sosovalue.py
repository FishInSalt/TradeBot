"""SoSoValue ETF summary-history API client."""
from __future__ import annotations

import httpx

from src.utils.cache import RateLimitHit

_SOSOVALUE_URL = "https://openapi.sosovalue.com/openapi/v1/etfs/summary-history"


class SoSoValueClient:
    """Fetches per-day ETF summary rows from SoSoValue.

    Returns raw rows (list[dict]); the service layer handles the multi-row
    dedup + cum-delta computation (spec §5.3).

    Auth header is `x-soso-api-key` (lowercase, hyphenated). Other common
    spellings (X-API-KEY, Bearer) return 401 — confirmed in spec §2.4.
    """

    def __init__(self, http: httpx.AsyncClient, api_key: str) -> None:
        self._http = http
        self._api_key = api_key

    async def fetch_summary_history(self, symbol: str) -> list[dict]:
        headers = {"x-soso-api-key": self._api_key}
        params = {"symbol": symbol, "country_code": "US"}
        resp = await self._http.get(_SOSOVALUE_URL, params=params, headers=headers)
        if resp.status_code == 429:
            raise RateLimitHit(f"SoSoValue rate limited for {symbol}")
        resp.raise_for_status()
        return resp.json().get("data", []) or []
