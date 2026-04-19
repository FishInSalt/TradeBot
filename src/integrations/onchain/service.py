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

        # === Phase 1: normalize + first-occurrence dedup ===
        # Original `{a.get("symbol"): a for a in raw if a.get("symbol")}` had
        # two gaps: case/whitespace sensitivity, and silent overwrite when
        # multiple rows share a symbol. Fix: strip+upper, keep first-
        # occurrence, emit schema-drift WARN on duplicates within tracked
        # symbols. Untracked symbols skip silently to avoid log noise.
        #
        # IMPORTANT: DefiLlama top-level `circulating` is already
        # across-every-chain (see defillama.py:16-17). Multi-row same-symbol
        # should be treated as schema drift (e.g., if DefiLlama splits into
        # per-chain rows), NOT summed — summing would double-count under the
        # current schema.
        by_sym: dict[str, dict] = {}
        seen_duplicates: set[str] = set()
        for asset in raw:
            sym_raw = asset.get("symbol")
            if not sym_raw:
                continue
            sym = sym_raw.strip().upper()
            if sym not in _TRACKED_SYMBOLS:
                continue
            if sym in by_sym:
                seen_duplicates.add(sym)
                continue  # first occurrence wins
            by_sym[sym] = asset
        if seen_duplicates:
            logger.warning(
                "DefiLlama schema drift: multiple rows for symbol(s) %s; "
                "using first occurrence. Review if aggregation semantics changed.",
                ", ".join(sorted(seen_duplicates)),
            )

        # === Phase 2: extract per-symbol + build totals ===
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
