"""rebuild v_alert_lifecycle (array-unnest + delivery 双通道补进 existing DB)

修一处静默视图漂移：v_alert_lifecycle 的两次纯 views.py 改动从未到达 existing DB。
- #71-era：trigger_context 由单对象变 JSON 数组后，triggers CTE 改用 json_each
  逐元素取值（旧标量路径 `$.type` 对数组匹配 0 行）。
- iter-midcycle-event-injection (#74)：delivery 双通道列 + injected_events 分支。

两次都只改 src/storage/views.py 单源、没发重建迁移；而 existing DB 走 init_db Path 1
(`alembic upgrade head`)、_apply_views 只在空库 Path 3 跑且非幂等 → existing DB 的视图
冻结在 af87432ee6dd(net_pnl) 期的标量路径版本。后果（实测 sim#17/#19）：trigger_context
是数组 → 旧视图 triggers 匹配 0 → analyze_sim triggered_rate 恒报 0.0(真值 ~0.60)、
webui 把已触发告警显示为 active。纯取证/观测层失真，不碰 agent 决策/撮合。

本迁移把 existing DB 的 v_alert_lifecycle 重建为 views.py 当前单源（array-unnest +
delivery）。只重建这一个视图：v_cycle_metrics / v_order_lifecycle 实测 IN SYNC（这期间
没改过 views.py）。不碰 batch_alter_table，故无 dangling-view 重解析坑，无需 drop 另两视图。

downgrade 同样重建为当前单源（收敛式迁移）：本迁移无 schema delta 可逆——down_revision
8c48305247c3 处 fresh DB 的 canonical v_alert_lifecycle 本就等于当前 SQL（_apply_views
应用 views.py HEAD），故 downgrade 不回退到 stale 版本（那只会重新引入 bug），而是幂等
再断言一次当前 SQL。与 8c48305247c3.downgrade 重建全部视图的先例一致。当前 SQL 引用
injected_events 列——该列在 down_revision(8c48305247c3 > 7244c7b7185d) 仍在，故 SQL 有效。

Revision ID: b43e33764d90
Revises: 8c48305247c3
Create Date: 2026-06-13 21:39:03.580909

"""
from __future__ import annotations
from collections.abc import Sequence

from alembic import op

from src.storage.views import V_ALERT_LIFECYCLE_SQL


# revision identifiers, used by Alembic.
revision: str = "b43e33764d90"
down_revision: str | None = "8c48305247c3"
branch_labels: Sequence[str] | None = None
depends_on: Sequence[str] | None = None


def upgrade() -> None:
    op.execute("DROP VIEW IF EXISTS v_alert_lifecycle")
    op.execute(V_ALERT_LIFECYCLE_SQL)


def downgrade() -> None:
    # 收敛式迁移：见 module docstring——无 schema delta，downgrade 不回退到 stale 版本
    # （那会重新引入漂移 bug），而是幂等再应用当前单源 SQL。
    op.execute("DROP VIEW IF EXISTS v_alert_lifecycle")
    op.execute(V_ALERT_LIFECYCLE_SQL)
