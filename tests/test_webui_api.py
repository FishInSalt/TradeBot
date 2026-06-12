def test_schemas_importable():
    from src.webui import schemas
    s = schemas.LiveStatus(status="active", last_active_at=None, position=None,
                           open_orders=[], active_alerts=[])
    assert s.model_dump()["status"] == "active"


import pytest
from datetime import datetime, timezone
from fastapi.testclient import TestClient

from src.storage.database import get_session
from src.storage.models import Session as SessionModel, AgentCycle

UTC = timezone.utc


@pytest.fixture
async def seeded(engine):
    la = datetime(2026, 6, 12, 10, 0, tzinfo=UTC)
    async with get_session(engine) as s:
        s.add(SessionModel(id="s1", name="n1", symbol="BTC/USDT:USDT", initial_balance=10000.0,
                           status="active", scheduler_interval_min=15, last_active_at=la))
        s.add(AgentCycle(session_id="s1", cycle_id="c1", triggered_by="scheduled",
                         decision="d1", tokens_consumed=100, execution_status="ok",
                         state_snapshot='{"balance":{"total_usdt":10000.0}}', created_at=la))
        await s.commit()
    return engine


def _client(engine):
    from src.webui.app import create_app, get_engine
    app = create_app()
    app.dependency_overrides[get_engine] = lambda: engine
    return TestClient(app)


@pytest.mark.asyncio
async def test_api_endpoints(seeded):
    c = _client(seeded)
    assert c.get("/api/sessions").status_code == 200
    assert c.get("/api/sessions").json()[0]["id"] == "s1"
    assert c.get("/api/sessions/s1").json()["scheduler_interval_min"] == 15
    cyc = c.get("/api/sessions/s1/cycles").json()
    assert cyc[0]["cycle_label"] == "c1"
    pk = cyc[0]["id"]
    assert c.get(f"/api/cycles/{pk}").json()["decision"] == "d1"
    assert c.get("/api/sessions/s1/performance").json()["initial_balance"] == 10000.0
    live = c.get("/api/sessions/s1/live").json()
    assert live["status"] == "active"
    assert live["last_active_at"].endswith("Z")          # 出站时间戳带 Z（UTC 归一化）
    assert c.get("/api/cycles/999999").status_code == 404
    assert c.get("/api/sessions/nope/performance").status_code == 404   # 缺失会话统一 404
    assert c.get("/api/sessions/nope/live").status_code == 404
