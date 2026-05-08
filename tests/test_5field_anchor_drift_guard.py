"""AC-9: 5-field anchor 联合命中率 drift-guard.

CI 行为：pytest.skip(reason="sim DB not present") 当 --sim-db 未提供。
W3 上线前手动:
    pytest tests/test_5field_anchor_drift_guard.py \\
        --sim-db data/tradebot.db \\
        --session-id 8f2ca0cb-1c2a-4dc7-8927-198e6c0d98d3
"""
import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

# T13 Step 13.3.5 实测 baseline = 0.966 (sim #8 BTC, 177 ok cycles)
# 向下取 2 位小数最稳: 0.96
_BASELINE_HIT_RATE = 0.96
_DRIFT_THRESHOLD = _BASELINE_HIT_RATE - 0.05    # 0.91


@pytest.fixture
def sim_db_path(request):
    p = request.config.getoption("--sim-db")
    if not p:
        pytest.skip("sim DB not present (use --sim-db <path> to run drift-guard)")
    return p


@pytest.fixture
def session_id_filter(request):
    sid = request.config.getoption("--session-id")
    if not sid:
        pytest.skip("--session-id required for drift-guard scoping")
    return sid


@pytest.mark.asyncio
async def test_5field_anchor_drift_guard(sim_db_path, session_id_filter):
    """T20.2: AVG(five_field_complete) WHERE is_ok_cycle=1 ≥ baseline - 5pp。"""
    engine = create_async_engine(f"sqlite+aiosqlite:///{sim_db_path}")
    try:
        async with engine.connect() as conn:
            row = (await conn.execute(text(
                "SELECT AVG(five_field_complete) AS hit_rate, "
                "       COUNT(*) AS total "
                "FROM v_cycle_metrics "
                "WHERE is_ok_cycle=1 AND session_id=:sid"
            ), {"sid": session_id_filter})).mappings().one()
    finally:
        await engine.dispose()

    if row["total"] == 0:
        pytest.skip(f"no ok cycles for session {session_id_filter}")

    hit_rate = row["hit_rate"]
    assert hit_rate >= _DRIFT_THRESHOLD, (
        f"5-field anchor hit rate {hit_rate:.3f} < threshold {_DRIFT_THRESHOLD:.3f} "
        f"(baseline {_BASELINE_HIT_RATE:.3f}); persona LIKE pattern may have drifted, "
        f"see spec §6 AC-9 + §9 风险表 row 1"
    )
