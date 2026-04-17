from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone

import httpx

from src.integrations.news.calendar import ForexFactoryClient
from src.integrations.news.coindesk import CoinDeskNewsClient
from src.integrations.news.fear_greed import FearGreedClient
from src.integrations.news.models import InformationEvent, extract_base_currency
from src.integrations.news.okx_announcements import OKXAnnouncementsClient
from src.integrations.news.okx_status import OKXStatusClient
from src.utils.cache import RateLimitHit, TTLCache

logger = logging.getLogger(__name__)

# Cache TTLs (seconds)
_NEWS_TTL = 900.0  # 15 min
_FGI_TTL = 21600.0  # 6 hours
_CALENDAR_TTL = 21600.0  # 6 hours
_OKX_TTL = 600.0  # 10 min


class NewsService:
    """Aggregates all news/alert data sources with caching.

    All upstream sources are keyless (CoinDesk News, FGI, ForexFactory, OKX).
    No quota tracking — if a source returns HTTP 429, TTLCache serves stale
    data if present; otherwise the get_* method returns an empty result.
    """

    def __init__(
        self,
        http: httpx.AsyncClient | None = None,
    ) -> None:
        # Accept injected http client for testability; default to real one otherwise.
        self._http = http if http is not None else httpx.AsyncClient(timeout=5.0)
        self._owns_http = http is None  # only close http if we created it
        self._cache = TTLCache()

        # Clients (all keyless)
        self._news = CoinDeskNewsClient(self._http)
        self._fgi = FearGreedClient(self._http)
        self._calendar = ForexFactoryClient(self._http)
        self._announcements = OKXAnnouncementsClient(self._http)
        self._status = OKXStatusClient(self._http)

    async def get_news(
        self,
        symbol: str,
        news_filter: str | None = None,
        max_per_group: int = 5,
    ) -> tuple[list[InformationEvent], list[InformationEvent]] | None:
        """Fetch news headlines, split into (symbol_news, general_news).

        Returns two lists: symbol-specific headlines and general crypto news.
        If symbol_news < max_per_group, general_news gets extra slots (total = max_per_group * 2).

        Returns None when the upstream (CoinDesk) errored and there is no
        stale cache to fall back on — mirrors the None-on-outage contract
        used by get_announcements / get_macro_events (spec §3.5).
        """
        cache_key = f"news:{news_filter}"

        try:
            all_posts = await self._cache.get_or_fetch(
                cache_key, _NEWS_TTL,
                lambda: self._news.fetch_posts(news_filter),
            )
        except RateLimitHit:
            # TTLCache already tried stale cache and didn't have any
            logger.warning("CoinDesk 429 with no cache, degrading")
            return None
        except Exception:
            logger.warning("CoinDesk fetch failed", exc_info=True)
            return None

        return self._split_news(all_posts, symbol, max_per_group)

    @staticmethod
    def _split_news(
        posts: list[InformationEvent],
        symbol: str,
        max_per_group: int,
    ) -> tuple[list[InformationEvent], list[InformationEvent]]:
        base = extract_base_currency(symbol)
        symbol_news = [p for p in posts if base in p.symbols]
        general_news = [p for p in posts if base not in p.symbols]

        sym_count = min(len(symbol_news), max_per_group)
        sym_selected = symbol_news[:sym_count]
        gen_count = max_per_group * 2 - sym_count
        gen_selected = general_news[:gen_count]

        return sym_selected, gen_selected

    async def get_fear_greed_index(self) -> InformationEvent | None:
        try:
            return await self._cache.get_or_fetch("fgi", _FGI_TTL, self._fgi.fetch)
        except Exception:
            logger.warning("FGI fetch failed", exc_info=True)
            return None

    async def get_macro_events(self, lookahead_hours: int) -> list[InformationEvent] | None:
        """Returns None when the ForexFactory feed is unavailable so callers can
        distinguish a genuinely empty window from an upstream outage."""
        try:
            all_events: list[InformationEvent] = await self._cache.get_or_fetch(
                "macro_calendar", _CALENDAR_TTL, self._calendar.fetch_events
            )
        except Exception:
            logger.warning("ForexFactory fetch failed", exc_info=True)
            return None

        now = datetime.now(timezone.utc)
        cutoff = now + timedelta(hours=lookahead_hours)
        return [e for e in all_events if now <= e.timestamp <= cutoff]

    async def get_announcements(self, lookback_hours: int) -> list[InformationEvent] | None:
        """Returns None only when every OKX source errored (announcements AND status),
        so callers can distinguish a genuinely quiet window from a full outage.

        Per-source filtering:
          - okx_announcements: publish-time lookback (past `lookback_hours`)
          - okx_status: no filter — the OKX API already scopes results via
            `state=scheduled|ongoing`, and `timestamp` reflects maintenance
            begin time which may legitimately lie in the future.
        """
        cutoff = datetime.now(timezone.utc) - timedelta(hours=lookback_hours)
        results: list[InformationEvent] = []
        success_count = 0

        try:
            ann_events = await self._cache.get_or_fetch(
                "okx_ann", _OKX_TTL, self._announcements.fetch
            )
            results.extend(e for e in ann_events if e.timestamp >= cutoff)
            success_count += 1
        except RateLimitHit:
            logger.warning("OKX rate limited for okx_ann")
        except Exception:
            logger.warning("OKX fetch failed for okx_ann", exc_info=True)

        try:
            status_events = await self._cache.get_or_fetch(
                "okx_status", _OKX_TTL, self._status.fetch
            )
            results.extend(status_events)
            success_count += 1
        except RateLimitHit:
            logger.warning("OKX rate limited for okx_status")
        except Exception:
            logger.warning("OKX fetch failed for okx_status", exc_info=True)

        if success_count == 0:
            return None

        return results

    async def close(self) -> None:
        if self._owns_http:
            await self._http.aclose()
