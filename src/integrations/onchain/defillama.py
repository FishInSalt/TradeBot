"""DefiLlama stablecoins API client."""
from __future__ import annotations

import httpx

from src.utils.cache import RateLimitHit

_DEFILLAMA_URL = "https://stablecoins.llama.fi/stablecoins"


class DefiLlamaClient:
    """Fetches stablecoin supply snapshot from DefiLlama.

    Returns the raw `peggedAssets` list so the service layer can filter to
    the specific symbols it cares about. Response is ~250KB covering every
    tracked stablecoin across every chain; we only keep the top-level
    `circulating` / `circulatingPrevWeek` values (no auth, no rate limit).
    """

    def __init__(self, http: httpx.AsyncClient) -> None:
        self._http = http

    async def fetch_stablecoins(self) -> list[dict]:
        resp = await self._http.get(_DEFILLAMA_URL)
        if resp.status_code == 429:
            raise RateLimitHit("DefiLlama rate limited")
        resp.raise_for_status()
        return resp.json().get("peggedAssets", []) or []
