# src/integrations/macro/service.py
"""MacroService — aggregates CoinGecko /global + FRED + Alpha Vantage."""
from __future__ import annotations

import asyncio
import logging
from typing import Any

import httpx

from src.integrations.macro.alpha_vantage import (
    AlphaVantageClient, alpha_vantage_ttl_seconds,
)
from src.integrations.macro.cg_global import CoinGeckoGlobalClient
from src.integrations.macro.fred import FREDClient
from src.integrations.macro.models import (
    EquityQuote, FREDObservation, MacroSnapshot,
)
from src.utils.cache import RateLimitHit, TTLCache

logger = logging.getLogger(__name__)

# Cache TTLs (seconds), spec §2.1 / §2.2
_CG_TTL = 900.0       # 15 min — crypto market 24/7
_FRED_TTL = 21600.0   # 6 h — daily-granularity series

_FRED_SERIES = ("DTWEXBGS", "VIXCLS", "DGS10", "T10Y2Y", "T10YIE")


class MacroService:
    """Aggregates 3 sub-sources with per-source caching and independent
    degradation (spec §3.2). Each sub-source failure yields None for the
    corresponding MacroSnapshot field(s); the other fields are unaffected.
    """

    def __init__(
        self,
        fred_key: str,
        av_key: str,
        cg_key: str,
        http: httpx.AsyncClient | None = None,
    ) -> None:
        # http=None  → we create and own the client; close() will aclose it.
        # http=client → caller injected it (typical for tests), caller owns
        #   lifecycle. Mirrors NewsService's convention. See spec §3.5 and
        #   src/integrations/news/service.py:36 for the canonical pattern.
        self._http = http if http is not None else httpx.AsyncClient(timeout=5.0)
        self._owns_http = http is None
        self._cache = TTLCache()

        self._cg = CoinGeckoGlobalClient(self._http, cg_key)
        self._fred = FREDClient(self._http, fred_key)
        self._av = AlphaVantageClient(self._http, av_key)

    async def get_snapshot(self) -> MacroSnapshot:
        cg_data, fred_data, av_data = await asyncio.gather(
            self._fetch_cg(),
            self._fetch_fred_all(),
            self._fetch_av_all(),
        )
        return MacroSnapshot(
            btc_dominance=cg_data.get("btc_dominance"),
            eth_dominance=cg_data.get("eth_dominance"),
            total_mcap_usd=cg_data.get("total_mcap_usd"),
            mcap_change_24h_pct=cg_data.get("mcap_change_24h_pct"),
            usd_index_broad_tw=fred_data.get("DTWEXBGS"),
            vix=fred_data.get("VIXCLS"),
            treasury_10y=fred_data.get("DGS10"),
            spread_10y_2y=fred_data.get("T10Y2Y"),
            inflation_10y=fred_data.get("T10YIE"),
            spy=av_data.get("SPY"),
            qqq=av_data.get("QQQ"),
        )

    async def _fetch_cg(self) -> dict[str, Any]:
        """CG source; returns dict-of-None on any failure."""
        empty = {
            "btc_dominance": None, "eth_dominance": None,
            "total_mcap_usd": None, "mcap_change_24h_pct": None,
        }
        try:
            return await self._cache.get_or_fetch(
                "cg:global", _CG_TTL, self._cg.fetch_global,
            )
        except RateLimitHit:
            logger.warning("CoinGecko /global rate limited, no stale cache")
            return empty
        except Exception:
            logger.warning("CoinGecko /global fetch failed", exc_info=True)
            return empty

    async def _fetch_fred_all(self) -> dict[str, FREDObservation | None]:
        """5 FRED series in parallel — per-series degradation."""
        results = await asyncio.gather(
            *[
                self._fetch_fred_one(s)
                for s in _FRED_SERIES
            ]
        )
        return dict(zip(_FRED_SERIES, results))

    async def _fetch_fred_one(self, series_id: str) -> FREDObservation | None:
        try:
            return await self._cache.get_or_fetch(
                f"fred:{series_id}", _FRED_TTL,
                lambda sid=series_id: self._fred.fetch_latest(sid),
            )
        except RateLimitHit:
            logger.warning("FRED rate limited for %s, no stale cache", series_id)
            return None
        except Exception:
            logger.warning("FRED fetch failed for %s", series_id, exc_info=True)
            return None

    async def _fetch_av_all(self) -> dict[str, EquityQuote | None]:
        """SPY + QQQ serially (1 req/sec limit). Per-symbol TTL picked at
        call time — weekend/after-hours caches live longer (spec §5.2)."""
        result: dict[str, EquityQuote | None] = {}
        for sym in ("SPY", "QQQ"):
            ttl = alpha_vantage_ttl_seconds()
            try:
                result[sym] = await self._cache.get_or_fetch(
                    f"av:{sym}", ttl,
                    lambda s=sym: self._av.fetch_quote(s),
                )
            except RateLimitHit:
                logger.warning("AV soft rate-limited for %s, no stale cache", sym)
                result[sym] = None
            except Exception:
                logger.warning("AV fetch failed for %s", sym, exc_info=True)
                result[sym] = None
        return result

    async def close(self) -> None:
        if self._owns_http:
            await self._http.aclose()
