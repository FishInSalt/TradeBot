# src/integrations/crypto_etf/service.py
"""CryptoEtfService — fetches + computes daily ETF flows via cum-delta."""
from __future__ import annotations

import logging
from typing import Any

import httpx

from src.integrations.crypto_etf.models import ETFFlowEntry
from src.integrations.crypto_etf.sosovalue import SoSoValueClient
from src.utils.cache import RateLimitHit, TTLCache

logger = logging.getLogger(__name__)

_ETF_TTL = 14400.0  # 4 hours (spec §2.4)


class CryptoEtfService:
    """Aggregates SoSoValue ETF flow data with TTLCache + cum-delta algorithm.

    Supports BTC + ETH. Exposes `get_etf_flows(symbol, days)` returning a
    list of N daily ETFFlowEntry rows (most recent first), or None on outage.
    """

    def __init__(
        self,
        api_key: str,
        http: httpx.AsyncClient | None = None,
    ) -> None:
        self._http = http if http is not None else httpx.AsyncClient(timeout=5.0)
        self._owns_http = http is None
        self._cache = TTLCache()
        self._client = SoSoValueClient(self._http, api_key)

    async def get_etf_flows(
        self, symbol: str, days: int = 7,
    ) -> list[ETFFlowEntry] | None:
        """Compute daily flows from cumulative inflows (multi-row safe).

        SoSoValue returns multiple rows per date (Friday + most recent
        unsettled day). Smoke-test verified cum_net_inflow and
        total_net_assets are cross-row identical; only total_net_inflow
        differs. Computing delta of cum_net_inflow gives the canonical
        daily flow without depending on row ordering.
        """
        days = max(1, min(days, 14))  # clamp (spec §3.3)

        try:
            raw: list[dict] = await self._cache.get_or_fetch(
                f"etf:{symbol}", _ETF_TTL,
                lambda s=symbol: self._client.fetch_summary_history(s),
            )
        except RateLimitHit:
            logger.warning("SoSoValue rate limited for %s, no stale cache", symbol)
            return None
        except Exception:
            logger.warning("SoSoValue fetch failed for %s", symbol, exc_info=True)
            return None

        # A genuinely empty response is not a short-window problem — SoSoValue
        # has continuous BTC/ETH ETF history since the Jan-2024 launch, so an
        # empty `data` array almost always signals schema drift, a silent 401,
        # or upstream outage rather than "the requested window lacks history".
        # Return None (outage) rather than [] (data-gap) so the tool layer
        # renders "temporarily unavailable", not a reassuring "insufficient data".
        if not raw:
            logger.warning(
                "SoSoValue returned empty response for %s — likely schema drift "
                "or silent upstream failure", symbol,
            )
            return None

        # Dedup by date — first occurrence wins. cum_net_inflow AND
        # total_net_assets are cross-row identical for same-date rows
        # (smoke-test verified), so first occurrence reflects the canonical
        # EOD values.
        seen: dict[str, dict] = {}
        for r in raw:
            date = r.get("date")
            if not date:
                continue
            seen.setdefault(date, r)

        # Descending order by date (most recent first).
        sorted_desc = sorted(seen.values(), key=lambda x: x["date"], reverse=True)

        # Need days+1 distinct dates to compute `days` deltas. This is a
        # data-gap, NOT an outage — the upstream call succeeded; the window
        # just doesn't have enough history yet. Per spec §3.5 three-state
        # contract, return [] (empty container = data-gap) rather than None
        # (reserved for source unavailability), so the tool layer can
        # render a distinct message to the agent.
        if len(sorted_desc) < days + 1:
            logger.info(
                "Insufficient data for %s ETF flows: %d rows, need %d",
                symbol, len(sorted_desc), days + 1,
            )
            return []

        flows: list[ETFFlowEntry] = []
        try:
            for i in range(days):
                today = sorted_desc[i]
                yest = sorted_desc[i + 1]
                flows.append(ETFFlowEntry(
                    date=today["date"],
                    net_inflow_usd=float(today["cum_net_inflow"])
                                   - float(yest["cum_net_inflow"]),
                    cumulative_usd=float(today["cum_net_inflow"]),
                    aum_usd=float(today["total_net_assets"]),
                ))
        except (TypeError, ValueError, KeyError):
            logger.warning(
                "Malformed %s ETF row — missing/non-numeric field", symbol,
                exc_info=True,
            )
            return None
        return flows

    async def close(self) -> None:
        if self._owns_http:
            await self._http.aclose()
