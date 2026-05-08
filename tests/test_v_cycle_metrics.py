"""AC-5: v_cycle_metrics 字段集 + 5-field anchor + cache_hit_rate_derived 派生正确。"""
import json

import pytest
from sqlalchemy import text

from src.storage.models import AgentCycle


@pytest.mark.asyncio
async def test_v_cycle_metrics_returns_38_columns(db_session):
    """T14.1: SELECT * FROM v_cycle_metrics 返回 38 列（spec §5.2 字段表）。"""
    # Insert a fixture row so SELECT * 不返回 empty
    db_session.add(AgentCycle(
        session_id="test-cols",
        cycle_id="cols01",
        triggered_by="scheduled",
        execution_status="ok",
        decision="placeholder",
        state_snapshot=json.dumps({"position": None}),
        tokens_consumed=0,
    ))
    await db_session.commit()

    rows = (await db_session.execute(text(
        "SELECT * FROM v_cycle_metrics WHERE session_id='test-cols'"
    ))).mappings().all()
    assert rows, "expected at least 1 row from fixture insert"

    cols = set(rows[0].keys())
    expected_subset = {
        "session_id", "cycle_id", "triggered_by", "execution_status",
        "created_at", "model_id",
        "wall_time_ms", "llm_call_ms", "tool_total_ms",
        "tokens_consumed", "input_tokens", "output_tokens",
        "cache_read_tokens", "cache_write_tokens",
        "reasoning_tokens", "cache_hit_rate", "cache_hit_rate_derived",
        "position_size", "position_side", "position_leverage",
        "position_unrealized_pnl", "position_pnl_pct",
        "balance_free_usdt", "ticker_last", "state_captured_at",
        "pending_orders_count", "active_alerts_count", "snapshot_errors_count",
        "has_position", "decision_length",
        "has_stance", "has_active_commitments", "has_this_cycle_delta",
        "has_thesis_invalidation", "has_watch_list", "five_field_complete",
        "is_ok_cycle", "is_forensic_cycle",
    }
    assert expected_subset.issubset(cols), f"missing: {expected_subset - cols}"
    assert len(cols) == 38, f"expected 38 cols got {len(cols)}: {cols}"


@pytest.mark.asyncio
async def test_v_cycle_metrics_5field_anchors_detect(db_session):
    """T14.2: 5-field LIKE 4-variant pattern 正确识别 fixture cycle。"""
    fixture_cycle = AgentCycle(
        session_id="test-anchor-5field",
        cycle_id="anchor01",
        triggered_by="scheduled",
        execution_status="ok",
        decision=(
            "(1) Stance: long, thesis intact.\n"
            "(2) Active commitments: 0.05 BTC long.\n"
            "(3) This cycle delta: noop.\n"
            "(4) Thesis & invalidation: trend up; SL @ 80000.\n"
        ),
        state_snapshot=json.dumps({"position": None, "balance": {"free_usdt": 100.0}}),
        tokens_consumed=1000, input_tokens=800, cache_read_tokens=500,
        wall_time_ms=2000,
    )
    db_session.add(fixture_cycle)
    await db_session.commit()

    row = (await db_session.execute(text(
        "SELECT has_stance, has_active_commitments, has_this_cycle_delta, "
        "has_thesis_invalidation, has_watch_list, five_field_complete "
        "FROM v_cycle_metrics WHERE session_id='test-anchor-5field'"
    ))).mappings().one()

    assert row["has_stance"] == 1
    assert row["has_active_commitments"] == 1
    assert row["has_this_cycle_delta"] == 1
    assert row["has_thesis_invalidation"] == 1
    assert row["has_watch_list"] == 0      # 缺 (5)
    assert row["five_field_complete"] == 1  # 4 mandatory met


@pytest.mark.asyncio
async def test_v_cycle_metrics_cache_hit_rate_derived(db_session):
    """T14.3: cache_hit_rate_derived = cache_read * 100 / input_tokens（portable 派生）。"""
    fixture_cycle = AgentCycle(
        session_id="test-cache-rate",
        cycle_id="rate01",
        triggered_by="scheduled",
        execution_status="ok",
        decision="placeholder",
        state_snapshot=json.dumps({"position": None}),
        tokens_consumed=1200,
        input_tokens=1000,
        cache_read_tokens=750,    # 75% hit
        cache_hit_rate=0.0,       # legacy 列若为 0 模拟非 DeepSeek
    )
    db_session.add(fixture_cycle)
    await db_session.commit()

    row = (await db_session.execute(text(
        "SELECT cache_hit_rate, cache_hit_rate_derived "
        "FROM v_cycle_metrics WHERE session_id='test-cache-rate'"
    ))).mappings().one()

    assert row["cache_hit_rate"] == 0.0      # legacy DeepSeek-only 字段
    assert row["cache_hit_rate_derived"] == pytest.approx(75.0)   # portable 派生


@pytest.mark.asyncio
async def test_v_cycle_metrics_tool_total_ms_zero_when_no_tools(db_session):
    """T14.5 (PR #42 fix): tool_total_ms COALESCE 让无 tool 调用 cycle = 0 不是 NULL,
    使下游 AVG/SUM 聚合不需特殊处理 NULL。"""
    fixture_cycle = AgentCycle(
        session_id="test-no-tools",
        cycle_id="notool01",
        triggered_by="scheduled",
        execution_status="ok",
        decision="hold without tool calls",
        state_snapshot=json.dumps({"position": None}),
        tokens_consumed=100,
    )
    db_session.add(fixture_cycle)
    await db_session.commit()

    row = (await db_session.execute(text(
        "SELECT tool_total_ms FROM v_cycle_metrics WHERE session_id='test-no-tools'"
    ))).mappings().one()

    assert row["tool_total_ms"] == 0    # COALESCE 把 SUM 的 NULL 转 0


@pytest.mark.asyncio
async def test_v_cycle_metrics_is_ok_excludes_empty_decision(db_session):
    """T14.4: is_ok_cycle 排除 empty-string decision（防 R2-7 result.output='' 边界）。"""
    fixture_cycle = AgentCycle(
        session_id="test-empty-decision",
        cycle_id="empty01",
        triggered_by="scheduled",
        execution_status="ok",
        decision="",                       # empty string
        state_snapshot=json.dumps({"position": None}),
        tokens_consumed=100,
    )
    db_session.add(fixture_cycle)
    await db_session.commit()

    row = (await db_session.execute(text(
        "SELECT is_ok_cycle FROM v_cycle_metrics WHERE session_id='test-empty-decision'"
    ))).mappings().one()

    assert row["is_ok_cycle"] == 0    # length(decision)=0 → 不算 ok
