"""R2-7 §10.2 — _capture_state_snapshot + _capture_trigger_context 单元测。"""
from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock

import pytest

from src.integrations.exchange.base import (
    Balance,
    FillEvent,
    Order,
    Position,
    PriceLevelAlertInfo,
    Ticker,
)
from src.services.cycle_capture import (
    _capture_state_snapshot,
    _capture_trigger_context,
)
from src.services.price_alert import AlertInfo


@pytest.fixture
def deps_with_position():
    """Mocked TradingDeps with one short position + balance + ticker + 1 pending limit + 0 alerts."""
    deps = MagicMock()
    deps.symbol = "BTC/USDT:USDT"

    deps.exchange = MagicMock()
    deps.exchange.fetch_positions = AsyncMock(return_value=[
        Position(
            symbol="BTC/USDT:USDT", side="short", contracts=0.265,
            entry_price=75350.0, unrealized_pnl=12.34, leverage=5,
            liquidation_price=79500.0,
        )
    ])
    deps.exchange.fetch_balance = AsyncMock(return_value=Balance(
        total_usdt=10134.5, free_usdt=10047.3, used_usdt=87.2,
    ))
    deps.exchange.fetch_open_orders = AsyncMock(return_value=[
        Order(
            id="ord-abc", symbol="BTC/USDT:USDT", side="buy",
            order_type="limit", amount=0.013, price=75550.0, status="open",
            is_algo=False, trigger_price=None,
        )
    ])
    deps.exchange.get_contract_size = AsyncMock(return_value=1.0)
    deps.exchange.get_price_level_alerts = MagicMock(return_value=[])
    deps.exchange.get_alert_params = MagicMock(return_value=None)

    deps.market_data = MagicMock()
    deps.market_data.get_ticker = AsyncMock(return_value=Ticker(
        symbol="BTC/USDT:USDT", last=75123.5, bid=75123.0, ask=75124.0,
        high=76200.0, low=74900.0, base_volume=1234.56,
        timestamp=1746098096000,
    ))
    return deps


@pytest.fixture
def deps_flat():
    """Mocked TradingDeps with flat position (no position)."""
    deps = MagicMock()
    deps.symbol = "BTC/USDT:USDT"
    deps.exchange = MagicMock()
    deps.exchange.fetch_positions = AsyncMock(return_value=[])
    deps.exchange.fetch_balance = AsyncMock(return_value=Balance(
        total_usdt=10000.0, free_usdt=10000.0, used_usdt=0.0,
    ))
    deps.exchange.fetch_open_orders = AsyncMock(return_value=[])
    deps.exchange.get_price_level_alerts = MagicMock(return_value=[])
    deps.exchange.get_alert_params = MagicMock(return_value=None)
    deps.market_data = MagicMock()
    deps.market_data.get_ticker = AsyncMock(return_value=Ticker(
        symbol="BTC/USDT:USDT", last=75000.0, bid=74999.0, ask=75001.0,
        high=75500.0, low=74500.0, base_volume=1000.0, timestamp=1746098096000,
    ))
    return deps


# === T-SS: state_snapshot ===


async def test_state_snapshot_no_position(deps_flat):
    """T-SS-1: 无持仓 cycle → snapshot.position = None, balance/market 有值。"""
    snap = await _capture_state_snapshot("cyc-001", deps_flat)
    assert snap["position"] is None
    assert snap["balance"]["total_usdt"] == 10000.0
    assert snap["market"]["ticker_last"] == 75000.0
    assert snap["pending_orders"] == []
    assert snap["active_alerts"] == []
    assert snap["_errors"] == []
    assert snap["_cycle_id"] == "cyc-001"


async def test_state_snapshot_with_position(deps_with_position):
    """T-SS-2: 有持仓 cycle → position 含 8 字段（含 pnl_pct_of_notional 衍生计算）。"""
    snap = await _capture_state_snapshot("cyc-002", deps_with_position)
    p = snap["position"]
    assert p["symbol"] == "BTC/USDT:USDT"
    assert p["side"] == "short"
    assert p["contracts"] == 0.265
    assert p["entry_price"] == 75350.0
    assert p["unrealized_pnl"] == 12.34
    assert p["leverage"] == 5
    assert p["liquidation_price"] == 79500.0
    # pnl_pct_of_notional = 12.34 / (75350 * 0.265) * 100 ≈ 0.0618
    assert p["pnl_pct_of_notional"] == pytest.approx(0.0618, rel=1e-3)


async def test_state_snapshot_pending_orders_detail(deps_with_position):
    """T-SS-3: pending_orders detail 完整（含 8 字段）。"""
    snap = await _capture_state_snapshot("cyc-003", deps_with_position)
    assert len(snap["pending_orders"]) == 1
    o = snap["pending_orders"][0]
    assert set(o.keys()) == {
        "id", "order_type", "side", "price", "trigger_price",
        "amount", "status", "is_algo",
    }


async def test_state_snapshot_active_alerts_detail():
    """T-SS-4: active_alerts detail 完整 + 单 symbol filter。"""
    deps = MagicMock()
    deps.symbol = "BTC/USDT:USDT"
    deps.exchange = MagicMock()
    deps.exchange.fetch_positions = AsyncMock(return_value=[])
    deps.exchange.fetch_balance = AsyncMock(return_value=Balance(
        total_usdt=1.0, free_usdt=1.0, used_usdt=0.0,
    ))
    deps.exchange.fetch_open_orders = AsyncMock(return_value=[])
    deps.exchange.get_price_level_alerts = MagicMock(return_value=[
        {"id": "a1", "symbol": "BTC/USDT:USDT", "direction": "above", "price": 76000.0, "reasoning": "test"},
        {"id": "a2", "symbol": "ETH/USDT:USDT", "direction": "below", "price": 3000.0, "reasoning": "other"},
    ])
    deps.market_data = MagicMock()
    deps.market_data.get_ticker = AsyncMock(return_value=Ticker(
        symbol="BTC/USDT:USDT", last=1.0, bid=1.0, ask=1.0, high=1.0, low=1.0,
        base_volume=1.0, timestamp=0,
    ))
    snap = await _capture_state_snapshot("cyc-004", deps)
    assert len(snap["active_alerts"]) == 1, "应只含 BTC 的 alert"
    assert snap["active_alerts"][0]["id"] == "a1"
    assert snap["active_alerts"][0]["price"] == 76000.0


async def test_state_snapshot_ticker_fetch_failed(deps_flat):
    """T-SS-5: ticker fetch 失败 → market = None + _errors 含 ticker_fetch_failed。"""
    deps_flat.market_data.get_ticker = AsyncMock(side_effect=RuntimeError("network"))
    snap = await _capture_state_snapshot("cyc-005", deps_flat)
    assert snap["market"] is None
    assert any("ticker_fetch_failed" in e for e in snap["_errors"])


async def test_state_snapshot_position_fetch_failed(deps_with_position):
    """T-SS-6: position fetch 失败 → position = None + _errors 标记。"""
    deps_with_position.exchange.fetch_positions = AsyncMock(side_effect=RuntimeError("api"))
    snap = await _capture_state_snapshot("cyc-006", deps_with_position)
    assert snap["position"] is None
    assert any("position_fetch_failed" in e for e in snap["_errors"])


async def test_state_snapshot_all_failed():
    """T-SS-7: 全部 6 个 best-effort fetch 失败 → 所有字段 None + _errors 6 项 + 不抛异常。"""
    deps = MagicMock()
    deps.symbol = "BTC/USDT:USDT"
    deps.exchange = MagicMock()
    deps.exchange.fetch_positions = AsyncMock(side_effect=RuntimeError())
    deps.exchange.fetch_balance = AsyncMock(side_effect=RuntimeError())
    deps.exchange.fetch_open_orders = AsyncMock(side_effect=RuntimeError())
    deps.exchange.get_price_level_alerts = MagicMock(side_effect=RuntimeError())
    deps.exchange.get_alert_params = MagicMock(side_effect=RuntimeError())
    deps.market_data = MagicMock()
    deps.market_data.get_ticker = AsyncMock(side_effect=RuntimeError())

    snap = await _capture_state_snapshot("cyc-007", deps)
    assert snap["position"] is None
    assert snap["balance"] is None
    assert snap["market"] is None
    assert snap["pending_orders"] == []
    assert snap["active_alerts"] == []
    assert snap["volatility_alert"] is None
    assert len(snap["_errors"]) == 6


async def test_state_snapshot_json_round_trip(deps_with_position):
    """T-SS-8: snapshot json.dumps + json.loads round-trip 不丢字段。"""
    snap = await _capture_state_snapshot("cyc-008", deps_with_position)
    serialized = json.dumps(snap)
    restored = json.loads(serialized)
    assert restored == snap


async def test_state_snapshot_balance_field_name(deps_flat):
    """T-SS-9 (E2 校准): balance 字段名是 total_usdt 不是 equity_usdt。"""
    snap = await _capture_state_snapshot("cyc-009", deps_flat)
    assert "total_usdt" in snap["balance"]
    assert "equity_usdt" not in snap["balance"]


async def test_state_snapshot_pnl_pct_zero_position():
    """T-SS-10: entry_price=0 或 contracts=0 → pnl_pct_of_notional = None（不除 0）。"""
    deps = MagicMock()
    deps.symbol = "BTC/USDT:USDT"
    deps.exchange = MagicMock()
    deps.exchange.fetch_positions = AsyncMock(return_value=[
        Position(
            symbol="BTC/USDT:USDT", side="long", contracts=0.0,
            entry_price=0.0, unrealized_pnl=0.0, leverage=1,
            liquidation_price=None,
        )
    ])
    deps.exchange.get_contract_size = AsyncMock(return_value=1.0)
    deps.exchange.fetch_balance = AsyncMock(return_value=Balance(
        total_usdt=1.0, free_usdt=1.0, used_usdt=0.0,
    ))
    deps.exchange.fetch_open_orders = AsyncMock(return_value=[])
    deps.exchange.get_price_level_alerts = MagicMock(return_value=[])
    deps.market_data = MagicMock()
    deps.market_data.get_ticker = AsyncMock(return_value=Ticker(
        symbol="BTC/USDT:USDT", last=1.0, bid=1.0, ask=1.0, high=1.0, low=1.0,
        base_volume=1.0, timestamp=0,
    ))
    snap = await _capture_state_snapshot("cyc-010", deps)
    assert snap["position"]["pnl_pct_of_notional"] is None


async def test_state_snapshot_always_returns_dict_never_none():
    """T-SS-11 (PR #35 I1): _capture_state_snapshot 永不 return None / raise，
    即使 deps.exchange / deps.market_data 全部 None 或抛异常仍返回完整 dict.

    锁住 cli/app.py:json.dumps(state_snapshot_var) 无条件不会 TypeError 的契约.
    schema state_snapshot 列虽然 nullable，但当前所有写入路径保证非 NULL.
    """
    # 极端 case: deps 几乎所有字段都设为 raise
    deps = MagicMock()
    deps.symbol = "BTC/USDT:USDT"
    deps.exchange = MagicMock()
    deps.exchange.fetch_positions = AsyncMock(side_effect=Exception("xxx"))
    deps.exchange.fetch_balance = AsyncMock(side_effect=Exception("xxx"))
    deps.exchange.fetch_open_orders = AsyncMock(side_effect=Exception("xxx"))
    deps.exchange.get_price_level_alerts = MagicMock(side_effect=Exception("xxx"))
    deps.exchange.get_alert_params = MagicMock(side_effect=Exception("xxx"))
    deps.market_data = MagicMock()
    deps.market_data.get_ticker = AsyncMock(side_effect=Exception("xxx"))

    snap = await _capture_state_snapshot("cyc-always-dict", deps)
    # 必须是 dict (不是 None)
    assert isinstance(snap, dict), f"应返回 dict 不是 {type(snap).__name__}"
    # _cycle_id 必填（绝不 KeyError）
    assert snap["_cycle_id"] == "cyc-always-dict"
    # _errors 必填 list (即使空)
    assert isinstance(snap["_errors"], list)
    # json.dumps 必须不抛 TypeError (锁住 cli/app.py 写入路径契约)
    serialized = json.dumps(snap)
    assert isinstance(serialized, str) and len(serialized) > 0


async def test_state_snapshot_captures_volatility_alert(deps_with_position):
    """T-SS-12 (B3): 设了波动告警 → snapshot.volatility_alert = {threshold_pct, window_minutes}。"""
    deps_with_position.exchange.get_alert_params = MagicMock(return_value=(1.5, 15))
    snap = await _capture_state_snapshot("c-vol-1", deps_with_position)
    assert snap["volatility_alert"] == {"threshold_pct": 1.5, "window_minutes": 15}


async def test_state_snapshot_volatility_alert_none_when_disabled(deps_with_position):
    """T-SS-13 (B3): 未设波动告警（get_alert_params 返 None）→ volatility_alert = None。"""
    deps_with_position.exchange.get_alert_params = MagicMock(return_value=None)
    snap = await _capture_state_snapshot("c-vol-2", deps_with_position)
    assert snap["volatility_alert"] is None


async def test_state_snapshot_volatility_alert_error_isolated(deps_with_position):
    """T-SS-14 (B3): getter 抛异常 → volatility_alert 留 None + _errors 标记、不抛。"""
    deps_with_position.exchange.get_alert_params = MagicMock(side_effect=RuntimeError("boom"))
    snap = await _capture_state_snapshot("c-vol-3", deps_with_position)
    assert snap["volatility_alert"] is None
    assert any("volatility_alert_read_failed" in e for e in snap["_errors"])


# === T-TC: trigger_context ===


def test_trigger_context_scheduled():
    """T-TC-1: scheduled trigger → {type: scheduled_tick}。"""
    result = _capture_trigger_context("cyc-tc1", "scheduled", None)
    assert result == {"type": "scheduled_tick"}


def test_trigger_context_fill_event():
    """T-TC-2: conditional FillEvent → 12 字段全保留 (P1-2)。"""
    fe = FillEvent(
        order_id="ord-1", symbol="BTC/USDT:USDT", side="sell",
        position_side="short", trigger_reason="stop_loss",
        fill_price=75600.0, amount=0.265, fee=1.5, pnl=-125.0,
        timestamp=1746098000000, is_full_close=True,
    )
    result = _capture_trigger_context("cyc-tc2", "conditional", fe)
    assert result["type"] == "fill"
    assert result["trigger_reason"] == "stop_loss"
    assert result["fee"] == 1.5
    assert result["position_side"] == "short"
    assert result["timestamp"] == 1746098000000
    assert result["is_full_close"] is True
    assert set(result.keys()) == {
        "type", "trigger_reason", "symbol", "side", "position_side",
        "amount", "fill_price", "fee", "pnl", "order_id", "timestamp",
        "is_full_close",
    }


def test_trigger_context_price_level_alert():
    """T-TC-3: PriceLevelAlertInfo → 7 字段含 alert_id (P1-1 + T2 mirror)。"""
    pla = PriceLevelAlertInfo(
        symbol="BTC/USDT:USDT", target_price=75600.0, direction="above",
        current_price=75623.0, reasoning="FOMC reaction watch",
        timestamp=1746098000000,
        alert_id="fomc0001",
    )
    result = _capture_trigger_context("cyc-tc3", "alert", pla)
    assert result["type"] == "price_level_alert"
    assert result["target_price"] == 75600.0
    assert result["timestamp"] == 1746098000000
    assert result["alert_id"] == "fomc0001"
    assert set(result.keys()) == {
        "type", "alert_id", "symbol", "current_price",
        "target_price", "direction", "reasoning", "timestamp",
    }


def test_trigger_context_percentage_alert():
    """T-TC-4: AlertInfo (percentage) → 7 字段 reference_price/change_pct/window_minutes (E4 校准)。"""
    ai = AlertInfo(
        symbol="BTC/USDT:USDT", current_price=76847.0, reference_price=75123.0,
        change_pct=2.3, window_minutes=60, timestamp=1746098000000,
    )
    result = _capture_trigger_context("cyc-tc4", "alert", ai)
    assert result["type"] == "percentage_alert"
    assert result["reference_price"] == 75123.0
    assert result["change_pct"] == 2.3
    assert result["window_minutes"] == 60
    assert set(result.keys()) == {
        "type", "symbol", "current_price", "reference_price",
        "change_pct", "window_minutes", "timestamp",
    }


def test_trigger_context_attribute_error_fallback():
    """T-TC-5 (Issue 2): context 类型不符 → AttributeError → return None + log warning。"""
    bad_ctx = MagicMock(spec=[])  # 无任何属性
    result = _capture_trigger_context("cyc-tc5", "conditional", bad_ctx)
    assert result is None


# === T-TH: _extract_thinking_text ===


def test_extract_thinking_from_messages_with_thinking_part():
    """T-TH-1: thinking model (mock ThinkingPart) → reasoning = 拼接 content。"""
    from pydantic_ai.messages import ModelResponse, TextPart, ThinkingPart
    from src.cli.app import _extract_thinking_text

    msgs = [
        ModelResponse(parts=[
            ThinkingPart(content="reasoning step 1"),
            TextPart(content="visible output"),
        ])
    ]
    text = _extract_thinking_text(msgs)
    assert text == "reasoning step 1"


def test_extract_thinking_no_thinking_part_returns_none():
    """T-TH-2: 非 thinking model (无 ThinkingPart) → reasoning = None。"""
    from pydantic_ai.messages import ModelResponse, TextPart
    from src.cli.app import _extract_thinking_text

    msgs = [ModelResponse(parts=[TextPart(content="output only")])]
    assert _extract_thinking_text(msgs) is None


def test_extract_thinking_multiple_parts_joined():
    """T-TH-3: 多个 ThinkingPart → 用 \\n\\n 拼接。"""
    from pydantic_ai.messages import ModelResponse, ThinkingPart
    from src.cli.app import _extract_thinking_text

    msgs = [
        ModelResponse(parts=[ThinkingPart(content="part 1")]),
        ModelResponse(parts=[ThinkingPart(content="part 2")]),
    ]
    text = _extract_thinking_text(msgs)
    assert text == "part 1\n\npart 2"


def test_extract_thinking_no_truncation():
    """T-TH-4: thinking content 长度 > 4000 → 不截断。"""
    from pydantic_ai.messages import ModelResponse, ThinkingPart
    from src.cli.app import _extract_thinking_text

    long_text = "x" * 5000
    msgs = [ModelResponse(parts=[ThinkingPart(content=long_text)])]
    text = _extract_thinking_text(msgs)
    assert len(text) == 5000


# === webui-cycle-react-timeline: _safe_build_react_steps ===

def test_safe_build_react_steps_serializes():
    from src.cli.app import _safe_build_react_steps
    from tests.fixtures.cycle_fixtures import build_cycle_messages
    import json
    msgs = build_cycle_messages(
        thinking_segments=["t1"],
        tool_call_segments=[[("get_position", {}, "flat")]],
        final_text="decision",
    )
    raw = _safe_build_react_steps(msgs)
    assert raw is not None
    parsed = json.loads(raw)
    assert parsed[0]["tools"][0]["tool_name"] == "get_position"


def test_safe_build_react_steps_none_on_empty():
    from src.cli.app import _safe_build_react_steps
    assert _safe_build_react_steps([]) is None     # 空骨架 → None（不存 "[]"）


def test_safe_build_react_steps_isolates_exception(monkeypatch):
    """build 抛异常 → None（fail-isolated，绝不阻断 AgentCycle 写入，§5.3）。"""
    import src.cli.app as app_mod
    from src.cli.app import _safe_build_react_steps

    def boom(messages):
        raise RuntimeError("parts schema changed")
    monkeypatch.setattr(app_mod, "build_react_steps", boom)
    assert _safe_build_react_steps(["anything"]) is None
