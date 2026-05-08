"""AC-3: trade_actions.alert_id 在 add + cancel 两个 callers 都正确写入。"""
import re

import pytest
from sqlalchemy import select

from src.agent.tools_execution import (
    add_price_level_alert, cancel_price_level_alert, open_position,
)
from src.storage.models import TradeAction


@pytest.mark.asyncio
async def test_add_price_level_alert_writes_alert_id(deps_with_sim_exchange, db_session):
    """T8.1: add_price_level_alert 后 trade_actions.alert_id 与 exchange 返回一致。"""
    result = await add_price_level_alert(
        deps_with_sim_exchange,
        price=80000.0, direction="above", reasoning="resistance test",
    )
    assert "Price level alert set" in result

    row = (await db_session.execute(
        select(TradeAction)
        .where(TradeAction.action == "add_price_level_alert")
        .order_by(TradeAction.id.desc()).limit(1)
    )).scalar_one()

    assert row.alert_id is not None
    assert len(row.alert_id) == 8       # uuid4()[:8] 8-char hex
    assert row.reasoning.startswith("above 80000.0 |")  # add 路径保留 prefix


@pytest.mark.asyncio
async def test_cancel_price_level_alert_writes_alert_id_no_prefix(deps_with_sim_exchange, db_session):
    """T8.2: cancel_price_level_alert 后 alert_id 落专列；reasoning 无 prefix。"""
    add_result = await add_price_level_alert(
        deps_with_sim_exchange,
        price=80000.0, direction="above", reasoning="initial",
    )
    match = re.search(r"id=([0-9a-f]{8})", add_result)
    assert match, f"expected id= in {add_result!r}"
    alert_id = match.group(1)

    cancel_result = await cancel_price_level_alert(
        deps_with_sim_exchange,
        alert_id=alert_id, reasoning="invalidation hit",
    )
    assert "cancelled" in cancel_result

    row = (await db_session.execute(
        select(TradeAction)
        .where(TradeAction.action == "cancel_price_level_alert")
        .order_by(TradeAction.id.desc()).limit(1)
    )).scalar_one()

    assert row.alert_id == alert_id
    assert row.reasoning == "invalidation hit"
    assert "id=" not in row.reasoning


@pytest.mark.asyncio
async def test_other_callers_alert_id_remains_null(deps_with_sim_exchange, db_session):
    """T8.3: 9 个 zero-改动 callers (open_position 等) trade_actions.alert_id 为 NULL。"""
    await open_position(
        deps_with_sim_exchange,
        side="long", position_pct=10.0, leverage=2,
        reasoning="test open",
    )

    row = (await db_session.execute(
        select(TradeAction)
        .where(TradeAction.action == "open_position")
        .order_by(TradeAction.id.desc()).limit(1)
    )).scalar_one()

    assert row.alert_id is None
