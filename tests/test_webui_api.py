import pytest

pytest.importorskip("fastapi")  # fastapi 仅在 [webui] extra；缺失时跳过本模块而非收集阶段 ImportError

import json
from datetime import datetime, timezone

from fastapi.testclient import TestClient

from src.storage.database import get_session
from src.storage.models import Session as SessionModel, AgentCycle


def test_schemas_importable():
    from src.webui import schemas
    s = schemas.LiveStatus(status="active", last_active_at=None, position=None,
                           open_orders=[], active_alerts=[], tokens_consumed_total=0)
    assert s.model_dump()["status"] == "active"

UTC = timezone.utc


@pytest.fixture
async def seeded(engine):
    la = datetime(2026, 6, 12, 10, 0, tzinfo=UTC)
    async with get_session(engine) as s:
        s.add(SessionModel(id="s1", name="n1", symbol="BTC/USDT:USDT", initial_balance=10000.0,
                           status="active", scheduler_interval_min=15, last_active_at=la))
        s.add(AgentCycle(session_id="s1", cycle_id="c1", triggered_by="scheduled",
                         decision="d1", tokens_consumed=100, execution_status="ok",
                         trigger_context=json.dumps([{"type": "scheduled_tick"}]),  # 稳态 list 形态
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
    assert c.get("/api/sessions/s1").json()["system_prompt"] is None
    cyc = c.get("/api/sessions/s1/cycles").json()
    assert cyc[0]["cycle_label"] == "c1"
    pk = cyc[0]["id"]
    cd = c.get(f"/api/cycles/{pk}")
    assert cd.status_code == 200                                    # list trigger_context 不再 500（PR#75 回归）
    assert cd.json()["decision"] == "d1"
    assert cd.json()["trigger_context"] == [{"type": "scheduled_tick"}]
    assert c.get("/api/sessions/s1/performance").json()["initial_balance"] == 10000.0
    live = c.get("/api/sessions/s1/live").json()
    assert live["status"] == "active"
    assert live["last_active_at"].endswith("Z")          # 出站时间戳带 Z（UTC 归一化）
    assert c.get("/api/cycles/999999").status_code == 404
    assert c.get("/api/sessions/nope").status_code == 404               # 单资源缺失 → 404
    assert c.get("/api/sessions/nope/performance").status_code == 404
    assert c.get("/api/sessions/nope/live").status_code == 404
    # cycles 是集合端点：未知 session 返 200 + []（集合语义，非 404）
    missing_cycles = c.get("/api/sessions/nope/cycles")
    assert missing_cycles.status_code == 200
    assert missing_cycles.json() == []
    # limit 越界 → FastAPI 422（堵 limit=-1 → SQLite LIMIT -1 拉全量）
    assert c.get("/api/sessions/s1/cycles?limit=-1").status_code == 422
    assert c.get("/api/sessions/s1/cycles?limit=500").status_code == 422


@pytest.mark.asyncio
async def test_api_cycle_detail_includes_tool_result(engine):
    from src.storage.models import ToolCall as TC
    la = datetime(2026, 6, 12, 10, 0, tzinfo=UTC)
    async with get_session(engine) as s:
        s.add(SessionModel(id="s1", name="n1", symbol="BTC/USDT:USDT", initial_balance=10000.0,
                           status="active", scheduler_interval_min=15, last_active_at=la))
        s.add(AgentCycle(session_id="s1", cycle_id="c1", triggered_by="scheduled",
                         decision="d1", tokens_consumed=100, execution_status="ok",
                         trigger_context=json.dumps([{"type": "scheduled_tick"}]),
                         state_snapshot='{"balance":{"total_usdt":10000.0}}', created_at=la))
        s.add(TC(session_id="s1", cycle_id="c1", tool_name="get_market_data",
                 status="ok", duration_ms=8, args=None, result="=== Ticker ===\nlast 63000"))
        await s.commit()
    c = _client(engine)
    cyc = c.get("/api/sessions/s1/cycles").json()
    cd = c.get(f"/api/cycles/{cyc[0]['id']}")
    assert cd.status_code == 200
    tcs = cd.json()["tool_calls"]
    assert tcs[0]["result"] == "=== Ticker ===\nlast 63000"


def test_ohlcv_schemas_importable():
    from src.webui import schemas
    bar = schemas.OhlcvBar(at=datetime(2026, 6, 12, 10, 0, tzinfo=UTC),
                           open=1.0, high=2.0, low=0.5, close=1.5, volume=10.0)
    s = schemas.OhlcvSeries(symbol="BTC/USDT:USDT", timeframe="1h", bars=[bar])
    dumped = s.model_dump()
    assert dumped["timeframe"] == "1h"
    assert dumped["bars"][0]["open"] == 1.0


@pytest.mark.asyncio
async def test_ohlcv_endpoint(engine, monkeypatch):
    from datetime import timedelta
    from unittest.mock import AsyncMock
    import ccxt
    from src.webui import queries, ohlcv_cache
    start = datetime(2026, 5, 15, 12, 0, tzinfo=UTC)
    async with get_session(engine) as s:
        s.add(SessionModel(id="s1", name="n1", symbol="BTC/USDT:USDT", initial_balance=10000.0,
                           status="active", scheduler_interval_min=15, timeframe="1H",
                           created_at=start, last_active_at=start + timedelta(hours=2)))
        await s.commit()
    # 内存 engine → cache_dir None（断言不污染真 data/）；mock fetch
    monkeypatch.setattr(ohlcv_cache, "cache_dir_for", lambda eng: None)
    bars = [[1_778_846_400_000, 1.0, 2.0, 0.5, 1.5, 10.0]]
    monkeypatch.setattr(queries, "fetch_ohlcv_window", AsyncMock(return_value=bars))

    c = _client(engine)
    # 200 + 默认 tf = 会话归一 timeframe（1H → 1h）
    r = c.get("/api/sessions/s1/ohlcv")
    assert r.status_code == 200
    body = r.json()
    assert body["timeframe"] == "1h"
    assert body["symbol"] == "BTC/USDT:USDT"
    assert body["bars"][0]["at"].endswith("Z")          # UTC 归一带 Z
    # 显式合法 tf 透传
    assert c.get("/api/sessions/s1/ohlcv?timeframe=5m").json()["timeframe"] == "5m"
    # 显式非法 tf → 400
    assert c.get("/api/sessions/s1/ohlcv?timeframe=ZZ").status_code == 400
    assert c.get("/api/sessions/s1/ohlcv?timeframe=1M").status_code == 400   # 月，6 框外
    # 未知 sid → 404
    assert c.get("/api/sessions/nope/ohlcv").status_code == 404


@pytest.mark.asyncio
async def test_ohlcv_endpoint_fetch_failure_503(engine, monkeypatch):
    from datetime import timedelta
    from unittest.mock import AsyncMock
    import ccxt
    from src.webui import queries, ohlcv_cache
    start = datetime(2026, 5, 15, 12, 0, tzinfo=UTC)
    async with get_session(engine) as s:
        s.add(SessionModel(id="s1", name="n1", symbol="BTC/USDT:USDT", initial_balance=10000.0,
                           status="active", scheduler_interval_min=15, timeframe="1h",
                           created_at=start, last_active_at=start + timedelta(hours=2)))
        await s.commit()
    monkeypatch.setattr(ohlcv_cache, "cache_dir_for", lambda eng: None)
    monkeypatch.setattr(queries, "fetch_ohlcv_window",
                        AsyncMock(side_effect=ccxt.NetworkError("dead")))
    c = _client(engine)
    r = c.get("/api/sessions/s1/ohlcv")
    assert r.status_code == 503
    assert r.json()["detail"] == "NetworkError"          # 仅类名（redaction 纪律）
