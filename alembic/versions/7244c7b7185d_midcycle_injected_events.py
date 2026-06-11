"""iter-midcycle-event-injection: agent_cycles.injected_events nullable column

Mid-cycle 注入事件取证（JSON 数组 / NULL）。注：v_alert_lifecycle 的注入通道
SQL 只改 src/storage/views.py 单源（fresh DB 生效）——旧 DB 不重建 view，
与 #71 view 变更先例一致（历史数据本无注入行，旧 view 不失真）。

Revision ID: 7244c7b7185d
Revises: e70e70a8879d
Create Date: 2026-06-12 02:48:51.296888
"""
from __future__ import annotations
from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa

from src.storage.views import (
    V_CYCLE_METRICS_SQL,
    V_ALERT_LIFECYCLE_SQL,
    V_ORDER_LIFECYCLE_SQL,
    ALL_VIEW_NAMES,
)


revision: str = "7244c7b7185d"
down_revision: str | None = "e70e70a8879d"
branch_labels: Sequence[str] | None = None
depends_on: Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("agent_cycles", schema=None) as batch_op:
        batch_op.add_column(sa.Column("injected_events", sa.Text(), nullable=True))


def downgrade() -> None:
    # Drop views first — views reference agent_cycles and SQLite's
    # batch_alter_table (temp-table rename) fails if any view references the
    # table（与 4ee6c95d0430 p4_prompt_snapshot downgrade 同先例）。
    for view in reversed(ALL_VIEW_NAMES):
        op.execute(f"DROP VIEW IF EXISTS {view}")

    with op.batch_alter_table("agent_cycles", schema=None) as batch_op:
        batch_op.drop_column("injected_events")

    # Re-create views — downgrade lands at e70e70a8879d which still has 3 views.
    op.execute(V_CYCLE_METRICS_SQL)
    op.execute(V_ALERT_LIFECYCLE_SQL)
    op.execute(V_ORDER_LIFECYCLE_SQL)
