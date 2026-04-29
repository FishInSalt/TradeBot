# Iter 4 — DecisionLog 写入路径补全 (T0-1 PR-B)

**Date**: 2026-04-29
**Branch**: `feature/iter-t0-1-decisionlog-write-path`
**Source todo**: `.working/pre-next-observation-todos.md` §T0-1 PR-B
**Brainstorm 决议**: §B1 (decision/status 双字段) / §B2 (market_summary deprecated) / §T0-1 (写入路径补全) / §8.1 (窗口期 mismatch 回填)
**前置依赖**:
1. Iter 3 PR #28 merge (`81af223`, 2026-04-29 12:07:26 +08:00) — code-level schema 演进 + Alembic infra 已就位
2. **user 必须先重启 main.py 一次** 让 `init_db` 三态 sentinel path 2 触发 `stamp base` + `upgrade head`，migration 此时才真正 apply 到 production DB（schema 加 `status` 列 + 109 行历史 backfill `status='ok' / decision='legacy'`）

**实测前置状态校准（2026-04-29 spec review 时）**:
- `data/tradebot.db.decision_logs` 实测 **109 行** 全部 `decision='completed'`
- schema 仍是 pre-Iter3：无 `status` 列，`decision VARCHAR(50)`，无 `alembic_version` 表
- → Iter 3 migration **尚未 apply**；本 Iter 实施 / 验证 / backfill 必须等 user 重启后再做
**预估工作量**: 0.5 天（含 8 个测试 + 窗口期 SQL 回填）

---

## 1. 背景与动机

### 1.1 Iter 3 留下的"半成品"状态

Iter 3 仅完成 schema 演进（双字段就位）+ 历史 backfill；**写入路径未变**：

- `app.py:253` 仍硬编码 `decision="completed"`
- `app.py:170` forensic 路径仍写 `decision="usage_limit_exceeded"`（B1 决议视为语义冲突 — `decision` 字面是状态、`status` 字面是 ok）
- `reasoning` 仍 `[:500]` 硬截断（W1 实测 max 800-1500 chars，60-70% 长尾被切）
- 双字段 schema 实际未被有效写入新数据

→ Iter 3 merge 到 Iter 4 merge 的窗口期内，任何 usage_limit 触发都会产生 `decision='usage_limit_exceeded' AND status='ok'` 的 mismatch 行（紧密衔接下应 0 / 个位数）。

### 1.2 已完成的 brainstorm 决议（沿用，不重做）

- **§B1**: 双字段方案 — `decision` 写决策类型派生（`open_long/open_short/close/adjust/hold`），`status` 写执行状态（`ok/usage_limit_exceeded`）；正交 2D 交叉分析能力是核心动机
- **§B2**: `market_summary` deprecated — 写入路径**不传**该字段；schema 字段保留 nullable（C 档时一并 drop）；替代路径走 `tool_calls.cycle_id` JOIN
- **§T0-1**: 写入路径双处改 + reasoning cap 调到 4000 + 测试断言迁移
- **§8.1**: 第一步 SQL 回填窗口期 mismatch 行，固化 `iter3_merge_iso8601 = 2026-04-29T04:07:26+00:00`（UTC）

### 1.3 本 Iter 内做出的 brainstorm 校准

- **C1 (forensic 路径 decision 取值)**: `decision=_derive_decision_from_actions(...)` — 与成功路径**完全一致**的派生（usage_limit 触发时 `_record_action` 的独立 session 已 commit 部分 actions，可信）；正交 2D 矩阵 (`agent 决定 open 但 cycle 病理失败`) 由此查询才有意义
- **C2 (派生函数签名)**: `async def _derive_decision_from_actions(session, session_id, cycle_id) -> str` — 复用 outer session（避免冗余连接）
- **C3 (派生函数位置)**: `src/cli/app.py` 内部 private helper — YAGNI，未来若有第二消费方再抽 `src/storage/derivations.py`
- **C4 (回填执行机制)**: spec 内固化 SQL + UTC ISO8601；user 在 PR merge 后手动跑一次（紧密衔接下行数极少，脚本化收益不抵复杂度）；不进 Alembic（data fix 不属于 schema 变更）
- **C5 (adjust_actions 集合)**: 选项 (c) — `set_next_wake` **单独归 hold**（语义：排下次醒非交易动作）。`adjust_actions` 8 个：`set_stop_loss / set_take_profit / adjust_leverage / set_price_alert / add_price_level_alert / cancel_price_level_alert / place_limit_order / cancel_order`。hold 触发：cycle 0 actions **或** 仅含 `set_next_wake`
- **C6 (测试覆盖)**: 8 个派生函数单元测试（5 enum × 边界 + 优先级 + session 隔离 + set_next_wake-only → hold）+ 集成测增量

### 1.4 与未来 Iter 的关系

明牌后置项详见 §2.2 out-of-scope 表（O1-O6）；本 Iter 不重复列。

---

## 2. 设计目标

### 2.1 In-scope（5 类必须改动）

- **G1**: `app.py` 成功路径补全（`decision` 派生 + `status="ok"` + `reasoning[:4000]`；维持不传 `market_summary`）
- **G2**: `app.py` forensic 路径补全（`decision` 派生 + `status="usage_limit_exceeded"` + `reasoning[:4000]`；维持不传 `market_summary`）
- **G3**: 新增 `_derive_decision_from_actions` private helper（async，复用 session）
- **G4**: 测试迁移（test_usage_limits.py:103 + grep 排查 + 8 个派生函数单测 + 集成测增量）
- **G5**: 第一步窗口期 mismatch 行 SQL 回填（PR merge 后由 user 跑）

### 2.2 Out-of-scope（明牌不做）

| # | 项 | 留给 |
|---|---|---|
| O1 | LLM 3-retry 失败 `return None` 路径不写 DecisionLog | W2 spec follow-up |
| O2 | `market_summary` drop column | C 档 schema 演进 PR |
| O3 | 历史 109 行 `decision='legacy'` 重新派生 | 已弃，永久保留 'legacy' |
| O4 | `decision` 改 SQLAlchemy `Enum` 类型 | YAGNI，String(20) + drift guard test 即可 |
| O5 | `status` 扩展到 `error / partial` 等值 | 仅保留 `ok / usage_limit_exceeded` 两值 |
| O6 | 改 11 个 `_record_action` 调用站点的 action 字面量 | 行为兼容，不动 |

---

## 3. 派生函数设计 (G3)

### 3.1 签名

```python
async def _derive_decision_from_actions(
    session: AsyncSession,
    session_id: str,
    cycle_id: str,
) -> str:
    """从 trade_actions 反查 cycle 内已 commit 的 actions，按优先级派生 decision 类型。

    优先级（高 → 低）：open_position > close_position > adjust > hold
    返回 5 类 enum 之一: open_long / open_short / close / adjust / hold

    note: usage_limit 路径下，_record_action 的独立 session 已 commit
    部分 actions，本函数读取的视图是该时点 DB 的真实快照。
    """
```

### 3.2 派生逻辑

```python
ADJUST_ACTIONS = frozenset({
    "set_stop_loss",
    "set_take_profit",
    "adjust_leverage",
    "set_price_alert",
    "add_price_level_alert",
    "cancel_price_level_alert",
    "place_limit_order",
    "cancel_order",
})
# Note: set_next_wake / open_position / close_position 不在此集合
# (per spec §C5 决议: set_next_wake 单独归 hold)


async def _derive_decision_from_actions(
    session: AsyncSession,
    session_id: str,
    cycle_id: str,
) -> str:
    try:
        rows = (await session.execute(
            select(TradeAction).where(
                TradeAction.session_id == session_id,
                TradeAction.cycle_id == cycle_id,
            ).order_by(TradeAction.id)  # first-match 语义稳定（防 cycle 极端有 2 条 open_position 时返回非确定）
        )).scalars().all()
    except (SQLAlchemyError, OSError):  # 仅吞 DB 故障类；代码 bug fail-fast
        logger.exception(
            f"derive_decision SELECT failed for cycle {cycle_id}; falling back to 'derive_error'"
        )
        return "derive_error"  # 独立 enum 不污染 'legacy' 历史语义（详见 §8.1 enum 表）

    for a in rows:
        if a.action == "open_position":
            if a.side not in ("long", "short"):
                # spec §3.5: skip 此 row，downstream 接管
                continue
            return f"open_{a.side}"  # open_long / open_short
    if any(a.action == "close_position" for a in rows):
        return "close"
    if any(a.action in ADJUST_ACTIONS for a in rows):
        return "adjust"
    return "hold"  # 0 actions OR 仅含 set_next_wake
```

**Source-of-truth 假设（重要）**: 派生函数依赖 `trade_actions` 表是 cycle 内行为的真源。`tools_execution.py:25-40` `_record_action` 包裹 `try/except` + `logger.warning("Failed to record TradeAction")` — **写入失败时仅 log warning 不抛错**。极端情况："订单真成功但 trade_actions 写入失败" → 派生函数反查得错误结果（如真开仓但派生为 'hold'）。属已知数据完整性 gap，**本 Iter 不修**；W2 观察期 SQL 解读时需注意此误判可能。

**异常处理范围（spec §3.2 fallback）**: 收窄到 `(SQLAlchemyError, OSError)` — 仅吞 DB 连接 / 文件系统类故障；`TypeError` / `AttributeError` / `KeyError` 等代码 bug fail-fast 抛出（forensic 路径整条 DecisionLog 写入失败 → cycle 丢失，但 bug 立即暴露）。理由：代码 bug 应在 T1-T8 测试期暴露，到 prod 仍 raise 比静默落 `'derive_error'` 错值更好。`SQLAlchemyError` 从 `sqlalchemy.exc` 导入。

### 3.3 优先级语义

| 优先级 | 触发条件 | 输出 |
|---|---|---|
| 1 | cycle 含至少一个 `open_position` | `open_{side}` (long/short) |
| 2 | cycle 含 `close_position`（无 open） | `close` |
| 3 | cycle 含 `ADJUST_ACTIONS` 任一（且无 open/close） | `adjust` |
| 4 | cycle 0 actions **或** 仅含 `set_next_wake` | `hold` |

注意优先级是 **early-return** 语义：到第 3 行时一定已无 open/close（被 1/2 拦截）。`[set_stop_loss, set_next_wake]` 这种组合命中第 3 行（`set_stop_loss` 是 ADJUST），不会因含 `set_next_wake` 而退到 hold。

`open_position` 取首个匹配项的 `side`（实操中一个 cycle 不会同时出现 long+short open，先按宽容设计）。

### 3.4 enum 长度核对（与 String(20) 约束）

| 值 | 字符数 |
|---|---|
| `open_long` | 9 |
| `open_short` | 10 |
| `close` | 5 |
| `adjust` | 6 |
| `hold` | 4 |
| `derive_error` | 12 |

最长 `derive_error` = 12，String(20) 余量充足 ✅。

### 3.5 边界与异常处理

**`open_position.side ∉ {"long", "short"}` 兜底**（覆盖 NULL 与异常字符串两类）：

`trade_actions.side` schema 为 `String(10) nullable=True`，无 enum 约束。两类污染源：

1. **NULL**: 实操中 11 个 `_record_action` 站点（spec §8.3）全部传具体 side，但 schema nullable 允许 NULL
2. **异常字符串**: `tools_execution.py` 内 `open_position` 的 `order_side = "buy" if side == "long" else "sell"` 是 fallthrough 二元判定 — 任何非 `"long"` 字符串都按 short 单子处理，但 `_record_action(side=side)` 写入 `trade_actions.side` 是**原字面量**。LLM 若生成 `side="banana"` → 订单按 sell 走但 DB 存 `side="banana"`

派生函数必须兜底防止 `f"open_None"` / `f"open_banana"` 类污染：

```python
for a in rows:
    if a.action == "open_position":
        if a.side not in ("long", "short"):
            logger.warning(
                f"open_position with unexpected side={a.side!r} "
                f"in cycle {cycle_id}; skipping this row, downstream "
                f"classification (close/adjust/hold) takes over"
            )
            continue  # 跳过此 row，循环继续；后续 close/adjust/hold 分支接管
        return f"open_{a.side}"
```

**实际 fallback 落点取决于 cycle 内其他 actions**：
- `[open_position(side=None)]` → 0 其他 action → `'hold'`
- `[open_position(side=None), set_stop_loss]` → 命中 adjust → `'adjust'`
- `[open_position(side=None), close_position]` → 命中 close → `'close'`

实施阶段在 plan 内细化此分支的测试（T8.5: open_position(side=None) + adjust 类 → `'adjust'` + warning log）。

---

## 4. 写入路径改动 (G1 + G2)

### 4.1 成功路径 (G1) — `app.py:248-258`

```python
# Before (Iter 3 落地后)
async with get_session(engine) as session:
    session.add(
        DecisionLog(
            session_id=deps.session_id,
            cycle_id=cycle_id,
            trigger_type=trigger_type,
            decision="completed",                                    # 硬编码
            reasoning=result.output[:500],                            # 硬截断
            model_used=getattr(model, 'model_name', str(model)) if model else str(agent.model),
            tokens_used=tokens,
        )
    )
    await session.commit()

# After
async with get_session(engine) as session:
    decision = await _derive_decision_from_actions(
        session, deps.session_id, cycle_id
    )
    session.add(
        DecisionLog(
            session_id=deps.session_id,
            cycle_id=cycle_id,
            trigger_type=trigger_type,
            decision=decision,                                        # 派生
            status="ok",                                              # 双字段方案
            reasoning=result.output[:4000],                           # cap 4000
            model_used=getattr(model, 'model_name', str(model)) if model else str(agent.model),
            tokens_used=tokens,
        )
    )
    await session.commit()
```

**market_summary 措辞校准**: 当前代码块**从未**传过 `market_summary`（B2 实测 grep `market_summary=` codebase 0 处）。本 Iter 不新增写入即满足 B2 决议；非 "改为不传"，是 "维持不传 + 显式确认"。无代码删除项。

### 4.2 forensic 路径 (G2) — `app.py:165-175`

```python
# Before
except UsageLimitExceeded as e:
    logger.error(f"Cycle {cycle_id} hit usage limit: {e}")
    async with get_session(engine) as session:
        session.add(DecisionLog(
            session_id=deps.session_id,
            cycle_id=cycle_id,
            trigger_type=trigger_type,
            decision="usage_limit_exceeded",                          # 语义冲突
            reasoning=str(e)[:500],                                   # 硬截断
            model_used=getattr(model, 'model_name', str(model)) if model else str(agent.model),
            tokens_used=0,
        ))
        await session.commit()
    return None

# After
except UsageLimitExceeded as e:
    logger.error(f"Cycle {cycle_id} hit usage limit: {e}")
    async with get_session(engine) as session:
        decision = await _derive_decision_from_actions(
            session, deps.session_id, cycle_id
        )
        session.add(DecisionLog(
            session_id=deps.session_id,
            cycle_id=cycle_id,
            trigger_type=trigger_type,
            decision=decision,                                        # 派生（spec §C1）
            status="usage_limit_exceeded",                            # 双字段方案
            reasoning=str(e)[:4000],                                  # cap 4000
            model_used=getattr(model, 'model_name', str(model)) if model else str(agent.model),
            tokens_used=0,
        ))
        await session.commit()
    return None
```

### 4.3 双路径正交矩阵

| `decision` | `status='ok'` | `status='usage_limit_exceeded'` |
|---|---|---|
| `open_long` | 正常开多仓完成 | agent 想开多仓 / cycle 中 limit 触发 |
| `open_short` | 正常开空仓完成 | agent 想开空仓 / cycle 中 limit 触发 |
| `close` | 正常平仓完成 | agent 想平仓 / cycle 中 limit 触发 |
| `adjust` | 正常调整 SL/TP/alert/limit/lev | agent 调整中 / cycle 中 limit 触发 |
| `hold` | 纯观察 / 仅排下次醒 | agent 还没决策 / cycle 中 limit 触发 |

→ 这是 §B1 双字段方案的**核心实现**。

---

## 5. 测试覆盖 (G4)

### 5.1 派生函数单元测试（8 个）

新建 `tests/test_derive_decision.py`：

| # | 用例 | 输入 (cycle 内 actions) | 期望返回 |
|---|---|---|---|
| T1 | `open_long` 派生 | `[open_position(side='long')]` | `"open_long"` |
| T2 | `open_short` 派生 | `[open_position(side='short')]` | `"open_short"` |
| T3 | `close` 派生 | `[close_position]` | `"close"` |
| T4 | `adjust` 派生 | `[set_stop_loss]` | `"adjust"` |
| T5 | `hold` — 0 actions | `[]` | `"hold"` |
| T6 | `hold` — 仅 set_next_wake | `[set_next_wake]` | `"hold"` |
| T7 | 优先级 — open + adjust 同 cycle | `[open_position(side='long'), set_stop_loss]` | `"open_long"` |
| T8 | session 隔离 | session_A cycle X 有 open；查 session_B cycle X | `"hold"`（不互窜） |

**T8 实操含义**: cycle_id 实测生成是 `str(uuid.uuid4())[:8]`（`app.py:113`，UUID4 前 8 chars，16^8 = 4.3B 空间）。单 session 内碰撞概率极低，但**跨 session 长尾下可能重复**。T8 双重目的：
1. 防 SELECT 漏 `session_id` WHERE 子句的 defensive sanity check
2. 防真实跨 session cycle_id 碰撞导致误派生

**Fixture 备注**: T6/T7/T8 需向测试 DB 真插入 1+ 行 `TradeAction`（T7 需 2 行同 cycle，T8 需跨 session）；T1-T5 同样需要 fixture 但行数 ≤1。具体 fixture 模板由 writing-plans 阶段细化。

### 5.2 现有测试迁移

**C1 — `tests/test_usage_limits.py:103`**:

```python
# Before
select(DecisionLog).where(DecisionLog.decision == "usage_limit_exceeded")

# After
select(DecisionLog).where(DecisionLog.status == "usage_limit_exceeded")
```

T2 整体语义改为：forensic 路径写一行 `status='usage_limit_exceeded'`，`decision` 是派生值（mock 路径下无 actions → `'hold'`）。

**C2 — grep 全 codebase 排查其他依赖硬编码**:

```bash
# 实施起手必跑：
grep -rn 'decision == "completed"' tests/ src/
grep -rn 'decision="completed"' tests/ src/
grep -rn 'decision == "usage_limit_exceeded"' tests/ src/
grep -rn 'decision="usage_limit_exceeded"' tests/ src/
```

预期命中点：
- `src/cli/app.py:253` — 本 Iter 改
- `src/cli/app.py:170` — 本 Iter 改
- `tests/test_usage_limits.py:103` — 本 Iter 迁移
- 其他命中处需逐一审，决议是否同步迁移

### 5.3 集成测增量

- T9: 成功路径写 `status='ok'` + `reasoning` 截断到 4000（喂超长 result.output 验证）
- T10: forensic 路径写 `status='usage_limit_exceeded'` + `decision` 派生（mock 已 commit 的 trade_actions 验证派生联通）

### 5.4 Drift guard (T11) — **强制**

`tools_execution.py` 11 个 `_record_action(action="...")` 字面量是 `ADJUST_ACTIONS ∪ {open_position, close_position, set_next_wake}` 的真源。**T11 drift guard 必须实施**（与 Iter 5 D' "加新工具忘配置 → CI fail" 同款纪律）：

```python
import re
from pathlib import Path

# spec §5.4 固化：单行正则方案 + 站点数 sanity check
_ACTION_LITERAL_RE = re.compile(r'_record_action\b[^)]*?\baction\s*=\s*["\']([a-z_]+)["\']', re.DOTALL)
_EXPECTED_RECORD_ACTION_SITES = 11  # spec §8.3 实测


def _grep_record_action_literals(path: str) -> set[str]:
    """单行正则扫 tools_execution.py 内 _record_action(...) 调用块的 action 字面量。
    实测 spec §8.3 — 11 个站点全部跨 4 行 kwargs，action="..." 在 await 后第 2 行单行字面量。"""
    src = Path(path).read_text()
    matches = _ACTION_LITERAL_RE.findall(src)
    # Sanity: 防 regex false-empty / 站点被重命名漏扫
    assert len(matches) == _EXPECTED_RECORD_ACTION_SITES, (
        f"扫描站点数 {len(matches)} ≠ 期望 {_EXPECTED_RECORD_ACTION_SITES}（spec §8.3）；"
        f"可能 regex 失效或站点被重命名/新增 — 实测命中: {matches}"
    )
    return set(matches)


def test_adjust_actions_drift_guard():
    """tools_execution.py 内所有 _record_action 调用站点的 action 字面量
    必须落入 derive_decision_type 的分类（adjust 集合 / open_position /
    close_position / set_next_wake），否则新增 action 漏分类会静默落 hold。"""
    actual_actions = _grep_record_action_literals("src/agent/tools_execution.py")
    expected_categories = ADJUST_ACTIONS | {"open_position", "close_position", "set_next_wake"}
    drift = actual_actions - expected_categories
    assert not drift, f"新增未分类的 action: {drift}（请更新 ADJUST_ACTIONS 或派生逻辑）"
```

理由：未来 Iter 7+ 若新增 `_record_action(action="X")` 而忘配置派生逻辑，新 action 会静默落 hold（污染 W2 观察期 SQL 分析）。drift guard 让此类漏配置在 CI 阶段暴露。**不是 nice-to-have，是必须**。

**同款实测参考**（Iter 5 D' T8）: `tests/test_trader_agent.py:69` `test_registered_tool_names_matches_agent_tools` — 断言 `REGISTERED_TOOL_NAMES` ↔ `create_trader_agent` 实际注册一致。本 Iter T11 是相同模式（声明集合 ↔ 真实代码字面量）的应用。

**方案固化（spec 阶段决议，不留 plan）**: 选用单行正则（`_ACTION_LITERAL_RE`）+ 站点数 sanity check（`== 11`）。理由：
- spec §8.3 实测 11 个站点**全部** `action="..."` 在 await 后第 2 行单行字面量 — 单行正则覆盖完整
- 站点数 sanity 防 regex false-empty（如 regex 写错全零命中）和"新加站点漏扫" 双向保护
- AST 方案（`ast.parse` + walk `Call`）鲁棒但复杂，当前信息完备时收益不抵复杂度
- 未来若 `_record_action` 调用改成 dict-spread 或动态 action（不太可能），切 AST 路径再议

### 5.5 派生输出长度 drift guard (T12) — **强制**

T11 检测的是**输入侧**（`_record_action` action 字面量是否落入分类）；T12 检测**输出侧**（派生输出 enum 字符串是否仍 ≤ String(20) 约束）。低成本防未来加新 enum 超约束：

```python
def test_derive_output_fits_decision_column():
    """派生函数输出 enum 字符串必须 ≤ DecisionLog.decision String(20)。
    防未来加新 enum (如 'open_long_with_alert' = 19) 后再加一字符直接溢出。"""
    enum_values = {"open_long", "open_short", "close", "adjust", "hold", "derive_error"}
    assert max(len(v) for v in enum_values) <= 20, \
        f"派生输出 > 20 chars: {[v for v in enum_values if len(v) > 20]}"
```

`derive_error` 一并纳入（派生函数 try/except fallback 路径输出，spec §3.2 source-of-truth 假设段）。`legacy` **不**纳入此集合 — 它是 Iter 3 historical-only 标识，**不是 Iter 4+ 派生函数运行时输出**。

---

## 6. 第一步窗口期 mismatch 行回填 (G5)

**前置（强制）**: user 必须先重启 main.py 让 Iter 3 migration apply（参见 §1.1 实测前置状态校准）；否则本节所有 SQL 因 `status` 列不存在 fail (`Error: no such column: status`)。

**Dry-run 建议**: 跑回填 SQL 前先 `SELECT created_at FROM decision_logs LIMIT 1;` 核对实存格式（实测 2026-04-29 时是 naive `YYYY-MM-DD HH:MM:SS.ffffff`，无 timezone offset；`datetime()` 函数对两端均 normalize 为 naive UTC，比较语义正确）。若 SQLAlchemy 升级或 `_utcnow` 实现改动导致格式变化（如开始存带 offset 的 ISO 文本），需重新核对 SQL 是否仍正确再执行。

### 6.1 SQL 模板（已固化 timestamp）

```sql
-- iter3_merge_iso8601 (UTC) = 2026-04-29T04:07:26+00:00
-- (= 2026-04-29 12:07:26 +08:00 = git show 81af223)

-- 时序注：实际 mismatch 行只能来自 user 重启 main.py 后写入的 cycle。
-- Iter3 merge → user 重启 之间的旧进程写的 'completed' 行会被 migration
-- Step 5b ("UPDATE decision_logs SET decision='legacy'" 无 WHERE) 全量转 'legacy'，
-- 不命中下方 WHERE decision='usage_limit_exceeded' 条件。
-- 此 SQL 用 iter3_merge_iso8601 作过滤起点是冗余但安全（多包不丢）。

-- (1) 识别窗口期 mismatch 行
SELECT id, created_at, decision, status FROM decision_logs
WHERE decision = 'usage_limit_exceeded'
  AND status = 'ok'
  AND datetime(created_at) > datetime('2026-04-29T04:07:26+00:00');
-- 期望：行数 = Iter 3→4 窗口期 usage_limit 触发次数（紧密衔接下应 0 / 个位数）

-- (2) 回填（与 Iter 3 §4.2 Step 5a 同语义）
UPDATE decision_logs
   SET status = 'usage_limit_exceeded', decision = 'legacy'
 WHERE decision = 'usage_limit_exceeded'
   AND status = 'ok'
   AND datetime(created_at) > datetime('2026-04-29T04:07:26+00:00');

-- (3) 验证回填结果（应返回 0 行）
SELECT COUNT(*) FROM decision_logs
 WHERE decision = 'usage_limit_exceeded'
   AND status = 'ok'
   AND datetime(created_at) > datetime('2026-04-29T04:07:26+00:00');
```

### 6.2 占位符命名校准

历史文档（`pre-next-observation-todos.md` § 与 Iter 3 spec §8.1）写过 `<iter4_merge_iso8601>` 的占位符 — 实际语义是 **iter3 merge timestamp**（窗口起点），与 iter4 merge 时刻无关。本 spec 已固化为 `2026-04-29T04:07:26+00:00`（无占位符）。

### 6.3 执行时机与责任

- **执行者**：user（PR merge 后 Bash 跑 `sqlite3 data/tradebot.db`）
- **执行时点**：本 Iter PR merge → 跑 (1) 看行数 → 跑 (2) 回填 → 跑 (3) 验证 0 行
- **本 Iter PR 内不含**：不写 `scripts/backfill_*.py`，不进 alembic migration（per spec §C4 决议）

---

## 7. 验证 SQL（spec §T0-1 acceptance）

**前置（强制）**: user 必须先重启 main.py 让 Iter 3 migration apply（参见 §1.1）；本节所有 SQL 依赖 `status` 列。同样适用 §6 的 dry-run 建议（`created_at` 实存格式核对）。

PR merge 后跑 1 cycle，SQL 检查 4 项：

```sql
-- (1) reasoning 长度 cap 已放（无截断到 500 的硬墙）
SELECT MAX(LENGTH(reasoning)) FROM decision_logs
 WHERE created_at > datetime('2026-04-29T04:07:26+00:00');
-- 期望：> 500（实测 max 800-1500 chars 内）
-- 注：directional smoke check，非 hard assertion。若刚跑 cycle reasoning 短不算 cap 失败，
-- 是 LLM 输出本身短；多跑几轮直到出现 ≥500 chars reasoning 行才算验证通过。

-- (2) decision 字段为 5 类派生值之一
SELECT decision, COUNT(*) FROM decision_logs
 WHERE created_at > datetime('2026-04-29T04:07:26+00:00')
 GROUP BY decision;
-- 期望：仅出现 open_long / open_short / close / adjust / hold（不再有 'completed'）

-- (3) status 字段写入正确
SELECT status, COUNT(*) FROM decision_logs
 WHERE created_at > datetime('2026-04-29T04:07:26+00:00')
 GROUP BY status;
-- 期望：normal cycle = 'ok' / forensic cycle = 'usage_limit_exceeded'

-- (4) market_summary 列值 = NULL（B2 不写）
SELECT COUNT(*) FROM decision_logs
 WHERE created_at > datetime('2026-04-29T04:07:26+00:00')
   AND market_summary IS NOT NULL;
-- 期望：0
```

---

## 8. 关键事实（Grounding Facts）

### 8.1 `decision` 字段 7 个枚举值

| 值 | 触发 | 写入方 |
|---|---|---|
| `open_long` | cycle 含 `open_position` + side='long' | 派生函数 |
| `open_short` | cycle 含 `open_position` + side='short' | 派生函数 |
| `close` | cycle 含 `close_position`（无 open） | 派生函数 |
| `adjust` | cycle 仅含调整类 action（8 个） | 派生函数 |
| `hold` | cycle 0 actions **或** 仅含 set_next_wake | 派生函数 |
| `legacy` | Iter 3 历史 backfill 标识（**historical-only**） | 仅 Iter 3 migration 写过；Iter 4+ 不再产生 |
| `derive_error` | 派生函数 SELECT 异常 fallback（罕见） | 派生函数 try/except 兜底（spec §3.2） |

**`legacy` vs `derive_error` 语义边界**：
- `legacy`：W1 历史 109 行（Iter 3 backfill），表"无 cycle_id JOIN，历史值不可信"
- `derive_error`：W2+ 运行时（Iter 4+），表"派生 SELECT 抛异常，本应有可信派生但 DB 故障"

→ W2 观察期 SQL 分析 `WHERE decision='legacy'` 仍是纯历史集，`derive_error` 单独统计 DB 故障频率，互不污染。

约束：`String(20)` — 最长 `derive_error` = 12 chars，余量足。

### 8.2 `status` 字段 2 个枚举值

| 值 | 触发 | 写入方 |
|---|---|---|
| `ok` | LLM run 正常完成 | G1 normal 路径 + Iter 3 server_default |
| `usage_limit_exceeded` | pydantic-ai `UsageLimitExceeded` 异常 | G2 forensic 路径 |

约束：`String(30)` — 最长 `usage_limit_exceeded` = 20 chars（10 char buffer per Iter 3 校准）。

### 8.3 `tools_execution.py` 11 个 `_record_action` 站点（实测）

```
L86-89   open_position
L122-125 close_position
L148-151 set_stop_loss
L178-181 set_take_profit
L193-196 adjust_leverage
L219-222 set_price_alert
L244-247 add_price_level_alert
L271-274 cancel_price_level_alert
L290-293 set_next_wake
L338-341 place_limit_order
L367-370 cancel_order
```
（精确闭合范围；每个 `_record_action(...)` 跨 4 行 kwargs。）

按 spec §C5 决议分类：

| 类别 | actions | 派生输出 |
|---|---|---|
| open（带 side） | `open_position` | `open_{side}` |
| close | `close_position` | `close` |
| adjust（8 个） | `set_stop_loss / set_take_profit / adjust_leverage / set_price_alert / add_price_level_alert / cancel_price_level_alert / place_limit_order / cancel_order` | `adjust` |
| hold-only | `set_next_wake` | `hold` |

### 8.4 `decision` × `status` 正交 2D 查询能力

```sql
-- 主动 hold 频率
SELECT COUNT(*) FROM decision_logs WHERE decision='hold' AND status='ok';

-- usage_limit 触发分布在哪些决策阶段
SELECT decision, COUNT(*) FROM decision_logs
 WHERE status='usage_limit_exceeded' GROUP BY decision;

-- "agent 决定 open 但 cycle 病理失败" 是否存在
SELECT COUNT(*) FROM decision_logs
 WHERE decision LIKE 'open_%' AND status='usage_limit_exceeded';
```

### 8.5 Iter 3 merge timestamp（窗口起点）

```
commit  81af22344e0650db193c103b70c3146182b1ecf3
local   2026-04-29 12:07:26 +08:00
UTC     2026-04-29T04:07:26+00:00
```

### 8.6 Iter 3 后已 landed schema

- `decision_logs.decision String(20) NOT NULL`（无 server_default）
- `decision_logs.status String(30) NOT NULL server_default='ok'`
- `decision_logs.reasoning Text nullable`（无长度限制；4000 是运行时防御 cap，非 schema 约束）
- `decision_logs.market_summary Text nullable`（DEPRECATED 内联注释，**仍存在不 drop**）
- `trade_actions.cycle_id String(50) nullable`（Iter 3 加，本 Iter 派生函数依赖）

---

## 9. 风险与回滚

### 9.1 风险点

| 风险 | 等级 | 缓解 |
|---|---|---|
| 派生函数 SELECT 慢路径 | 低 | `ix_trade_actions_session_id` 已存在（单列），WHERE 实际扫描范围是 session 内总 actions（W2 baseline 仍很小，单 session 累计数千行级），不是 cycle 内 3-10 行；index 命中 session_id 候选集后 cycle_id 由 SQL 层 row-by-row 过滤（非 Python 层） |
| `trade_actions` 缺 `(session_id, cycle_id)` 复合索引 | 低（可监控） | **不在本 Iter scope**——Iter 4 是写入路径补全不是 schema 演进；W2 观察期监控派生 SELECT 单次延迟，若 p95 > 50 ms 或单次 > 200 ms 触发独立 mini-PR 加索引（同 Iter 3 §G7 `ix_decision_logs_session_id_cycle_id` 做法）。退路条款明牌不做防御性预加 |
| forensic 路径派生 → DB 异常 → 二次抛错 | 低 | **已决议** try/except + fallback `'derive_error'`（独立 enum 不污染 `'legacy'` 历史语义；详见 §3.2 派生函数代码 + Source-of-truth 假设段）；不留 plan 阶段决议 |
| 窗口期 mismatch 行 > 个位数 | 低 | 紧密衔接（Iter 3 merge → Iter 4 PR open < 1 day）；若 > 10 行查 W2 baseline 触发频率 |
| set_next_wake 单独归 hold 影响下游分析 | 中 | spec 明牌；W2 观察期 SQL 文档需说明 hold = 0-action ∪ wake-only 双义 |
| Iter 4 PR merge 后 schema 滞后 → INSERT `status='ok'` 失败 | 极低（明牌不防御） | Iter 3 PR #28 `init_db` sentinel path 2 已自动 stamp+upgrade — user 启动 main.py 即触发 migration apply（schema 必含 `status` 列），Iter 4 写入路径才有机会跑。**user 不启动 main.py = 进程不在跑 = 无 INSERT 路径**，不构成生产风险。**显式拒绝做防御代码**（如 `_assert_schema_has_status_column()`）：增加无意义启动期检查，违反三态 sentinel 设计本意 |

### 9.2 回滚路径

| 故障 | 回滚方式 |
|---|---|
| 写入路径破 | `git revert` 本 PR 单一 commit；schema 不动 |
| 派生函数 bug | 同上 — 派生函数仅本 PR 引入 |
| 窗口期回填 SQL 跑错 | mismatch 行只是 `status='ok' → 'usage_limit_exceeded'`，反向 SQL 即可还原（破坏性低） |

### 9.3 不可逆变化

- `reasoning[:500]` → `reasoning[:4000]` 仅影响**新写入**，历史 109 行不动
- `decision='legacy'` 在 user 重启 main.py 触发 Iter 3 migration apply 后固化，本 Iter 不二次回填

---

## 10. 实施步骤（实施阶段细化为 Plan）

本 spec 提供 brainstorm 结论；实施 step-by-step 由 `superpowers:writing-plans` 阶段产出。摘要框架：

1. **Step A**: TDD 写 8 个派生函数单元测试 (T1-T8)
2. **Step B**: 实施 `_derive_decision_from_actions` 让 T1-T8 全过
3. **Step C**: 改 `app.py:253` 成功路径 (G1) + 写 T9 集成测 + **同 commit 更新 `src/storage/models.py:92` 行内注释 `truncated to 500 chars` → `truncated to 4000 chars`**（防 doc-rot）
4. **Step D**: 改 `app.py:170` forensic 路径 (G2) + 改 `test_usage_limits.py:103` (C1)
5. **Step E**: grep 排查 (C2) + 余量决议是否同步迁移
6. **Step F**: 跑全套测试 + manual smoke (1 cycle) + spec §7 验证 SQL
7. **Step G**: PR merge 后 user 跑 §6 窗口期 SQL 回填 (G5)
8. **Step H**: T11 drift guard 强制实施（与 Iter 5 D' 同款纪律，未来加新 action 无此 guard 会静默归 hold）

---

## 11. 文档归档

- 本 spec: `docs/superpowers/specs/2026-04-29-iter4-decisionlog-write-path-design.md`
- 实施计划: `.working/plans/2026-04-29-iter4-decisionlog-write-path-plan.md`（实施阶段产出）
- 勾 `.working/pre-next-observation-todos.md` checklist: Iter 4 行 + commit hash
- memory 更新: `project_w2_prep_progress.md` 标 Iter 4 ✅ landed

### 11.1 W2 观察期 follow-up 锚点（merge 时一并归档）

- **hold 双义 caveat**: W2 观察期 SQL 模板文档（位置待定，建立时同步加注释）需明示 `decision='hold'` 同时承载两类语义：(a) cycle 0 actions（纯观察），(b) cycle 仅含 `set_next_wake`（排下次醒）。SQL 分析需要区分时按 `JOIN trade_actions ... WHERE action='set_next_wake'` 二次过滤
- **memory 同步锚点**: PR merge 时在 `project_w2_prep_progress.md` 或独立 `project_w2_sql_analysis_caveats.md`（若建立）加 hold 双义条目，防 W2 期 SQL 分析忘记此 caveat
- **derive_error vs legacy 分析边界**: W2 SQL 文档同处需注释 `legacy` 是 historical-only（W1 109 行），`derive_error` 是 runtime DB 故障兜底（spec §8.1 语义边界段）；`COUNT(*) WHERE decision='derive_error'` 可作为 DB 健康度监控指标

---

**End of Iter 4 Spec**.
