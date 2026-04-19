"""Tests for macro API clients (FRED, CoinGecko, Alpha Vantage)."""
import httpx
import pytest

from src.utils.cache import RateLimitHit


# ===== FRED =====

FRED_RESPONSE_VIX = {
    "realtime_start": "2026-04-18",
    "realtime_end": "2026-04-18",
    "observation_start": "1600-01-01",
    "observation_end": "9999-12-31",
    "units": "lin",
    "output_type": 1,
    "file_type": "json",
    "order_by": "observation_date",
    "sort_order": "desc",
    "count": 1,
    "offset": 0,
    "limit": 3,
    "observations": [
        {"realtime_start": "2026-04-18", "realtime_end": "2026-04-18",
         "date": "2026-04-16", "value": "17.94"},
    ],
}

FRED_RESPONSE_WITH_NA = {
    "observations": [
        {"date": "2026-04-17", "value": "."},  # FRED uses "." for missing
        {"date": "2026-04-16", "value": "17.94"},
    ],
}


async def test_fred_parse_latest():
    from src.integrations.macro.fred import FREDClient
    transport = httpx.MockTransport(
        lambda req: httpx.Response(200, json=FRED_RESPONSE_VIX)
    )
    async with httpx.AsyncClient(transport=transport) as http:
        client = FREDClient(http, api_key="test-key")
        obs = await client.fetch_latest("VIXCLS")
    assert obs.series_id == "VIXCLS"
    assert obs.date == "2026-04-16"
    assert obs.value == 17.94


async def test_fred_passes_api_key_and_params():
    """FRED expects api_key, series_id, file_type, limit, sort_order in query."""
    from src.integrations.macro.fred import FREDClient
    captured_params: dict = {}

    def handler(req: httpx.Request) -> httpx.Response:
        captured_params.update(dict(req.url.params))
        return httpx.Response(200, json=FRED_RESPONSE_VIX)

    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(transport=transport) as http:
        client = FREDClient(http, api_key="my-fred-key")
        await client.fetch_latest("VIXCLS")
    assert captured_params["series_id"] == "VIXCLS"
    assert captured_params["api_key"] == "my-fred-key"
    assert captured_params["file_type"] == "json"
    assert captured_params["limit"] == "3"
    assert captured_params["sort_order"] == "desc"


async def test_fred_skips_na_value_returns_next():
    """FRED returns '.' for missing readings; skip to next real observation."""
    from src.integrations.macro.fred import FREDClient
    transport = httpx.MockTransport(
        lambda req: httpx.Response(200, json=FRED_RESPONSE_WITH_NA)
    )
    async with httpx.AsyncClient(transport=transport) as http:
        client = FREDClient(http, api_key="k")
        obs = await client.fetch_latest("VIXCLS")
    assert obs.date == "2026-04-16"
    assert obs.value == 17.94


async def test_fred_all_na_returns_none():
    """If every observation is missing, return None (treated as degraded)."""
    from src.integrations.macro.fred import FREDClient
    body = {"observations": [{"date": "2026-04-17", "value": "."}]}
    transport = httpx.MockTransport(lambda req: httpx.Response(200, json=body))
    async with httpx.AsyncClient(transport=transport) as http:
        client = FREDClient(http, api_key="k")
        obs = await client.fetch_latest("VIXCLS")
    assert obs is None


async def test_fred_empty_observations_returns_none():
    from src.integrations.macro.fred import FREDClient
    transport = httpx.MockTransport(
        lambda req: httpx.Response(200, json={"observations": []})
    )
    async with httpx.AsyncClient(transport=transport) as http:
        client = FREDClient(http, api_key="k")
        obs = await client.fetch_latest("VIXCLS")
    assert obs is None


async def test_fred_429_raises_rate_limit():
    from src.integrations.macro.fred import FREDClient
    transport = httpx.MockTransport(lambda req: httpx.Response(429))
    async with httpx.AsyncClient(transport=transport) as http:
        client = FREDClient(http, api_key="k")
        with pytest.raises(RateLimitHit):
            await client.fetch_latest("VIXCLS")


async def test_fred_5xx_error_does_not_leak_api_key():
    """5xx from FRED must raise HTTPStatusError with a sanitized message —
    httpx's default message contains the full URL including the api_key
    query param, and service-layer `exc_info=True` would otherwise
    serialize that URL into application logs.
    """
    from src.integrations.macro.fred import FREDClient
    transport = httpx.MockTransport(lambda req: httpx.Response(500))
    async with httpx.AsyncClient(transport=transport) as http:
        client = FREDClient(http, api_key="SECRET-FRED-KEY")
        with pytest.raises(httpx.HTTPStatusError) as exc_info:
            await client.fetch_latest("VIXCLS")
    assert "SECRET-FRED-KEY" not in str(exc_info.value)
    assert "500" in str(exc_info.value)
    assert "VIXCLS" in str(exc_info.value)


# ===== CoinGecko /global =====

CG_GLOBAL_RESPONSE = {
    "data": {
        "active_cryptocurrencies": 18000,
        "markets": 1100,
        "total_market_cap": {"usd": 2_692_000_000_000.0},
        "total_volume": {"usd": 80_000_000_000.0},
        "market_cap_percentage": {"btc": 57.31, "eth": 10.79, "usdt": 3.5},
        "market_cap_change_percentage_24h_usd": 2.58,
        "updated_at": 1776499200,
    },
}


async def test_cg_global_parse_response():
    from src.integrations.macro.cg_global import CoinGeckoGlobalClient
    transport = httpx.MockTransport(
        lambda req: httpx.Response(200, json=CG_GLOBAL_RESPONSE)
    )
    async with httpx.AsyncClient(transport=transport) as http:
        client = CoinGeckoGlobalClient(http, api_key="demo-key")
        data = await client.fetch_global()
    assert data["btc_dominance"] == 57.31
    assert data["eth_dominance"] == 10.79
    assert data["total_mcap_usd"] == 2_692_000_000_000.0
    assert data["mcap_change_24h_pct"] == 2.58


async def test_cg_global_sends_demo_key_header():
    from src.integrations.macro.cg_global import CoinGeckoGlobalClient
    captured_headers: dict = {}

    def handler(req: httpx.Request) -> httpx.Response:
        captured_headers.update(dict(req.headers))
        return httpx.Response(200, json=CG_GLOBAL_RESPONSE)

    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(transport=transport) as http:
        client = CoinGeckoGlobalClient(http, api_key="my-demo-key")
        await client.fetch_global()
    assert captured_headers.get("x-cg-demo-api-key") == "my-demo-key"


async def test_cg_global_429_raises_rate_limit():
    from src.integrations.macro.cg_global import CoinGeckoGlobalClient
    transport = httpx.MockTransport(lambda req: httpx.Response(429))
    async with httpx.AsyncClient(transport=transport) as http:
        client = CoinGeckoGlobalClient(http, api_key="k")
        with pytest.raises(RateLimitHit):
            await client.fetch_global()


async def test_cg_global_missing_fields_return_nones():
    """If CG adjusts its response schema, degrade by returning None per-field."""
    from src.integrations.macro.cg_global import CoinGeckoGlobalClient
    body = {"data": {"market_cap_percentage": {}, "total_market_cap": {}}}
    transport = httpx.MockTransport(lambda req: httpx.Response(200, json=body))
    async with httpx.AsyncClient(transport=transport) as http:
        client = CoinGeckoGlobalClient(http, api_key="k")
        data = await client.fetch_global()
    assert data["btc_dominance"] is None
    assert data["eth_dominance"] is None
    assert data["total_mcap_usd"] is None
    assert data["mcap_change_24h_pct"] is None


async def test_cg_global_null_nested_fields_return_nones():
    """Explicitly exercise the `or {}` guard — CG sometimes returns null."""
    from src.integrations.macro.cg_global import CoinGeckoGlobalClient
    body = {"data": {"market_cap_percentage": None, "total_market_cap": None}}
    transport = httpx.MockTransport(lambda req: httpx.Response(200, json=body))
    async with httpx.AsyncClient(transport=transport) as http:
        client = CoinGeckoGlobalClient(http, api_key="k")
        data = await client.fetch_global()
    assert data["btc_dominance"] is None
    assert data["eth_dominance"] is None
    assert data["total_mcap_usd"] is None
    assert data["mcap_change_24h_pct"] is None


async def test_cg_global_top_level_null_data_returns_nones():
    """Guard against `{"data": null}` at the top level. `.get("data", {})`
    returns None when the key exists with a null value — the `or {}` cascade
    must prevent the subsequent nested `.get()` calls from AttributeError-ing."""
    from src.integrations.macro.cg_global import CoinGeckoGlobalClient
    transport = httpx.MockTransport(
        lambda req: httpx.Response(200, json={"data": None})
    )
    async with httpx.AsyncClient(transport=transport) as http:
        client = CoinGeckoGlobalClient(http, api_key="k")
        data = await client.fetch_global()
    assert data["btc_dominance"] is None
    assert data["eth_dominance"] is None
    assert data["total_mcap_usd"] is None
    assert data["mcap_change_24h_pct"] is None


# ===== Alpha Vantage =====

AV_RESPONSE_SPY = {
    "Global Quote": {
        "01. symbol": "SPY",
        "05. price": "710.1400",
        "06. volume": "50000000",
        "07. latest trading day": "2026-04-17",
        "09. change": "8.49",
        "10. change percent": "1.21%",
    },
}

AV_RESPONSE_RATE_LIMIT = {
    "Information": (
        "Thank you for using Alpha Vantage! "
        "Our standard API rate limit is 25 requests per day."
    ),
}

AV_RESPONSE_NOTE_LIMIT = {
    "Note": "Thank you for using Alpha Vantage! 5 calls/min exceeded.",
}


async def test_av_parse_quote():
    from src.integrations.macro.alpha_vantage import AlphaVantageClient
    transport = httpx.MockTransport(
        lambda req: httpx.Response(200, json=AV_RESPONSE_SPY)
    )
    async with httpx.AsyncClient(transport=transport) as http:
        client = AlphaVantageClient(http, api_key="k")
        quote = await client.fetch_quote("SPY")
    assert quote.symbol == "SPY"
    assert quote.price == 710.14
    assert quote.change_pct == 1.21
    assert quote.latest_trading_day == "2026-04-17"


async def test_av_information_field_raises_rate_limit():
    """AV returns HTTP 200 + body containing 'Information' on rate limit.
    Client must raise RateLimitHit, not return a malformed Quote."""
    from src.integrations.macro.alpha_vantage import AlphaVantageClient
    transport = httpx.MockTransport(
        lambda req: httpx.Response(200, json=AV_RESPONSE_RATE_LIMIT)
    )
    async with httpx.AsyncClient(transport=transport) as http:
        client = AlphaVantageClient(http, api_key="k")
        with pytest.raises(RateLimitHit, match="rate limit"):
            await client.fetch_quote("SPY")


async def test_av_note_field_also_raises_rate_limit():
    """Older AV error responses use 'Note' key instead of 'Information'."""
    from src.integrations.macro.alpha_vantage import AlphaVantageClient
    transport = httpx.MockTransport(
        lambda req: httpx.Response(200, json=AV_RESPONSE_NOTE_LIMIT)
    )
    async with httpx.AsyncClient(transport=transport) as http:
        client = AlphaVantageClient(http, api_key="k")
        with pytest.raises(RateLimitHit):
            await client.fetch_quote("SPY")


async def test_av_unexpected_shape_raises_value_error():
    """If the response has neither Global Quote nor Information/Note, flag it
    as a hard error so it shows up as 'temporarily unavailable' rather than
    silently degrading."""
    from src.integrations.macro.alpha_vantage import AlphaVantageClient
    transport = httpx.MockTransport(
        lambda req: httpx.Response(200, json={"unexpected": "shape"})
    )
    async with httpx.AsyncClient(transport=transport) as http:
        client = AlphaVantageClient(http, api_key="k")
        with pytest.raises(ValueError):
            await client.fetch_quote("SPY")


async def test_av_empty_global_quote_raises_value_error():
    """AV returns empty 'Global Quote' dict for invalid ticker symbols."""
    from src.integrations.macro.alpha_vantage import AlphaVantageClient
    transport = httpx.MockTransport(
        lambda req: httpx.Response(200, json={"Global Quote": {}})
    )
    async with httpx.AsyncClient(transport=transport) as http:
        client = AlphaVantageClient(http, api_key="k")
        with pytest.raises(ValueError):
            await client.fetch_quote("BADTICKER")


async def test_av_429_also_raises_rate_limit():
    from src.integrations.macro.alpha_vantage import AlphaVantageClient
    transport = httpx.MockTransport(lambda req: httpx.Response(429))
    async with httpx.AsyncClient(transport=transport) as http:
        client = AlphaVantageClient(http, api_key="k")
        with pytest.raises(RateLimitHit):
            await client.fetch_quote("SPY")


async def test_av_5xx_error_does_not_leak_api_key():
    """5xx from AV must raise HTTPStatusError with a sanitized message.
    AV's apikey is a query param, so httpx's default message would include
    it — the client must override that message to avoid leaking the key
    through service-level `exc_info=True` traceback logging.
    """
    from src.integrations.macro.alpha_vantage import AlphaVantageClient
    transport = httpx.MockTransport(lambda req: httpx.Response(500))
    async with httpx.AsyncClient(transport=transport) as http:
        client = AlphaVantageClient(http, api_key="SECRET-AV-KEY")
        with pytest.raises(httpx.HTTPStatusError) as exc_info:
            await client.fetch_quote("SPY")
    assert "SECRET-AV-KEY" not in str(exc_info.value)
    assert "500" in str(exc_info.value)
    assert "SPY" in str(exc_info.value)


async def test_av_throttles_consecutive_calls(monkeypatch):
    """AV enforces 1 req/sec hard limit. Client must call asyncio.sleep for
    at least _MIN_INTERVAL (~1.1s) on the second consecutive call.

    We patch asyncio.sleep to record call durations without actually waiting,
    so CI does not burn ~1.1s per invocation."""
    import src.integrations.macro.alpha_vantage as av_module

    sleep_durations: list[float] = []

    async def fake_sleep(duration: float) -> None:
        sleep_durations.append(duration)

    monkeypatch.setattr(av_module.asyncio, "sleep", fake_sleep)

    transport = httpx.MockTransport(
        lambda req: httpx.Response(200, json=AV_RESPONSE_SPY)
    )
    async with httpx.AsyncClient(transport=transport) as http:
        client = av_module.AlphaVantageClient(http, api_key="k")
        await client.fetch_quote("SPY")  # first call: no sleep, _last_fetch_at=0
        await client.fetch_quote("SPY")  # second call: must sleep ~_MIN_INTERVAL

    # First call should NOT have slept (elapsed since monotonic()=0 is huge).
    # Second call MUST have slept very close to _MIN_INTERVAL.
    assert len(sleep_durations) == 1, (
        f"expected exactly one throttle sleep, got {sleep_durations}"
    )
    # Generous band for monotonic-clock jitter between the two time.monotonic()
    # reads bracketing the http.get() call (which runs in the same test event
    # loop; mock transport is near-instant).
    assert 0.9 <= sleep_durations[0] <= 1.2, (
        f"throttle sleep duration {sleep_durations[0]} outside expected band"
    )


async def test_daily_count_increments_on_success(monkeypatch):
    """Each successful fetch_quote increments the daily counter."""
    import src.integrations.macro.alpha_vantage as av_module

    async def fake_sleep(duration: float) -> None:
        return None
    monkeypatch.setattr(av_module.asyncio, "sleep", fake_sleep)

    transport = httpx.MockTransport(
        lambda req: httpx.Response(200, json=AV_RESPONSE_SPY)
    )
    async with httpx.AsyncClient(transport=transport) as http:
        client = av_module.AlphaVantageClient(http, api_key="k")
        assert client._daily_count == 0
        await client.fetch_quote("SPY")
        assert client._daily_count == 1
        await client.fetch_quote("QQQ")
        assert client._daily_count == 2


async def test_daily_count_warning_at_threshold_only_once(monkeypatch, caplog):
    """Warning fires at count>=20 the FIRST time only; repeats same day do not spam."""
    import logging
    import src.integrations.macro.alpha_vantage as av_module

    async def fake_sleep(duration: float) -> None:
        return None
    monkeypatch.setattr(av_module.asyncio, "sleep", fake_sleep)

    transport = httpx.MockTransport(
        lambda req: httpx.Response(200, json=AV_RESPONSE_SPY)
    )
    async with httpx.AsyncClient(transport=transport) as http:
        client = av_module.AlphaVantageClient(http, api_key="k")
        # Fast-forward: simulate 19 prior calls having already consumed quota
        client._daily_count = 19
        caplog.clear()
        with caplog.at_level(logging.WARNING, logger=av_module.__name__):
            await client.fetch_quote("SPY")  # count goes 19 → 20 → warning
            warnings_after_first = [
                r for r in caplog.records if r.levelno == logging.WARNING
                and "daily budget" in r.getMessage()
            ]
            await client.fetch_quote("SPY")  # count → 21, no repeat
            await client.fetch_quote("SPY")  # count → 22, no repeat
            warnings_after_third = [
                r for r in caplog.records if r.levelno == logging.WARNING
                and "daily budget" in r.getMessage()
            ]
    assert len(warnings_after_first) == 1, (
        f"expected 1 budget warning at count=20, got {len(warnings_after_first)}"
    )
    assert len(warnings_after_third) == 1, (
        f"expected still 1 (no repeat); got {len(warnings_after_third)}"
    )
    # Spec §7.2 requires "(date %s UTC)" in the message for observation-period
    # log/alert correlation. Guard against format drift dropping the UTC label.
    assert "UTC" in warnings_after_first[0].getMessage(), (
        f"missing UTC marker in warning: {warnings_after_first[0].getMessage()!r}"
    )
    # Also verify the actual date string (from self._daily_count_date) is in
    # the warning, not a stale or unrelated date. Guards against regressions
    # where someone accidentally interpolates a different datetime source.
    assert client._daily_count_date in warnings_after_first[0].getMessage(), (
        f"expected date {client._daily_count_date!r} in warning message, got: "
        f"{warnings_after_first[0].getMessage()!r}"
    )


async def test_daily_count_resets_on_new_date(monkeypatch):
    """When UTC date string changes, _daily_count and _warned_today both reset."""
    import src.integrations.macro.alpha_vantage as av_module

    async def fake_sleep(duration: float) -> None:
        return None
    monkeypatch.setattr(av_module.asyncio, "sleep", fake_sleep)

    # Mutable single-element holder lets the test control WHEN the date flips,
    # independent of HOW MANY TIMES _increment_daily_count reads it. Previous
    # iter-based approach enumerated exact call counts and would StopIteration
    # if the implementation added any extra _utc_date_str() lookup (e.g. in
    # a log format call) — that's implementation coupling, not behavior.
    current_date = ["2026-04-19"]
    monkeypatch.setattr(av_module, "_utc_date_str",
                        lambda: current_date[0])

    transport = httpx.MockTransport(
        lambda req: httpx.Response(200, json=AV_RESPONSE_SPY)
    )
    async with httpx.AsyncClient(transport=transport) as http:
        client = av_module.AlphaVantageClient(http, api_key="k")
        assert client._daily_count_date == "2026-04-19"
        client._warned_today = True  # simulate prior same-day warning
        await client.fetch_quote("SPY")  # same day, count → 1
        assert client._daily_count == 1
        assert client._warned_today is True  # not reset
        await client.fetch_quote("SPY")  # same day, count → 2
        assert client._daily_count == 2

        # Flip the clock — NEXT fetch must observe a new day and reset.
        current_date[0] = "2026-04-20"
        await client.fetch_quote("SPY")
        # On the new day: counter resets first, THEN this call increments it
        assert client._daily_count == 1, (
            f"expected count=1 after date flip (reset + this call), got {client._daily_count}"
        )
        assert client._daily_count_date == "2026-04-20"
        assert client._warned_today is False, "warned flag should reset on date flip"
