"""OnchainService — aggregates DefiLlama stablecoin supply."""
from __future__ import annotations

import logging
from typing import TypedDict

import httpx

from src.integrations.onchain.defillama import DefiLlamaClient
from src.integrations.onchain.models import StablecoinSnapshot, StablecoinTotal
from src.utils.cache import RateLimitHit, TTLCache

logger = logging.getLogger(__name__)

_STABLECOIN_TTL = 21600.0  # 6 hours (spec §2.5)
_TRACKED_SYMBOLS = ("USDT", "USDC")


class StablecoinResult(TypedDict):
    coins: list[StablecoinSnapshot]
    total: StablecoinTotal


class OnchainService:
    """Fetches stablecoin snapshot: per-symbol + aggregate."""

    def __init__(
        self,
        http: httpx.AsyncClient | None = None,
    ) -> None:
        self._http = http if http is not None else httpx.AsyncClient(timeout=5.0)
        self._owns_http = http is None
        self._cache = TTLCache()
        self._client = DefiLlamaClient(self._http)

    async def get_stablecoin_snapshot(self) -> StablecoinResult | None:
        try:
            raw = await self._cache.get_or_fetch(
                "defillama:stablecoins", _STABLECOIN_TTL,
                self._client.fetch_stablecoins,
            )
        except RateLimitHit:
            logger.warning("DefiLlama rate limited, no stale cache")
            return None
        except Exception:
            logger.warning("DefiLlama fetch failed", exc_info=True)
            return None

        by_sym = {a.get("symbol"): a for a in raw if a.get("symbol")}

        coins: list[StablecoinSnapshot] = []
        total_circ = 0.0
        total_prev = 0.0
        for sym in _TRACKED_SYMBOLS:
            asset = by_sym.get(sym)
            if asset is None:
                continue
            circulating = float(
                (asset.get("circulating") or {}).get("peggedUSD", 0.0)
            )
            prev_week = float(
                (asset.get("circulatingPrevWeek") or {}).get("peggedUSD", 0.0)
            )
            delta = circulating - prev_week
            pct = (delta / prev_week * 100.0) if prev_week > 0 else 0.0
            coins.append(StablecoinSnapshot(
                symbol=sym,
                circulating_usd=circulating,
                change_7d_usd=delta,
                change_7d_pct=pct,
            ))
            total_circ += circulating
            total_prev += prev_week

        total_delta = total_circ - total_prev
        total_pct = (total_delta / total_prev * 100.0) if total_prev > 0 else 0.0
        total = StablecoinTotal(
            total_circulating_usd=total_circ,
            total_change_7d_usd=total_delta,
            total_change_7d_pct=total_pct,
        )

        return {"coins": coins, "total": total}

    async def close(self) -> None:
        if self._owns_http:
            await self._http.aclose()
