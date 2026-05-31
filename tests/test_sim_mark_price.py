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


async def test_get_mark_price_returns_real_mark_not_last():
    ex = make_sim_exchange()
    await _advance(ex, make_ticker(last=60000.0), mark=59000.0)
    assert await ex.get_mark_price("BTC/USDT:USDT") == 59000.0  # mark, not last 60000


async def test_get_mark_price_raises_before_seed():
    ex = make_sim_exchange()
    ex._latest_mark_price = None
    with pytest.raises(RuntimeError, match="No mark price"):
        await ex.get_mark_price("BTC/USDT:USDT")


async def test_unrealized_pnl_uses_mark_not_bid_ask():
    ex = make_sim_exchange()            # contract_size=1.0
    ex._leverage["BTC/USDT:USDT"] = 5
    await ex.create_order("BTC/USDT:USDT", "buy", "market", 0.01)
    await _advance(ex, make_ticker(last=50000.0, bid=50000.0, ask=50000.0), mark=50000.0)   # fill @ 50000
    # ticker bid up to 51990 but mark only 51000 — uPnL must read mark
    await _advance(ex, make_ticker(last=52000.0, bid=51990.0, ask=52010.0), mark=51000.0)
    pos = (await ex.fetch_positions("BTC/USDT:USDT"))[0]
    assert pos.unrealized_pnl == pytest.approx((51000 - 50000) * 0.01)  # 10.0, not bid-based 19.9


async def test_unrealized_pnl_short_uses_mark():
    ex = make_sim_exchange()
    ex._leverage["BTC/USDT:USDT"] = 5
    await ex.create_order("BTC/USDT:USDT", "sell", "market", 0.01)
    await _advance(ex, make_ticker(last=50000.0, bid=50000.0, ask=50000.0), mark=50000.0)   # fill @ 50000
    await _advance(ex, make_ticker(last=48000.0, bid=47990.0, ask=48010.0), mark=49000.0)
    pos = (await ex.fetch_positions("BTC/USDT:USDT"))[0]
    assert pos.unrealized_pnl == pytest.approx((50000 - 49000) * 0.01)  # 10.0 mark-based, not ask-based 19.9


async def test_liquidation_triggers_on_mark_not_bid():
    ex = make_sim_exchange(initial_balance=1000.0)
    ex._leverage["BTC/USDT:USDT"] = 100
    await ex.create_order("BTC/USDT:USDT", "buy", "market", 0.01)
    await _advance(ex, make_ticker(last=50000.0, bid=50000.0, ask=50000.0), mark=50000.0)  # fill @ 50000
    liq = (await ex.fetch_positions("BTC/USDT:USDT"))[0].liquidation_price
    # bid dips below liq but mark stays above → survive (mark-driven, not bid)
    await _advance(ex, make_ticker(last=liq - 10, bid=liq - 10, ask=liq - 10), mark=liq + 50)
    assert "BTC/USDT:USDT" in ex._positions
    # mark dips below liq → liquidated
    await _advance(ex, make_ticker(last=liq + 5, bid=liq - 20, ask=liq + 5), mark=liq - 1)
    assert "BTC/USDT:USDT" not in ex._positions


async def test_liquidation_fill_price_is_bid_not_mark():
    fills = []
    async def collect(f):
        fills.append(f)
    ex = make_sim_exchange(initial_balance=1000.0)
    ex._fill_callback = collect
    ex._leverage["BTC/USDT:USDT"] = 100
    await ex.create_order("BTC/USDT:USDT", "buy", "market", 0.01)
    await _advance(ex, make_ticker(last=50000.0, bid=50000.0, ask=50000.0), mark=50000.0)
    liq = (await ex.fetch_positions("BTC/USDT:USDT"))[0].liquidation_price
    await _advance(ex, make_ticker(last=liq + 5, bid=liq - 20, ask=liq + 5), mark=liq - 1)
    liq_fill = [f for f in fills if f.trigger_reason == "liquidation"][0]
    assert liq_fill.fill_price == liq - 20   # 盘口 bid, NOT mark (liq-1)
