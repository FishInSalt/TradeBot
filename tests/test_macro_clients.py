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
