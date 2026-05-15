"""Tests for Task 10: register_close_order_entry wired in close-direction tools."""
import pathlib
import pytest
from unittest.mock import AsyncMock, MagicMock
from src.integrations.exchange.base import Position, Order

_REPO_ROOT = pathlib.Path(__file__).resolve().parents[1]
_TRADER_PY = _REPO_ROOT / "src" / "agent" / "trader.py"


def _make_deps(*, position_side="long", entry_price=80000.0, contracts=0.1,
               order_id="oid1"):
    deps = MagicMock()
    deps.symbol = "BTC/USDT:USDT"
    deps.fee_rate = 0.0005
    deps.exchange = MagicMock()
    deps.exchange.fetch_positions = AsyncMock(return_value=[
        Position(
            symbol="BTC/USDT:USDT",
            side=position_side,
            contracts=contracts,
            entry_price=entry_price,
            unrealized_pnl=10.0,
            leverage=10,
            liquidation_price=72000.0,
            created_at=None,
        ),
    ])
    deps.exchange.has_pending_market_order = MagicMock(return_value=False)
    deps.exchange.get_contract_size = AsyncMock(return_value=1.0)
    ticker = MagicMock()
    ticker.bid = 80100.0
    ticker.ask = 80110.0
    ticker.last = 80105.0
    deps.market_data = MagicMock()
    deps.market_data.get_ticker = AsyncMock(return_value=ticker)
    deps.exchange.create_order = AsyncMock(return_value=Order(
        id=order_id,
        symbol="BTC/USDT:USDT",
        side="sell" if position_side == "long" else "buy",
        order_type="market",
        amount=contracts,
        price=None,
        status="open",
        fee=None,
        is_algo=False,
        trigger_price=None,
    ))
    deps.exchange.register_close_order_entry = MagicMock()
    deps.exchange.fetch_open_orders = AsyncMock(return_value=[])
    deps.exchange.cancel_order = AsyncMock()
    deps.exchange.algo_trigger_reference = "last"
    deps.approval_gate = None
    deps.approval_enabled = False
    deps.db_engine = None  # _record_action no-op
    return deps


@pytest.mark.asyncio
async def test_close_position_calls_register_close_order_entry():
    """close_position registers entry per submitted close order."""
    from src.agent.tools_execution import close_position

    deps = _make_deps(order_id="oid1", entry_price=80000.0)
    await close_position(deps, reasoning="test")

    deps.exchange.register_close_order_entry.assert_called_once_with("oid1", 80000.0)


@pytest.mark.asyncio
async def test_set_stop_loss_calls_register_close_order_entry():
    """set_stop_loss registers entry after creating stop order."""
    from src.agent.tools_execution import set_stop_loss

    deps = _make_deps(order_id="sl1", entry_price=80000.0)
    deps.exchange.create_order = AsyncMock(return_value=Order(
        id="sl1",
        symbol="BTC/USDT:USDT",
        side="sell",
        order_type="stop",
        amount=0.1,
        price=78000.0,
        status="open",
        fee=None,
        is_algo=False,
        trigger_price=None,
    ))

    await set_stop_loss(deps, price=78000.0, reasoning="trailing stop")

    deps.exchange.register_close_order_entry.assert_called_once_with("sl1", 80000.0)


@pytest.mark.asyncio
async def test_set_take_profit_calls_register_close_order_entry():
    """set_take_profit registers entry after creating take-profit order."""
    from src.agent.tools_execution import set_take_profit

    deps = _make_deps(order_id="tp1", entry_price=80000.0)
    deps.exchange.create_order = AsyncMock(return_value=Order(
        id="tp1",
        symbol="BTC/USDT:USDT",
        side="sell",
        order_type="take_profit",
        amount=0.1,
        price=85000.0,
        status="open",
        fee=None,
        is_algo=False,
        trigger_price=None,
    ))

    await set_take_profit(deps, price=85000.0, reasoning="target reached")

    deps.exchange.register_close_order_entry.assert_called_once_with("tp1", 80000.0)


# === Task 20: open_position Est. entry fee output ===

def _make_open_deps(*, fee_rate=0.0005, free_usdt=1000.0, leverage=10, last=80000.0,
                    order_id="op1"):
    """Deps fixture for open_position tests."""
    deps = MagicMock()
    deps.symbol = "BTC/USDT:USDT"
    deps.fee_rate = fee_rate

    balance = MagicMock()
    balance.free_usdt = free_usdt
    deps.exchange = MagicMock()
    deps.exchange.fetch_balance = AsyncMock(return_value=balance)
    deps.exchange.has_pending_market_order = MagicMock(return_value=False)
    deps.exchange.set_leverage = AsyncMock()
    deps.exchange.get_contract_size = AsyncMock(return_value=1.0)

    # quantity = (free_usdt * position_pct/100 * leverage) / last
    # With position_pct=10: (1000 * 0.1 * 10) / 80000 = 0.125
    deps.exchange.amount_to_precision = MagicMock(side_effect=lambda sym, qty: qty)

    ticker = MagicMock()
    ticker.last = last
    deps.market_data = MagicMock()
    deps.market_data.get_ticker = AsyncMock(return_value=ticker)

    deps.exchange.create_order = AsyncMock(return_value=Order(
        id=order_id,
        symbol="BTC/USDT:USDT",
        side="buy",
        order_type="market",
        amount=0.125,
        price=None,
        status="open",
        fee=None,
        is_algo=False,
        trigger_price=None,
    ))
    deps.approval_gate = None
    deps.approval_enabled = False
    deps.db_engine = None  # _record_action no-op
    return deps


@pytest.mark.asyncio
async def test_open_position_output_includes_est_entry_fee():
    """open_position return string includes Est. entry fee with notional × rate caption."""
    from src.agent.tools_execution import open_position

    # fee_rate=0.0005, free_usdt=1000, position_pct=100, leverage=10, last=80000
    # usdt_amount = 1000 * 1.0 = 1000
    # quantity = (1000 * 10) / 80000 = 0.125
    # notional = 80000 * 0.125 = 10000; est_fee = 10000 * 0.0005 = 5.00
    deps = _make_open_deps(fee_rate=0.0005, free_usdt=1000.0, leverage=10, last=80000.0)
    out = await open_position(deps, side="long", position_pct=100, leverage=10, reasoning="t")
    assert "Est. entry fee: ~-5.00 USDT" in out
    assert "(notional ~10,000.00 × ~0.050%)" in out


def test_open_position_wrapper_docstring_mentions_fee():
    """Wrapper docstring preserves fill-timing sentence + appends fee mention."""
    import ast

    src = _TRADER_PY.read_text()
    tree = ast.parse(src)

    # Walk all function defs to find the open_position wrapper inside create_trader_agent
    docstring = None
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == "open_position":
            # There may be multiple; grab the one that has a docstring body
            if (node.body and isinstance(node.body[0], ast.Expr)
                    and isinstance(node.body[0].value, ast.Constant)):
                docstring = node.body[0].value.value
                break

    assert docstring is not None, "open_position wrapper docstring not found in trader.py"
    assert "Position fills via market order; you will receive a fill notification" in docstring
    assert "Entry incurs taker fee = notional × fee_rate. Fill notification reports actual fee." in docstring


# === Task 21: close_position round-trip net PnL + approval message net view ===

def _make_close_deps(*, position_side="long", entry_price=80000.0, contracts=0.5,
                     unrealized_pnl=50.0, fee_rate=0.0005, order_id="oid1",
                     bid=80100.0, ask=80110.0):
    """Deps fixture for close_position Task-21 tests."""
    from src.integrations.exchange.base import Position, Order
    deps = MagicMock()
    deps.symbol = "BTC/USDT:USDT"
    deps.fee_rate = fee_rate
    deps.exchange = MagicMock()
    deps.exchange.fetch_positions = AsyncMock(return_value=[
        Position(
            symbol="BTC/USDT:USDT",
            side=position_side,
            contracts=contracts,
            entry_price=entry_price,
            unrealized_pnl=unrealized_pnl,
            leverage=10,
            liquidation_price=72000.0,
            created_at=None,
        ),
    ])
    deps.exchange.has_pending_market_order = MagicMock(return_value=False)
    deps.exchange.get_contract_size = AsyncMock(return_value=1.0)
    ticker = MagicMock()
    ticker.bid = bid
    ticker.ask = ask
    ticker.last = (bid + ask) / 2
    deps.market_data = MagicMock()
    deps.market_data.get_ticker = AsyncMock(return_value=ticker)
    deps.exchange.create_order = AsyncMock(return_value=Order(
        id=order_id,
        symbol="BTC/USDT:USDT",
        side="sell" if position_side == "long" else "buy",
        order_type="market",
        amount=contracts,
        price=None,
        status="open",
        fee=None,
        is_algo=False,
        trigger_price=None,
    ))
    deps.exchange.register_close_order_entry = MagicMock()
    deps.exchange.fetch_open_orders = AsyncMock(return_value=[])
    deps.exchange.cancel_order = AsyncMock()
    deps.exchange.algo_trigger_reference = "last"
    deps.approval_gate = None
    deps.approval_enabled = False
    deps.db_engine = None
    return deps


@pytest.mark.asyncio
async def test_close_position_output_includes_round_trip_net_pnl():
    """close_position output: Est. exit fee + Est. net PnL (round-trip) breakdown.

    Position long entry=80000 contracts=0.5, ticker.bid=80100.0, fee_rate=0.0005
    entry_fee = 80000*0.5*0.0005 = 20; exit_notional = 80100*0.5 = 40050; exit_fee = 20.025
    unrealized = 50.0
    net = -20 + 50 - 20.025 = +9.975 → +9.98
    """
    from src.agent.tools_execution import close_position

    deps = _make_close_deps(
        position_side="long", entry_price=80000.0, contracts=0.5,
        unrealized_pnl=50.0, fee_rate=0.0005, bid=80100.0,
    )
    out = await close_position(deps, reasoning="t")

    assert "Est. exit fee: ~-20.03 USDT" in out
    assert "Est. net PnL: ~+9.97 USDT" in out
    assert "round-trip = entry fee ~-20.00 + unrealized +50.00 + est. exit fee ~-20.03" in out


@pytest.mark.asyncio
async def test_close_position_approval_message_includes_gross_and_net():
    """Approval gate action_desc format includes 'gross' and 'net (round-trip)'."""
    from src.agent.tools_execution import close_position, _check_approval

    deps = _make_close_deps(
        position_side="long", entry_price=80000.0, contracts=0.5,
        unrealized_pnl=50.0, fee_rate=0.0005, bid=80100.0,
    )

    captured = {}

    async def _fake_check(deps_, action_type, action_desc, qty, price):
        captured["action_desc"] = action_desc
        return True  # approve

    import src.agent.tools_execution as te_mod
    original = te_mod._check_approval
    te_mod._check_approval = _fake_check
    try:
        await close_position(deps, reasoning="t")
    finally:
        te_mod._check_approval = original

    desc = captured.get("action_desc", "")
    assert "gross" in desc, f"'gross' not in action_desc: {desc!r}"
    assert "net (round-trip)" in desc, f"'net (round-trip)' not in action_desc: {desc!r}"


def test_close_position_wrapper_docstring_mentions_fee():
    """Wrapper docstring appends fee/net-PnL mention."""
    import ast

    src = _TRADER_PY.read_text()
    tree = ast.parse(src)

    docstring = None
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == "close_position":
            if (node.body and isinstance(node.body[0], ast.Expr)
                    and isinstance(node.body[0].value, ast.Constant)):
                docstring = node.body[0].value.value
                break

    assert docstring is not None, "close_position wrapper docstring not found in trader.py"
    assert "Close incurs taker fee on exit." in docstring
    assert "est. exit fee" in docstring
    assert "est. round-trip net PnL" in docstring


# === Task 22: place_limit_order Est. entry fee if filled output ===

def _make_limit_deps(*, fee_rate=0.0005, free_usdt=1000.0, order_id="lim1"):
    """Deps fixture for place_limit_order Task-22 tests."""
    from src.integrations.exchange.base import Balance, Order
    deps = MagicMock()
    deps.symbol = "BTC/USDT:USDT"
    deps.fee_rate = fee_rate

    balance = MagicMock(spec=Balance)
    balance.free_usdt = free_usdt
    deps.exchange = MagicMock()
    deps.exchange.fetch_positions = AsyncMock(return_value=[])
    deps.exchange.set_leverage = AsyncMock()
    deps.exchange.fetch_balance = AsyncMock(return_value=balance)
    deps.exchange.get_contract_size = AsyncMock(return_value=1.0)
    deps.exchange.register_close_order_entry = MagicMock()
    # pass-through precision so quantity = raw_quantity
    deps.exchange.amount_to_precision = MagicMock(side_effect=lambda sym, qty: qty)
    deps.exchange.create_order = AsyncMock(return_value=Order(
        id=order_id,
        symbol="BTC/USDT:USDT",
        side="buy",
        order_type="limit",
        amount=0.125,
        price=80000.0,
        status="open",
        fee=None,
        is_algo=False,
        trigger_price=None,
    ))
    deps.approval_gate = None
    deps.approval_enabled = False
    deps.db_engine = None  # _record_action no-op
    return deps


@pytest.mark.asyncio
async def test_place_limit_order_output_includes_est_entry_fee_if_filled():
    """place_limit_order output: Est. entry fee if filled (uses limit price for notional)."""
    from src.agent.tools_execution import place_limit_order

    # fee_rate=0.0005, free_usdt=1000, position_pct=100, leverage=10, price=80000
    # usdt_amount = 1000 * 100/100 = 1000
    # quantity = (1000 * 10) / 80000 = 0.125
    # notional = 80000 * 0.125 = 10000; est_fee = 10000 * 0.0005 = 5.00
    deps = _make_limit_deps(fee_rate=0.0005, free_usdt=1000.0)
    out = await place_limit_order(deps, side="long", price=80000, position_pct=100, leverage=10, reasoning="t")
    assert "Est. entry fee if filled: ~-5.00 USDT" in out
    assert "(notional ~10,000.00 × ~0.050%)" in out
    assert "Note: This tool only submits the order" in out


def test_place_limit_order_wrapper_docstring_mentions_fee():
    """Wrapper docstring appends maker/taker fee sentence."""
    import ast

    src = _TRADER_PY.read_text()
    tree = ast.parse(src)

    docstring = None
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == "place_limit_order":
            if (node.body and isinstance(node.body[0], ast.Expr)
                    and isinstance(node.body[0].value, ast.Constant)):
                docstring = node.body[0].value.value
                break

    assert docstring is not None, "place_limit_order wrapper docstring not found in trader.py"
    assert "Limit fill incurs maker or taker fee depending on fill condition." in docstring


def test_execution_tool_docstrings_no_evaluation_words():
    """Execution tools docstrings do not include evaluation/nudge phrases.

    F-dim drift guard. Approved fee-related vocabulary: 'taker fee', 'fee_rate',
    'notional', 'round-trip', 'gross', 'net'. Forbidden phrases (not bare words
    — bare 'must'/'avoid' are too common in legitimate fact statements like
    'amount must be > 0' or 'to avoid OCO cancellation'):
    - 'erode capital' / 'friction cost' / 'frequent small trades' (Layer 1 nudge family)
    - 'you should' / 'you must' / 'be careful' / 'should avoid' (evaluative directives)
    """
    import ast
    src = _TRADER_PY.read_text()
    tree = ast.parse(src)
    # 提取 trader.py wrapper docstrings for open_position / close_position /
    # set_stop_loss / set_take_profit / place_limit_order / cancel_order
    target_tools = {"open_position", "close_position", "set_stop_loss",
                    "set_take_profit", "place_limit_order", "cancel_order"}
    forbidden = ["erode capital", "friction cost", "frequent small trades",
                 "you should", "you must", "be careful", "should avoid"]

    # Walk AST for AsyncFunctionDef nodes matching target tool names; extract docstring
    for node in ast.walk(tree):
        if isinstance(node, ast.AsyncFunctionDef) and node.name in target_tools:
            ds = ast.get_docstring(node) or ""
            ds_lower = ds.lower()
            for word in forbidden:
                assert word not in ds_lower, (
                    f"{node.name} docstring contains forbidden word '{word}': {ds!r}"
                )


# === ultrareview R2 Imp #2: place_limit_order register hook for limit-as-close ===

@pytest.mark.asyncio
async def test_place_limit_order_registers_entry_when_closing_existing_position():
    """Limit-as-close (reverse direction of existing position): register entry_price
    so OKX _parse_fill_event can attach it. Without this hook, limit-close fills
    on OKX would systematically miss round-trip net rendering (cache miss → degrade).
    """
    from src.agent.tools_execution import place_limit_order

    deps = _make_limit_deps(fee_rate=0.0005, free_usdt=1000.0, order_id="limit-close-1")
    # existing long position; limit short (sell) → close
    deps.exchange.fetch_positions = AsyncMock(return_value=[Position(
        symbol="BTC/USDT:USDT", side="long", contracts=0.1, entry_price=79000.0,
        unrealized_pnl=0.0, leverage=10, liquidation_price=72000.0, created_at=None,
    )])
    await place_limit_order(deps, side="short", price=81000.0,
                            position_pct=100, leverage=10, reasoning="lock profit")

    deps.exchange.register_close_order_entry.assert_called_once_with("limit-close-1", 79000.0)


@pytest.mark.asyncio
async def test_place_limit_order_does_not_register_on_open():
    """Same-direction limit (scale-in or fresh open): NO register call.
    Open fills carry entry_price=None by design — register would pollute cache.
    """
    from src.agent.tools_execution import place_limit_order

    deps = _make_limit_deps(fee_rate=0.0005, free_usdt=1000.0)
    # no positions → fresh open
    deps.exchange.fetch_positions = AsyncMock(return_value=[])
    await place_limit_order(deps, side="long", price=80000.0,
                            position_pct=100, leverage=10, reasoning="entry")

    deps.exchange.register_close_order_entry.assert_not_called()

    # same-side position → scale-in (still open semantic)
    deps.exchange.register_close_order_entry.reset_mock()
    deps.exchange.fetch_positions = AsyncMock(return_value=[Position(
        symbol="BTC/USDT:USDT", side="long", contracts=0.05, entry_price=79500.0,
        unrealized_pnl=0.0, leverage=10, liquidation_price=72000.0, created_at=None,
    )])
    await place_limit_order(deps, side="long", price=80500.0,
                            position_pct=100, leverage=10, reasoning="scale-in")

    deps.exchange.register_close_order_entry.assert_not_called()
