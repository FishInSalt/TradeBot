# src/integrations/macro/alpha_vantage.py
"""Alpha Vantage client — US equity quotes (SPY, QQQ).

Handles two AV quirks:
1. Rate-limit responses are HTTP 200 + body containing 'Information' / 'Note'.
2. AV enforces a 1 req/sec hard limit — a per-client throttle avoids soft
   limiting on burst calls.
"""
from __future__ import annotations

import asyncio
import logging
import time
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

import httpx

from src.integrations.macro.models import EquityQuote
from src.utils.cache import RateLimitHit

_AV_URL = "https://www.alphavantage.co/query"
_NY = ZoneInfo("America/New_York")

logger = logging.getLogger(__name__)


def _utc_date_str() -> str:
    """UTC date string ("YYYY-MM-DD") used as the quota-window key.

    UTC is the default; observation-period validation tracks whether AV's
    actual reset clock matches UTC. If real reset happens in another zone
    with > 1h offset, this helper is the single switch to change.
    """
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


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

    Tracks a best-effort daily call counter (UTC window, resets lazily on
    the next call after midnight UTC) and emits a WARNING at 80% of the
    25-req/day free-tier budget. Observation period will verify the UTC
    reset assumption (spec §7.2).
    """

    _MIN_INTERVAL = 1.1  # 1 req/sec hard limit + 100ms safety margin
    _DAILY_BUDGET = 25              # AV free tier hard limit
    _WARN_THRESHOLD = 20            # 80% of 25 — observation heads-up

    def __init__(self, http: httpx.AsyncClient, api_key: str) -> None:
        self._http = http
        self._api_key = api_key
        self._last_fetch_at: float = 0.0
        self._daily_count: int = 0
        self._daily_count_date: str = _utc_date_str()
        self._warned_today: bool = False

    def _increment_daily_count(self) -> None:
        """Record one consumed AV quota unit. Resets on UTC date flip,
        emits a WARNING exactly once per day when the 80%-of-budget
        threshold is crossed. Reset is lazy (checked on every call rather
        than by a timer) — counter is best-effort infrastructure
        observability, process restarts also zero it."""
        today = _utc_date_str()
        if today != self._daily_count_date:
            self._daily_count = 0
            self._daily_count_date = today
            self._warned_today = False
        self._daily_count += 1
        if self._daily_count >= self._WARN_THRESHOLD and not self._warned_today:
            logger.warning(
                "AV daily budget at %d/%d (date %s UTC)",
                self._daily_count, self._DAILY_BUDGET, self._daily_count_date,
            )
            self._warned_today = True

    async def fetch_quote(self, symbol: str) -> EquityQuote:
        elapsed = time.monotonic() - self._last_fetch_at
        if elapsed < self._MIN_INTERVAL:
            await asyncio.sleep(self._MIN_INTERVAL - elapsed)

        # consumed_quota: True when the request reached AV and AV processed it
        # (success, soft rate limit, hard 429, response shape error, or JSON
        # parse error on a 2xx). False for HTTP 4xx/5xx (industry assumption:
        # AV doesn't bill error responses) and for network errors. flag is
        # consulted in `finally` so we increment exactly once regardless of
        # which branch raises.
        consumed_quota = False
        try:
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
                # Advance the throttle clock on network failure too so
                # retries respect the 1 req/sec hard limit.
                self._last_fetch_at = time.monotonic()

            if resp.status_code == 429:
                # Hard 429: AV enforced quota — count as consumed.
                consumed_quota = True
                raise RateLimitHit(f"Alpha Vantage hard 429 for {symbol}")
            if resp.is_error:
                # 4xx/5xx other than 429: assumed non-billed; see spec §3.1.
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

            # Past is_error: AV returned 2xx/3xx — quota consumed even if
            # downstream parsing fails. Set flag BEFORE resp.json() so that
            # a JSONDecodeError (AV occasionally returns 200 + non-JSON
            # error page) is still counted.
            consumed_quota = True
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
        finally:
            if consumed_quota:
                self._increment_daily_count()
