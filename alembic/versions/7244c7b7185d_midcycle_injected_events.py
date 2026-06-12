"""iter-midcycle-event-injection: agent_cycles.injected_events nullable column

Mid-cycle 注入事件取证（JSON 数组 / NULL）。注：v_alert_lifecycle 的注入通道
SQL 只改 src/storage/views.py 单源（fresh DB 生效）——旧 DB 不重建 view，
与 #71 view 变更先例一致（历史数据本无注入行，旧 view 不失真）。

注：本迭代 Task 6 之后 views.py 的 v_alert_lifecycle 引用 injected_events 列——
downgrade 若用当前单源 SQL 重建，会留下引用已删列的 dangling view：CREATE 本身
lazy 成功，但任何后续 batch_alter_table 的 temp-table RENAME 会触发 SQLite 重解析
全部 view 而报错（实测 e70e70a8879d.downgrade 的 sessions rename 即炸，alembic
roundtrip 测试覆盖此链）。故 downgrade 用 _V_ALERT_LIFECYCLE_SQL_PRE_ITER 冻结
快照重建（与 af87432ee6dd._V_CYCLE_METRICS_SQL_PRE_ITER 同先例）；re-upgrade 时
af87432ee6dd 之上无 copy-rename 型 batch_alter，单源 SQL 在 head 恢复一致。

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
    V_ORDER_LIFECYCLE_SQL,
    ALL_VIEW_NAMES,
)


revision: str = "7244c7b7185d"
down_revision: str | None = "e70e70a8879d"
branch_labels: Sequence[str] | None = None
depends_on: Sequence[str] | None = None


# Pre-iter V_ALERT_LIFECYCLE_SQL (copy verbatim from `git show 6d2b560:src/storage/views.py`).
# Frozen for downgrade: the single-source SQL references injected_events post-Task-6,
# which no longer exists after this revision's downgrade drops the column.
_V_ALERT_LIFECYCLE_SQL_PRE_ITER = """
-- direction 不在本 view 投影 (spec §4.2 OOS); analyst 需 above/below 时从 trigger_context
-- 数组取 (spec 2026-06-08 起 trigger_context 是 JSON 数组):
--   SELECT json_extract(e.value, '$.direction')
--   FROM agent_cycles ac,
--        json_each(CASE WHEN json_type(ac.trigger_context)='array' THEN ac.trigger_context
--                       ELSE json_array(json(ac.trigger_context)) END) e
--   WHERE ac.cycle_id = <triggered cycle> AND json_extract(e.value,'$.type')='price_level_alert'
-- (T2 已 mirror PriceLevelAlertInfo.alert_id + direction 等到 trigger_context JSON;
--  PR #42 review v5 I-1)
CREATE VIEW v_alert_lifecycle AS
WITH registers AS (
  SELECT session_id, alert_id,
         created_at AS registered_at,
         price AS target_price,
         reasoning AS register_reasoning
  FROM trade_actions
  WHERE action='add_price_level_alert' AND alert_id IS NOT NULL
),
triggers AS (
  -- spec 2026-06-08: trigger_context is now a JSON array (one element per drained event);
  -- unnest it. Legacy single-object rows are wrapped in json_array() first — bare
  -- json_each('{...}') on an object iterates BY KEY (one row per field), polluting the
  -- result. Drop the old `triggered_by='alert'` clause: a price-level alert batched with a
  -- fill has triggered_by='conditional', so that clause would silently drop it; filter
  -- per-element on '$.type' instead. ALL per-element reads come from json_each.value.
  SELECT ac.session_id,
         json_extract(e.value, '$.alert_id') AS alert_id,
         ac.created_at AS triggered_at,
         CAST(json_extract(e.value, '$.current_price') AS REAL) AS triggered_price
  FROM agent_cycles ac,
       json_each(
         CASE WHEN json_type(ac.trigger_context) = 'array'
              THEN ac.trigger_context
              ELSE json_array(json(ac.trigger_context)) END
       ) e
  WHERE ac.trigger_context IS NOT NULL
    AND json_extract(e.value, '$.type') = 'price_level_alert'
    AND json_extract(e.value, '$.alert_id') IS NOT NULL
  -- ELSE branch assumes legacy rows are valid JSON (written by json.dumps); a manually
  -- inserted malformed string would raise at query time — not guarded (never happens in practice).
),
cancels AS (
  SELECT session_id, alert_id,
         created_at AS cancelled_at,
         reasoning AS cancel_reasoning
  FROM trade_actions
  WHERE action='cancel_price_level_alert' AND alert_id IS NOT NULL
),
cancel_attempts AS (
  -- IS NOT NULL filter aligns with registers/triggers/cancels CTEs above; without
  -- it, tool_calls 缺 alert_id key 的 row 会聚成单 NULL 组污染 raw CTE 输出
  -- (final SELECT 的 LEFT JOIN 当前掩盖此问题, 但 CTE 单独使用即坑; PR #42 review v5 I-3).
  SELECT session_id,
         json_extract(args, '$.alert_id') AS alert_id,
         COUNT(*) AS attempt_count,
         SUM(CASE WHEN status='biz_error' THEN 1 ELSE 0 END) AS attempt_failures
  FROM tool_calls
  WHERE tool_name='cancel_price_level_alert'
    AND json_extract(args, '$.alert_id') IS NOT NULL
  GROUP BY session_id, json_extract(args, '$.alert_id')
)
SELECT
  r.session_id,
  r.alert_id,
  r.registered_at,
  r.target_price,
  r.register_reasoning,
  t.triggered_at,
  t.triggered_price,
  c.cancelled_at,
  c.cancel_reasoning,
  COALESCE(ca.attempt_count, 0)    AS cancel_attempt_count,
  COALESCE(ca.attempt_failures, 0) AS cancel_attempt_failures,
  CASE
    WHEN t.triggered_at IS NOT NULL THEN 'triggered'
    WHEN c.cancelled_at IS NOT NULL THEN 'cancelled'
    ELSE 'active'
  END AS final_status
FROM registers r
LEFT JOIN triggers       t  ON t.session_id=r.session_id  AND t.alert_id=r.alert_id
LEFT JOIN cancels        c  ON c.session_id=r.session_id  AND c.alert_id=r.alert_id
LEFT JOIN cancel_attempts ca ON ca.session_id=r.session_id AND ca.alert_id=r.alert_id
"""


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
    op.execute(_V_ALERT_LIFECYCLE_SQL_PRE_ITER)
    op.execute(V_ORDER_LIFECYCLE_SQL)
