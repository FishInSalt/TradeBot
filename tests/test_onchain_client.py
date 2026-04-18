"""Tests for DefiLlama stablecoins client."""
import httpx
import pytest

from src.utils.cache import RateLimitHit


DEFILLAMA_RESPONSE = {
    "peggedAssets": [
        {
            "id": "1",
            "symbol": "USDT",
            "name": "Tether",
            "circulating": {"peggedUSD": 186_620_000_000.0},
            "circulatingPrevDay": {"peggedUSD": 186_500_000_000.0},
            "circulatingPrevWeek": {"peggedUSD": 184_290_000_000.0},
        },
        {
            "id": "2",
            "symbol": "USDC",
            "name": "USD Coin",
            "circulating": {"peggedUSD": 42_180_000_000.0},
            "circulatingPrevDay": {"peggedUSD": 42_100_000_000.0},
            "circulatingPrevWeek": {"peggedUSD": 41_670_000_000.0},
        },
        {
            "id": "3",
            "symbol": "DAI",
            "name": "Dai",
            "circulating": {"peggedUSD": 5_300_000_000.0},
            "circulatingPrevDay": {"peggedUSD": 5_300_000_000.0},
            "circulatingPrevWeek": {"peggedUSD": 5_250_000_000.0},
        },
    ],
}


async def test_defillama_parse_all_assets():
    from src.integrations.onchain.defillama import DefiLlamaClient
    transport = httpx.MockTransport(
        lambda req: httpx.Response(200, json=DEFILLAMA_RESPONSE)
    )
    async with httpx.AsyncClient(transport=transport) as http:
        client = DefiLlamaClient(http)
        assets = await client.fetch_stablecoins()
    assert len(assets) == 3
    by_sym = {a["symbol"]: a for a in assets}
    assert by_sym["USDT"]["circulating"]["peggedUSD"] == 186_620_000_000.0
    assert by_sym["USDC"]["circulatingPrevWeek"]["peggedUSD"] == 41_670_000_000.0


async def test_defillama_429_raises_rate_limit():
    from src.integrations.onchain.defillama import DefiLlamaClient
    transport = httpx.MockTransport(lambda req: httpx.Response(429))
    async with httpx.AsyncClient(transport=transport) as http:
        client = DefiLlamaClient(http)
        with pytest.raises(RateLimitHit):
            await client.fetch_stablecoins()


async def test_defillama_empty_peggedassets_returns_empty_list():
    from src.integrations.onchain.defillama import DefiLlamaClient
    transport = httpx.MockTransport(
        lambda req: httpx.Response(200, json={"peggedAssets": []})
    )
    async with httpx.AsyncClient(transport=transport) as http:
        client = DefiLlamaClient(http)
        assets = await client.fetch_stablecoins()
    assert assets == []


async def test_defillama_null_peggedassets_returns_empty_list():
    """Exercise the `or []` guard — DefiLlama may return null for the field."""
    from src.integrations.onchain.defillama import DefiLlamaClient
    transport = httpx.MockTransport(
        lambda req: httpx.Response(200, json={"peggedAssets": None})
    )
    async with httpx.AsyncClient(transport=transport) as http:
        client = DefiLlamaClient(http)
        assets = await client.fetch_stablecoins()
    assert assets == []


# Model tests
def test_stablecoin_snapshot_fields():
    from src.integrations.onchain.models import StablecoinSnapshot
    s = StablecoinSnapshot(
        symbol="USDT",
        circulating_usd=186.62e9,
        change_7d_usd=2.33e9,
        change_7d_pct=1.27,
    )
    assert s.symbol == "USDT"
    assert s.change_7d_pct == 1.27


def test_stablecoin_total_fields():
    from src.integrations.onchain.models import StablecoinTotal
    t = StablecoinTotal(
        total_circulating_usd=319.61e9,
        total_change_7d_usd=3.85e9,
        total_change_7d_pct=1.22,
    )
    assert t.total_circulating_usd == 319.61e9
