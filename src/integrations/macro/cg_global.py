# src/integrations/macro/cg_global.py
"""CoinGecko /global API client — crypto market overview."""
from __future__ import annotations

import httpx

from src.utils.cache import RateLimitHit

_CG_GLOBAL_URL = "https://api.coingecko.com/api/v3/global"


class CoinGeckoGlobalClient:
    """Fetches crypto market totals: BTC/ETH dominance, total mcap, 24h change.

    Requires a Demo-tier API key (30 req/min) passed in the
    `x-cg-demo-api-key` header. Spec §2.1 explains why keyless access is
    insufficient (5-15 req/min unstable cap).

    Returns a dict of primitives rather than a dataclass so MacroService can
    assemble the final MacroSnapshot without a per-source boilerplate type.
    """

    def __init__(self, http: httpx.AsyncClient, api_key: str) -> None:
        self._http = http
        self._api_key = api_key

    async def fetch_global(self) -> dict[str, float | None]:
        headers = {"x-cg-demo-api-key": self._api_key}
        resp = await self._http.get(_CG_GLOBAL_URL, headers=headers)
        if resp.status_code == 429:
            raise RateLimitHit("CoinGecko rate limited")
        resp.raise_for_status()

        # `or {}` guards against top-level `{"data": null}` — .get() returns
        # None (not the default) when the key exists with a null value, which
        # would then AttributeError on the nested .get() calls below. Same
        # idiom as sosovalue.py and defillama.py.
        data = resp.json().get("data") or {}
        mcap_pct = data.get("market_cap_percentage", {}) or {}
        total_mcap = data.get("total_market_cap", {}) or {}

        # Each field may be missing if CG adjusts schema. None-per-field
        # keeps the downstream degradation path per-field rather than
        # per-source (spec §3.2 sub-source independence).
        return {
            "btc_dominance": mcap_pct.get("btc"),
            "eth_dominance": mcap_pct.get("eth"),
            "total_mcap_usd": total_mcap.get("usd"),
            "mcap_change_24h_pct": data.get("market_cap_change_percentage_24h_usd"),
        }
