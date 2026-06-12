"""FastAPI 只读观察台。薄 HTTP 层：解析参数 → 调 queries → 返回 schemas。"""
from __future__ import annotations

import os
from pathlib import Path

from fastapi import Depends, FastAPI, HTTPException, Query, Request
from fastapi.staticfiles import StaticFiles
from sqlalchemy.ext.asyncio import AsyncEngine

from src.webui import queries, schemas
from src.webui.db import make_readonly_engine

_DEFAULT_DB = "data/tradebot.db"


def get_engine(request: Request) -> AsyncEngine:    # 测试用 dependency_overrides[get_engine] 覆盖
    return request.app.state.engine


def create_app(db_path: str | None = None) -> FastAPI:
    app = FastAPI(title="TradeBot WebUI", docs_url="/api/docs", openapi_url="/api/openapi.json")
    app.state.engine = make_readonly_engine(db_path or os.environ.get("TRADEBOT_DB", _DEFAULT_DB))

    @app.get("/api/sessions", response_model=list[schemas.SessionSummary])
    async def _sessions(eng: AsyncEngine = Depends(get_engine)):
        return await queries.list_sessions(eng)

    @app.get("/api/sessions/{sid}", response_model=schemas.SessionDetail)
    async def _session(sid: str, eng: AsyncEngine = Depends(get_engine)):
        d = await queries.get_session_detail(eng, sid)
        if d is None:
            raise HTTPException(404, "session not found")
        return d

    @app.get("/api/sessions/{sid}/cycles", response_model=list[schemas.CycleRow])
    async def _cycles(sid: str, limit: int = Query(50, ge=1, le=200),
                      before_id: int | None = None, after_id: int | None = None,
                      eng: AsyncEngine = Depends(get_engine)):
        # limit 由 FastAPI 校验 [1,200]（越界 422）——堵住 limit=-1 → SQLite LIMIT -1 拉全量的口子
        return await queries.get_cycles(eng, sid, limit=limit,
                                        before_id=before_id, after_id=after_id)

    @app.get("/api/cycles/{pk}", response_model=schemas.CycleDetail)
    async def _cycle(pk: int, eng: AsyncEngine = Depends(get_engine)):
        d = await queries.get_cycle_detail(eng, pk)
        if d is None:
            raise HTTPException(404, "cycle not found")
        return d

    @app.get("/api/sessions/{sid}/performance", response_model=schemas.Performance)
    async def _perf(sid: str, eng: AsyncEngine = Depends(get_engine)):
        p = await queries.get_performance(eng, sid)
        if p is None:
            raise HTTPException(404, "session not found")
        return p

    @app.get("/api/sessions/{sid}/live", response_model=schemas.LiveStatus)
    async def _live(sid: str, eng: AsyncEngine = Depends(get_engine)):
        ls = await queries.get_live_status(eng, sid)
        if ls is None:
            raise HTTPException(404, "session not found")
        return ls

    # 前端静态资源（Phase 1b 产出 frontend/dist）；存在才挂，避免开发期报错
    dist = Path(__file__).resolve().parent.parent.parent / "frontend" / "dist"
    if dist.is_dir():
        app.mount("/", StaticFiles(directory=str(dist), html=True), name="spa")

    return app


app = create_app()
