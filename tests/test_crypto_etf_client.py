"""Tests for SoSoValue ETF client."""
import httpx
import pytest

from src.utils.cache import RateLimitHit


# Response captured from real API (spec §2.4 smoke test).
SOSOVALUE_RESPONSE_BTC = {
    "code": 0,
    "data": [
        # 2026-04-17 has 3 rows with identical cum_net_inflow but different
        # total_net_inflow — the exact multi-row pattern cum_delta handles.
        {"date": "2026-04-17", "cum_net_inflow": 57_739_993_739.43,
         "total_net_inflow": 663_911_366.47,
         "total_net_assets": 101_450_000_000.0,
         "total_value_traded": 2_500_000_000.0},
        {"date": "2026-04-17", "cum_net_inflow": 57_739_993_739.43,
         "total_net_inflow": 996_375_546.47,
         "total_net_assets": 101_450_000_000.0,
         "total_value_traded": 2_600_000_000.0},
        {"date": "2026-04-17", "cum_net_inflow": 57_739_993_739.43,
         "total_net_inflow": 1_617_957_506.54,
         "total_net_assets": 101_450_000_000.0,
         "total_value_traded": 2_700_000_000.0},
        {"date": "2026-04-16", "cum_net_inflow": 57_076_082_372.97,
         "total_net_inflow": 26_051_070.56,
         "total_net_assets": 97_900_000_000.0,
         "total_value_traded": 2_000_000_000.0},
        {"date": "2026-04-15", "cum_net_inflow": 57_050_031_302.41,
         "total_net_inflow": 186_031_234.0,
         "total_net_assets": 97_500_000_000.0,
         "total_value_traded": 1_800_000_000.0},
    ],
}


async def test_sosovalue_returns_raw_rows():
    from src.integrations.crypto_etf.sosovalue import SoSoValueClient
    transport = httpx.MockTransport(
        lambda req: httpx.Response(200, json=SOSOVALUE_RESPONSE_BTC)
    )
    async with httpx.AsyncClient(transport=transport) as http:
        client = SoSoValueClient(http, api_key="k")
        rows = await client.fetch_summary_history("BTC")
    assert len(rows) == 5
    assert rows[0]["date"] == "2026-04-17"
    assert rows[0]["cum_net_inflow"] == 57_739_993_739.43


async def test_sosovalue_sends_required_header_and_query():
    from src.integrations.crypto_etf.sosovalue import SoSoValueClient
    captured_headers: dict = {}
    captured_params: dict = {}

    def handler(req: httpx.Request) -> httpx.Response:
        captured_headers.update(dict(req.headers))
        captured_params.update(dict(req.url.params))
        return httpx.Response(200, json=SOSOVALUE_RESPONSE_BTC)

    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(transport=transport) as http:
        client = SoSoValueClient(http, api_key="my-soso-key")
        await client.fetch_summary_history("BTC")
    # Lowercase hyphenated — spec §2.4 "strict case sensitivity"
    assert captured_headers.get("x-soso-api-key") == "my-soso-key"
    assert captured_params["symbol"] == "BTC"
    assert captured_params["country_code"] == "US"


async def test_sosovalue_401_raises():
    """401 is an auth problem, not a rate limit — surface as hard error.
    raise_for_status raises httpx.HTTPStatusError; the service layer catches
    it as a generic Exception and degrades to None."""
    from src.integrations.crypto_etf.sosovalue import SoSoValueClient
    transport = httpx.MockTransport(lambda req: httpx.Response(401))
    async with httpx.AsyncClient(transport=transport) as http:
        client = SoSoValueClient(http, api_key="bad-key")
        with pytest.raises(httpx.HTTPStatusError):
            await client.fetch_summary_history("BTC")


async def test_sosovalue_429_raises_rate_limit():
    from src.integrations.crypto_etf.sosovalue import SoSoValueClient
    transport = httpx.MockTransport(lambda req: httpx.Response(429))
    async with httpx.AsyncClient(transport=transport) as http:
        client = SoSoValueClient(http, api_key="k")
        with pytest.raises(RateLimitHit):
            await client.fetch_summary_history("BTC")


async def test_sosovalue_empty_data_returns_empty_list():
    from src.integrations.crypto_etf.sosovalue import SoSoValueClient
    transport = httpx.MockTransport(
        lambda req: httpx.Response(200, json={"code": 0, "data": []})
    )
    async with httpx.AsyncClient(transport=transport) as http:
        client = SoSoValueClient(http, api_key="k")
        rows = await client.fetch_summary_history("BTC")
    assert rows == []
