"""p4_prompt_snapshot

Revision ID: 4ee6c95d0430
Revises: 61ac4841a55d
Create Date: 2026-05-10 15:52:33.470052

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

from src.storage.views import (
    V_CYCLE_METRICS_SQL,
    V_ALERT_LIFECYCLE_SQL,
    V_ORDER_LIFECYCLE_SQL,
    ALL_VIEW_NAMES,
)


# revision identifiers, used by Alembic.
revision: str = '4ee6c95d0430'
down_revision: Union[str, Sequence[str], None] = '61ac4841a55d'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table("sessions", schema=None) as batch_op:
        batch_op.add_column(sa.Column("system_prompt", sa.Text(), nullable=True))
    with op.batch_alter_table("agent_cycles", schema=None) as batch_op:
        batch_op.add_column(sa.Column("user_prompt_snapshot", sa.Text(), nullable=True))


def downgrade() -> None:
    # Drop views first — v_cycle_metrics references agent_cycles and SQLite's
    # batch_alter_table (temp-table rename) fails if any view references the table.
    for view in reversed(ALL_VIEW_NAMES):
        op.execute(f"DROP VIEW IF EXISTS {view}")

    with op.batch_alter_table("agent_cycles", schema=None) as batch_op:
        batch_op.drop_column("user_prompt_snapshot")
    with op.batch_alter_table("sessions", schema=None) as batch_op:
        batch_op.drop_column("system_prompt")

    # Re-create views — downgrade lands at Phase 1 head which still has 3 views.
    op.execute(V_CYCLE_METRICS_SQL)
    op.execute(V_ALERT_LIFECYCLE_SQL)
    op.execute(V_ORDER_LIFECYCLE_SQL)
