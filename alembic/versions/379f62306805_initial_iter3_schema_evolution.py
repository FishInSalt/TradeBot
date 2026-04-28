"""initial_iter3_schema_evolution

Revision ID: 379f62306805
Revises: 
Create Date: 2026-04-29 00:10:54.217746

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

from src.storage.models import NAMING_CONVENTION   # ← ADDED

# revision identifiers, used by Alembic.
revision: str = '379f62306805'
down_revision: Union[str, Sequence[str], None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Step 1: 索引 rename（廉价 drop+create，不动数据）
    op.drop_index("ix_sim_orders_session_status", table_name="sim_orders")
    op.create_index("ix_sim_orders_session_id_status", "sim_orders", ["session_id", "status"])
    op.drop_index("ix_tool_calls_session_tool_time", table_name="tool_calls")
    op.create_index(
        "ix_tool_calls_session_id_tool_name_created_at",
        "tool_calls",
        ["session_id", "tool_name", "created_at"],
    )
    op.drop_index("ix_tool_calls_cycle", table_name="tool_calls")
    op.create_index("ix_tool_calls_cycle_id", "tool_calls", ["cycle_id"])

    # Step 2: tool_calls.args 直接 ADD COLUMN（无重建）
    op.add_column("tool_calls", sa.Column("args", sa.Text(), nullable=True))

    # Step 3: trade_actions.cycle_id 直接 ADD COLUMN
    op.add_column("trade_actions", sa.Column("cycle_id", sa.String(50), nullable=True))

    # Step 4: decision_logs batch_alter（3 个 ops + 约束名对齐 A.2）
    # 合并语义: Step 4a-4c 在 batch_alter 上下文内合并为单次 SQLite 表重建
    # （CREATE _new + INSERT SELECT + DROP old + RENAME），不是 3 次 109 行数据 copy。
    # 由 alembic batch 退出时合并 ops 行为（recreate-on-exit）保证。
    with op.batch_alter_table(
        "decision_logs",
        naming_convention=NAMING_CONVENTION,
    ) as batch_op:
        # 4a: decision String(50)→String(20)
        batch_op.alter_column("decision", type_=sa.String(20), existing_nullable=False)
        # 4b: 新增 status，server_default='ok' 让 INSERT _new SELECT old 满足 NOT NULL
        # 关键设计：server_default **保留**（不在 batch 内 alter 移除），原因详见 spec §4.2 "为什么保留 server_default"
        batch_op.add_column(
            sa.Column("status", sa.String(30), nullable=False, server_default="ok")
        )
        # 4c: 新增 ix_decision_logs_session_id_cycle_id（spec §T3-1 合并）
        batch_op.create_index(
            "ix_decision_logs_session_id_cycle_id",
            ["session_id", "cycle_id"],
        )

    # Step 5: 历史数据 backfill（109 行 decision 标 legacy）
    # 5a: 防御性 catch-net — pathological 行（decision='usage_limit_exceeded'）先抓 status 再标 legacy
    # 实测 W1 DB 0 行匹配（spec §1.2 注脚），此 UPDATE 在 W1 现状下空跑；保留是防御性零成本
    # 注意顺序：必须先抓 status 再无差别 backfill decision，否则 5b 跑完无 'usage_limit_exceeded' 行可抓
    op.execute(
        "UPDATE decision_logs SET status = 'usage_limit_exceeded' "
        "WHERE decision = 'usage_limit_exceeded'"
    )
    # 5b: 全部 decision 标 legacy（109 行，含 5a 已抓 status 的行）
    op.execute("UPDATE decision_logs SET decision = 'legacy'")
    # status 字段对未匹配 5a 的行已被 Step 4b 的 server_default 自动填充为 'ok'


def downgrade() -> None:
    # Step 5 逆向：decision='legacy' 不还原（破坏性 backfill 无可恢复源；B1 决议 109 行不可信）

    # Step 4 逆向：decision_logs batch_alter 全部回退
    with op.batch_alter_table("decision_logs", naming_convention=NAMING_CONVENTION) as batch_op:
        batch_op.drop_index("ix_decision_logs_session_id_cycle_id")
        batch_op.drop_column("status")
        batch_op.alter_column("decision", type_=sa.String(50), existing_nullable=False)

    # Step 3 逆向
    op.drop_column("trade_actions", "cycle_id")

    # Step 2 逆向
    op.drop_column("tool_calls", "args")

    # Step 1 逆向：索引名复原（drop convention 名 → 重建旧手写名）
    op.drop_index("ix_tool_calls_cycle_id", table_name="tool_calls")
    op.create_index("ix_tool_calls_cycle", "tool_calls", ["cycle_id"])
    op.drop_index("ix_tool_calls_session_id_tool_name_created_at", table_name="tool_calls")
    op.create_index(
        "ix_tool_calls_session_tool_time",
        "tool_calls",
        ["session_id", "tool_name", "created_at"],
    )
    op.drop_index("ix_sim_orders_session_id_status", table_name="sim_orders")
    op.create_index("ix_sim_orders_session_status", "sim_orders", ["session_id", "status"])
