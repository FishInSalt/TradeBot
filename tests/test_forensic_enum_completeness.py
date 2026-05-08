"""AC-12: forensic enum drift-guard — 防 R2-Next-J 等加新 forensic enum 后 view 漏判."""
import pytest
from sqlalchemy import text


@pytest.mark.asyncio
async def test_no_unknown_execution_status_enum(db_session):
    """T21.1: agent_cycles.execution_status ∈ {ok, retry_exhausted, usage_limit_exceeded}.

    如有新 enum 出现 → fail 提示需同步 v_cycle_metrics.is_forensic_cycle CASE 列举。
    与 spec §9 风险表 row 2 同源。
    """
    rows = (await db_session.execute(text(
        "SELECT DISTINCT execution_status "
        "FROM agent_cycles "
        "WHERE execution_status NOT IN ('ok','retry_exhausted','usage_limit_exceeded')"
    ))).mappings().all()

    unknown = [r["execution_status"] for r in rows]
    assert not unknown, (
        f"Unknown forensic enum(s) detected: {unknown}. "
        f"Update v_cycle_metrics.is_forensic_cycle CASE in alembic migration "
        f"and add the enum to this test's whitelist."
    )
