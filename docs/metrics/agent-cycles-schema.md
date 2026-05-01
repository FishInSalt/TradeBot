# Agent Cycle Schema (R2-7 起)

本文档承载 `agent_cycles` 表 schema 演进 audit + 跨期 SQL 兼容性指南。
取代 `decision-enum-timeline.md`（已删除，R2-7 派生 enum 路线 deprecated）。

## 表结构（R2-7 起）

| 字段 | 类型 | 含义 |
|---|---|---|
| id | INTEGER PK | — |
| session_id | VARCHAR(36) FK | sessions.id |
| cycle_id | VARCHAR(50) | unique cycle id within session |
| triggered_by | VARCHAR(20) | scheduled / conditional / alert |
| trigger_context | TEXT NULL | JSON: trigger 瞬间客观快照 (FillEvent / PriceLevelAlertInfo / AlertInfo metadata) |
| state_snapshot | TEXT NULL | JSON: 决策时系统层面客观快照 (position / balance / market / pending_orders / active_alerts + _errors) |
| decision | TEXT NULL | agent 最终对外文本 (`result.output` message)；NULL for forensic path |
| execution_status | VARCHAR(30) DEFAULT 'ok' | ok / usage_limit_exceeded |
| reasoning | TEXT NULL | agent thinking content (LLM ThinkingPart 拼接)；NULL if non-thinking model 或 forensic |
| model_id | VARCHAR(100) NULL | LLM 模型 id |
| tokens_consumed | INTEGER DEFAULT 0 | LLM token 计数 (cycle 总, forensic 路径 = 0) |
| created_at | DATETIME | timestamp |

Index: `ix_agent_cycles_session_id_cycle_id` (session_id, cycle_id)

## trigger_context JSON schema

详见 spec `docs/superpowers/specs/2026-05-01-iter-w2r2-7-agent-cycle-schema-reframe-design.md` §4.3.

| trigger_type | content type tag | dataclass 来源 | 字段数 |
|---|---|---|---|
| scheduled | scheduled_tick | (无) | 1 |
| conditional | fill | FillEvent (base.py:269-281) | 12 (11 + 1 type) |
| alert | price_level_alert | PriceLevelAlertInfo (base.py:284-291) | 7 (6 + 1 type) |
| alert | percentage_alert | AlertInfo (price_alert.py:9-15) | 7 (6 + 1 type) |

## state_snapshot JSON schema

详见 spec §4.4。结构：
- `position`: Position dataclass 7 字段 + 衍生 pnl_pct (8 keys 或 null)
- `balance`: 3 字段 (total_usdt / free_usdt / used_usdt)
- `market`: ticker_last + ticker_timestamp (exchange ms epoch) + fetched_at (本机 ISO8601)
- `pending_orders`: list of Order dict (含 R2-7 §4.7 trigger_price 字段)
- `active_alerts`: list, single-symbol filter (cycle 单 symbol 上下文)
- `_errors`: list of "{type}_fetch_failed: {ExceptionType}" 字符串
- `_cycle_id`: 字段冗余便于 grep system.log

## 历史 enum 时点（W2 SQL 跨期分析参考）

| 时点 | decision 含义 | reasoning 含义 |
|---|---|---|
| 2026-04-08 ~ 2026-04-26 | 'completed' (硬编码) | message |
| 2026-04-26 (Iter 5) | 'usage_limit_exceeded' / 'completed' | message + str(e) |
| 2026-04-29 (Iter 4 PR #29) | enum 9 类（open_*/close/adjust/hold/derive_error/legacy）| message cap 4000 |
| 2026-04-30 (R2-4 PR #33) | enum 12 类（拆 4 子集 + 上述）| message cap 4000 |
| **2026-05-01 起 (R2-7)** | **message 自由文本** \| NULL | **thinking content** \| NULL |

## SQL 跨期分析建议

```sql
-- 旧数据 GROUP BY decision (W1 / sim #4 期)
SELECT decision, COUNT(*) FROM agent_cycles
WHERE created_at < '2026-05-01' GROUP BY decision;

-- 新数据 LIKE 检索 (W2 期)
SELECT cycle_id, decision FROM agent_cycles
WHERE created_at >= '2026-05-01' AND decision LIKE '%open%';
-- ⚠️ 自由文本，pivot 仅作粗筛，结果需人工 review
```

## R2-8 接口契约

R2-7 spec §8 已为 R2-8 (P1-7 展示 MVP + N10 reasoning 注入) 提供完整 display 设计契约：
- §8.1 cycle header (7-1 + 7-4)
- §8.2 cycle 末小结 + 累计统计 (7-3)
- §8.3 trigger_context 渲染 (7-5)
- §8.4 session 终结报告 (7-8)
- §8.5 字段消费契约 (display.py 接口签名)

R2-7 PR 内 display.py 不改 param 名（保留 trigger_type / agent_output / tokens_used 为渲染层标签）；
param rename 由 R2-8 PR 决定。
