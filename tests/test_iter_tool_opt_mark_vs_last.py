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


# ============ Task 4: get_position mark integration ============

@pytest.fixture
def mock_deps_for_position():
    """Build a minimal `deps` mock with all IO returning fixture values."""
    import pandas as pd
    from src.integrations.exchange.base import Ticker, Position, Balance

    deps = MagicMock()
    deps.symbol = "BTC/USDT:USDT"
    deps.initial_balance = 10_000.0

    # Position: long with 0.5 contracts entry 80000, liq 51000
    deps.exchange.fetch_positions = AsyncMock(return_value=[
        Position(symbol="BTC/USDT:USDT", side="long", contracts=0.5,
                 entry_price=80_000.0, unrealized_pnl=500.0, leverage=10,
                 liquidation_price=51_000.0, created_at=None),
    ])
    deps.exchange.fetch_balance = AsyncMock(return_value=Balance(
        total_usdt=10_500.0, free_usdt=8_000.0, used_usdt=2_500.0,
    ))
    deps.exchange.fetch_open_orders = AsyncMock(return_value=[])
    deps.exchange.get_contract_size = AsyncMock(return_value=0.01)
    deps.exchange.get_mark_price = AsyncMock(return_value=80_000.0)
    deps.exchange.algo_trigger_reference = "last"
    deps.market_data.get_ticker = AsyncMock(return_value=Ticker(
        symbol="BTC/USDT:USDT", last=80_048.0, bid=80_040.0, ask=80_056.0,
        high=82_000.0, low=79_000.0, base_volume=12_000.0, timestamp=1_715_040_000_000,
    ))
    # Empty OHLCV → no ATR suffix; cleaner assertions
    deps.market_data.get_ohlcv_dataframe = AsyncMock(return_value=pd.DataFrame())
    return deps


@pytest.mark.asyncio
async def test_get_position_mark_line_byte_equal(mock_deps_for_position):
    """Spec §3.1 POS-5 Mark line variant (i) happy path: byte-equal Mark line
    rendering with explicit drift formula (last - mark) / mark * 100.
    """
    from src.agent.tools_perception import get_position

    # Fixture math: mark=80000, last=80048 → drift = (80048-80000)/80000*100 = +0.06%
    out = await get_position(mock_deps_for_position)
    assert "Mark: 80000.00 (Last: 80048.00, drift +0.06%)" in out


@pytest.mark.asyncio
async def test_get_position_drift_positive_sign_demo_magnitude(mock_deps_for_position):
    """Spec §5.1: demo-magnitude fixture using memory `project_okx_demo_mark_vs_last_drift`
    values. Note: memory writes -1.67% under (mark-last)/last convention;
    spec §4.1 uses (last-mark)/mark which gives +1.7033% → rounded +1.70%.
    Same physical observation; sign flips AND magnitude shifts ~0.03pp because
    denominator changes from last to mark. Test docstring reproduces this note
    to prevent future contributors from "fixing" the convention discrepancy.
    """
    from src.agent.tools_perception import get_position
    from src.integrations.exchange.base import Ticker

    mock_deps_for_position.exchange.get_mark_price = AsyncMock(return_value=76_680.30)
    mock_deps_for_position.market_data.get_ticker = AsyncMock(return_value=Ticker(
        symbol="BTC/USDT:USDT", last=77_986.30, bid=77_980.0, ask=77_990.0,
        high=78_500.0, low=77_000.0, base_volume=12_000.0, timestamp=1_715_040_000_000,
    ))

    out = await get_position(mock_deps_for_position)
    assert "Mark: 76680.30 (Last: 77986.30, drift +1.70%)" in out


@pytest.mark.asyncio
async def test_get_position_drift_negative_sign(mock_deps_for_position):
    """Spec §5.1: synthetic negative-sign guard. mark > last → drift negative.
    No claim of matching demo direction.
    """
    from src.agent.tools_perception import get_position
    from src.integrations.exchange.base import Ticker

    mock_deps_for_position.exchange.get_mark_price = AsyncMock(return_value=80_048.0)
    mock_deps_for_position.market_data.get_ticker = AsyncMock(return_value=Ticker(
        symbol="BTC/USDT:USDT", last=80_000.0, bid=79_990.0, ask=80_010.0,
        high=82_000.0, low=79_500.0, base_volume=12_000.0, timestamp=1_715_040_000_000,
    ))

    out = await get_position(mock_deps_for_position)
    assert "drift -0.06%" in out


@pytest.mark.asyncio
async def test_get_position_liquidation_distance_uses_mark(mock_deps_for_position):
    """Spec §5.1: distance anchor is mark, not last. Fixture: mark=80000,
    last=82000, liq=51000 → mark-anchored = (80000-51000)/80000 = 36.25%.
    Last-anchored would give (82000-51000)/82000 ≈ 37.80% — assertion
    verifies the mark-anchored value, so a regression to last-anchored fails.
    """
    from src.agent.tools_perception import get_position
    from src.integrations.exchange.base import Ticker

    mock_deps_for_position.exchange.get_mark_price = AsyncMock(return_value=80_000.0)
    mock_deps_for_position.market_data.get_ticker = AsyncMock(return_value=Ticker(
        symbol="BTC/USDT:USDT", last=82_000.0, bid=81_990.0, ask=82_010.0,
        high=82_500.0, low=80_000.0, base_volume=12_000.0, timestamp=1_715_040_000_000,
    ))

    out = await get_position(mock_deps_for_position)
    assert "Liquidation: 51000.00 (36.25% away)" in out


@pytest.mark.asyncio
async def test_get_position_mark_fetch_failure_isolated_to_liquidation(mock_deps_for_position):
    """Spec §5.1: mark fetch failure → Mark line omitted, Liquidation falls
    back to "(distance unavailable: mark fetch failed)", but Notional / Margin
    / Exit Orders all render normally (Exit Orders is anchored to ticker.last,
    independent of mark).

    Fixture includes active SL + TP orders so the test verifies the Exit
    Orders distance lines still render with "from last price" anchor wording
    when mark fetch fails — pins the core isolation invariant (mark-fail does
    not propagate to last-anchored Exit Orders math).
    """
    from src.agent.tools_perception import get_position
    from src.integrations.exchange.base import Order

    mock_deps_for_position.exchange.get_mark_price = AsyncMock(
        side_effect=RuntimeError("mark price fetch returned empty for BTC/USDT:USDT"),
    )
    # Active SL/TP so Exit Orders distance lines actually render
    mock_deps_for_position.exchange.fetch_open_orders = AsyncMock(return_value=[
        Order(id="sl-x", symbol="BTC/USDT:USDT", side="sell", order_type="stop",
              amount=0.5, price=78_000.0, status="open", is_algo=True,
              trigger_price=78_000.0),
        Order(id="tp-x", symbol="BTC/USDT:USDT", side="sell", order_type="take_profit",
              amount=0.5, price=82_000.0, status="open", is_algo=True,
              trigger_price=82_000.0),
    ])

    out = await get_position(mock_deps_for_position)
    # (a) Mark line omitted
    assert "Mark:" not in out
    # (b) Liquidation fallback
    assert "Liquidation: 51000.00 (distance unavailable: mark fetch failed)" in out
    # (c) Notional + Margin render normally
    assert "Notional value:" in out
    assert "Margin used:" in out
    # (d) Exit Orders section present
    assert "=== Exit Orders ===" in out
    # (e) Exit Orders distance lines render last-anchored despite mark fetch
    #     failure (core isolation invariant — Exit Orders math depends on
    #     ticker.last not on mark)
    assert "below last price" in out  # SL below current (78000 < 80048)
    assert "above last price" in out  # TP above current (82000 > 80048)


# ============ Task 5: get_position Exit Orders label swap ============

@pytest.mark.asyncio
async def test_get_position_exit_orders_label_last_price(mock_deps_for_position):
    """Spec §3.1 POS-5 (Exit Orders): _fmt_exit swaps "current" → trigger_ref
    word (which is "last" for OKX default + Sim). Substring assertion because
    line contains variable order price / amount.
    """
    from src.agent.tools_perception import get_position
    from src.integrations.exchange.base import Order

    mock_deps_for_position.exchange.fetch_open_orders = AsyncMock(return_value=[
        Order(id="abc1", symbol="BTC/USDT:USDT", side="sell", order_type="stop",
              amount=0.5, price=78_000.0, status="open", is_algo=True,
              trigger_price=78_000.0),
        Order(id="abc2", symbol="BTC/USDT:USDT", side="sell", order_type="take_profit",
              amount=0.5, price=82_000.0, status="open", is_algo=True,
              trigger_price=82_000.0),
    ])

    out = await get_position(mock_deps_for_position)
    # Substring guard: SL/TP exit lines mention "last price" not "current"
    assert "below last price" in out  # SL below current
    assert "above last price" in out  # TP above current
    assert "below current" not in out
    assert "above current" not in out


# ============ Task 6: get_open_orders single + OCO label swap ============

@pytest.mark.asyncio
async def test_get_open_orders_single_order_uses_last_price():
    """Spec §3.1 OO-7 non-OCO: _render_single_order takes a trigger_ref
    parameter; label uses "{trigger_ref} price" instead of "current".
    """
    from src.agent.tools_perception import get_open_orders
    from src.integrations.exchange.base import Order, Ticker

    deps = MagicMock()
    deps.symbol = "BTC/USDT:USDT"
    deps.exchange.algo_trigger_reference = "last"
    deps.exchange.fetch_open_orders = AsyncMock(return_value=[
        Order(id="ord-1", symbol="BTC/USDT:USDT", side="buy", order_type="limit",
              amount=0.5, price=79_000.0, status="open", is_algo=False,
              trigger_price=None),
    ])
    deps.market_data.get_ticker = AsyncMock(return_value=Ticker(
        symbol="BTC/USDT:USDT", last=80_000.0, bid=79_995.0, ask=80_005.0,
        high=82_000.0, low=79_500.0, base_volume=12_000.0, timestamp=1_715_040_000_000,
    ))

    out = await get_open_orders(deps)
    assert "from last price" in out
    assert "from current" not in out


@pytest.mark.asyncio
async def test_get_open_orders_oco_pair_uses_last_price():
    """Spec §3.1 OO-7 OCO: same-id stop + take_profit pair via inline render
    branch — both sl_dist and tp_dist suffixes use "{trigger_ref} price".
    """
    from src.agent.tools_perception import get_open_orders
    from src.integrations.exchange.base import Order, Ticker

    deps = MagicMock()
    deps.symbol = "BTC/USDT:USDT"
    deps.exchange.algo_trigger_reference = "last"
    deps.exchange.fetch_open_orders = AsyncMock(return_value=[
        Order(id="oco-1", symbol="BTC/USDT:USDT", side="sell", order_type="stop",
              amount=0.5, price=78_000.0, status="open", is_algo=True,
              trigger_price=78_000.0),
        Order(id="oco-1", symbol="BTC/USDT:USDT", side="sell", order_type="take_profit",
              amount=0.5, price=82_000.0, status="open", is_algo=True,
              trigger_price=82_000.0),
    ])
    deps.market_data.get_ticker = AsyncMock(return_value=Ticker(
        symbol="BTC/USDT:USDT", last=80_000.0, bid=79_995.0, ask=80_005.0,
        high=82_000.0, low=79_500.0, base_volume=12_000.0, timestamp=1_715_040_000_000,
    ))

    out = await get_open_orders(deps)
    # Both legs use the trigger_ref label
    assert out.count("from last price") == 2
    assert "from current" not in out


# ============ Task 7: set_stop_loss + set_take_profit message swap ============

@pytest.mark.asyncio
async def test_set_stop_loss_message_uses_last_price():
    """Spec §3.1 SL-2: success message swaps 'from current' → 'from
    {trigger_ref} price' (default 'last').
    """
    from src.agent.tools_execution import set_stop_loss
    from src.integrations.exchange.base import Order, Position, Ticker

    deps = MagicMock()
    deps.symbol = "BTC/USDT:USDT"
    deps.db_engine = None
    deps.exchange.algo_trigger_reference = "last"
    deps.exchange.fetch_positions = AsyncMock(return_value=[
        Position(symbol="BTC/USDT:USDT", side="long", contracts=0.5,
                 entry_price=80_000.0, unrealized_pnl=500.0, leverage=10,
                 liquidation_price=51_000.0, created_at=None),
    ])
    deps.exchange.fetch_open_orders = AsyncMock(return_value=[])
    deps.exchange.create_order = AsyncMock(return_value=Order(
        id="sl-1", symbol="BTC/USDT:USDT", side="sell", order_type="stop",
        amount=0.5, price=78_000.0, status="open", is_algo=True,
    ))
    deps.market_data.get_ticker = AsyncMock(return_value=Ticker(
        symbol="BTC/USDT:USDT", last=80_000.0, bid=79_995.0, ask=80_005.0,
        high=82_000.0, low=79_500.0, base_volume=12_000.0, timestamp=1_715_040_000_000,
    ))

    out = await set_stop_loss(deps, price=78_000.0, reasoning="below MA50")
    assert "from last price" in out
    assert "from current" not in out


@pytest.mark.asyncio
async def test_set_take_profit_message_uses_last_price():
    """Spec §3.1 TP-2: mirror of SL-2 for take_profit."""
    from src.agent.tools_execution import set_take_profit
    from src.integrations.exchange.base import Order, Position, Ticker

    deps = MagicMock()
    deps.symbol = "BTC/USDT:USDT"
    deps.db_engine = None
    deps.exchange.algo_trigger_reference = "last"
    deps.exchange.fetch_positions = AsyncMock(return_value=[
        Position(symbol="BTC/USDT:USDT", side="long", contracts=0.5,
                 entry_price=80_000.0, unrealized_pnl=500.0, leverage=10,
                 liquidation_price=51_000.0, created_at=None),
    ])
    deps.exchange.fetch_open_orders = AsyncMock(return_value=[])
    deps.exchange.create_order = AsyncMock(return_value=Order(
        id="tp-1", symbol="BTC/USDT:USDT", side="sell", order_type="take_profit",
        amount=0.5, price=82_000.0, status="open", is_algo=True,
    ))
    deps.market_data.get_ticker = AsyncMock(return_value=Ticker(
        symbol="BTC/USDT:USDT", last=80_000.0, bid=79_995.0, ask=80_005.0,
        high=82_000.0, low=79_500.0, base_volume=12_000.0, timestamp=1_715_040_000_000,
    ))

    out = await set_take_profit(deps, price=82_000.0, reasoning="resistance ceiling")
    assert "from last price" in out
    assert "from current" not in out


# ============ Task 8: algo_trigger_reference single-source-of-truth ============

@pytest.mark.asyncio
async def test_algo_trigger_reference_drives_label_text():
    """Spec §5.1 sentinel (drift guard): set deps.exchange.algo_trigger_reference
    to "mark" and verify all FIVE label sites emit "from mark price" — catches
    a future contributor hardcoding "last" at any site (every site must read
    through deps.exchange.algo_trigger_reference, no literal string anywhere).

    Note on scope: this test uses MagicMock for deps.exchange, so it exercises
    the instance-attribute pathway (deps.exchange.algo_trigger_reference is a
    directly-assigned MagicMock attribute). Class-attribute → instance lookup
    is covered separately by test_algo_trigger_reference_class_attribute_pathway
    below — together they pin both halves of the single-source-of-truth claim.

    Sites under test (5 sites, 6 emit points — OCO renders 2):
      (a) get_position Exit Orders _fmt_exit
      (b) get_open_orders _render_single_order (non-OCO)
      (c) get_open_orders OCO inline branch (sl_dist + tp_dist = 2 emits)
      (d) set_stop_loss success message
      (e) set_take_profit success message
    """
    from src.integrations.exchange.base import Order, Position, Ticker
    from src.agent.tools_perception import get_position, get_open_orders
    from src.agent.tools_execution import set_stop_loss, set_take_profit
    import pandas as pd

    # Common deps fixture
    deps = MagicMock()
    deps.symbol = "BTC/USDT:USDT"
    deps.initial_balance = 10_000.0
    deps.db_engine = None
    deps.exchange.algo_trigger_reference = "mark"
    deps.exchange.fetch_balance = AsyncMock(return_value=MagicMock(
        total_usdt=10_500.0, free_usdt=8_000.0, used_usdt=2_500.0,
    ))
    deps.exchange.get_contract_size = AsyncMock(return_value=0.01)
    deps.exchange.get_mark_price = AsyncMock(return_value=80_000.0)
    deps.market_data.get_ticker = AsyncMock(return_value=Ticker(
        symbol="BTC/USDT:USDT", last=80_000.0, bid=79_995.0, ask=80_005.0,
        high=82_000.0, low=79_500.0, base_volume=12_000.0, timestamp=1_715_040_000_000,
    ))
    deps.market_data.get_ohlcv_dataframe = AsyncMock(return_value=pd.DataFrame())

    # (a) get_position Exit Orders + (b/c covered separately)
    deps.exchange.fetch_positions = AsyncMock(return_value=[
        Position(symbol="BTC/USDT:USDT", side="long", contracts=0.5,
                 entry_price=80_000.0, unrealized_pnl=0.0, leverage=10,
                 liquidation_price=51_000.0, created_at=None),
    ])
    deps.exchange.fetch_open_orders = AsyncMock(return_value=[
        Order(id="sl-1", symbol="BTC/USDT:USDT", side="sell", order_type="stop",
              amount=0.5, price=78_000.0, status="open", is_algo=True,
              trigger_price=78_000.0),
    ])
    out_pos = await get_position(deps)
    assert "below mark price" in out_pos or "above mark price" in out_pos

    # (b) get_open_orders non-OCO
    deps.exchange.fetch_open_orders = AsyncMock(return_value=[
        Order(id="ord-1", symbol="BTC/USDT:USDT", side="buy", order_type="limit",
              amount=0.5, price=79_000.0, status="open", is_algo=False,
              trigger_price=None),
    ])
    out_oo = await get_open_orders(deps)
    assert "from mark price" in out_oo

    # (c) get_open_orders OCO
    deps.exchange.fetch_open_orders = AsyncMock(return_value=[
        Order(id="oco-1", symbol="BTC/USDT:USDT", side="sell", order_type="stop",
              amount=0.5, price=78_000.0, status="open", is_algo=True,
              trigger_price=78_000.0),
        Order(id="oco-1", symbol="BTC/USDT:USDT", side="sell", order_type="take_profit",
              amount=0.5, price=82_000.0, status="open", is_algo=True,
              trigger_price=82_000.0),
    ])
    out_oco = await get_open_orders(deps)
    assert out_oco.count("from mark price") == 2

    # (d) set_stop_loss
    deps.exchange.fetch_open_orders = AsyncMock(return_value=[])
    deps.exchange.create_order = AsyncMock(return_value=Order(
        id="sl-2", symbol="BTC/USDT:USDT", side="sell", order_type="stop",
        amount=0.5, price=78_000.0, status="open", is_algo=True,
    ))
    out_sl = await set_stop_loss(deps, price=78_000.0, reasoning="x")
    assert "from mark price" in out_sl

    # (e) set_take_profit
    deps.exchange.create_order = AsyncMock(return_value=Order(
        id="tp-2", symbol="BTC/USDT:USDT", side="sell", order_type="take_profit",
        amount=0.5, price=82_000.0, status="open", is_algo=True,
    ))
    out_tp = await set_take_profit(deps, price=82_000.0, reasoning="x")
    assert "from mark price" in out_tp


@pytest.mark.asyncio
async def test_algo_trigger_reference_class_attribute_pathway(monkeypatch):
    """Class-attribute → instance lookup proof. Complements the main sentinel
    test (drift guard) by exercising the runtime path that resolves
    `deps.exchange.algo_trigger_reference` through Python's attribute lookup
    chain (instance → class → mro) instead of via a MagicMock-assigned
    instance attribute.

    Narrow coverage: a single site (set_stop_loss) is exercised — other 4
    sites are wiring-identical (all read `deps.exchange.algo_trigger_reference`)
    and covered by the drift-guard test above. Together the two tests pin both
    the literal-string drift surface and the class-attribute lookup pathway.
    """
    from src.integrations.exchange.simulated import SimulatedExchange
    from src.integrations.exchange.base import Order, Position, Ticker
    from src.agent.tools_execution import set_stop_loss

    class _MarkSimExchange(SimulatedExchange):
        algo_trigger_reference = "mark"

    cfg = MagicMock(fee_rate=0.0005, precision={})
    ex = _MarkSimExchange(config=cfg, db_engine=None, session_id="sid",
                          symbol="BTC/USDT:USDT")
    ex._latest_ticker = Ticker(
        symbol="BTC/USDT:USDT", last=80_000.0, bid=79_995.0, ask=80_005.0,
        high=82_000.0, low=79_500.0, base_volume=12_000.0, timestamp=1_715_040_000_000,
    )

    # Class-attribute lookup invariants — confirms the override didn't leak
    # to the base class and that instance lookup reaches the subclass.
    assert ex.algo_trigger_reference == "mark"
    assert SimulatedExchange.algo_trigger_reference == "last"

    # Stub the methods set_stop_loss touches; algo_trigger_reference itself
    # stays class-resolved (not monkey-patched on the instance).
    monkeypatch.setattr(ex, "fetch_positions", AsyncMock(return_value=[
        Position(symbol="BTC/USDT:USDT", side="long", contracts=0.5,
                 entry_price=80_000.0, unrealized_pnl=0.0, leverage=10,
                 liquidation_price=51_000.0, created_at=None),
    ]))
    monkeypatch.setattr(ex, "fetch_open_orders", AsyncMock(return_value=[]))
    monkeypatch.setattr(ex, "create_order", AsyncMock(return_value=Order(
        id="sl-cap", symbol="BTC/USDT:USDT", side="sell", order_type="stop",
        amount=0.5, price=78_000.0, status="open", is_algo=True,
    )))

    deps = MagicMock()
    deps.symbol = "BTC/USDT:USDT"
    deps.exchange = ex
    deps.db_engine = None
    deps.market_data.get_ticker = AsyncMock(return_value=ex._latest_ticker)

    out = await set_stop_loss(deps, price=78_000.0, reasoning="x")
    assert "from mark price" in out
