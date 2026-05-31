import asyncio
import pytest
from unittest.mock import AsyncMock
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


# ---------------------------------------------------------------------------
# Task 5: mark data source — _seed_mark_price, _mark_loop, close()
# ---------------------------------------------------------------------------

async def test_seed_mark_price_extracts_value():
    ex = make_sim_exchange()
    ex._ccxt.fetch_mark_price = AsyncMock(return_value={"markPrice": 67000.0})
    assert await ex._seed_mark_price() == 67000.0


async def test_seed_mark_price_parses_real_string_markpx():
    # mock fidelity (spec §5 / project_iter2_mock_fidelity_lesson): real OKX
    # fetch_mark_price → parse_ticker → markPrice = safe_string(info,'markPx'),
    # i.e. a string not a float. float() must parse it. 'markPrice' key confirmed
    # in ccxt 4.5.47 okx.parse_ticker.
    ex = make_sim_exchange()
    ex._ccxt.fetch_mark_price = AsyncMock(return_value={"markPrice": "66500.5"})
    assert await ex._seed_mark_price() == 66500.5


async def test_seed_mark_price_fail_fast_after_retries(monkeypatch):
    monkeypatch.setattr("asyncio.sleep", AsyncMock())  # skip real 1s+2s backoff
    ex = make_sim_exchange()
    ex._ccxt.fetch_mark_price = AsyncMock(side_effect=RuntimeError("net"))
    with pytest.raises(RuntimeError):
        await ex._seed_mark_price()


async def test_mark_loop_updates_then_keeps_stale_on_error():
    ex = make_sim_exchange()
    ex._latest_mark_price = 50000.0
    # first push 51000, then raise (stale keeps 51000), then cancel to exit
    ex._ccxt.watch_mark_price = AsyncMock(
        side_effect=[{"markPrice": 51000.0}, RuntimeError("ws"), asyncio.CancelledError()]
    )
    await ex._mark_loop()
    assert ex._latest_mark_price == 51000.0   # updated, then kept stale through error
    assert ex._mark_error_count == 1          # exactly one error before cancel


async def test_mark_error_count_independent_of_ticker():
    ex = make_sim_exchange()
    ex._error_count = 7
    ex._ccxt.watch_mark_price = AsyncMock(side_effect=[{"markPrice": 52000.0}, asyncio.CancelledError()])
    await ex._mark_loop()
    assert ex._error_count == 7   # mark loop must NOT touch _error_count


async def test_mark_error_count_resets_after_recovery():
    ex = make_sim_exchange()
    ex._mark_error_count = 2
    # error bumps the count, then a successful watch resets it to 0, then cancel exits
    ex._ccxt.watch_mark_price = AsyncMock(
        side_effect=[RuntimeError("ws"), {"markPrice": 53000.0}, asyncio.CancelledError()]
    )
    await ex._mark_loop()
    assert ex._latest_mark_price == 53000.0   # recovered to the successful value
    assert ex._mark_error_count == 0          # reset on success (else branch)
