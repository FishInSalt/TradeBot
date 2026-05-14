"""Iter tool-opt-mark-vs-last tests.

Spec: docs/superpowers/specs/2026-05-14-iter-tool-opt-mark-vs-last-design.md

Test pattern:
- OKX-side: mock `_client` (CCXT) with MagicMock; mark endpoint returns full V5
  envelope `{"code": "0", "msg": "", "data": [{"instId", "instType", "markPx",
  "ts"}]}` per project_iter2_mock_fidelity_lesson.
- Sim-side: direct attribute set on `_latest_ticker`.
- Byte-equal for full lines with fully fixture-controlled values; substring for
  lines carrying variable order IDs / amounts / contracts.
"""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest


# ============ Task 1: BaseExchange attribute + abstract method ============

def test_base_algo_trigger_reference_default_last():
    """Spec §3.1: BaseExchange.algo_trigger_reference is a class attribute
    defaulting to "last". OKXExchange and SimulatedExchange inherit unchanged.
    """
    from src.integrations.exchange.base import BaseExchange
    from src.integrations.exchange.okx import OKXExchange
    from src.integrations.exchange.simulated import SimulatedExchange

    assert BaseExchange.algo_trigger_reference == "last"
    assert OKXExchange.algo_trigger_reference == "last"
    assert SimulatedExchange.algo_trigger_reference == "last"


# ============ Task 2: SimulatedExchange.get_mark_price ============

@pytest.mark.asyncio
async def test_sim_get_mark_price_returns_ticker_last():
    """Spec §3.1 SimulatedExchange row: get_mark_price returns the cached
    ticker.last. Sim has a single price source — mark = last. fetch_ticker is
    observation-only (no internal tick advance), so back-to-back invocation
    inside get_position's 6-tuple gather is safe.
    """
    from src.integrations.exchange.simulated import SimulatedExchange
    from src.integrations.exchange.base import Ticker

    cfg = MagicMock(fee_rate=0.0005, precision={})
    ex = SimulatedExchange(config=cfg, db_engine=None, session_id="sid", symbol="BTC/USDT:USDT")
    ex._latest_ticker = Ticker(
        symbol="BTC/USDT:USDT", last=80_000.0, bid=79_995.0, ask=80_005.0,
        high=82_000.0, low=79_500.0, base_volume=12_345.0, timestamp=1_715_040_000_000,
    )

    mark = await ex.get_mark_price("BTC/USDT:USDT")
    assert mark == 80_000.0


# ============ Task 3: OKXExchange.get_mark_price ============

@pytest.mark.asyncio
async def test_okx_get_mark_price_fetches_endpoint():
    """Spec §3.1: OKXExchange.get_mark_price hits public_get_public_mark_price
    and parses markPx as float. Mock response uses full V5 envelope per
    project_iter2_mock_fidelity_lesson.
    """
    from src.integrations.exchange.okx import OKXExchange

    ex = OKXExchange.__new__(OKXExchange)
    ex._client = MagicMock()
    ex._client.public_get_public_mark_price = AsyncMock(return_value={
        "code": "0", "msg": "",
        "data": [{"instId": "BTC-USDT-SWAP", "instType": "SWAP",
                  "markPx": "81920.10", "ts": "1715040000000"}],
    })
    ex._client.market = MagicMock(return_value={"id": "BTC-USDT-SWAP"})

    mark = await ex.get_mark_price("BTC/USDT:USDT")
    assert mark == 81920.10
    assert isinstance(mark, float)


@pytest.mark.asyncio
async def test_okx_get_mark_price_raises_on_empty_data():
    """Spec §3.1: empty `data` array → RuntimeError (no silent fallback)."""
    from src.integrations.exchange.okx import OKXExchange

    ex = OKXExchange.__new__(OKXExchange)
    ex._client = MagicMock()
    ex._client.public_get_public_mark_price = AsyncMock(return_value={
        "code": "0", "msg": "", "data": [],
    })
    ex._client.market = MagicMock(return_value={"id": "BTC-USDT-SWAP"})

    with pytest.raises(RuntimeError, match="mark price fetch returned empty"):
        await ex.get_mark_price("BTC/USDT:USDT")


@pytest.mark.asyncio
async def test_okx_get_mark_price_uses_inst_id_conversion():
    """Spec §3.1: instId is derived via self._client.market(symbol)["id"]
    (CCXT-unified symbol → OKX instId). For BTC/USDT:USDT this yields
    BTC-USDT-SWAP.
    """
    from src.integrations.exchange.okx import OKXExchange

    ex = OKXExchange.__new__(OKXExchange)
    ex._client = MagicMock()
    ex._client.public_get_public_mark_price = AsyncMock(return_value={
        "code": "0", "msg": "",
        "data": [{"instId": "BTC-USDT-SWAP", "instType": "SWAP",
                  "markPx": "81920.10", "ts": "1715040000000"}],
    })
    ex._client.market = MagicMock(return_value={"id": "BTC-USDT-SWAP"})

    await ex.get_mark_price("BTC/USDT:USDT")
    ex._client.public_get_public_mark_price.assert_awaited_once_with({
        "instType": "SWAP", "instId": "BTC-USDT-SWAP",
    })
