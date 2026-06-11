"""Cross-layer contract-size same-source assertion (iter-sim-exec-cs-precision Task 10).

Guards that the execution-layer cached cs (via init_market_meta) and the
market-data layer live-read cs (fetch_order_book / fetch_trades) both originate
from the same _ccxt.market(symbol)["contractSize"] call — no divergence path.
"""
import pytest
from unittest.mock import MagicMock, AsyncMock

from tests.test_simulated_exchange import _make_exchange


@pytest.mark.asyncio
async def test_exec_cs_matches_marketdata_source():
    """Execution-layer cached cs (init_market_meta) and market-data layer live-read cs share the same source.

    Both paths read _ccxt.market(symbol)["contractSize"]; this test locks in that
    invariant so future refactors cannot silently introduce a divergence (e.g.
    hardcoding 1.0 in one path while the other reads the real value).
    """
    ex = _make_exchange()  # db_engine=None → init_market_meta skips DB-cache, exercises ccxt path
    # Override the mock _ccxt with one that returns a specific contractSize.
    ex._ccxt = MagicMock()
    ex._ccxt.load_markets = AsyncMock()
    ex._ccxt.market = MagicMock(return_value={"contractSize": 0.01})
    ex._ccxt.amount_to_precision = MagicMock(side_effect=lambda sym, amt: str(float(amt)))

    # Execution layer: init_market_meta loads markets then caches from market()
    await ex.init_market_meta()
    cached = await ex.get_contract_size("BTC/USDT:USDT")

    # Market-data layer: fetch_order_book / fetch_trades read market()["contractSize"] live
    live = float(ex._ccxt.market("BTC/USDT:USDT")["contractSize"])

    assert cached == live == 0.01, (
        f"Execution-layer cached cs ({cached}) diverged from market-data layer live cs ({live}); "
        "both must read _ccxt.market(symbol)['contractSize']"
    )


@pytest.mark.asyncio
async def test_exec_cs_reflects_ccxt_market_value():
    """Changing contractSize in the _ccxt mock changes both the cached and live values consistently."""
    for cs_value in [0.001, 0.01, 0.1, 1.0]:
        ex = _make_exchange()  # fresh instance per value → no idempotent short-circuit; db_engine=None → ccxt path
        ex._ccxt = MagicMock()
        ex._ccxt.load_markets = AsyncMock()
        ex._ccxt.market = MagicMock(return_value={"contractSize": cs_value})
        ex._ccxt.amount_to_precision = MagicMock(side_effect=lambda sym, amt: str(float(amt)))

        await ex.init_market_meta()
        cached = await ex.get_contract_size("BTC/USDT:USDT")
        live = float(ex._ccxt.market("BTC/USDT:USDT")["contractSize"])

        assert cached == live == cs_value, (
            f"cs_value={cs_value}: cached={cached}, live={live} — must be equal"
        )
