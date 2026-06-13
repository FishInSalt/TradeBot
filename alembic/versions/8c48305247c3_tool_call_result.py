"""tool_call_result

Revision ID: 8c48305247c3
Revises: 7244c7b7185d
Create Date: 2026-06-13 20:43:36.209644

"""
from __future__ import annotations
from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa

from src.storage.views import ALL_VIEW_NAMES, ALL_VIEW_SQLS


# revision identifiers, used by Alembic.
revision: str = '8c48305247c3'
down_revision: str | None = '7244c7b7185d'
branch_labels: Sequence[str] | None = None
depends_on: Sequence[str] | None = None


def upgrade() -> None:
    # plain ADD COLUMN：SQLite 原生、不重建表、不触碰 view，无需 drop view
    op.add_column("tool_calls", sa.Column("result", sa.Text(), nullable=True))


def downgrade() -> None:
    # tool_calls 被 v_cycle_metrics / v_alert_lifecycle 引用；batch drop_column 的
    # temp-table rename 会重解析全部 view、rename 瞬间表不存在即炸。故先 DROP VIEW。
    # 重建用当前单源 ALL_VIEW_SQLS（无 view 引用 result，且 downgrade 落 7244c7b7185d
    # 处 injected_events 仍在 → 当前 SQL 全有效），不需 _PRE_ITER 冻结快照。
    for view in reversed(ALL_VIEW_NAMES):
        op.execute(f"DROP VIEW IF EXISTS {view}")
    with op.batch_alter_table("tool_calls", schema=None) as b:
        b.drop_column("result")
    for sql in ALL_VIEW_SQLS:
        op.execute(sql)
