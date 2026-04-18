# src/integrations/macro/alpha_vantage.py
"""Alpha Vantage client — US equity quotes (SPY, QQQ).

Handles two AV quirks:
1. Rate-limit responses are HTTP 200 + body containing 'Information' / 'Note'.
2. AV enforces a 1 req/sec hard limit — a per-client throttle avoids soft
   limiting on burst calls.
"""
from __future__ import annotations

import asyncio
import time
from datetime import datetime
from zoneinfo import ZoneInfo

import httpx

from src.integrations.macro.models import EquityQuote
from src.utils.cache import RateLimitHit

_AV_URL = "https://www.alphavantage.co/query"
_NY = ZoneInfo("America/New_York")


def alpha_vantage_ttl_seconds() -> float:
    """Time-of-day aware cache TTL for Alpha Vantage SPY/QQQ (spec §5.2).

    - Sat/Sun: 12h (data static, conserve 25/day budget)
    - Weekday 9:30-16:00 ET: 30min (catch intraday moves)
    - Weekday pre/after market: 4h

    NYSE holidays not handled — weekday holidays use the short TTL based on
    time-of-day, wasting a few API calls on static data. Acceptable for now;
    if observed budget pressure becomes an issue, add a holiday calendar.
    """
    now_et = datetime.now(_NY)
    if now_et.weekday() >= 5:  # Saturday=5, Sunday=6
        return 12 * 3600.0
    hour_min = now_et.hour + now_et.minute / 60.0
    if 9.5 <= hour_min < 16.0:
        return 30 * 60.0
    return 4 * 3600.0


class AlphaVantageClient:
    """Fetches GLOBAL_QUOTE (SPY, QQQ).

    Throttles outgoing HTTP to >= _MIN_INTERVAL seconds apart; cache hits
    in MacroService never reach this method so they don't incur the sleep.
    MacroService._fetch_av_all currently calls SPY then QQQ serially, so
    this throttle is partly redundant — kept as defensive measure in case a
    future refactor parallelizes per-symbol fetches.
    """

    _MIN_INTERVAL = 1.1  # 1 req/sec hard limit + 100ms safety margin

    def __init__(self, http: httpx.AsyncClient, api_key: str) -> None:
        self._http = http
        self._api_key = api_key
        self._last_fetch_at: float = 0.0

    async def fetch_quote(self, symbol: str) -> EquityQuote:
        elapsed = time.monotonic() - self._last_fetch_at
        if elapsed < self._MIN_INTERVAL:
            await asyncio.sleep(self._MIN_INTERVAL - elapsed)

        try:
            resp = await self._http.get(
                _AV_URL,
                params={
                    "function": "GLOBAL_QUOTE",
                    "symbol": symbol,
                    "apikey": self._api_key,
                },
            )
        finally:
            # Advance the clock even on failure so subsequent retries also
            # respect the hard limit.
            self._last_fetch_at = time.monotonic()

        if resp.status_code == 429:
            raise RateLimitHit(f"Alpha Vantage hard 429 for {symbol}")
        if resp.is_error:
            # Don't use raise_for_status — httpx's default HTTPStatusError
            # message includes the full request URL, which here contains the
            # apikey query param. `exc_info=True` in the service layer would
            # otherwise serialize the key into application logs.
            # NOTE (API key leakage boundary): `str(exc)` is sanitized, so
            # Python-stdlib traceback formatting is safe. `exc.request.url`
            # and `exc.response.request.url` still reference the original
            # URL with the apikey — if this project ever integrates Sentry /
            # Datadog / other APM that walks exception attributes, configure
            # their URL/query-string scrubber to redact `apikey=`.
            raise httpx.HTTPStatusError(
                f"Alpha Vantage returned HTTP {resp.status_code} for {symbol}",
                request=resp.request,
                response=resp,
            ) from None

        data = resp.json()
        # AV signals soft rate limit via HTTP 200 body. Both 'Information'
        # (new) and 'Note' (legacy) are observed — check both.
        soft_msg = data.get("Information") or data.get("Note")
        if soft_msg:
            raise RateLimitHit(f"Alpha Vantage soft rate limit: {soft_msg}")

        quote = data.get("Global Quote")
        if not quote:
            raise ValueError(
                f"Alpha Vantage returned unexpected shape: {list(data.keys())}"
            )

        return EquityQuote(
            symbol=quote.get("01. symbol", symbol),
            price=float(quote["05. price"]),
            change_pct=float(quote["10. change percent"].rstrip("%")),
            latest_trading_day=quote.get("07. latest trading day", ""),
        )
