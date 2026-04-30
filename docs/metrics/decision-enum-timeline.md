# Decision Enum 演进时间线

本文档承载 `decision_logs.decision` 字段的 enum 取值演进 audit 与 SQL 兼容性指南。

## 当前可见取值（截至 R2-4，2026-04-30）

| Enum 值 | 引入时间 | 引入 PR | 仍在写入？ |
|---|---|---|---|
| `legacy` | Iter 3 | PR #28 | 否（仅历史 backfill）|
| `open_long` / `open_short` / `close` / `hold` | Iter 4 | PR #29 | 是 |
| `derive_error` | Iter 4 | PR #29 | 是（DB 故障 fallback）|
| `adjust` | Iter 4 | PR #29 | **否（R2-4 起停写）** |
| `adjust_protect` / `adjust_entry_order` / `adjust_leverage` / `adjust_alert` | R2-4 | PR #33 | 是 |

## 字段职责分工（设计锚点）

`decision_logs.decision` 与 `trade_actions` 表的职责分工：

| 表 | 职责 | 信息粒度 |
|---|---|---|
| `trade_actions` | fact-of-record（动作流水）| 每 action 一行，全保留 |
| `decision_logs.decision` | **降维标签**（cycle 主导决策）| 每 cycle 一个 enum 值 |

`decision_logs.decision` 字段当前 0 生产读取路径（grep 全 src 树），唯一未来读者是观察期 SQL 分析者（人工临时查询），目标是「按主导决策类型快速 pivot」。

让 decision 字段保留多值（数组 / 主从 / 位掩码）= **打破 decision_logs 的"降维"职责** = 与 trade_actions 表语义重复 = schema 设计退步。

## 单值 decision 与 trade_actions 下钻

decision_logs.decision 是「cycle 主导决策标签」，按优先级（protect > entry_order > leverage > alert）取最高一类。多类 adjust 共存时低优先级类别**不在此字段反映**，但 trade_actions 表保留 cycle 内全部动作。

### 何时 GROUP BY decision（粗粒度）
- cycle 模式分布、主导决策频率分析

### 何时 JOIN trade_actions（细粒度）
- 想看「cycle 内同时有 PROTECT 和 ALERT 的占比」
- 想看「首挂 SL/TP vs trailing」（结合 cycle 时序，stateful 分析）

例：cycle 内 PROTECT + ALERT 共存频率
```sql
SELECT COUNT(DISTINCT cycle_id) FROM trade_actions
WHERE cycle_id IN (
    SELECT cycle_id FROM trade_actions
    WHERE action IN ('set_stop_loss','set_take_profit')
  ) AND action IN ('set_price_alert','add_price_level_alert','cancel_price_level_alert');
```

## SQL 兼容性提示

跨观察期分析时（W2 vs W1/sim #4）若按 decision 细分：
- W1 / sim #4 旧数据中 adjust_* 表示为 `'adjust'`
- 新观察期数据使用 4 个 `adjust_*` 子类
- 兼容查询: `decision LIKE 'adjust%' OR decision = 'adjust'`

## R2-4 决策语境（参考）

详见：
- `.working/sim4-issues-inventory.md §P0-3`
- `docs/superpowers/specs/2026-04-30-iter-w2r2-4-biz-error-and-decision-subtypes-design.md §5`

不做 backfill 的理由（A 方案）:
1. W2 主分析路径不影响（新 session_id 物理隔离）
2. 跨 session tax 中等不阻塞（一个 OR 条件）
3. 派生函数 stateless，trade_actions 留底完整 → 任何时候可重派生
4. 不动旧数据是项目一贯做法（Iter 4 引入 derive_error 时未 retroactive update 旧 'legacy' 行）
5. 机器可读 audit 零成本保留（DB 行原值是历史 metrics 输出 ground truth）

## 派生优先级排序的设计假设

`protect > entry_order > leverage > alert` 是基于业务直觉的默认排序。sim #4 实证只直接验证了 protect + alert 共存场景（`fdf20e56`），其他组合（如 entry_order + leverage 共存）频率未知。

此排序是 placeholder default。**trade_actions 永远留底完整动作流水**——若 W2 数据反证某种排序不合实际，后续 PR 仅需重派生历史 `decision_logs.decision`（无需 schema 演进）。
