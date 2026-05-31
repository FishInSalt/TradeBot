import pytest
from tests._fixtures import make_sim_exchange, make_ticker, _advance

pytestmark = pytest.mark.asyncio


async def test_make_sim_exchange_has_default_mark():
    ex = make_sim_exchange()
    assert ex._latest_mark_price == ex._latest_ticker.last  # default mark = last seed


async def test_advance_syncs_mark_then_processes_tick():
    ex = make_sim_exchange()
    await _advance(ex, make_ticker(last=60000.0), mark=59000.0)
    assert ex._latest_mark_price == 59000.0      # mark synced
    assert ex._latest_ticker.last == 60000.0     # ticker advanced


async def test_advance_without_mark_keeps_existing():
    ex = make_sim_exchange()
    ex._latest_mark_price = 51000.0
    await _advance(ex, make_ticker(last=60000.0))  # mark omitted
    assert ex._latest_mark_price == 51000.0       # unchanged


async def test_inject_mock_ccxt_mark_sources_are_awaitable():
    ex = make_sim_exchange()
    fetched = await ex._ccxt.fetch_mark_price("BTC/USDT:USDT")
    watched = await ex._ccxt.watch_mark_price("BTC/USDT:USDT")
    assert "markPrice" in fetched and isinstance(fetched["markPrice"], (int, float))
    assert "markPrice" in watched and isinstance(watched["markPrice"], (int, float))
