import pytest
from dataclasses import dataclass
from datetime import datetime, timezone
from unittest.mock import ANY, AsyncMock, MagicMock
import pandas as pd
import numpy as np
from src.integrations.exchange.base import Ticker, Balance, Position, Order


def _patch_now(monkeypatch, year=2026, month=5, day=12, hour=10, minute=23, second=0):
    """Helper to monkeypatch datetime.now in tools_execution module to a fixed UTC time.

    Pattern匹配 tests/test_av_time_of_day_cache.py:11 (FakeDateTime(datetime) 继承)。
    """
    from src.agent import tools_execution as mod
    fixed = datetime(year, month, day, hour, minute, second, tzinfo=timezone.utc)

    class FakeDateTime(datetime):
        @classmethod
        def now(cls, tz=None):
            return fixed if tz is None else fixed.astimezone(tz)

    monkeypatch.setattr(mod, "datetime", FakeDateTime)
    return fixed


@dataclass
class MockDeps:
    symbol: str
    timeframe: str
    market_data: AsyncMock
    exchange: AsyncMock
    technical: MagicMock
    memory: AsyncMock
    session_id: str = "test-session"
    cycle_id: str = "test-cycle"   # ← NEW: _record_action 路径需要
    db_engine: object = None
    approval_gate: object = None
    approval_enabled: bool = False
    wake_min_minutes: int = 1
    wake_max_minutes: int = 60
    set_next_wake_fn: object = None
    initial_balance: float = 10000.0
    fee_rate: float = 0.0005
    metrics: object = None
    news: object = None
    macro: object = None
    crypto_etf: object = None
    onchain: object = None


@pytest.fixture
def deps():
    d = MockDeps(
        symbol="BTC/USDT:USDT",
        timeframe="15m",
        market_data=AsyncMock(),
        exchange=AsyncMock(),
        technical=MagicMock(),
        memory=AsyncMock(),
    )
    d.market_data.get_ticker.return_value = Ticker(
        "BTC/USDT:USDT", 65000.0, 64999.0, 65001.0, 66000.0, 64000.0, 12345.6, 1712534400000
    )
    d.market_data.get_ohlcv_dataframe.return_value = pd.DataFrame(
        {
            "close": np.full(50, 65000.0),
            "open": np.full(50, 65000.0),
            "high": np.full(50, 65500.0),
            "low": np.full(50, 64500.0),
            "volume": np.full(50, 1000.0),
            "timestamp": range(50),
        }
    )
    d.technical.compute_indicators.return_value = {"rsi_14": 55.0}
    d.technical.format_for_llm.return_value = "RSI(14): 55.0"
    d.exchange.fetch_balance.return_value = Balance(10000.0, 8000.0, 2000.0)
    d.exchange.fetch_positions.return_value = [
        Position("BTC/USDT:USDT", "long", 0.01, 64000.0, 10.0, 3, 55000.0)
    ]
    d.exchange.create_order.return_value = Order(
        "o1", "BTC/USDT:USDT", "buy", "market", 0.01, 65000.0, "closed"
    )
    d.exchange.set_leverage = AsyncMock()
    d.exchange.amount_to_precision = MagicMock(side_effect=lambda sym, amt: round(amt, 3))
    d.exchange.fetch_open_orders = AsyncMock(return_value=[])
    d.exchange.cancel_order = AsyncMock()
    d.exchange.has_pending_market_order = MagicMock(return_value=False)
    d.memory.format_for_prompt.return_value = "No memories."
    d.exchange.get_alert_params = MagicMock(return_value=(5.0, 60))
    d.exchange.get_price_level_alerts = MagicMock(return_value=[])
    d.exchange.get_contract_size = AsyncMock(return_value=1.0)
    d.exchange.get_mark_price = AsyncMock(return_value=65000.0)
    d.exchange.register_close_order_entry = MagicMock()
    return d


async def test_get_market_data(deps):
    from src.agent.tools_perception import get_market_data

    result = await get_market_data(deps, "BTC/USDT:USDT", "15m")
    assert "65000" in result
    assert "=== Ticker" in result


async def test_get_position(deps):
    from src.agent.tools_perception import get_position

    result = await get_position(deps, "BTC/USDT:USDT")
    # iter-tool-opt-as-of-header: header now includes inline "@ HH:MM:SS UTC"
    import re as _re
    assert _re.search(
        r"=== Position \(BTC/USDT:USDT @ \d{2}:\d{2}:\d{2} UTC\) ===",
        result,
    ), result[:200]
    assert "Side: Long" in result
    assert "64,000" in result or "64000" in result


async def test_get_account_balance(deps):
    from src.agent.tools_perception import get_account_balance

    result = await get_account_balance(deps)
    assert "10000" in result
    assert "Return" in result or "return" in result.lower()


async def test_get_memories(deps):
    from src.agent.tools_perception import get_memories
    result = await get_memories(deps)
    assert "No memories" in result


async def test_open_position(deps):
    from src.agent.tools_execution import open_position
    result = await open_position(deps, "long", 20.0, 3, reasoning="RSI oversold")
    assert "submitted" in result.lower()
    assert "o1" in result
    deps.exchange.set_leverage.assert_called_once()


async def test_open_position_too_small(deps):
    from src.agent.tools_execution import open_position
    deps.exchange.amount_to_precision = MagicMock(return_value=0.0)
    result = await open_position(deps, "long", 0.001, 1, reasoning="test")
    assert "too small" in result.lower()


async def test_open_position_runtime_happy_path_both_sides(deps):
    """Runtime happy path for the two Literal-valid sides — covers impl branches
    (order_side="buy" for "long" / "sell" for "short" at impl line 93).

    Schema-level rejection of invalid values is asserted separately by
    test_open_position_schema_rejects_invalid_side_at_agent_layer, since
    direct impl invocation here bypasses pydantic-ai's enum validation.
    """
    from src.agent.tools_execution import open_position
    # side="long" — should resolve order_side="buy" via impl line 93
    result_long = await open_position(deps, "long", 20.0, 3, reasoning="long entry")
    assert "submitted" in result_long.lower()

    # Reset call tracking for second invocation
    deps.exchange.has_pending_market_order = MagicMock(return_value=False)
    # side="short" — should resolve order_side="sell" via impl line 93
    result_short = await open_position(deps, "short", 20.0, 3, reasoning="short entry")
    assert "submitted" in result_short.lower()


def test_open_position_schema_rejects_invalid_side_at_agent_layer():
    """Schema-level validation: pydantic-ai inspects signature; invalid
    values surface as ToolCallError before impl runs.

    Drift guard — if signature regresses to `side: str`, silent fallthrough
    to short (impl line 93: `"buy" if side == "long" else "sell"`) returns,
    which is wrong execution per principle 1 (fact-provider, not guard).
    """
    from typing import Literal, get_type_hints
    from src.agent.trader import create_trader_agent
    from src.config import PersonaConfig

    agent = create_trader_agent(model="test", persona_config=PersonaConfig())
    # `_function_toolset` is pydantic-ai private API; depended on intentionally
    # here as the only path to inspect the LLM-visible tool schema. Breakage on
    # pydantic-ai upgrade is acceptable — surfaces a real contract change.
    tool = agent._function_toolset.tools["open_position"]
    # pydantic-ai wraps the user function; resolve hints from the underlying
    # callable so we read the on-source annotation, not a Pydantic-rewritten one.
    hints = get_type_hints(tool.function)
    assert hints["side"] == Literal["long", "short"], (
        f"open_position side annotation drifted to {hints['side']!r}; "
        "expected Literal['long', 'short'] — wider type allows silent "
        "wrong execution (any non-'long' value falls through to short)."
    )
    # LLM-visible schema must surface the enum so pydantic-ai rejects
    # invalid values before impl runs (drift guard mirrors R2-1 pattern).
    schema = tool.tool_def.parameters_json_schema
    assert schema["properties"]["side"].get("enum") == ["long", "short"], (
        f"open_position side enum missing from LLM-visible schema: "
        f"{schema['properties']['side']!r}"
    )


async def test_close_position(deps):
    from src.agent.tools_execution import close_position
    result = await close_position(deps, reasoning="MACD death cross")
    assert "submitted" in result.lower()


async def test_close_position_no_positions(deps):
    from src.agent.tools_execution import close_position
    deps.exchange.fetch_positions.return_value = []
    result = await close_position(deps, reasoning="test")
    assert "no positions" in result.lower()


async def test_set_stop_loss_cancels_existing(deps):
    from src.agent.tools_execution import set_stop_loss
    deps.exchange.fetch_open_orders.return_value = [
        Order("old-sl", "BTC/USDT:USDT", "sell", "stop", 0.01, 60000.0, "open"),
    ]
    deps.exchange.cancel_order = AsyncMock()
    result = await set_stop_loss(deps, 63000.0, reasoning="trailing stop")
    assert "63000" in result
    deps.exchange.cancel_order.assert_called_once_with("old-sl", "BTC/USDT:USDT", is_algo=False)


async def test_set_take_profit(deps):
    from src.agent.tools_execution import set_take_profit
    deps.exchange.fetch_open_orders.return_value = []
    deps.exchange.cancel_order = AsyncMock()
    result = await set_take_profit(deps, 68000.0, reasoning="target reached")
    assert "68000" in result


async def test_adjust_leverage_rejects_when_holding_position(deps):
    """iter-tool-opt-adjust-leverage-guard: wrapper docstring claims 'cannot
    change while holding a position'; impl must enforce it (was phantom guard).

    Fixture's default fetch_positions returns one Position(..., leverage=3);
    impl must short-circuit before set_leverage and surface the current
    leverage in the reject string.
    """
    from src.agent.tools_execution import adjust_leverage
    # Default fixture has one held position with leverage=3 (see deps fixture).
    result = await adjust_leverage(deps, 5, reasoning="raise lev")
    assert "Cannot adjust leverage while holding a position" in result
    assert "3x" in result  # current leverage echoed
    deps.exchange.set_leverage.assert_not_called()


async def test_adjust_leverage_succeeds_when_no_position(deps):
    """Empty positions list -> proceeds with set_leverage (happy path)."""
    from src.agent.tools_execution import adjust_leverage
    deps.exchange.fetch_positions.return_value = []
    result = await adjust_leverage(deps, 5, reasoning="reducing risk")
    assert "5" in result
    deps.exchange.set_leverage.assert_called_once_with("BTC/USDT:USDT", 5)


async def test_get_open_orders(deps):
    from src.agent.tools_perception import get_open_orders
    deps.exchange.fetch_open_orders.return_value = [
        Order("sl1", "BTC/USDT:USDT", "sell", "stop", 0.01, 63000.0, "open"),
    ]
    result = await get_open_orders(deps)
    assert "STOP" in result
    assert "63000" in result


async def test_get_open_orders_empty(deps):
    from src.agent.tools_perception import get_open_orders
    deps.exchange.fetch_open_orders.return_value = []
    result = await get_open_orders(deps)
    assert "no pending" in result.lower()


async def test_get_trade_journal_empty(deps):
    from src.agent.tools_perception import get_trade_journal
    result = await get_trade_journal(deps)
    assert "no trade journal" in result.lower()


async def test_get_trade_journal_with_entries(tmp_path):
    """Test journal formatting with real DB entries and order lookup."""
    from src.storage.database import init_db, get_session
    from src.storage.models import Session, TradeAction
    from src.agent.tools_perception import get_trade_journal
    from unittest.mock import AsyncMock, MagicMock

    engine = await init_db(f"sqlite+aiosqlite:///{tmp_path}/journal_test.db")
    async with get_session(engine) as session:
        session.add(Session(id="s1", name="journal-test", initial_balance=100.0))
        await session.commit()
        session.add(TradeAction(
            session_id="s1", action="open_position", order_id="o1",
            symbol="BTC/USDT:USDT", side="long", reasoning="RSI oversold",
        ))
        session.add(TradeAction(
            session_id="s1", action="order_filled", order_id="o1",
            symbol="BTC/USDT:USDT", side="long", trigger_reason="market",
            reasoning="(exchange: market order filled @ 60200)",
        ))
        await session.commit()

    mock_deps = MagicMock()
    mock_deps.metrics = None  # prevent await deps.metrics.compute() TypeError
    mock_deps.db_engine = engine
    mock_deps.session_id = "s1"
    mock_deps.symbol = "BTC/USDT:USDT"
    mock_deps.exchange = AsyncMock()
    mock_deps.exchange.fetch_order.return_value = Order(
        "o1", "BTC/USDT:USDT", "buy", "market", 0.001, 60200.0, "closed", fee=0.03
    )

    result = await get_trade_journal(mock_deps)
    assert "open_position" in result
    assert "order_filled" in result
    assert "60200" in result
    assert "RSI oversold" in result
    await engine.dispose()


async def test_get_trade_journal_order_fetch_failure(tmp_path):
    """Journal should work even if order fetch fails."""
    from src.storage.database import init_db, get_session
    from src.storage.models import Session, TradeAction
    from src.agent.tools_perception import get_trade_journal
    from unittest.mock import AsyncMock, MagicMock

    engine = await init_db(f"sqlite+aiosqlite:///{tmp_path}/journal_fail.db")
    async with get_session(engine) as session:
        session.add(Session(id="s1", name="fail-test", initial_balance=100.0))
        await session.commit()
        session.add(TradeAction(
            session_id="s1", action="open_position", order_id="o-fail",
            symbol="BTC/USDT:USDT", side="long", reasoning="test",
        ))
        await session.commit()

    mock_deps = MagicMock()
    mock_deps.metrics = None  # prevent await deps.metrics.compute() TypeError
    mock_deps.db_engine = engine
    mock_deps.session_id = "s1"
    mock_deps.symbol = "BTC/USDT:USDT"
    mock_deps.exchange = AsyncMock()
    mock_deps.exchange.fetch_order.side_effect = ValueError("not found")

    result = await get_trade_journal(mock_deps)
    assert "open_position" in result
    assert "test" in result
    await engine.dispose()


async def test_set_price_volatility_alert_creates_when_none(deps):
    """First call: success string says 'set:', not 'replaced:'."""
    from src.agent.tools_execution import set_price_volatility_alert
    deps.exchange.get_alert_params = MagicMock(return_value=None)
    deps.exchange.set_volatility_alert = MagicMock()
    result = await set_price_volatility_alert(deps, 2.0, 30, reasoning="initial")
    assert "set:" in result
    assert "replaced" not in result
    assert "threshold=2.0%" in result
    assert "window=30min" in result
    deps.exchange.set_volatility_alert.assert_called_once_with(2.0, 30, deps.symbol)


async def test_set_price_volatility_alert_replaces_when_exists(deps):
    """Replace path: success string contains 'replaced:', 'was X/Y', 'rolling window reset'."""
    from src.agent.tools_execution import set_price_volatility_alert
    deps.exchange.get_alert_params = MagicMock(return_value=(5.0, 60))
    deps.exchange.set_volatility_alert = MagicMock()
    result = await set_price_volatility_alert(deps, 2.0, 30, reasoning="tighten")
    assert "replaced:" in result
    assert "was 5.0%/60min" in result
    assert "rolling window reset" in result
    deps.exchange.set_volatility_alert.assert_called_once_with(2.0, 30, deps.symbol)


async def test_set_price_volatility_alert_threshold_too_low(deps):
    from src.agent.tools_execution import set_price_volatility_alert
    deps.exchange.set_volatility_alert = MagicMock()
    result = await set_price_volatility_alert(deps, 0.05, 5, reasoning="test")
    assert "Invalid threshold_pct" in result
    deps.exchange.set_volatility_alert.assert_not_called()


async def test_set_price_volatility_alert_threshold_too_high(deps):
    from src.agent.tools_execution import set_price_volatility_alert
    deps.exchange.set_volatility_alert = MagicMock()
    result = await set_price_volatility_alert(deps, 55.0, 5, reasoning="test")
    assert "Invalid threshold_pct" in result
    deps.exchange.set_volatility_alert.assert_not_called()


async def test_set_price_volatility_alert_window_out_of_range(deps):
    from src.agent.tools_execution import set_price_volatility_alert
    deps.exchange.set_volatility_alert = MagicMock()

    result = await set_price_volatility_alert(deps, 3.0, 0, reasoning="test")
    assert "Invalid window_minutes" in result
    deps.exchange.set_volatility_alert.assert_not_called()

    result = await set_price_volatility_alert(deps, 3.0, 250, reasoning="test")
    assert "Invalid window_minutes" in result
    deps.exchange.set_volatility_alert.assert_not_called()


async def test_cancel_price_volatility_alert_when_active(deps):
    """Active path: clears slot, returns 'was X/Y' confirmation, records action."""
    from src.agent.tools_execution import cancel_price_volatility_alert
    deps.exchange.get_alert_params = MagicMock(return_value=(2.0, 30))
    deps.exchange.cancel_volatility_alert = MagicMock()
    result = await cancel_price_volatility_alert(deps, reasoning="market calmed")
    assert "Price volatility alert cancelled" in result
    assert "was 2.0%/30min" in result
    deps.exchange.cancel_volatility_alert.assert_called_once_with()


async def test_cancel_price_volatility_alert_when_none_idempotent(deps):
    """Already-unset path: ok with note, no mutation, no audit row."""
    from src.agent.tools_execution import cancel_price_volatility_alert
    deps.exchange.get_alert_params = MagicMock(return_value=None)
    deps.exchange.cancel_volatility_alert = MagicMock()
    result = await cancel_price_volatility_alert(deps, reasoning="cleanup")
    assert "No volatility alert active to cancel" in result
    deps.exchange.cancel_volatility_alert.assert_not_called()


async def test_get_active_alerts_volatility_section_when_unset(deps):
    """Unset path: section says 'Not set', NOT 'OFF'."""
    from src.agent.tools_perception import get_active_alerts
    deps.exchange.get_alert_params = MagicMock(return_value=None)
    deps.exchange.get_price_level_alerts = MagicMock(return_value=[])
    result = await get_active_alerts(deps)
    assert "Not set" in result
    assert "\nOFF" not in result  # `\n` anchor avoids matching "OFF" inside other words


async def test_get_active_alerts_volatility_section_when_set(deps):
    """Set path: section shows '{threshold}% in {window}min window'."""
    from src.agent.tools_perception import get_active_alerts
    deps.exchange.get_alert_params = MagicMock(return_value=(2.0, 30))
    deps.exchange.get_price_level_alerts = MagicMock(return_value=[])
    result = await get_active_alerts(deps)
    assert "2.0% in 30min window" in result


async def test_add_price_level_alert_success(deps):
    """add_price_level_alert should call exchange and return confirmation."""
    from src.agent.tools_execution import add_price_level_alert
    deps.exchange.add_price_level_alert = MagicMock(return_value="abc123")
    deps.exchange._latest_price = None
    result = await add_price_level_alert(deps, 58000.0, "below", reasoning="support level")
    assert "abc123" in result
    assert "below" in result
    deps.exchange.add_price_level_alert.assert_called_once_with(58000.0, "below", deps.symbol, "support level")


async def test_add_price_level_alert_invalid_direction(deps):
    """Invalid direction should return error without calling exchange."""
    from src.agent.tools_execution import add_price_level_alert
    deps.exchange.add_price_level_alert = MagicMock()
    result = await add_price_level_alert(deps, 58000.0, "sideways", reasoning="test")
    assert "invalid" in result.lower()
    deps.exchange.add_price_level_alert.assert_not_called()


async def test_add_price_level_alert_limit_reached(deps):
    """When exchange returns None (limit), tool returns limit message."""
    from src.agent.tools_execution import add_price_level_alert
    deps.exchange.add_price_level_alert = MagicMock(return_value=None)
    result = await add_price_level_alert(deps, 58000.0, "below", reasoning="test")
    assert "limit" in result.lower()


async def test_add_price_level_alert_immediate_warning(deps):
    """When current price already past target, return string carries
    `— fires on next tick` suffix on top of the unified success prefix
    (iter-tool-opt-apla-docstring-return)."""
    from src.agent.tools_execution import add_price_level_alert
    deps.exchange.add_price_level_alert = MagicMock(return_value="abc123")
    deps.exchange._latest_price = 57000.0  # already below 58000
    result = await add_price_level_alert(deps, 58000.0, "below", reasoning="support")
    assert result.startswith("Price level alert set: below 58000.00 (id=abc123)")
    assert "fires on next tick" in result
    assert "already below 58000.00" in result


async def test_set_next_wake_success(deps):
    """set_next_wake should call setter and return confirmation."""
    from src.agent.tools_execution import set_next_wake
    deps.wake_min_minutes = 1
    deps.wake_max_minutes = 60
    deps.set_next_wake_fn = MagicMock()
    result = await set_next_wake(deps, 10, reasoning="checking position")
    deps.set_next_wake_fn.assert_called_once_with(10, ANY)
    assert "10 min" in result


async def test_set_next_wake_forwards_reasoning_to_setter(deps):
    """set_next_wake forwards reasoning to set_next_wake_fn so the scheduler can
    echo it on the next scheduled fire (spec 2026-06-11)."""
    from src.agent.tools_execution import set_next_wake
    deps.wake_min_minutes = 1
    deps.wake_max_minutes = 60
    deps.set_next_wake_fn = MagicMock()
    await set_next_wake(deps, 10, reasoning="check 12:00 1H close below 62467")
    deps.set_next_wake_fn.assert_called_once_with(10, "check 12:00 1H close below 62467")


async def test_set_next_wake_rejects_above_max(deps):
    """Minutes above wake_max → reject; set_next_wake_fn not called."""
    from src.agent.tools_execution import set_next_wake
    deps.wake_min_minutes = 1
    deps.wake_max_minutes = 60
    deps.set_next_wake_fn = MagicMock()
    result = await set_next_wake(deps, 90, reasoning="test")
    deps.set_next_wake_fn.assert_not_called()
    assert "Cannot set wake to 90 min" in result
    assert "exceeds wake_max=60 min" in result
    assert "for this session" in result


async def test_set_next_wake_rejects_below_min(deps):
    """Minutes below wake_min → reject; set_next_wake_fn not called."""
    from src.agent.tools_execution import set_next_wake
    deps.wake_min_minutes = 1
    deps.wake_max_minutes = 60
    deps.set_next_wake_fn = MagicMock()
    result = await set_next_wake(deps, 0, reasoning="test")
    deps.set_next_wake_fn.assert_not_called()
    assert "Cannot set wake to 0 min" in result
    assert "below wake_min=1 min" in result


async def test_set_next_wake_reject_no_trade_action(deps, db_engine):
    """T2 reject path does not write trade_actions row."""
    from src.agent.tools_execution import set_next_wake
    from src.storage.models import TradeAction
    from sqlalchemy import select
    deps.wake_min_minutes = 1
    deps.wake_max_minutes = 60
    deps.set_next_wake_fn = MagicMock()
    deps.db_engine = db_engine

    await set_next_wake(deps, 90, reasoning="reject test")  # exceeds max

    async with db_engine.begin() as conn:
        rows = (await conn.execute(
            select(TradeAction).where(TradeAction.action == "set_next_wake")
        )).scalars().all()
    assert len(rows) == 0


async def test_set_next_wake_boundary_60_ok(deps):
    """T2.5: minutes=60 (wake_max boundary) → ok, fn called."""
    from src.agent.tools_execution import set_next_wake
    deps.wake_min_minutes = 1
    deps.wake_max_minutes = 60
    deps.set_next_wake_fn = MagicMock()
    result = await set_next_wake(deps, 60, reasoning="boundary test")
    deps.set_next_wake_fn.assert_called_once_with(60, ANY)
    assert "Next wake set to 60 min" in result


async def test_set_next_wake_boundary_61_rejects(deps):
    """T2.4: minutes=61 (wake_max+1) → reject."""
    from src.agent.tools_execution import set_next_wake
    deps.wake_min_minutes = 1
    deps.wake_max_minutes = 60
    deps.set_next_wake_fn = MagicMock()
    result = await set_next_wake(deps, 61, reasoning="boundary test")
    deps.set_next_wake_fn.assert_not_called()
    assert "exceeds wake_max=60 min" in result


async def test_set_next_wake_not_available(deps):
    """When set_next_wake_fn is None, return not-available message."""
    from src.agent.tools_execution import set_next_wake
    deps.set_next_wake_fn = None
    result = await set_next_wake(deps, 10, reasoning="test")
    assert "not available" in result.lower()


# === set_next_wake_at tests (T1.1-T1.10) — R2-Next-H Task 3 ===

async def test_set_next_wake_at_happy_path(deps, monkeypatch):
    """T1.1: now=10:23:00, target='10:37' → ok + delta=14 + trade_actions written."""
    from src.agent.tools_execution import set_next_wake_at
    _patch_now(monkeypatch)  # default 2026-05-12 10:23:00 UTC

    deps.wake_min_minutes = 1
    deps.wake_max_minutes = 60
    deps.set_next_wake_fn = MagicMock()
    result = await set_next_wake_at(deps, "10:37", reasoning="align 1h close")
    deps.set_next_wake_fn.assert_called_once_with(14, ANY)
    assert "Next wake set for 2026-05-12 10:37 UTC" in result
    assert "in 14 min" in result


async def test_set_next_wake_at_cross_day(deps, monkeypatch):
    """T1.2: now=23:50, target='00:37' → tomorrow 00:37, delta=47."""
    from src.agent.tools_execution import set_next_wake_at
    _patch_now(monkeypatch, hour=23, minute=50)
    deps.wake_min_minutes = 1
    deps.wake_max_minutes = 60
    deps.set_next_wake_fn = MagicMock()
    result = await set_next_wake_at(deps, "00:37", reasoning="cross-day test")
    deps.set_next_wake_fn.assert_called_once_with(47, ANY)
    assert "Next wake set for 2026-05-13 00:37 UTC" in result
    assert "in 47 min" in result


async def test_set_next_wake_at_exceeds_wake_max(deps, monkeypatch):
    """T1.4: target 97 min away → reject, fn not called."""
    from src.agent.tools_execution import set_next_wake_at
    _patch_now(monkeypatch)  # 10:23:00
    deps.wake_min_minutes = 1
    deps.wake_max_minutes = 60
    deps.set_next_wake_fn = MagicMock()
    result = await set_next_wake_at(deps, "12:00", reasoning="test")
    deps.set_next_wake_fn.assert_not_called()
    assert "Cannot wake at 12:00 UTC" in result
    assert "nearest future 2026-05-12 12:00 UTC" in result
    assert "in 97 min" in result
    assert "exceeds wake_max=60 min for this session" in result


async def test_set_next_wake_at_ceil_boundary_ok(deps, monkeypatch):
    """T1.5: now=10:23:30, target='10:24' → ceil(30/60)=1, ok (not reject)."""
    from src.agent.tools_execution import set_next_wake_at
    _patch_now(monkeypatch, second=30)  # 10:23:30
    deps.wake_min_minutes = 1
    deps.wake_max_minutes = 60
    deps.set_next_wake_fn = MagicMock()
    result = await set_next_wake_at(deps, "10:24", reasoning="ceil edge")
    deps.set_next_wake_fn.assert_called_once_with(1, ANY)
    assert "in 1 min" in result


async def test_set_next_wake_at_past_resolves_tomorrow_exceeds_max(deps, monkeypatch):
    """T1.6: now=10:23:00, target='10:23' (same minute past) → tomorrow → 1440 min → reject."""
    from src.agent.tools_execution import set_next_wake_at
    _patch_now(monkeypatch)
    deps.wake_min_minutes = 1
    deps.wake_max_minutes = 60
    deps.set_next_wake_fn = MagicMock()
    result = await set_next_wake_at(deps, "10:23", reasoning="past test")
    deps.set_next_wake_fn.assert_not_called()
    assert "Cannot wake at 10:23 UTC" in result
    assert "nearest future 2026-05-13 10:23 UTC" in result
    assert "in 1440 min" in result
    assert "exceeds wake_max=60 min" in result


async def test_set_next_wake_at_fn_none(deps):
    """T1.7: deps.set_next_wake_fn=None → 'Dynamic wake not available'."""
    from src.agent.tools_execution import set_next_wake_at
    deps.set_next_wake_fn = None
    result = await set_next_wake_at(deps, "10:37", reasoning="test")
    assert result == "Dynamic wake not available"


async def test_set_next_wake_at_reject_no_trade_action(deps, monkeypatch, db_engine):
    """T1.10: reject path does not write trade_actions row."""
    from src.agent.tools_execution import set_next_wake_at
    from src.storage.models import TradeAction
    from sqlalchemy import select
    _patch_now(monkeypatch)
    deps.wake_min_minutes = 1
    deps.wake_max_minutes = 60
    deps.set_next_wake_fn = MagicMock()
    deps.db_engine = db_engine

    await set_next_wake_at(deps, "12:00", reasoning="test")  # reject (97 min)

    async with db_engine.begin() as conn:
        rows = (await conn.execute(
            select(TradeAction).where(TradeAction.action == "set_next_wake_at")
        )).scalars().all()
    assert len(rows) == 0


@pytest.mark.parametrize("bad_input", ["foo", "25:00", "10:60", "10", "10:37:00", "", "3:05", "10:5"])
async def test_set_next_wake_at_format_invalid(deps, bad_input):
    """T1.3a: invalid format → reject with hint."""
    from src.agent.tools_execution import set_next_wake_at
    deps.wake_min_minutes = 1
    deps.wake_max_minutes = 60
    deps.set_next_wake_fn = MagicMock()
    result = await set_next_wake_at(deps, bad_input, reasoning="test")
    deps.set_next_wake_fn.assert_not_called()
    assert "Invalid target_time format" in result
    assert "2-digit hour and minute" in result
    assert "'10:37'" in result


@pytest.mark.parametrize("good_input,now_h,now_m,expected_delta", [
    ("00:00", 23, 30, 30),  # tomorrow 00:00 from 23:30 → 30 min
    ("23:59", 23, 0, 59),   # today 23:59 from 23:00 → 59 min
])
async def test_set_next_wake_at_format_edge_ok(deps, monkeypatch, good_input, now_h, now_m, expected_delta):
    """T1.3b: format edge (00:00 / 23:59) accepted by regex."""
    from src.agent.tools_execution import set_next_wake_at
    _patch_now(monkeypatch, hour=now_h, minute=now_m)
    deps.wake_min_minutes = 1
    deps.wake_max_minutes = 60
    deps.set_next_wake_fn = MagicMock()
    result = await set_next_wake_at(deps, good_input, reasoning="edge test")
    deps.set_next_wake_fn.assert_called_once_with(expected_delta, ANY)


async def test_set_next_wake_at_ceil_drift_guard_59s(deps, monkeypatch):
    """T1.8: ceil drift guard — now=10:23:01, target='10:24' (delta_sec=59) → ceil=1."""
    from src.agent.tools_execution import set_next_wake_at
    _patch_now(monkeypatch, second=1)
    deps.wake_min_minutes = 1
    deps.wake_max_minutes = 60
    deps.set_next_wake_fn = MagicMock()
    result = await set_next_wake_at(deps, "10:24", reasoning="drift")
    deps.set_next_wake_fn.assert_called_once_with(1, ANY)


async def test_set_next_wake_at_ceil_drift_guard_120s(deps, monkeypatch):
    """T1.8b: ceil drift guard — integer minute boundary, delta_sec=120 → ceil=2."""
    from src.agent.tools_execution import set_next_wake_at
    _patch_now(monkeypatch)
    deps.wake_min_minutes = 1
    deps.wake_max_minutes = 60
    deps.set_next_wake_fn = MagicMock()
    result = await set_next_wake_at(deps, "10:25", reasoning="120s boundary")
    deps.set_next_wake_fn.assert_called_once_with(2, ANY)


async def test_set_next_wake_at_below_wake_min_custom(deps, monkeypatch):
    """T1.8c: wake_min=2 fixture — ceil=1 < wake_min=2 → reject."""
    from src.agent.tools_execution import set_next_wake_at
    _patch_now(monkeypatch, second=30)
    deps.wake_min_minutes = 2  # custom
    deps.wake_max_minutes = 60
    deps.set_next_wake_fn = MagicMock()
    result = await set_next_wake_at(deps, "10:24", reasoning="custom wake_min")
    deps.set_next_wake_fn.assert_not_called()
    assert "Cannot wake at 10:24 UTC" in result
    assert "in 1 min" in result
    assert "below wake_min=2 min" in result


async def test_set_next_wake_at_trade_actions_reasoning_prefix(deps, monkeypatch, db_engine):
    """T1.9: trade_actions row reasoning prefix format."""
    from src.agent.tools_execution import set_next_wake_at
    from src.storage.models import TradeAction
    from sqlalchemy import select
    _patch_now(monkeypatch)
    deps.wake_min_minutes = 1
    deps.wake_max_minutes = 60
    deps.set_next_wake_fn = MagicMock()
    deps.db_engine = db_engine

    await set_next_wake_at(deps, "10:37", reasoning="align 1h close at 11:00 UTC")

    async with db_engine.begin() as conn:
        reasoning = (await conn.execute(
            select(TradeAction.reasoning).where(TradeAction.action == "set_next_wake_at")
        )).scalar_one()
    expected_prefix = "target=10:37 UTC resolves_to=2026-05-12 10:37 UTC interval=14min | align 1h close at 11:00 UTC"
    assert reasoning == expected_prefix


async def test_open_position_rejects_when_pending(deps):
    """open_position returns rejection message when market order is pending."""
    from src.agent.tools_execution import open_position
    deps.exchange.has_pending_market_order = MagicMock(return_value=True)
    result = await open_position(deps, "long", 20.0, 3, reasoning="test")
    assert "already pending" in result.lower()
    deps.exchange.create_order.assert_not_called()


async def test_open_position_allows_when_no_pending(deps):
    """open_position proceeds normally when no market order is pending."""
    from src.agent.tools_execution import open_position
    deps.exchange.has_pending_market_order = MagicMock(return_value=False)
    result = await open_position(deps, "long", 20.0, 3, reasoning="test")
    assert "submitted" in result.lower()


async def test_close_position_rejects_when_pending(deps):
    """close_position returns rejection message when close order is pending."""
    from src.agent.tools_execution import close_position
    from src.integrations.exchange.base import Position
    deps.exchange.fetch_positions = AsyncMock(return_value=[
        Position("BTC/USDT:USDT", "long", 0.01, 64000.0, 10.0, 3, 55000.0)
    ])
    deps.exchange.has_pending_market_order = MagicMock(return_value=True)
    result = await close_position(deps, reasoning="test")
    assert "already pending" in result.lower()
    deps.exchange.create_order.assert_not_called()


async def test_close_position_allows_when_no_pending(deps):
    """close_position proceeds when no same-direction pending order."""
    from src.agent.tools_execution import close_position
    from src.integrations.exchange.base import Position
    deps.exchange.fetch_positions = AsyncMock(return_value=[
        Position("BTC/USDT:USDT", "long", 0.01, 64000.0, 10.0, 3, 55000.0)
    ])
    deps.exchange.has_pending_market_order = MagicMock(return_value=False)
    result = await close_position(deps, reasoning="test")
    assert "submitted" in result.lower()
