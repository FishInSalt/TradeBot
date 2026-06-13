"""react_steps and tool_call_id

Revision ID: 6f460e071308
Revises: b43e33764d90
Create Date: 2026-06-14 03:28:04.145085

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

from src.storage.views import ALL_VIEW_NAMES, ALL_VIEW_SQLS


# revision identifiers, used by Alembic.
revision: str = '6f460e071308'
down_revision: Union[str, Sequence[str], None] = 'b43e33764d90'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # plain ADD COLUMN：SQLite 原生、不重建表、不触碰 view，无需 drop view（§7）。
    op.add_column("agent_cycles", sa.Column("react_steps", sa.Text(), nullable=True))
    op.add_column("tool_calls", sa.Column("tool_call_id", sa.String(length=100), nullable=True))


def downgrade() -> None:
    # agent_cycles 与 tool_calls 均被 v_cycle_metrics 引用；batch drop_column 的 temp-table
    # rename 会重解析全部 view、rename 瞬间表不存在即炸。故先 DROP VIEW，drop 两列后用单源
    # ALL_VIEW_SQLS 重建（沿用 8c48305247c3 既有写法，§7）。
    for view in reversed(ALL_VIEW_NAMES):
        op.execute(f"DROP VIEW IF EXISTS {view}")
    with op.batch_alter_table("agent_cycles", schema=None) as b:
        b.drop_column("react_steps")
    with op.batch_alter_table("tool_calls", schema=None) as b:
        b.drop_column("tool_call_id")
    for sql in ALL_VIEW_SQLS:
        op.execute(sql)
