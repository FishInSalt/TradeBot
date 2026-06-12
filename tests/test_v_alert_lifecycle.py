"""AC-6: v_alert_lifecycle register/trigger/cancel 三态 + cancel_attempts 统计。"""
import json

import pytest
from sqlalchemy import text

from src.storage.models import TradeAction, AgentCycle, ToolCall


@pytest.mark.asyncio
async def test_alert_lifecycle_active_state(db_session):
    """T16.1: 仅 register 无 trigger/cancel → final_status='active'。"""
    db_session.add(TradeAction(
        session_id="test-lifecycle-active",
        cycle_id="cyc01",
        action="add_price_level_alert",
        alert_id="active01",
        symbol="BTC/USDT:USDT",
        price=80000.0,
        reasoning="above 80000.0 | resistance",
    ))
    await db_session.commit()

    row = (await db_session.execute(text(
        "SELECT final_status, target_price, cancel_attempt_count "
        "FROM v_alert_lifecycle WHERE alert_id='active01'"
    ))).mappings().one()

    assert row["final_status"] == "active"
    assert row["target_price"] == 80000.0
    assert row["cancel_attempt_count"] == 0


@pytest.mark.asyncio
async def test_alert_lifecycle_triggered_state(db_session):
    """T16.2: register + trigger cycle → final_status='triggered'。"""
    db_session.add(TradeAction(
        session_id="test-lifecycle-trig",
        cycle_id="cyc01", action="add_price_level_alert",
        alert_id="trig0001", symbol="BTC/USDT:USDT", price=80000.0,
        reasoning="above 80000",
    ))
    db_session.add(AgentCycle(
        session_id="test-lifecycle-trig",
        cycle_id="cyc02",
        triggered_by="alert",
        trigger_context=json.dumps({
            "type": "price_level_alert",
            "alert_id": "trig0001",
            "current_price": 80050.0,
            "target_price": 80000.0,
            "direction": "above",
        }),
        state_snapshot=json.dumps({"position": None}),
        decision="hold",
    ))
    await db_session.commit()

    row = (await db_session.execute(text(
        "SELECT final_status, triggered_at, triggered_price "
        "FROM v_alert_lifecycle WHERE alert_id='trig0001'"
    ))).mappings().one()

    assert row["final_status"] == "triggered"
    assert row["triggered_at"] is not None
    assert row["triggered_price"] == 80050.0


@pytest.mark.asyncio
async def test_alert_lifecycle_cancelled_state(db_session):
    """T16.3: register + cancel → final_status='cancelled'。"""
    db_session.add(TradeAction(
        session_id="test-lifecycle-cancel",
        cycle_id="cyc01", action="add_price_level_alert",
        alert_id="canc0001", symbol="BTC/USDT:USDT", price=80000.0,
        reasoning="above 80000",
    ))
    db_session.add(TradeAction(
        session_id="test-lifecycle-cancel",
        cycle_id="cyc02", action="cancel_price_level_alert",
        alert_id="canc0001", symbol="BTC/USDT:USDT",
        reasoning="invalidated",
    ))
    await db_session.commit()

    row = (await db_session.execute(text(
        "SELECT final_status, cancelled_at FROM v_alert_lifecycle WHERE alert_id='canc0001'"
    ))).mappings().one()

    assert row["final_status"] == "cancelled"
    assert row["cancelled_at"] is not None


@pytest.mark.asyncio
async def test_alert_lifecycle_cancel_attempts_aggregation(db_session):
    """T16.4: cancel_attempts 累计 tool_calls 调用数 + biz_error 失败数。"""
    db_session.add(TradeAction(
        session_id="test-lifecycle-attempts",
        cycle_id="cyc01", action="add_price_level_alert",
        alert_id="att00001", symbol="BTC/USDT:USDT", price=80000.0,
        reasoning="above",
    ))
    db_session.add(ToolCall(
        session_id="test-lifecycle-attempts", cycle_id="cyc02",
        tool_name="cancel_price_level_alert", status="ok", duration_ms=100,
        args=json.dumps({"alert_id": "att00001", "reasoning": "invalidated"}),
    ))
    db_session.add(ToolCall(
        session_id="test-lifecycle-attempts", cycle_id="cyc03",
        tool_name="cancel_price_level_alert", status="biz_error",
        error_type="alert_not_found", duration_ms=50,
        args=json.dumps({"alert_id": "att00001", "reasoning": "retry"}),
    ))
    await db_session.commit()

    row = (await db_session.execute(text(
        "SELECT cancel_attempt_count, cancel_attempt_failures "
        "FROM v_alert_lifecycle WHERE alert_id='att00001'"
    ))).mappings().one()

    assert row["cancel_attempt_count"] == 2
    assert row["cancel_attempt_failures"] == 1


@pytest.mark.asyncio
async def test_alert_lifecycle_triggered_via_array(db_session):
    """New model: trigger_context is a JSON array; the price_level_alert element resolves."""
    db_session.add(TradeAction(
        session_id="test-arr", cycle_id="c1", action="add_price_level_alert",
        alert_id="arr0001", symbol="BTC/USDT:USDT", price=80000.0, reasoning="above 80000",
    ))
    db_session.add(AgentCycle(
        session_id="test-arr", cycle_id="c2", triggered_by="alert",
        trigger_context=json.dumps([
            {"type": "price_level_alert", "alert_id": "arr0001",
             "current_price": 80050.0, "target_price": 80000.0, "direction": "above"},
        ]),
        state_snapshot=json.dumps({"position": None}), decision="hold",
    ))
    await db_session.commit()
    row = (await db_session.execute(text(
        "SELECT final_status, triggered_price FROM v_alert_lifecycle WHERE alert_id='arr0001'"
    ))).mappings().one()
    assert row["final_status"] == "triggered"
    assert row["triggered_price"] == 80050.0


@pytest.mark.asyncio
async def test_alert_lifecycle_alert_batched_with_fill(db_session):
    """A price-level alert batched with a fill has triggered_by='conditional' — the
    dropped `triggered_by='alert'` clause means it must STILL resolve."""
    db_session.add(TradeAction(
        session_id="test-mix", cycle_id="c1", action="add_price_level_alert",
        alert_id="mix0001", symbol="BTC/USDT:USDT", price=80000.0, reasoning="above 80000",
    ))
    db_session.add(AgentCycle(
        session_id="test-mix", cycle_id="c2", triggered_by="conditional",  # dominant = fill
        trigger_context=json.dumps([
            {"type": "fill", "trigger_reason": "tp", "symbol": "BTC/USDT:USDT"},
            {"type": "price_level_alert", "alert_id": "mix0001",
             "current_price": 80050.0, "target_price": 80000.0, "direction": "above"},
        ]),
        state_snapshot=json.dumps({"position": None}), decision="hold",
    ))
    await db_session.commit()
    row = (await db_session.execute(text(
        "SELECT final_status, triggered_price FROM v_alert_lifecycle WHERE alert_id='mix0001'"
    ))).mappings().one()
    assert row["final_status"] == "triggered"
    assert row["triggered_price"] == 80050.0


@pytest.mark.asyncio
async def test_alert_lifecycle_two_alerts_one_batch_fan_out(db_session):
    """Two distinct price_level_alerts batched in one cycle's trigger_context array each
    resolve to their own view row (json_each fan-out)."""
    for aid, price in (("batch_a1", 80000.0), ("batch_a2", 81000.0)):
        db_session.add(TradeAction(
            session_id="test-fanout", cycle_id="c1", action="add_price_level_alert",
            alert_id=aid, symbol="BTC/USDT:USDT", price=price, reasoning=f"above {price}",
        ))
    db_session.add(AgentCycle(
        session_id="test-fanout", cycle_id="c2", triggered_by="alert",
        trigger_context=json.dumps([
            {"type": "price_level_alert", "alert_id": "batch_a1",
             "current_price": 80050.0, "target_price": 80000.0, "direction": "above"},
            {"type": "price_level_alert", "alert_id": "batch_a2",
             "current_price": 81050.0, "target_price": 81000.0, "direction": "above"},
        ]),
        state_snapshot=json.dumps({"position": None}), decision="hold",
    ))
    await db_session.commit()
    rows = (await db_session.execute(text(
        "SELECT alert_id, final_status, triggered_price FROM v_alert_lifecycle "
        "WHERE session_id='test-fanout' ORDER BY alert_id"
    ))).mappings().all()
    assert len(rows) == 2
    assert rows[0]["alert_id"] == "batch_a1" and rows[0]["final_status"] == "triggered"
    assert rows[0]["triggered_price"] == 80050.0
    assert rows[1]["alert_id"] == "batch_a2" and rows[1]["triggered_price"] == 81050.0


@pytest.mark.asyncio
async def test_alert_lifecycle_filters_null_alert_id(db_session):
    """T16.5: 历史数据 alert_id NULL 行被 view 自动过滤（不污染输出）。"""
    db_session.add(TradeAction(
        session_id="test-lifecycle-null",
        cycle_id="cyc01", action="add_price_level_alert",
        alert_id=None,
        symbol="BTC/USDT:USDT", price=80000.0,
        reasoning="legacy row",
    ))
    await db_session.commit()

    rows = (await db_session.execute(text(
        "SELECT * FROM v_alert_lifecycle WHERE session_id='test-lifecycle-null'"
    ))).mappings().all()

    assert len(rows) == 0    # NULL alert_id 完全不进 view


# === iter-midcycle-event-injection §7: injected 通道 + delivery 列 ===

@pytest.mark.asyncio
async def test_injected_channel_triggers_alert(db_session):
    """注入消费的 alert 经 injected_events 通道可见：triggered + delivery='injected'。"""
    db_session.add(TradeAction(
        session_id="test-lc-inj", cycle_id="cyc01",
        action="add_price_level_alert", alert_id="inj00001",
        symbol="BTC/USDT:USDT", price=61634.0, reasoning="below 61634",
    ))
    db_session.add(AgentCycle(
        session_id="test-lc-inj", cycle_id="cyc02", triggered_by="scheduled",
        injected_events=json.dumps([{
            "event": {
                "type": "price_level_alert", "alert_id": "inj00001",
                "symbol": "BTC/USDT:USDT", "current_price": 61630.5,
                "target_price": 61634.0, "direction": "below",
                "reasoning": "below 61634", "timestamp": 1765300000000,
            },
            "after_tool": "get_taker_flow", "offset_ms": 73000,
        }]),
        state_snapshot=json.dumps({"position": None}), decision="noted",
    ))
    await db_session.commit()

    row = (await db_session.execute(text(
        "SELECT final_status, triggered_at, triggered_price, delivery "
        "FROM v_alert_lifecycle WHERE alert_id='inj00001'"
    ))).mappings().one()
    assert row["final_status"] == "triggered"
    assert row["triggered_at"] is not None
    assert row["triggered_price"] == 61630.5
    assert row["delivery"] == "injected"


@pytest.mark.asyncio
async def test_wake_channel_delivery_label(db_session):
    """既有 wake 通道行为不回归 + delivery='wake' 标注。"""
    db_session.add(TradeAction(
        session_id="test-lc-wake", cycle_id="cyc01",
        action="add_price_level_alert", alert_id="wak00001",
        symbol="BTC/USDT:USDT", price=80000.0, reasoning="above 80000",
    ))
    db_session.add(AgentCycle(
        session_id="test-lc-wake", cycle_id="cyc02", triggered_by="alert",
        trigger_context=json.dumps([{
            "type": "price_level_alert", "alert_id": "wak00001",
            "current_price": 80050.0, "target_price": 80000.0, "direction": "above",
        }]),
        state_snapshot=json.dumps({"position": None}), decision="hold",
    ))
    await db_session.commit()

    row = (await db_session.execute(text(
        "SELECT final_status, delivery FROM v_alert_lifecycle WHERE alert_id='wak00001'"
    ))).mappings().one()
    assert row["final_status"] == "triggered"
    assert row["delivery"] == "wake"


@pytest.mark.asyncio
async def test_dual_channel_each_delivered_once(db_session):
    """双通道并存不漏：同 session 一 wake 一 injected，各自恰好一行。

    注：本测试覆盖的是"两个不同 alert 各走一通道"。"同一 alert 同时出现在
    trigger_context 与 injected_events"的去重边界 view **刻意不守护**——
    该互斥是注入语义（注入即消费）的运行期不变量，若真出现双行，本身就是
    上游 bug 的取证信号，UNION ALL 不去重恰好让它可见。"""
    db_session.add_all([
        TradeAction(
            session_id="test-lc-dual", cycle_id="cyc01",
            action="add_price_level_alert", alert_id="dualwake",
            symbol="BTC/USDT:USDT", price=80000.0, reasoning="above 80000",
        ),
        TradeAction(
            session_id="test-lc-dual", cycle_id="cyc01",
            action="add_price_level_alert", alert_id="dualinje",
            symbol="BTC/USDT:USDT", price=61634.0, reasoning="below 61634",
        ),
        AgentCycle(
            session_id="test-lc-dual", cycle_id="cyc02", triggered_by="alert",
            trigger_context=json.dumps([{
                "type": "price_level_alert", "alert_id": "dualwake",
                "current_price": 80050.0,
            }]),
            injected_events=json.dumps([{
                "event": {"type": "price_level_alert", "alert_id": "dualinje",
                          "current_price": 61630.5},
                "after_tool": "get_position", "offset_ms": 1000,
            }]),
            state_snapshot=json.dumps({"position": None}), decision="busy cycle",
        ),
    ])
    await db_session.commit()

    rows = (await db_session.execute(text(
        "SELECT alert_id, delivery FROM v_alert_lifecycle "
        "WHERE session_id='test-lc-dual' ORDER BY alert_id"
    ))).mappings().all()
    assert [(r["alert_id"], r["delivery"]) for r in rows] == [
        ("dualinje", "injected"), ("dualwake", "wake"),
    ]


@pytest.mark.asyncio
async def test_injected_null_event_capture_failure_skipped(db_session):
    """capture best-effort 失败形态 {"event": null}：view 安全跳过零垃圾行。

    writer（midcycle_injector）在 _capture_trigger_context 返 None 时合法产生该形态；
    json_extract('$.event.type') 对 null event 得 NULL，被 type 过滤吸收——本测试把
    该 SQLite json 语义钉成回归守护（Task 6 quality review Minor 2）。"""
    db_session.add(TradeAction(
        session_id="test-lc-null", cycle_id="cyc01",
        action="add_price_level_alert", alert_id="nullcap1",
        symbol="BTC/USDT:USDT", price=61634.0, reasoning="below 61634",
    ))
    db_session.add(AgentCycle(
        session_id="test-lc-null", cycle_id="cyc02", triggered_by="scheduled",
        injected_events=json.dumps([
            {"event": None, "after_tool": "get_position", "offset_ms": 1},
        ]),
        state_snapshot=json.dumps({"position": None}), decision="noted",
    ))
    await db_session.commit()

    row = (await db_session.execute(text(
        "SELECT final_status, delivery FROM v_alert_lifecycle WHERE alert_id='nullcap1'"
    ))).mappings().one()
    assert row["final_status"] == "active", "null-event 记录不得让 alert 误判 triggered"
    assert row["delivery"] is None
