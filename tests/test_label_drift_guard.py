"""F5 — analyze_sim ↔ diff_sim row-label drift guard.

spec docs/superpowers/specs/2026-05-09-iter-w2r2-obs-followup-a-design.md §5.
pyproject asyncio_mode='auto' — no @pytest.mark.asyncio needed.

NOTE: 使用 AsyncSession + sessionmaker 拿 ORM 对象（与 scripts/analyze_sim.py:55-71
一致）。直接 engine.connect() + select(SessionModel) + scalar_one() 在 Connection
级别返回的是 first column (id: str) 而非 ORM 实体，后续 session.id / session.symbol 会失败。

`engine` fixture 复用 tests/conftest.py:26-29（in-memory），无需本文件重定义。
"""
from __future__ import annotations

import re
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import sessionmaker

from src.storage.models import Session as SessionModel

from scripts.analyze_sim import _render_pnl, _render_cost, _render_behavior
from scripts.diff_sim import PNL_LABELS, COST_STATIC_LABELS, BEH_STATIC_LABELS
from tests._sim_fixtures import make_session


_LABEL_ROW_RE = re.compile(r"^\|\s*([^\|]+?)\s*\|")


def _parse_label_column(md_output: str) -> set[str]:
    """Extract first-column labels from a markdown table; skip header + separator."""
    out: set[str] = set()
    for line in md_output.splitlines():
        m = _LABEL_ROW_RE.match(line)
        if not m:
            continue
        label = m.group(1).strip()
        if label in ("Metric", ""):  # header
            continue
        if set(label) <= set("-: "):  # separator row "|---|"
            continue
        out.add(label)
    return out


async def _load_session(engine, sid: str) -> SessionModel:
    """Load full ORM SessionModel via AsyncSession (parallel to analyze_sim.py:55-71)."""
    async_session = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with async_session() as db:
        result = await db.execute(select(SessionModel).where(SessionModel.id == sid))
        return result.scalars().one()


async def test_render_pnl_emits_all_pnl_labels_AC_F5_1(engine):
    """AC-F5-1: _render_pnl ⊇ PNL_LABELS."""
    sid = await make_session(engine, name="drift_pnl")
    session = await _load_session(engine, sid)
    output = await _render_pnl(engine, session, [])
    emitted = _parse_label_column(output)
    missing = set(PNL_LABELS) - emitted
    assert not missing, f"_render_pnl missing labels: {missing}"


async def test_render_cost_emits_all_cost_labels_AC_F5_2(engine):
    """AC-F5-2: _render_cost ⊇ COST_STATIC_LABELS."""
    sid = await make_session(engine, name="drift_cost")
    session = await _load_session(engine, sid)
    output = await _render_cost(engine, session)
    emitted = _parse_label_column(output)
    missing = set(COST_STATIC_LABELS) - emitted
    assert not missing, f"_render_cost missing labels: {missing}"


async def test_render_behavior_emits_all_beh_labels_AC_F5_3(engine):
    """AC-F5-3: _render_behavior ⊇ BEH_STATIC_LABELS."""
    sid = await make_session(engine, name="drift_beh")
    session = await _load_session(engine, sid)
    output = await _render_behavior(engine, session)
    emitted = _parse_label_column(output)
    missing = set(BEH_STATIC_LABELS) - emitted
    assert not missing, f"_render_behavior missing labels: {missing}"
