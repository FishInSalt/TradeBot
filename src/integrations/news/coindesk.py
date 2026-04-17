from __future__ import annotations

import httpx

from src.integrations.news.models import InformationEvent
from src.utils.cache import RateLimitHit

_COINDESK_URL = "https://data-api.coindesk.com/news/v1/article/list"

# Map user-facing filter → CoinDesk sentiment values
_SENTIMENT_MAP = {
    "positive": "POSITIVE",
    "negative": "NEGATIVE",
    "neutral": "NEUTRAL",
}


class CoinDeskNewsClient:
    """CoinDesk Data News API client — crypto news headlines with sentiment.

    No auth required. Response shape:
      { "Data": [ {TITLE, PUBLISHED_ON, URL, SOURCE_DATA.NAME, CATEGORY_DATA[], SENTIMENT, ...}, ... ], "Err": {} }
    """

    def __init__(self, http: httpx.AsyncClient) -> None:
        self._http = http

    async def fetch_posts(self, news_filter: str | None = None) -> list[InformationEvent]:
        params: dict[str, str | int] = {"lang": "EN", "limit": 20}
        if news_filter is not None:
            mapped = _SENTIMENT_MAP.get(news_filter)
            if mapped is not None:
                params["sentiment"] = mapped

        resp = await self._http.get(_COINDESK_URL, params=params)
        if resp.status_code == 429:
            raise RateLimitHit("CoinDesk rate limited")
        resp.raise_for_status()

        return self._parse(resp.json())

    @staticmethod
    def _parse(data: dict) -> list[InformationEvent]:
        from datetime import datetime, timezone

        events: list[InformationEvent] = []
        for article in data.get("Data", []):
            raw_cats = article.get("CATEGORY_DATA") or []
            symbols = [c.get("NAME", "") for c in raw_cats if c.get("NAME")]
            source_name = (article.get("SOURCE_DATA") or {}).get("NAME", "")

            pub_raw = article.get("PUBLISHED_ON")
            try:
                ts = datetime.fromtimestamp(int(pub_raw), tz=timezone.utc)
            except (TypeError, ValueError):
                ts = datetime.now(timezone.utc)

            events.append(
                InformationEvent(
                    timestamp=ts,
                    source="coindesk",
                    category="news",
                    importance="medium",
                    title=article.get("TITLE", ""),
                    content=source_name,
                    url=article.get("URL", ""),
                    symbols=symbols,
                )
            )
        return events
