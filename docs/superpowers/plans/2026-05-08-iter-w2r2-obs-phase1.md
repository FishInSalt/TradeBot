# Phase 1 Observability Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Land Phase 1 of the observability roadmap — agent_cycles 加 8 timing/token 列 + trade_actions 加 alert_id 列 + 3 derived SQL views (`v_cycle_metrics` / `v_alert_lifecycle` / `v_order_lifecycle`) — establishing a derivation layer that eliminates ad-hoc query overhead and unifies 5-field anchor / lifecycle analysis across sims.

**Architecture:** Single alembic migration adds 9 nullable columns + 3 read-only views; `_record_action` 签名 + `PriceLevelAlertInfo` dataclass 同步加 `alert_id` 字段解锁 alert lifecycle 数据通路；cli/app.py 三个 INSERT 路径填值；views 用 CTE + multi-LIKE 4-variant 派生 5-field anchor + json_extract 抽 state_snapshot + correlated subquery 关联 cycle 与 order。无业务逻辑变更，仅派生层 + 数据通路扩展。

**Tech Stack:** Python 3.13 / pydantic-ai 1.78 (frozen) / SQLAlchemy 2.x async / SQLite 3.50.4 (aiosqlite) / alembic 1.18 / pytest

**Spec:** `docs/superpowers/specs/2026-05-08-iter-w2r2-obs-phase1-design.md`

**Branch:** `feature/iter-w2r2-obs-phase1` (already created, spec doc commit `f29ee42` landed)

**⚠️ Sequencing constraint (per memory `feedback_plan_doc_commit_first`)**: **本 plan doc 必须作为第二个 commit landed 后才能开始 T0**。当前 spec 已 land (`f29ee42`)；plan doc 仍 untracked 时 → 先 commit plan，再启动实施。每个 task 末尾的 commit 节奏构成第 3 个起的 commit 序列。

**⚠️ Environment**: 本 plan 所有 `python` / `pytest` / `alembic` / `sqlite3` 命令假定 **venv 已激活**（`source .venv/bin/activate`）。subagent 启动时如未激活先 `source` 一次，本 plan 内不再逐处加前缀。

**Acceptance Criteria coverage**:
- AC-1 → T5 + T24 (alembic roundtrip)
- AC-2 → T10/T11 + T12 (cli/app.py 三路径填值)
- AC-3 → T7 + T8 (alert_id 两 callers)
- AC-4 → T1 + T2 (PriceLevelAlertInfo dataclass + trigger_context mirror)
- AC-5 → T13 + T14 (v_cycle_metrics)
- AC-6 → T15 + T16 (v_alert_lifecycle)
- AC-7 → T17 + T18 (v_order_lifecycle)
- AC-8 → T19 (历史兼容)
- AC-9 → T20 (5-field drift-guard)
- AC-10 → T22 (benchmark)
- AC-11 → T0 (cache_read 语义对齐)
- AC-12 → T21 (forensic enum drift-guard)

---

## File Structure

| File | Role | Status |
|---|---|---|
| `src/integrations/exchange/base.py:206,286` | `PriceLevelAlertInfo` dataclass 加 `alert_id` 字段（最末）；`_check_price_levels` 实例化传值 | Modify |
| `src/services/cycle_capture.py:54-62` | `_capture_trigger_context` 把 `alert_id` 镜像到 trigger_context JSON | Modify |
| `src/storage/models.py:57-99` | `TradeAction.alert_id` (新) + `AgentCycle` 加 8 字段 (timing 2 + tokens 6) | Modify |
| `alembic/versions/<rev>_phase1_observability.py` | 单 migration：add 9 cols + create 3 views + 完整 downgrade | Create |
| `src/agent/tools_execution.py:19,244,267` | `_record_action` 签名加 `alert_id`；add/cancel 两 callers 传值；cancel 删 `id={alert_id} \|` prefix | Modify |
| `src/cli/app.py:513,526,568,651` | retry loop 加 `llm_start/llm_end`；3 INSERT 路径填 8 字段（双轨变量保 logger 兼容）| Modify |
| `tests/conftest.py` | 加 `make_usage` fixture factory + `--sim-db`/`--session-id` pytest options | Modify |
| `tests/test_price_level_alert_info_alert_id.py` | AC-4 PriceLevelAlertInfo 7 字段 + auto-trigger + trigger_context 镜像 | Create |
| `tests/test_record_action_alert_id.py` | AC-3 add/cancel 两 callers 传 alert_id | Create |
| `tests/test_alembic_roundtrip_phase1.py` | AC-1 upgrade/downgrade roundtrip + view drop 顺序 | Create |
| `tests/test_run_agent_cycle_phase1.py` | AC-2 三路径 8 字段填值 | Create |
| `tests/test_v_cycle_metrics.py` | AC-5 v_cycle_metrics 字段断言 + 5-field anchor + cache_hit_rate_derived | Create |
| `tests/test_v_alert_lifecycle.py` | AC-6 register/trigger/cancel 三态 + cancel_attempts | Create |
| `tests/test_v_order_lifecycle.py` | AC-7 lifetime/trigger_drift_pct/originated_cycle_id | Create |
| `tests/test_view_historical_compat.py` | AC-8 历史 sim DB SELECT * 不 raise | Create |
| `tests/test_5field_anchor_drift_guard.py` | AC-9 5-field 联合命中率 (CI skip / W3 manual gate) | Create |
| `tests/test_forensic_enum_completeness.py` | AC-12 enum 差集断言 | Create |
| `tests/test_view_performance.py` | AC-10 view query < 100ms (sim #8 178 行级)| Create |
| `scripts/benchmark_view_phase1.py` | AC-10 offline benchmark | Create |
| 现有 ~3 test files (test_cycle_log / test_agent_cycle_injection / test_usage_limits) | 切到 `make_usage` factory（cache_read/cache_write/input_tokens/output_tokens 标准属性）| Modify |

**Decomposition note**: alembic migration 是单文件 — 所有 schema + view 改动同一 revision；migration 文件分多个 task 增量构建（T4 加列 → T13 加 v_cycle_metrics → T15 加 v_alert_lifecycle → T17 加 v_order_lifecycle），通过 `op.execute(_VIEW_SQL)` 字符串常量隔离避免 merge conflict。每个 view task 独立测试 / commit / 可回滚。

---

## Task 0: AC-11 cache_read 语义对齐前置验证

**Files:**
- Create (临时): `scripts/probe_usage.py`（独立于 wizard 的最小 probe 脚本；执行后删）
- Modify: `.gitignore`（加 `data/*.bak-*` 防 6 个 backup 文件累积 30MB）
- Run: probe script on dev environment
- Output: `docs/superpowers/specs/2026-05-08-iter-w2r2-obs-phase1-design.md` §5.5.1 Note 1 回填实测结论

**Note**: 这是 plan 实施前置验证，不写 production code。结果决定续 plan / 回 spec。

- [ ] **Step 0.0: 加 `.gitignore` 防 backup 文件入仓**

```bash
grep -q "data/\*.bak-\*" .gitignore || echo "data/*.bak-*" >> .gitignore
git add .gitignore
git commit -m "chore(iter-w2r2-obs-phase1): T0 .gitignore data/*.bak-* (T6/T13/T15/T17/T24 backups)"
```

- [ ] **Step 0.1: 创建 scripts/probe_usage.py — 独立最小 probe 脚本**

> **不侵入 production code** — `src/__main__.py` 仅 `asyncio.run(run())` 不传参；`run()` 是交互式 wizard 不接受 `--session-id` / `--max-cycles`。改写最小 probe 脚本直接调 `agent.run(...)` 一次抓 raw usage，独立于 wizard。

Create `scripts/probe_usage.py`:

```python
"""T0 AC-11 一次性 probe — 抓 pydantic-ai usage standard attrs vs DeepSeek vendor key 对比.

不进 production code；T0 Step 0.4 完成后删除文件。

设计选择：用最小裸 `pydantic_ai.Agent`（无 tool 注册），避免 `create_trader_agent`
注册 26 tools 后 LLM 决定调用 tool 时因 ctx.deps=None raise。ModelManager 真实 API
链 (load_models → create_model) 拿 model 实例。

Usage:
    python scripts/probe_usage.py
"""
import asyncio
import sys
from pathlib import Path

# Project root on path
sys.path.insert(0, str(Path(__file__).parent.parent))

from pydantic_ai import Agent
from src.services.model_manager import ModelManager


async def main():
    # ModelManager 真实 API: ModelManager(config_path) → load_models() → create_model(config)
    mm = ModelManager(config_path=Path("config/models.json"))
    models = mm.load_models()
    if not models:
        print("ERROR: no models in config/models.json — 请先用 wizard 配 model")
        sys.exit(1)
    model_config = models[0]    # 取第一个 model 作 probe
    print(f"Using model: {model_config.id} ({model_config.provider})")
    model = mm.create_model(model_config)

    # 裸 Agent (无 tool 注册) — 避免 deps 问题
    agent = Agent(model, output_type=str)

    # 最小 prompt — 一轮 LLM call 即可抓 usage
    result = await agent.run("Hello, please reply briefly with one word.")

    usage = result.usage()
    print("=" * 60)
    print("T0 PROBE — pydantic-ai usage attrs vs DeepSeek vendor key")
    print("=" * 60)
    print(f"usage.input_tokens       = {getattr(usage, 'input_tokens', None)}")
    print(f"usage.output_tokens      = {getattr(usage, 'output_tokens', None)}")
    print(f"usage.cache_read_tokens  = {getattr(usage, 'cache_read_tokens', None)}")
    print(f"usage.cache_write_tokens = {getattr(usage, 'cache_write_tokens', None)}")
    print(f"usage.total_tokens       = {usage.total_tokens if usage else None}")
    print(f"usage.details            = {usage.details if usage else None}")
    print()

    # AC-11 (a) 比对
    cache_read = getattr(usage, 'cache_read_tokens', 0) or 0
    cache_hit = (usage.details or {}).get('prompt_cache_hit_tokens', 0)
    if max(cache_read, cache_hit) > 0:
        rel_err_a = abs(cache_read - cache_hit) / max(cache_read, cache_hit)
        print(f"AC-11 (a) cache_read vs prompt_cache_hit: {cache_read} vs {cache_hit} (rel err {rel_err_a:.1%})")
    else:
        print("AC-11 (a) both 0 — first cycle 无 cache，重跑获取数据")

    # AC-11 (b) 比对
    input_tok = getattr(usage, 'input_tokens', 0) or 0
    cache_miss = (usage.details or {}).get('prompt_cache_miss_tokens', 0)
    sum_hit_miss = cache_hit + cache_miss
    if max(input_tok, sum_hit_miss) > 0:
        rel_err_b = abs(input_tok - sum_hit_miss) / max(input_tok, sum_hit_miss)
        print(f"AC-11 (b) input_tokens vs (cache_hit + cache_miss): {input_tok} vs {sum_hit_miss} (rel err {rel_err_b:.1%})")


if __name__ == "__main__":
    asyncio.run(main())
```

- [ ] **Step 0.2: 跑 probe script 抓输出（🧑 User-run）**

> **🧑 User-run**: 此 step 要真实 LLM API key + 网络（无 DB 副作用，probe 不写 session 数据）。**请你跑这条命令并把输出全部贴回对话**：

```bash
python scripts/probe_usage.py
```

Expected: 输出含 `usage.input_tokens=N usage.cache_read_tokens=M ... details={'prompt_cache_hit_tokens': ..., ...}` + AC-11 (a) (b) 相对误差行。

**如 AC-11 (a)/(b) 任一相对误差 > 5%** → 暂停 plan 回 spec 评估（spec §6 AC-11 触发条件）。

- [ ] **Step 0.3: 比对两轨 + 判定续 plan / 回 spec**

手动比对：
- (a) `usage.cache_read_tokens` vs `details['prompt_cache_hit_tokens']`：相对误差 = `abs(read - hit) / max(read, hit)`
- (b) `usage.input_tokens` vs `details['prompt_cache_hit_tokens'] + details['prompt_cache_miss_tokens']`：相对误差同公式

判定（按 AC-11）：
- (a) AND (b) 任一相对误差 ≤ 5% → ✅ **续 plan**，把实测数字写入 spec §5.5.1 Note 1
- 任一 > 5% → 🛑 **暂停 plan 回 spec 评估**：是否启用 `cache_hit_rate_derived` 作主指标 / `cache_hit_rate` 列 deprecated 标注

- [ ] **Step 0.4: 删除临时 probe script + 把实测结论写入 spec**

删除 Step 0.1 创建的 `scripts/probe_usage.py`（不进 commit）：

```bash
rm scripts/probe_usage.py
git status scripts/    # 应无 untracked / modified — 验证撤回完整
```

> **副作用注**: probe script 不写 DB（仅 agent.run 一次取 usage），无 cycle row 进 agent_cycles；因此 T13 view 落地时无 "cache_hit_rate_derived 在该 cycle NULL" 的边界问题。production code 路径完全干净。

在 spec `§5.5.1 Note 1` 末尾添加一行：

```markdown
**T0 实测结论 (YYYY-MM-DD)**: usage.cache_read_tokens=N vs details.prompt_cache_hit_tokens=M（相对误差 X%）；usage.input_tokens=P vs (cache_hit + cache_miss)=Q（相对误差 Y%）。两轨语义对齐 ✓ → 续 plan。
```

- [ ] **Step 0.5: Commit spec 回填**

```bash
git add docs/superpowers/specs/2026-05-08-iter-w2r2-obs-phase1-design.md
git commit -m "docs(iter-w2r2-obs-phase1): T0 cache_read alignment verified"
```

---

## Task 1: PriceLevelAlertInfo 加 alert_id 字段

**Files:**
- Modify: `src/integrations/exchange/base.py:206-209` (实例化加 alert_id)
- Modify: `src/integrations/exchange/base.py:285-292` (dataclass 加字段)
- Modify: `tests/test_price_level_alert.py:46` (现有 fixture 加 alert_id)
- Modify: `tests/test_cycle_capture.py:286` (现有 fixture 加 alert_id)
- Test: `tests/test_price_level_alert_info_alert_id.py` (Create — AC-4)

- [ ] **Step 1.0: 验证 PriceLevelAlertInfo 实例化点完整 scope**

> 防御性 grep — 加 alert_id 必填字段后破坏面是全仓库每个 `PriceLevelAlertInfo(...)` 实例化点（含 tests）。Plan 假设仅 3 处（base.py:206 / test_price_level_alert.py:46 / test_cycle_capture.py:286）；如 grep 出第 4+ 处，停下增补 fixture 后再继续。

```bash
grep -rn "PriceLevelAlertInfo(" --include="*.py" | grep -v isinstance
```

Expected (verify 输出严格匹配):
```
src/integrations/exchange/base.py:206:                triggered.append(PriceLevelAlertInfo(
tests/test_cycle_capture.py:286:    pla = PriceLevelAlertInfo(
tests/test_price_level_alert.py:46:    info = PriceLevelAlertInfo(
```

如多/少：暂停 plan，向用户报告新增/移除文件，更新本 task 范围后再继续。

- [ ] **Step 1.1: 写 AC-4 失败测试**

Create `tests/test_price_level_alert_info_alert_id.py`:

```python
"""AC-4: PriceLevelAlertInfo 加 alert_id 字段 + auto-trigger 实例化 + trigger_context 镜像。"""
import pytest
from src.integrations.exchange.base import PriceLevelAlertInfo


def test_price_level_alert_info_has_alert_id_field():
    """T1.1: dataclass 7 字段含 alert_id（无默认值，必填）。"""
    info = PriceLevelAlertInfo(
        symbol="BTC/USDT:USDT", target_price=75000.0, direction="above",
        current_price=74950.0, reasoning="test alert",
        timestamp=1746098000000, alert_id="abc12345",
    )
    assert info.alert_id == "abc12345"


def test_price_level_alert_info_alert_id_required():
    """T1.2: alert_id 必填（dataclass 无默认值）— 缺失 raise TypeError。"""
    with pytest.raises(TypeError, match="alert_id"):
        PriceLevelAlertInfo(  # type: ignore[call-arg]
            symbol="BTC/USDT:USDT", target_price=75000.0, direction="above",
            current_price=74950.0, reasoning="test alert",
            timestamp=1746098000000,
        )


def test_price_level_alert_info_field_count():
    """T1.3: 字段总数为 7（防 future drift）。"""
    from dataclasses import fields
    assert len(fields(PriceLevelAlertInfo)) == 7
```

- [ ] **Step 1.2: 跑测试确认失败**

```bash
pytest tests/test_price_level_alert_info_alert_id.py -v
```

Expected: 3 个测试全 FAIL（`alert_id` 字段不存在 / TypeError mismatch）。

- [ ] **Step 1.3: 修改 base.py — dataclass 加 alert_id 字段（最末）**

Modify `src/integrations/exchange/base.py:285-292`:

```python
@dataclass
class PriceLevelAlertInfo:
    symbol: str
    target_price: float
    direction: str          # "above" / "below"
    current_price: float
    reasoning: str
    timestamp: int
    alert_id: str           # 新（位置最末，无默认值；与 timestamp 同 metadata 风格）
```

- [ ] **Step 1.4: 修改 base.py:206 — `_check_price_levels` 实例化传 alert_id**

Modify `src/integrations/exchange/base.py:206-210`:

```python
triggered.append(PriceLevelAlertInfo(
    symbol=alert["symbol"], target_price=alert["price"],
    direction=alert["direction"], current_price=current_price,
    reasoning=alert["reasoning"], timestamp=timestamp,
    alert_id=alert["id"],           # 新
))
```

- [ ] **Step 1.5: 修复现有 fixture（test_price_level_alert.py:46）**

Modify `tests/test_price_level_alert.py:43-53`:

```python
def test_price_level_alert_info_fields():
    info = PriceLevelAlertInfo(
        symbol="BTC/USDT:USDT", target_price=58000.0,
        direction="below", current_price=57900.0,
        reasoning="Key support level", timestamp=1712534400000,
        alert_id="testid01",                     # 新
    )
    assert info.direction == "below"
    assert info.target_price == 58000.0
```

- [ ] **Step 1.6: 修复现有 fixture（test_cycle_capture.py:286）**

Modify `tests/test_cycle_capture.py:283-295`:

```python
def test_trigger_context_price_level_alert():
    """T-TC-3: PriceLevelAlertInfo → 7 字段含 timestamp (P1-1)。"""
    pla = PriceLevelAlertInfo(
        symbol="BTC/USDT:USDT", target_price=75600.0, direction="above",
        current_price=75623.0, reasoning="FOMC reaction watch",
        timestamp=1746098000000,
        alert_id="fomc0001",                     # 新
    )
    result = _capture_trigger_context("cyc-tc3", "alert", pla)
    assert result["type"] == "price_level_alert"
    # ... 现有断言保留
```

- [ ] **Step 1.7: 跑全部相关测试确认通过**

```bash
pytest tests/test_price_level_alert_info_alert_id.py tests/test_price_level_alert.py tests/test_cycle_capture.py -v
```

Expected: 全 PASS（包括新 3 个 + 现有改 fixture 仍 pass）。

- [ ] **Step 1.8: Commit**

```bash
git add src/integrations/exchange/base.py tests/test_price_level_alert_info_alert_id.py tests/test_price_level_alert.py tests/test_cycle_capture.py
git commit -m "feat(iter-w2r2-obs-phase1): T1 PriceLevelAlertInfo 加 alert_id 字段 (AC-4 partial)"
```

---

## Task 2: cycle_capture trigger_context 镜像 alert_id

**Files:**
- Modify: `src/services/cycle_capture.py:54-62`
- Modify: `tests/test_cycle_capture.py:286` (扩 trigger_context.alert_id 断言)

- [ ] **Step 2.1: 扩 test_cycle_capture.py 的 trigger_context 断言**

Modify `tests/test_cycle_capture.py:283-300`（扩展 Step 1.6 那个测试）：

```python
def test_trigger_context_price_level_alert():
    """T-TC-3: PriceLevelAlertInfo → 7 字段含 alert_id (P1-1 + T2 mirror)。"""
    pla = PriceLevelAlertInfo(
        symbol="BTC/USDT:USDT", target_price=75600.0, direction="above",
        current_price=75623.0, reasoning="FOMC reaction watch",
        timestamp=1746098000000,
        alert_id="fomc0001",
    )
    result = _capture_trigger_context("cyc-tc3", "alert", pla)
    assert result["type"] == "price_level_alert"
    assert result["target_price"] == 75600.0
    assert result["timestamp"] == 1746098000000
    assert result["alert_id"] == "fomc0001"     # 新（T2 mirror 验证）
    # 字段集断言扩 alert_id
    assert set(result.keys()) == {
        "type", "alert_id", "symbol", "current_price",
        "target_price", "direction", "reasoning", "timestamp",
    }
```

- [ ] **Step 2.2: 跑测试确认失败**

```bash
pytest tests/test_cycle_capture.py::test_trigger_context_price_level_alert -v
```

Expected: FAIL with `KeyError: 'alert_id'` or `assert ... == ...` mismatch.

- [ ] **Step 2.3: 修改 cycle_capture.py 镜像 alert_id**

Modify `src/services/cycle_capture.py:51-62`:

```python
        if trigger_type == "alert" and context is not None:
            if isinstance(context, PriceLevelAlertInfo):
                # base.py:285-292: 7 字段（含 T1 新加 alert_id）+ type
                return {
                    "type": "price_level_alert",
                    "alert_id": context.alert_id,    # 新（T2 mirror）
                    "symbol": context.symbol,
                    "current_price": context.current_price,
                    "target_price": context.target_price,
                    "direction": context.direction,
                    "reasoning": context.reasoning,
                    "timestamp": context.timestamp,
                }
```

- [ ] **Step 2.4: 跑测试确认通过**

```bash
pytest tests/test_cycle_capture.py -v
```

Expected: 全 PASS。

- [ ] **Step 2.5: Commit**

```bash
git add src/services/cycle_capture.py tests/test_cycle_capture.py
git commit -m "feat(iter-w2r2-obs-phase1): T2 trigger_context 镜像 alert_id (AC-4 complete)"
```

---

## Task 3: models.py 加 9 列（TradeAction.alert_id + AgentCycle 8 字段）

**Files:**
- Modify: `src/storage/models.py:57-99` (TradeAction + AgentCycle)

注：仅改 ORM model 不写 alembic（T4 才创建 migration）。本 task 后 SQLAlchemy schema 与 DB schema 暂时不一致，但代码不实际写入新字段所以不破。

- [ ] **Step 3.1: 修改 TradeAction 加 alert_id 字段**

Modify `src/storage/models.py:57-75`（TradeAction 类末尾加新字段）：

```python
class TradeAction(Base):
    """Agent 的交易操作日志 — append-only 事件模型。"""

    __tablename__ = "trade_actions"

    id: Mapped[int] = mapped_column(primary_key=True)
    session_id: Mapped[str] = mapped_column(String(36), ForeignKey("sessions.id"), index=True)
    cycle_id: Mapped[str | None] = mapped_column(String(50), nullable=True)
    action: Mapped[str] = mapped_column(String(30))
    order_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    alert_id: Mapped[str | None] = mapped_column(String(50), nullable=True)  # 新（T3）
    symbol: Mapped[str] = mapped_column(String(50))
    side: Mapped[str | None] = mapped_column(String(10), nullable=True)
    trigger_reason: Mapped[str | None] = mapped_column(String(20), nullable=True)
    price: Mapped[float | None] = mapped_column(Float, nullable=True)
    pnl: Mapped[float | None] = mapped_column(Float, nullable=True)
    reasoning: Mapped[str | None] = mapped_column(Text, nullable=True)
    fee: Mapped[float | None] = mapped_column(Float, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)
```

- [ ] **Step 3.2: 修改 AgentCycle 加 8 字段**

Modify `src/storage/models.py:77-99`（AgentCycle 类末尾，`tokens_consumed` 之后、`created_at` 之前）：

```python
class AgentCycle(Base):
    """One agent cycle record. R2-7 五维度叙事 framing + Phase 1 timing/token 拆分。"""

    __tablename__ = "agent_cycles"
    __table_args__ = (
        Index("ix_agent_cycles_session_id_cycle_id", "session_id", "cycle_id"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    session_id: Mapped[str] = mapped_column(String(36), ForeignKey("sessions.id"), index=True)
    cycle_id: Mapped[str] = mapped_column(String(50))
    triggered_by: Mapped[str] = mapped_column(String(20))
    trigger_context: Mapped[str | None] = mapped_column(Text, nullable=True)
    state_snapshot: Mapped[str | None] = mapped_column(Text, nullable=True)
    decision: Mapped[str | None] = mapped_column(Text, nullable=True)
    execution_status: Mapped[str] = mapped_column(String(30), default="ok", server_default="ok")
    reasoning: Mapped[str | None] = mapped_column(Text, nullable=True)
    model_id: Mapped[str | None] = mapped_column(String(100), nullable=True)
    tokens_consumed: Mapped[int] = mapped_column(Integer, default=0)
    # === Phase 1 新加 (T3) ===
    wall_time_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    llm_call_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    input_tokens: Mapped[int | None] = mapped_column(Integer, nullable=True)
    output_tokens: Mapped[int | None] = mapped_column(Integer, nullable=True)
    cache_read_tokens: Mapped[int | None] = mapped_column(Integer, nullable=True)
    cache_write_tokens: Mapped[int | None] = mapped_column(Integer, nullable=True)
    reasoning_tokens: Mapped[int | None] = mapped_column(Integer, nullable=True)
    cache_hit_rate: Mapped[float | None] = mapped_column(Float, nullable=True)
    # === END Phase 1 ===
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)
```

- [ ] **Step 3.3: 跑现有 tests 确认 ORM 改动不破已有逻辑**

```bash
pytest tests/test_alembic_migration.py tests/test_cycle_capture.py tests/test_run_agent_cycle.py -v
```

Expected: 全 PASS（新字段 nullable，default None，现有写入路径不读不写 → 透明）。如有 fixture 用 alembic upgrade bootstrap 测试 DB 触发 "no such column" → confirm H1 风险，T4 commit 时一并修复。

- [ ] **Step 3.4: 不单独 commit — 等 T4 alembic 一起提交**

> **H1 ORM-DB skew avoidance**: 不在此 commit；T3 的 models.py 改动 + T4 的 alembic migration 合并为单 commit，避免 ORM 与 DB schema 不一致窗口期破测试。继续 T4。

---

## Task 4: alembic migration — 9 列 add + downgrade

**Files:**
- Create: `alembic/versions/<rev>_phase1_observability.py` (revision id 用 alembic revision 自动生成)

- [ ] **Step 4.1: 生成 alembic revision skeleton**

Run:

```bash
alembic revision -m "phase1 observability"
```

Output: 创建 `alembic/versions/<auto_rev_id>_phase1_observability.py`。记下 `<auto_rev_id>`（如 `a1b2c3d4e5f6`）。

- [ ] **Step 4.2: 实现 upgrade()/downgrade() — 仅 9 列（views 留 T13/T15/T17）**

Replace 自动生成的 file body with:

```python
"""phase1 observability

Revision ID: <auto_rev_id>
Revises: eeeee565cb36
Create Date: <auto>

Phase 1 spec §5.1.3: agent_cycles 加 8 列 (timing 2 + tokens 6) +
trade_actions 加 alert_id 列 + 3 read-only views (v_cycle_metrics /
v_alert_lifecycle / v_order_lifecycle).
"""
from __future__ import annotations
from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa


revision: str = "<auto_rev_id>"
down_revision: str | None = "eeeee565cb36"
branch_labels: Sequence[str] | None = None
depends_on: Sequence[str] | None = None


# View SQL 字符串（T13/T15/T17 后续 task 填充；本 task 仅占位空字符串）
_V_CYCLE_METRICS_SQL = ""      # T13 填充
_V_ALERT_LIFECYCLE_SQL = ""    # T15 填充
_V_ORDER_LIFECYCLE_SQL = ""    # T17 填充


def upgrade() -> None:
    # P1+P2: agent_cycles 加 8 列（全 nullable）
    with op.batch_alter_table("agent_cycles", schema=None) as batch_op:
        batch_op.add_column(sa.Column("wall_time_ms",       sa.Integer, nullable=True))
        batch_op.add_column(sa.Column("llm_call_ms",        sa.Integer, nullable=True))
        batch_op.add_column(sa.Column("input_tokens",       sa.Integer, nullable=True))
        batch_op.add_column(sa.Column("output_tokens",      sa.Integer, nullable=True))
        batch_op.add_column(sa.Column("cache_read_tokens",  sa.Integer, nullable=True))
        batch_op.add_column(sa.Column("cache_write_tokens", sa.Integer, nullable=True))
        batch_op.add_column(sa.Column("reasoning_tokens",   sa.Integer, nullable=True))
        batch_op.add_column(sa.Column("cache_hit_rate",     sa.Float,   nullable=True))

    # X 配套: trade_actions 加 alert_id（nullable）
    with op.batch_alter_table("trade_actions", schema=None) as batch_op:
        batch_op.add_column(sa.Column("alert_id", sa.String(50), nullable=True))

    # P5+P6: 创建 3 个 view（占位 — 后续 task 填充 SQL 字符串）
    if _V_CYCLE_METRICS_SQL:
        op.execute(_V_CYCLE_METRICS_SQL)
    if _V_ALERT_LIFECYCLE_SQL:
        op.execute(_V_ALERT_LIFECYCLE_SQL)
    if _V_ORDER_LIFECYCLE_SQL:
        op.execute(_V_ORDER_LIFECYCLE_SQL)


def downgrade() -> None:
    # Drop views first (column dependency)
    op.execute("DROP VIEW IF EXISTS v_order_lifecycle")
    op.execute("DROP VIEW IF EXISTS v_alert_lifecycle")
    op.execute("DROP VIEW IF EXISTS v_cycle_metrics")

    # Drop trade_actions.alert_id
    with op.batch_alter_table("trade_actions", schema=None) as batch_op:
        batch_op.drop_column("alert_id")

    # Drop agent_cycles 8 列（按 add 顺序的反向 — alembic 惯例，参 R2-7 模式）
    with op.batch_alter_table("agent_cycles", schema=None) as batch_op:
        for col in ("cache_hit_rate", "reasoning_tokens", "cache_write_tokens",
                    "cache_read_tokens", "output_tokens", "input_tokens",
                    "llm_call_ms", "wall_time_ms"):
            batch_op.drop_column(col)
```

替换 `<auto_rev_id>` 为 Step 4.1 生成的实际 id。

- [ ] **Step 4.3: 跑 alembic upgrade 验证 SQL 正确**

```bash
# Backup current DB first
cp data/tradebot.db data/tradebot.db.bak-pre-t4

# Upgrade to head
alembic upgrade head

# Verify 列已加
sqlite3 data/tradebot.db "PRAGMA table_info(agent_cycles)" | grep -E "wall_time_ms|input_tokens"
sqlite3 data/tradebot.db "PRAGMA table_info(trade_actions)" | grep alert_id
```

Expected: agent_cycles 8 新列出现；trade_actions.alert_id 出现。

- [ ] **Step 4.4: 跑 alembic downgrade -1 验证 reversible**

```bash
alembic downgrade -1
sqlite3 data/tradebot.db "PRAGMA table_info(agent_cycles)" | grep -c "wall_time_ms"  # 期望 0
sqlite3 data/tradebot.db "PRAGMA table_info(trade_actions)" | grep -c "alert_id"     # 期望 0
```

Expected: 两 grep 都返回 0（列已删）。

- [ ] **Step 4.5: 重新 upgrade 留 head 状态**

```bash
alembic upgrade head
```

- [ ] **Step 4.6: Commit (T3 + T4 合并)**

> **H1 ORM-DB skew avoidance**: T3 (models.py 9 列 ORM) + T4 (alembic 9 列 add/drop) 合并为单 commit，确保 ORM schema 与 DB schema 任何时点都一致。

```bash
git add src/storage/models.py alembic/versions/<auto_rev_id>_phase1_observability.py
git commit -m "feat(iter-w2r2-obs-phase1): T3+T4 models 9 列 ORM + alembic migration (views 占位)"
```

---

## Task 5: AC-1 alembic roundtrip test

**Files:**
- Test: `tests/test_alembic_roundtrip_phase1.py` (Create)

- [ ] **Step 5.1: 写 AC-1 测试**

Create `tests/test_alembic_roundtrip_phase1.py`:

```python
"""AC-1: alembic upgrade + downgrade roundtrip — 9 列 add/drop + 3 view create/drop."""
import subprocess
import sqlite3
from pathlib import Path

import pytest


PHASE1_REV = "<auto_rev_id>"   # 替换为 T4 生成的 revision id
PREV_REV = "eeeee565cb36"


@pytest.fixture
def temp_db(tmp_path):
    """Init fresh empty DB at PREV_REV (R2-7 head before Phase 1)."""
    db = tmp_path / "test.db"
    # Bootstrap fresh DB up to PREV_REV
    env = {"DATABASE_URL": f"sqlite:///{db}"}
    subprocess.run(["alembic", "upgrade", PREV_REV], check=True, env=env)
    return str(db), env


def test_upgrade_adds_8_agent_cycles_columns(temp_db):
    """T5.1: upgrade head 后 agent_cycles 含 8 新列。"""
    db, env = temp_db
    subprocess.run(["alembic", "upgrade", "head"], check=True, env=env)

    conn = sqlite3.connect(db)
    cols = {row[1] for row in conn.execute("PRAGMA table_info(agent_cycles)")}
    expected = {"wall_time_ms", "llm_call_ms", "input_tokens", "output_tokens",
                "cache_read_tokens", "cache_write_tokens", "reasoning_tokens",
                "cache_hit_rate"}
    assert expected.issubset(cols), f"missing: {expected - cols}"


def test_upgrade_adds_trade_actions_alert_id(temp_db):
    """T5.2: upgrade head 后 trade_actions 含 alert_id 列。"""
    db, env = temp_db
    subprocess.run(["alembic", "upgrade", "head"], check=True, env=env)

    conn = sqlite3.connect(db)
    cols = {row[1] for row in conn.execute("PRAGMA table_info(trade_actions)")}
    assert "alert_id" in cols


def test_downgrade_drops_all_columns(temp_db):
    """T5.3: upgrade head → downgrade -1 后 9 列全消失（roundtrip clean）。"""
    db, env = temp_db
    subprocess.run(["alembic", "upgrade", "head"], check=True, env=env)
    subprocess.run(["alembic", "downgrade", "-1"], check=True, env=env)

    conn = sqlite3.connect(db)
    ac_cols = {row[1] for row in conn.execute("PRAGMA table_info(agent_cycles)")}
    ta_cols = {row[1] for row in conn.execute("PRAGMA table_info(trade_actions)")}

    assert "wall_time_ms" not in ac_cols
    assert "alert_id" not in ta_cols


def test_upgrade_idempotent_after_downgrade(temp_db):
    """T5.4: down → up 二次 roundtrip 仍可上 head（防 alembic state 污染）。"""
    db, env = temp_db
    subprocess.run(["alembic", "upgrade", "head"], check=True, env=env)
    subprocess.run(["alembic", "downgrade", "-1"], check=True, env=env)
    subprocess.run(["alembic", "upgrade", "head"], check=True, env=env)

    conn = sqlite3.connect(db)
    cols = {row[1] for row in conn.execute("PRAGMA table_info(agent_cycles)")}
    assert "wall_time_ms" in cols   # 二次 upgrade 仍生效
```

- [ ] **Step 5.2: 跑测试确认 4 个 PASS**

```bash
pytest tests/test_alembic_roundtrip_phase1.py -v
```

Expected: 4 个全 PASS（views 部分将在 T13+ 后再扩展）。

- [ ] **Step 5.3: Commit**

```bash
git add tests/test_alembic_roundtrip_phase1.py
git commit -m "feat(iter-w2r2-obs-phase1): T5 AC-1 alembic 9-col roundtrip test"
```

---

## Task 6: 历史 sim DB 兼容预检（schema-only）

**Files:**
- Run-only: 不创建文件，仅手动 verify

注：本 task 是手动 sanity check — 验证 T4 migration 在含 178 cycles 的实际 sim DB 上不破。views 还没创建（留 T13+），所以测试限于"加列后老数据 SELECT 不 raise"。

- [ ] **Step 6.1: 备份当前 data/tradebot.db**

```bash
cp data/tradebot.db data/tradebot.db.bak-pre-t6
```

- [ ] **Step 6.2: 跑 SELECT * 抽样验证 schema 兼容**

```bash
sqlite3 data/tradebot.db "SELECT cycle_id, triggered_by, wall_time_ms, input_tokens FROM agent_cycles LIMIT 5"
sqlite3 data/tradebot.db "SELECT id, action, alert_id FROM trade_actions LIMIT 5"
```

Expected: SELECT 不 raise；新列全 NULL（历史数据未填）。

- [ ] **Step 6.3: 备份保留记录**

```bash
# 备份文件保留作 disaster recovery，主 DB 保持 head 状态
ls -lh data/tradebot.db.bak-pre-t6  # confirm 存在
```

无 commit 步骤（手动 verify 不进 git）。

---

## Task 7: _record_action 签名扩展 + 2 callers

**Files:**
- Modify: `src/agent/tools_execution.py:19-43` (_record_action)
- Modify: `src/agent/tools_execution.py:244-264` (add_price_level_alert caller)
- Modify: `src/agent/tools_execution.py:267-289` (cancel_price_level_alert caller)

- [ ] **Step 7.1: 修改 _record_action 签名加 alert_id 参数**

Modify `src/agent/tools_execution.py:19-43`:

```python
async def _record_action(
    deps: TradingDeps, action: str,
    order_id: str | None = None,
    alert_id: str | None = None,        # 新（T7）
    side: str | None = None,
    price: float | None = None,
    pnl: float | None = None,
    reasoning: str | None = None,
) -> None:
    """写入一条 TradeAction 记录。写入失败不影响 tool 返回（容错）。"""
    if deps.db_engine is None:
        return
    from src.storage.database import get_session
    from src.storage.models import TradeAction

    try:
        async with get_session(deps.db_engine) as session:
            session.add(TradeAction(
                session_id=deps.session_id,
                cycle_id=deps.cycle_id,
                action=action,
                order_id=order_id,
                alert_id=alert_id,          # 新（T7）
                symbol=deps.symbol,
                side=side,
                price=price,
                pnl=pnl,
                reasoning=reasoning,
            ))
            await session.commit()
    except Exception:
        logger.warning("Failed to record TradeAction", exc_info=True)
```

注意：9 个 zero-改动 callers (open_position / close_position / set_stop_loss 等) 因 default `None` 自动透明，无需改。

- [ ] **Step 7.2: 修改 add_price_level_alert caller 传 alert_id（保留 reasoning prefix）**

Modify `src/agent/tools_execution.py:248-251`:

```python
    await _record_action(
        deps, action="add_price_level_alert",
        alert_id=alert_id,                              # 新（T7）
        price=price,
        reasoning=f"{direction} {price} | {reasoning}", # 保留 — direction 信息在 trade_actions
                                                        # 没专列（side 列已被 long/short 占用）
    )
```

- [ ] **Step 7.3: 修改 cancel_price_level_alert caller 传 alert_id + 删 prefix**

Modify `src/agent/tools_execution.py:283-286`:

```python
    if ok:
        await _record_action(
            deps, action="cancel_price_level_alert",
            alert_id=alert_id,                          # 新（T7）
            reasoning=reasoning,                        # 删 `id={alert_id} | ` prefix
                                                        # （alert_id 已落 trade_actions.alert_id 专列）
        )
```

- [ ] **Step 7.4: 跑现有 tests/test_alert_lifecycle.py / test_tool_recorder.py / test_trade_action.py 确认不破**

```bash
pytest tests/test_alert_lifecycle.py tests/test_tool_recorder.py tests/test_trade_action.py -v
```

Expected: 全 PASS（既有断言不读 alert_id 列，新参数 None default 透明）。

- [ ] **Step 7.5: Commit**

```bash
git add src/agent/tools_execution.py
git commit -m "feat(iter-w2r2-obs-phase1): T7 _record_action 加 alert_id 参数 + 2 callers (AC-3 partial)"
```

---

## Task 8: AC-3 / AC-4 两 callers + dataclass 完整测试

**Files:**
- Test: `tests/test_record_action_alert_id.py` (Create)

- [ ] **Step 8.1: 写 AC-3 测试**

Create `tests/test_record_action_alert_id.py`:

```python
"""AC-3: trade_actions.alert_id 在 add + cancel 两个 callers 都正确写入。"""
import asyncio
import pytest
from sqlalchemy import select

from src.agent.tools_execution import (
    add_price_level_alert, cancel_price_level_alert,
)
from src.storage.models import TradeAction


@pytest.mark.asyncio
async def test_add_price_level_alert_writes_alert_id(deps_with_sim_exchange, db_session):
    """T8.1: add_price_level_alert 后 trade_actions.alert_id 与 exchange 返回一致。"""
    result = await add_price_level_alert(
        deps_with_sim_exchange,
        price=80000.0, direction="above", reasoning="resistance test",
    )
    assert "Price level alert set" in result

    # SELECT 最新 trade_actions row
    row = (await db_session.execute(
        select(TradeAction)
        .where(TradeAction.action == "add_price_level_alert")
        .order_by(TradeAction.id.desc()).limit(1)
    )).scalar_one()

    assert row.alert_id is not None
    assert len(row.alert_id) == 8       # uuid4()[:8] 8-char hex
    assert row.reasoning.startswith("above 80000.0 |")  # add 路径保留 prefix


@pytest.mark.asyncio
async def test_cancel_price_level_alert_writes_alert_id_no_prefix(deps_with_sim_exchange, db_session):
    """T8.2: cancel_price_level_alert 后 alert_id 落专列；reasoning 无 prefix。"""
    # First add an alert
    add_result = await add_price_level_alert(
        deps_with_sim_exchange,
        price=80000.0, direction="above", reasoning="initial",
    )
    # Extract alert_id from return
    import re
    match = re.search(r"id=([0-9a-f]{8})", add_result)
    assert match, f"expected id= in {add_result!r}"
    alert_id = match.group(1)

    # Cancel
    cancel_result = await cancel_price_level_alert(
        deps_with_sim_exchange,
        alert_id=alert_id, reasoning="invalidation hit",
    )
    assert "cancelled" in cancel_result

    # SELECT cancel row
    row = (await db_session.execute(
        select(TradeAction)
        .where(TradeAction.action == "cancel_price_level_alert")
        .order_by(TradeAction.id.desc()).limit(1)
    )).scalar_one()

    assert row.alert_id == alert_id      # 专列填值
    assert row.reasoning == "invalidation hit"   # 无 `id={alert_id} | ` prefix
    assert "id=" not in row.reasoning            # double-check 旧 prefix 已删


@pytest.mark.asyncio
async def test_other_callers_alert_id_remains_null(deps_with_sim_exchange, db_session):
    """T8.3: 9 个 zero-改动 callers (open_position 等) trade_actions.alert_id 为 NULL。"""
    from src.agent.tools_execution import open_position
    await open_position(
        deps_with_sim_exchange,
        side="long", position_pct=10.0, leverage=2,
        reasoning="test open",
    )

    row = (await db_session.execute(
        select(TradeAction)
        .where(TradeAction.action == "open_position")
        .order_by(TradeAction.id.desc()).limit(1)
    )).scalar_one()

    assert row.alert_id is None   # default 透明
```

- [ ] **Step 8.2: 跑测试确认 PASS**

```bash
pytest tests/test_record_action_alert_id.py -v
```

Expected: 3 个全 PASS（依赖已有 `deps_with_sim_exchange` 和 `db_session` fixture，应在 conftest.py 已定义；如不存在 plan 实施时 grep `tests/conftest.py` 找等价 fixture 并替换）。

- [ ] **Step 8.3: Commit**

```bash
git add tests/test_record_action_alert_id.py
git commit -m "feat(iter-w2r2-obs-phase1): T8 AC-3 add/cancel alert_id tests"
```

---

## Task 9: pytest fixture factory + Phase 1 测试 fixtures（conftest.py 扩展）

**Files:**
- Modify: `tests/conftest.py` (加 `make_usage` factory + 5 个 Phase 1 fixture + pytest options)

注：本 task 提供 ~3 既有测试 migration 的基础设施（T23 用），并被 T8/T12/T19 直接使用。

**M2 fixture 扩展** — 经 verify `tests/conftest.py` 仅有 `settings` / `trader_config` / `engine` / `session_with_row` 4 个 fixture，T8/T12/T19 引用的以下 fixture 不存在，本 task 一并添加：
- `db_engine` (T12, T19) — 测试 DB engine
- `db_session` (T8, T12, T14, T16, T18) — async session
- `deps_factory` (T12) — TradingDeps factory for run_agent_cycle test
- `deps_with_sim_exchange` (T8) — TradingDeps with SimulatedExchange
- `db_engine_with_real_db` (T19) — copy of data/tradebot.db for historical compat

- [ ] **Step 9.1: 在 tests/conftest.py 末尾加 make_usage factory**

Append to `tests/conftest.py`:

```python
# === Phase 1 (T9): make_usage factory for unified RunUsage mocking ===
import pytest
from unittest.mock import MagicMock


@pytest.fixture
def make_usage():
    """Factory for pydantic-ai RunUsage mock with Phase 1 standard attrs.

    Default values reflect a typical DeepSeek cycle (input ~1000, cache_read 70%).
    Override per test as needed for happy/forensic/edge-case scenarios.

    Usage:
        def test_xxx(make_usage):
            usage = make_usage(input_tokens=500, cache_read_tokens=300)
            mock_result.usage.return_value = usage
    """
    def _make(
        input_tokens: int = 1000,
        output_tokens: int = 200,
        cache_read_tokens: int = 700,
        cache_write_tokens: int = 0,
        details: dict | None = None,
    ):
        if details is None:
            # 默认含 DeepSeek vendor keys 镜像（双轨 logger 兼容）
            details = {
                "prompt_cache_hit_tokens": cache_read_tokens,
                "prompt_cache_miss_tokens": input_tokens - cache_read_tokens,
                "reasoning_tokens": 0,
            }
        usage = MagicMock()
        usage.total_tokens = input_tokens + output_tokens
        usage.input_tokens = input_tokens
        usage.output_tokens = output_tokens
        usage.cache_read_tokens = cache_read_tokens
        usage.cache_write_tokens = cache_write_tokens
        usage.details = details
        return usage
    return _make


# === Phase 1 (T9): pytest CLI options for AC-9 sim DB drift-guard ===
def pytest_addoption(parser):
    parser.addoption(
        "--sim-db", action="store", default=None,
        help="Path to archived sim DB for drift-guard tests (skip if not provided)"
    )
    parser.addoption(
        "--session-id", action="store", default=None,
        help="Session ID to filter within --sim-db (used by AC-9 baseline test)"
    )


# === Phase 1 (T9): Phase 1 测试 fixtures (used by T8/T12/T14/T16/T18/T19) ===
import os
import pytest_asyncio
import shutil
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine, async_sessionmaker
from src.storage.models import Base


@pytest_asyncio.fixture
async def db_engine(tmp_path):
    """Async engine on a fresh tmp DB with full schema via alembic upgrade head.

    **不用 Base.metadata.create_all** — SQLAlchemy metadata 不知道 alembic 创建的
    view (op.execute("CREATE VIEW ...")), 导致 T14/T16/T18/T21 跑 SELECT * FROM
    v_cycle_metrics 会 OperationalError "no such table"。改用 alembic upgrade head
    作 single source of truth：schema (含 9 列) + 3 view 全部由 alembic migration 创建。

    Trade-off: 每个测试 fixture 启动 ~1-2s alembic overhead；可接受。
    """
    import subprocess
    db_path = tmp_path / "phase1_test.db"
    db_url = f"sqlite:///{db_path}"
    # subprocess 跑 alembic 避免 in-process alembic state 污染
    subprocess.run(
        ["alembic", "upgrade", "head"],
        env={**os.environ, "DATABASE_URL": db_url},
        check=True, capture_output=True,
    )
    engine = create_async_engine(f"sqlite+aiosqlite:///{db_path}")
    yield engine
    await engine.dispose()


@pytest_asyncio.fixture
async def db_session(db_engine):
    """Async session bound to db_engine; auto-rollback on test exit."""
    async_session_factory = async_sessionmaker(db_engine, expire_on_commit=False)
    async with async_session_factory() as session:
        yield session


@pytest.fixture
def deps_factory(db_engine):
    """Factory for TradingDeps with mocked exchange (used by run_agent_cycle tests).

    Returns a callable that creates a fresh TradingDeps per call;
    使 T12 三路径测试可独立配 deps。

    **TradingDeps 必填字段** (verify by trader.py:25-45)：
    symbol, timeframe, market_data, exchange, technical, memory, session_id
    （technical 必填易漏，参考 tests/test_cycle_log.py:52 mock 模式）。
    """
    from src.agent.trader import TradingDeps
    from src.services.market_data import MarketDataService
    from src.services.technical import TechnicalAnalysisService
    from src.services.memory_service import MemoryService
    from src.services.approval_gate import ApprovalGate
    from src.integrations.exchange.simulated import SimulatedExchange
    from src.integrations.exchange.base import ExchangeConfig
    from unittest.mock import MagicMock

    def _make(symbol="BTC/USDT:USDT", session_id=None):
        if session_id is None:
            import uuid
            session_id = str(uuid.uuid4())
        config = ExchangeConfig(name="simulated", fee_rate=0.0005, precision={symbol: 3})
        exchange = SimulatedExchange(
            config=config, db_engine=db_engine,
            session_id=session_id, symbol=symbol,
        )
        deps = TradingDeps(
            session_id=session_id, symbol=symbol,
            timeframe="15m", exchange=exchange,
            market_data=MarketDataService(exchange),
            technical=MagicMock(spec=TechnicalAnalysisService),  # T9-#4: 必填漏补
            memory=MagicMock(format_for_prompt=MagicMock(return_value="No relevant memories.")),
            approval_gate=ApprovalGate(enabled=False, timeout_seconds=30, console=MagicMock()),
            approval_enabled=False, db_engine=db_engine,
        )
        return deps
    return _make


@pytest_asyncio.fixture
async def deps_with_sim_exchange(deps_factory):
    """TradingDeps with SimulatedExchange — used by T8 alert_id tests."""
    return deps_factory()


@pytest_asyncio.fixture
async def db_engine_with_real_db(tmp_path):
    """Copy of data/tradebot.db + 自含 alembic upgrade head (AC-8 historical compat).

    **不依赖主 DB schema 状态** — copy 后显式 alembic upgrade head 到副本，
    确保 fresh checkout / 主 DB 处于 R2-7 head 时也可跑（不受 T13/T15/T17 副作用影响）。
    Suitable for verifying 3 views work on real sim history (sim #7 105 cycles +
    sim #8 178 cycles).
    """
    import subprocess
    src = "data/tradebot.db"
    dst = tmp_path / "compat_test.db"
    shutil.copy(src, dst)
    # 显式 alembic upgrade head 到副本（不依赖主 DB 状态）
    subprocess.run(
        ["alembic", "upgrade", "head"],
        env={**os.environ, "DATABASE_URL": f"sqlite:///{dst}"},
        check=True, capture_output=True,
    )
    engine = create_async_engine(f"sqlite+aiosqlite:///{dst}")
    yield engine
    await engine.dispose()
```

- [ ] **Step 9.2: 写 make_usage factory 单元测试**

Create `tests/test_make_usage_factory.py`:

```python
"""T9: make_usage factory 单元测试 — defaults + override + 双轨 details mirror。"""

def test_make_usage_defaults(make_usage):
    """T9.1: 无参调用返回 DeepSeek-typical mock。"""
    usage = make_usage()
    assert usage.input_tokens == 1000
    assert usage.output_tokens == 200
    assert usage.total_tokens == 1200
    assert usage.cache_read_tokens == 700
    assert usage.cache_write_tokens == 0
    assert usage.details["prompt_cache_hit_tokens"] == 700
    assert usage.details["prompt_cache_miss_tokens"] == 300


def test_make_usage_override(make_usage):
    """T9.2: 可独立覆盖每个属性。"""
    usage = make_usage(
        input_tokens=500, output_tokens=100,
        cache_read_tokens=400, cache_write_tokens=10,
    )
    assert usage.input_tokens == 500
    assert usage.cache_read_tokens == 400
    assert usage.details["prompt_cache_hit_tokens"] == 400
    assert usage.details["prompt_cache_miss_tokens"] == 100


def test_make_usage_custom_details(make_usage):
    """T9.3: 显式传 details 覆盖默认（vendor mismatch 测试场景）。"""
    usage = make_usage(
        input_tokens=1000, cache_read_tokens=500,
        details={"reasoning_tokens": 50},
    )
    assert usage.details == {"reasoning_tokens": 50}
    # cache_read 仍走标准属性，不依赖 details
    assert usage.cache_read_tokens == 500
```

- [ ] **Step 9.3: 写 AC-11 hit_rate 公式 regression unit test**

> **#9 修订**：AC-11 不只是 T0 manual research，必须有 CI 自动化 regression guard 防 R2-Next-J 等未来升级 pydantic-ai 时悄悄改 hit_rate 公式语义。

Create `tests/test_hit_rate_formula.py`:

```python
"""AC-11 regression guard — hit_rate 公式语义 (spec §5.5.1) 不被未来 pydantic-ai
升级悄悄改变；T0 是 manual research 一次性验证，本测试是 CI 自动化兜底。
"""
import pytest


def test_hit_rate_formula_consistent_with_legacy_logger(make_usage):
    """T9.4 (AC-11): cli/app.py 现有 hit_rate 公式 = cache_hit / (hit + miss) * 100；
    spec §5.5.1 双轨设计后 logger 仍用此公式（DeepSeek vendor key），DB cache_hit_rate
    列存此值。本测试断言 mock cycle 上公式一致。
    """
    usage = make_usage(input_tokens=1000, cache_read_tokens=700)
    # legacy 公式 (logger compat)
    cache_hit = usage.details["prompt_cache_hit_tokens"]
    cache_miss = usage.details["prompt_cache_miss_tokens"]
    hit_rate = (cache_hit / (cache_hit + cache_miss) * 100) if (cache_hit + cache_miss) > 0 else 0.0

    # AC-11 (a): cache_read_tokens ≈ prompt_cache_hit_tokens (5% 误差内)
    rel_err_a = abs(usage.cache_read_tokens - cache_hit) / max(usage.cache_read_tokens, cache_hit)
    assert rel_err_a < 0.05, f"AC-11 (a) violated: cache_read={usage.cache_read_tokens} hit={cache_hit}"

    # AC-11 (b): input_tokens ≈ cache_hit + cache_miss (5% 误差内)
    rel_err_b = abs(usage.input_tokens - (cache_hit + cache_miss)) / max(usage.input_tokens, cache_hit + cache_miss)
    assert rel_err_b < 0.05, f"AC-11 (b) violated: input={usage.input_tokens} sum={cache_hit + cache_miss}"

    # 公式断言
    assert hit_rate == pytest.approx(70.0)  # 700 / 1000


def test_hit_rate_derived_portable_formula(make_usage):
    """T9.5 (AC-11): cache_hit_rate_derived 派生公式 = cache_read * 100 / input_tokens
    (provider-agnostic，spec §5.2.3 推荐分析端用)。
    """
    usage = make_usage(input_tokens=1000, cache_read_tokens=750)
    derived = usage.cache_read_tokens * 100.0 / usage.input_tokens
    assert derived == pytest.approx(75.0)


def test_hit_rate_derived_null_on_zero_input(make_usage):
    """T9.6 (AC-11): cache_hit_rate_derived 在 input_tokens=0 时应 NULL（spec view CASE）。"""
    usage = make_usage(input_tokens=0, cache_read_tokens=0)
    # SQL view CASE WHEN input_tokens > 0 THEN ... ELSE NULL — Python equivalent:
    derived = (usage.cache_read_tokens * 100.0 / usage.input_tokens) if usage.input_tokens > 0 else None
    assert derived is None
```

- [ ] **Step 9.4: 跑 make_usage factory test + AC-11 regression test + 验证 5 个 Phase 1 fixture 可用**

```bash
pytest tests/test_make_usage_factory.py tests/test_hit_rate_formula.py -v

# Verify 新 fixture 解析（用 --collect-only 确认 fixture chain 不破）
pytest tests/test_make_usage_factory.py --fixtures 2>&1 | grep -E "db_engine|db_session|deps_factory|deps_with_sim_exchange|db_engine_with_real_db"
```

Expected: 3 + 3 个测试 PASS；`--fixtures` 输出含全 5 个新 fixture。

- [ ] **Step 9.5: Commit**

```bash
git add tests/conftest.py tests/test_make_usage_factory.py tests/test_hit_rate_formula.py
git commit -m "feat(iter-w2r2-obs-phase1): T9 make_usage factory + 5 fixtures + AC-11 regression test (M2 + #9)"
```

---

## Task 10: cli/app.py happy path 写 8 字段（双轨变量）

**Files:**
- Modify: `src/cli/app.py:599-617` (token extraction — 双轨)
- Modify: `src/cli/app.py:513-520` (retry loop — llm_start/llm_end)
- Modify: `src/cli/app.py:651-668` (happy path INSERT — 8 字段)

- [ ] **Step 10.1: 修改 retry loop 加 llm_start/llm_end**

Modify `src/cli/app.py:513-520`:

```python
    result = None
    llm_call_ms = None              # 新（T10）— 默认 None；happy 路径覆写
    for attempt in range(3):
        try:
            llm_start = datetime.now(timezone.utc)        # 新（T10）
            result = await agent.run(
                prompt,
                usage_limits=USAGE_LIMITS_PER_CYCLE,
                **run_kwargs,
            )
            llm_end = datetime.now(timezone.utc)          # 新（T10）
            llm_call_ms = int((llm_end - llm_start).total_seconds() * 1000)  # 新（T10）
            break
```

注：`llm_call_ms = None` 在 retry loop 之前预设；happy path 覆写为实际值；forensic 路径（T11）保留 None。

- [ ] **Step 10.2: 修改 token extraction — 双轨变量**

Modify `src/cli/app.py:599-617`（在 `usage = result.usage()` 之后）:

```python
    usage = result.usage()
    tokens = usage.total_tokens if usage else 0
    details = (usage.details or {}) if usage else {}

    # === 旧变量名保留（cli/app.py:613-616 logger.info + sim log 解析脚本兼容）===
    reasoning_tokens = details.get("reasoning_tokens", 0)
    cache_hit   = details.get("prompt_cache_hit_tokens", 0)    # DeepSeek-specific
    cache_miss  = details.get("prompt_cache_miss_tokens", 0)
    input_total = cache_hit + cache_miss
    hit_rate = (cache_hit / input_total * 100) if input_total > 0 else 0.0

    # === 新变量 — pydantic-ai 标准属性给 DB 写入（更 portable + AC-11 验证一致）===
    cache_read  = usage.cache_read_tokens  if usage else 0
    cache_write = usage.cache_write_tokens if usage else 0
    input_tok   = usage.input_tokens       if usage else 0
    output_tok  = usage.output_tokens      if usage else 0

    logger.info(
        f"cycle {cycle_id} tokens: total={tokens} reasoning={reasoning_tokens} "
        f"cache_hit={cache_hit} cache_miss={cache_miss} rate={hit_rate:.1f}%"
    )
    budget.record(tokens)
```

- [ ] **Step 10.3: 修改 happy path INSERT — 加 8 字段**

Modify `src/cli/app.py:651-668`（happy path 的 `session.add(AgentCycle(...))`）:

```python
    async with get_session(engine) as session:
        session.add(
            AgentCycle(
                session_id=deps.session_id,
                cycle_id=cycle_id,
                triggered_by=trigger_type,
                trigger_context=json.dumps(trigger_context_var) if trigger_context_var else None,
                state_snapshot=json.dumps(state_snapshot_var),
                reasoning=thinking_text,
                decision=result.output,
                execution_status="ok",
                model_id=model_id_var,
                tokens_consumed=tokens,
                # === Phase 1 新加 (T10) ===
                wall_time_ms=int((datetime.now(timezone.utc) - cycle_started_at).total_seconds() * 1000),
                llm_call_ms=llm_call_ms,
                input_tokens=input_tok,
                output_tokens=output_tok,
                cache_read_tokens=cache_read,
                cache_write_tokens=cache_write,
                reasoning_tokens=reasoning_tokens,
                cache_hit_rate=hit_rate,
            )
        )
        await session.commit()
```

- [ ] **Step 10.4: 跑现有 test_run_agent_cycle.py 确认 happy 路径不破**

```bash
pytest tests/test_run_agent_cycle.py -v -k "not retry and not usage_limit"
```

Expected: happy 路径测试全 PASS（旧 fixture 用 mock_usage，仍兼容因 default 0/None/details）。**部分测试可能失败因 mock_usage 未含新 attrs**——这部分留 T23 集中迁移到 `make_usage`。本步只 verify happy path 主流程不 raise。

如有失败但是是 attr-missing 错（非语义错），记下失败 test 名，T23 时统一处理。

- [ ] **Step 10.5: Commit**

```bash
git add src/cli/app.py
git commit -m "feat(iter-w2r2-obs-phase1): T10 cli/app.py happy path 8 fields + 双轨变量 (AC-2 partial)"
```

---

## Task 11: cli/app.py forensic 路径 8 字段

**Files:**
- Modify: `src/cli/app.py:526-538` (UsageLimitExceeded path)
- Modify: `src/cli/app.py:568-581` (retry_exhausted path)

- [ ] **Step 11.1: 修改 UsageLimitExceeded forensic INSERT**

Modify `src/cli/app.py:526-538`:

```python
        except UsageLimitExceeded as e:
            llm_call_ms = None      # T11: forensic NULL（覆盖 T10 默认；显式记录）
            logger.error(f"Cycle {cycle_id} hit usage limit: {e}")
            async with get_session(engine) as session:
                session.add(AgentCycle(
                    session_id=deps.session_id,
                    cycle_id=cycle_id,
                    triggered_by=trigger_type,
                    trigger_context=json.dumps(trigger_context_var) if trigger_context_var else None,
                    state_snapshot=json.dumps(state_snapshot_var),
                    reasoning=None,
                    decision=None,
                    execution_status="usage_limit_exceeded",
                    model_id=model_id_var,
                    tokens_consumed=0,
                    # === Phase 1 新加 (T11 forensic) ===
                    wall_time_ms=int((datetime.now(timezone.utc) - cycle_started_at).total_seconds() * 1000),
                    llm_call_ms=llm_call_ms,    # None
                    input_tokens=None,
                    output_tokens=None,
                    cache_read_tokens=None,
                    cache_write_tokens=None,
                    reasoning_tokens=None,
                    cache_hit_rate=None,
                ))
                await session.commit()
            # ... existing post-write logic 不变
```

- [ ] **Step 11.2: 修改 retry_exhausted forensic INSERT**

Modify `src/cli/app.py:568-581`（最后一次 attempt 失败的 forensic write）:

```python
            else:
                logger.error(f"LLM call failed after 3 attempts: {e}")
                llm_call_ms = None      # T11: forensic NULL
                err_class = type(e).__name__
                err_raw = str(e)
                err_msg = (err_raw[:200] + "...") if len(err_raw) > 200 else err_raw
                async with get_session(engine) as session:
                    session.add(AgentCycle(
                        session_id=deps.session_id,
                        cycle_id=cycle_id,
                        triggered_by=trigger_type,
                        trigger_context=json.dumps(trigger_context_var) if trigger_context_var else None,
                        state_snapshot=json.dumps(state_snapshot_var),
                        reasoning=None,
                        decision=None,
                        execution_status="retry_exhausted",
                        model_id=model_id_var,
                        tokens_consumed=0,
                        # === Phase 1 新加 (T11 forensic) ===
                        wall_time_ms=int((datetime.now(timezone.utc) - cycle_started_at).total_seconds() * 1000),
                        llm_call_ms=llm_call_ms,    # None
                        input_tokens=None,
                        output_tokens=None,
                        cache_read_tokens=None,
                        cache_write_tokens=None,
                        reasoning_tokens=None,
                        cache_hit_rate=None,
                    ))
                    await session.commit()
```

- [ ] **Step 11.3: 跑现有 test_run_agent_cycle.py 的 forensic 测试**

```bash
pytest tests/test_run_agent_cycle.py -v -k "retry or usage_limit"
```

Expected: forensic 测试 PASS 或 attr-missing 错（如失败属 mock_usage 需 migrate，留 T23 处理）。

- [ ] **Step 11.4: Commit**

```bash
git add src/cli/app.py
git commit -m "feat(iter-w2r2-obs-phase1): T11 cli/app.py forensic 路径 8 字段 (AC-2 complete)"
```

---

## Task 12: AC-2 三路径 unit test

**Files:**
- Test: `tests/test_run_agent_cycle_phase1.py` (Create)

- [ ] **Step 12.1: 写 AC-2 三路径测试**

Create `tests/test_run_agent_cycle_phase1.py`:

```python
"""AC-2: 三路径 8 字段填值符合 spec §5.5.1/§5.5.2 规则。"""
import asyncio
from unittest.mock import MagicMock, AsyncMock, patch
import pytest
from sqlalchemy import select

from src.cli.app import run_agent_cycle, TokenBudget
from src.storage.models import AgentCycle


@pytest.mark.asyncio
async def test_happy_path_fills_all_8_fields(make_usage, deps_factory, db_engine, db_session):
    """T12.1 (AC-2 happy): 全 8 字段非 NULL 且符合 §5.5.1 公式。"""
    usage = make_usage(
        input_tokens=1500, output_tokens=300,
        cache_read_tokens=1050, cache_write_tokens=10,
    )
    mock_result = MagicMock()
    mock_result.usage.return_value = usage
    mock_result.output = "(1) Stance: long. (2) Active: ..."
    mock_result.new_messages.return_value = []

    mock_agent = MagicMock()
    mock_agent.run = AsyncMock(return_value=mock_result)

    deps = deps_factory()
    budget = TokenBudget(daily_max=100000)
    await run_agent_cycle(
        mock_agent, deps, "scheduled", budget, db_engine,
    )

    row = (await db_session.execute(
        select(AgentCycle).order_by(AgentCycle.id.desc()).limit(1)
    )).scalar_one()

    assert row.execution_status == "ok"
    assert row.wall_time_ms is not None and row.wall_time_ms > 0
    assert row.llm_call_ms is not None and row.llm_call_ms >= 0
    assert row.input_tokens == 1500
    assert row.output_tokens == 300
    assert row.cache_read_tokens == 1050
    assert row.cache_write_tokens == 10
    assert row.reasoning_tokens == 0
    assert row.cache_hit_rate == pytest.approx(70.0)   # 1050/1500*100


@pytest.mark.asyncio
async def test_usage_limit_exceeded_only_wall_time_filled(deps_factory, db_engine, db_session):
    """T12.2 (AC-2 forensic): UsageLimitExceeded 路径仅 wall_time_ms 填，其余 NULL。"""
    from pydantic_ai.exceptions import UsageLimitExceeded

    mock_agent = MagicMock()
    mock_agent.run = AsyncMock(side_effect=UsageLimitExceeded("test limit"))

    deps = deps_factory()
    budget = TokenBudget(daily_max=100000)
    await run_agent_cycle(
        mock_agent, deps, "scheduled", budget, db_engine,
    )

    row = (await db_session.execute(
        select(AgentCycle).order_by(AgentCycle.id.desc()).limit(1)
    )).scalar_one()

    assert row.execution_status == "usage_limit_exceeded"
    assert row.wall_time_ms is not None and row.wall_time_ms > 0
    assert row.llm_call_ms is None
    assert row.input_tokens is None
    assert row.output_tokens is None
    assert row.cache_read_tokens is None
    assert row.cache_write_tokens is None
    assert row.reasoning_tokens is None
    assert row.cache_hit_rate is None
    assert row.tokens_consumed == 0


@pytest.mark.asyncio
async def test_retry_exhausted_only_wall_time_filled(deps_factory, db_engine, db_session):
    """T12.3 (AC-2 forensic): retry_exhausted 路径仅 wall_time_ms 填，其余 NULL。"""
    mock_agent = MagicMock()
    mock_agent.run = AsyncMock(side_effect=RuntimeError("network down"))

    deps = deps_factory()
    budget = TokenBudget(daily_max=100000)
    with patch("asyncio.sleep", new=AsyncMock()):  # skip backoff for fast test
        await run_agent_cycle(
            mock_agent, deps, "scheduled", budget, db_engine,
        )

    row = (await db_session.execute(
        select(AgentCycle).order_by(AgentCycle.id.desc()).limit(1)
    )).scalar_one()

    assert row.execution_status == "retry_exhausted"
    assert row.wall_time_ms is not None and row.wall_time_ms > 0
    assert row.llm_call_ms is None
    for col in ("input_tokens", "output_tokens", "cache_read_tokens",
                "cache_write_tokens", "reasoning_tokens", "cache_hit_rate"):
        assert getattr(row, col) is None, f"{col} expected None"
```

- [ ] **Step 12.2: 跑测试确认 PASS**

```bash
pytest tests/test_run_agent_cycle_phase1.py -v
```

Expected: 3 个全 PASS。如 `deps_factory` fixture 不存在，grep `tests/conftest.py` / `tests/test_run_agent_cycle.py` 找等价 deps factory；plan 实施时统一替换。

- [ ] **Step 12.3: Commit**

```bash
git add tests/test_run_agent_cycle_phase1.py
git commit -m "feat(iter-w2r2-obs-phase1): T12 AC-2 三路径 8 字段 unit test"
```

---

## Task 13: alembic migration 加 v_cycle_metrics view

**Files:**
- Modify: `alembic/versions/<rev>_phase1_observability.py` (填充 `_V_CYCLE_METRICS_SQL` 占位)

- [ ] **Step 13.1: 填充 _V_CYCLE_METRICS_SQL 字符串**

Replace the `_V_CYCLE_METRICS_SQL = ""` placeholder line in migration with（保 spec §5.2.2 SQL 字面一致）:

```python
_V_CYCLE_METRICS_SQL = """
CREATE VIEW v_cycle_metrics AS
WITH ac_with_anchors AS (
  SELECT
    ac.*,
    CASE WHEN ac.decision LIKE '%(1) Stance%' OR ac.decision LIKE '%(1) **Stance%'
           OR ac.decision LIKE '%**(1) Stance%' OR ac.decision LIKE '%**(1)** Stance%'
         THEN 1 ELSE 0 END AS has_stance,
    CASE WHEN ac.decision LIKE '%(2) Active%' OR ac.decision LIKE '%(2) **Active%'
           OR ac.decision LIKE '%**(2) Active%' OR ac.decision LIKE '%**(2)** Active%'
         THEN 1 ELSE 0 END AS has_active_commitments,
    CASE WHEN ac.decision LIKE '%(3) This cycle%' OR ac.decision LIKE '%(3) **This cycle%'
           OR ac.decision LIKE '%**(3) This cycle%' OR ac.decision LIKE '%**(3)** This cycle%'
         THEN 1 ELSE 0 END AS has_this_cycle_delta,
    CASE WHEN ac.decision LIKE '%(4) Thesis%' OR ac.decision LIKE '%(4) **Thesis%'
           OR ac.decision LIKE '%**(4) Thesis%' OR ac.decision LIKE '%**(4)** Thesis%'
         THEN 1 ELSE 0 END AS has_thesis_invalidation,
    CASE WHEN ac.decision LIKE '%(5) Watch%' OR ac.decision LIKE '%(5) **Watch%'
           OR ac.decision LIKE '%**(5) Watch%' OR ac.decision LIKE '%**(5)** Watch%'
         THEN 1 ELSE 0 END AS has_watch_list
  FROM agent_cycles ac
)
SELECT
  ac.session_id, ac.cycle_id, ac.triggered_by, ac.execution_status,
  ac.created_at, ac.model_id,
  ac.wall_time_ms, ac.llm_call_ms,
  (SELECT SUM(tc.duration_ms) FROM tool_calls tc
   WHERE tc.session_id=ac.session_id AND tc.cycle_id=ac.cycle_id) AS tool_total_ms,
  ac.tokens_consumed, ac.input_tokens, ac.output_tokens,
  ac.cache_read_tokens, ac.cache_write_tokens,
  ac.reasoning_tokens,
  ac.cache_hit_rate,
  CASE WHEN ac.input_tokens IS NOT NULL AND ac.input_tokens > 0
       THEN ac.cache_read_tokens * 100.0 / ac.input_tokens
       ELSE NULL END AS cache_hit_rate_derived,
  CAST(json_extract(ac.state_snapshot, '$.position.contracts')      AS REAL)    AS position_size,
       json_extract(ac.state_snapshot, '$.position.side')                       AS position_side,
  CAST(json_extract(ac.state_snapshot, '$.position.leverage')       AS INTEGER) AS position_leverage,
  CAST(json_extract(ac.state_snapshot, '$.position.unrealized_pnl') AS REAL)    AS position_unrealized_pnl,
  CAST(json_extract(ac.state_snapshot, '$.position.pnl_pct')        AS REAL)    AS position_pnl_pct,
  CAST(json_extract(ac.state_snapshot, '$.balance.free_usdt')       AS REAL)    AS balance_free_usdt,
  CAST(json_extract(ac.state_snapshot, '$.market.ticker_last')      AS REAL)    AS ticker_last,
       json_extract(ac.state_snapshot, '$.market.fetched_at')                   AS state_captured_at,
  json_array_length(json_extract(ac.state_snapshot, '$.pending_orders')) AS pending_orders_count,
  json_array_length(json_extract(ac.state_snapshot, '$.active_alerts'))  AS active_alerts_count,
  json_array_length(json_extract(ac.state_snapshot, '$._errors'))        AS snapshot_errors_count,
  CASE WHEN json_extract(ac.state_snapshot, '$.position') IS NOT NULL
       THEN 1 ELSE 0 END AS has_position,
  length(ac.decision) AS decision_length,
  ac.has_stance, ac.has_active_commitments, ac.has_this_cycle_delta,
  ac.has_thesis_invalidation, ac.has_watch_list,
  CASE WHEN (ac.has_stance + ac.has_active_commitments
           + ac.has_this_cycle_delta + ac.has_thesis_invalidation) >= 4
       THEN 1 ELSE 0 END AS five_field_complete,
  CASE WHEN ac.execution_status='ok'
        AND ac.decision IS NOT NULL
        AND length(ac.decision) > 0
       THEN 1 ELSE 0 END AS is_ok_cycle,
  CASE WHEN ac.execution_status IN ('retry_exhausted','usage_limit_exceeded')
       THEN 1 ELSE 0 END AS is_forensic_cycle
FROM ac_with_anchors ac
"""
```

- [ ] **Step 13.2: 重 alembic upgrade 应用 view（含 backup）**

> **M1**: T4 后到 T13 间任何 dev cycle 写入 8 新列的数据，downgrade -1 会丢；先 backup。

```bash
cp data/tradebot.db data/tradebot.db.bak-pre-t13
alembic downgrade -1
alembic upgrade head
```

- [ ] **Step 13.3: 手动 SELECT 抽样验证 view 字段**

```bash
sqlite3 data/tradebot.db "SELECT cycle_id, has_stance, five_field_complete, is_ok_cycle FROM v_cycle_metrics WHERE session_id='8f2ca0cb-1c2a-4dc7-8927-198e6c0d98d3' LIMIT 5"
```

Expected: 5 行返回，含 1/0 boolean 列；不 raise。

- [ ] **Step 13.3.5: 实测 5-field 联合 baseline + decision gate（< 90% 触发回 spec）**

> **#7 修订**：baseline 实测必须在 view commit **之前** — 如低于 90% 决策回滚阈值（spec §3 决策矩阵 #2），需暂停 plan 回 spec 重审 view 形态；如已 commit 再发现需 revert 多个 commits 浪费 context。

```bash
sqlite3 data/tradebot.db "
SELECT
  AVG(five_field_complete) AS hit_rate,
  COUNT(*) AS total_ok_cycles
FROM v_cycle_metrics
WHERE is_ok_cycle=1
  AND session_id='8f2ca0cb-1c2a-4dc7-8927-198e6c0d98d3'
"
```

记下输出（如 `hit_rate=0.94, total_ok_cycles=171`）。

**Decision gate**:
- baseline ≥ **0.90** → ✅ 续 Step 13.4 commit；记下 baseline 数字（T20 Step 20.3 时填入 `_BASELINE_HIT_RATE` 常量）
- baseline < **0.90** → 🛑 **不 commit**；alembic downgrade -1 撤销 view；plan 暂停回 spec §3 决策矩阵 #2 重审 view 形态：
  - 是否切物化表（P5-A）？
  - 是否改 anchor pattern（如 5 anchor 减到 3 mandatory）？
  - 是否改 LIKE 4-variant（如增加 anchored prefix 兼容更多 markdown 形态）？

- [ ] **Step 13.4: Commit (baseline ≥ 0.90 时执行)**

```bash
git add alembic/versions/<rev>_phase1_observability.py
git commit -m "feat(iter-w2r2-obs-phase1): T13 alembic 加 v_cycle_metrics view (38 列；baseline X.XX)"
```

（X.XX 替换为 Step 13.3.5 实测数字。）

---

## Task 14: AC-5 v_cycle_metrics 测试

**Files:**
- Test: `tests/test_v_cycle_metrics.py` (Create)

- [ ] **Step 14.1: 写 AC-5 字段断言测试**

Create `tests/test_v_cycle_metrics.py`:

```python
"""AC-5: v_cycle_metrics 字段集 + 5-field anchor + cache_hit_rate_derived 派生正确。"""
import json
from datetime import datetime, timezone
import pytest
from sqlalchemy import text

from src.storage.models import AgentCycle


@pytest.mark.asyncio
async def test_v_cycle_metrics_returns_38_columns(db_session):
    """T14.1: SELECT * FROM v_cycle_metrics 返回 38 列（spec §5.2 字段表）。"""
    rows = (await db_session.execute(text(
        "SELECT * FROM v_cycle_metrics LIMIT 1"
    ))).mappings().all()
    if not rows:
        pytest.skip("empty agent_cycles table")

    cols = set(rows[0].keys())
    expected_subset = {
        "session_id", "cycle_id", "triggered_by", "execution_status",
        "created_at", "model_id",
        "wall_time_ms", "llm_call_ms", "tool_total_ms",
        "tokens_consumed", "input_tokens", "output_tokens",
        "cache_read_tokens", "cache_write_tokens",
        "reasoning_tokens", "cache_hit_rate", "cache_hit_rate_derived",
        "position_size", "position_side", "position_leverage",
        "position_unrealized_pnl", "position_pnl_pct",
        "balance_free_usdt", "ticker_last", "state_captured_at",
        "pending_orders_count", "active_alerts_count", "snapshot_errors_count",
        "has_position", "decision_length",
        "has_stance", "has_active_commitments", "has_this_cycle_delta",
        "has_thesis_invalidation", "has_watch_list", "five_field_complete",
        "is_ok_cycle", "is_forensic_cycle",
    }
    assert expected_subset.issubset(cols), f"missing: {expected_subset - cols}"
    assert len(cols) == 38, f"expected 38 cols got {len(cols)}: {cols}"


@pytest.mark.asyncio
async def test_v_cycle_metrics_5field_anchors_detect(db_session):
    """T14.2: 5-field LIKE 4-variant pattern 正确识别 fixture cycle。"""
    fixture_cycle = AgentCycle(
        session_id="test-anchor-5field",
        cycle_id="anchor01",
        triggered_by="scheduled",
        execution_status="ok",
        decision=(
            "(1) Stance: long, thesis intact.\n"
            "(2) Active commitments: 0.05 BTC long.\n"
            "(3) This cycle delta: noop.\n"
            "(4) Thesis & invalidation: trend up; SL @ 80000.\n"
        ),
        state_snapshot=json.dumps({"position": None, "balance": {"free_usdt": 100.0}}),
        tokens_consumed=1000, input_tokens=800, cache_read_tokens=500,
        wall_time_ms=2000,
    )
    db_session.add(fixture_cycle)
    await db_session.commit()

    row = (await db_session.execute(text(
        "SELECT has_stance, has_active_commitments, has_this_cycle_delta, "
        "has_thesis_invalidation, has_watch_list, five_field_complete "
        "FROM v_cycle_metrics WHERE session_id='test-anchor-5field'"
    ))).mappings().one()

    assert row["has_stance"] == 1
    assert row["has_active_commitments"] == 1
    assert row["has_this_cycle_delta"] == 1
    assert row["has_thesis_invalidation"] == 1
    assert row["has_watch_list"] == 0      # 缺 (5)
    assert row["five_field_complete"] == 1  # 4 mandatory met


@pytest.mark.asyncio
async def test_v_cycle_metrics_cache_hit_rate_derived(db_session):
    """T14.3: cache_hit_rate_derived = cache_read * 100 / input_tokens（portable 派生）。"""
    fixture_cycle = AgentCycle(
        session_id="test-cache-rate",
        cycle_id="rate01",
        triggered_by="scheduled",
        execution_status="ok",
        decision="placeholder",
        state_snapshot=json.dumps({"position": None}),
        tokens_consumed=1200,
        input_tokens=1000,
        cache_read_tokens=750,    # 75% hit
        cache_hit_rate=0.0,       # legacy 列若为 0 模拟非 DeepSeek
    )
    db_session.add(fixture_cycle)
    await db_session.commit()

    row = (await db_session.execute(text(
        "SELECT cache_hit_rate, cache_hit_rate_derived "
        "FROM v_cycle_metrics WHERE session_id='test-cache-rate'"
    ))).mappings().one()

    assert row["cache_hit_rate"] == 0.0      # legacy DeepSeek-only 字段
    assert row["cache_hit_rate_derived"] == pytest.approx(75.0)   # portable 派生


@pytest.mark.asyncio
async def test_v_cycle_metrics_is_ok_excludes_empty_decision(db_session):
    """T14.4: is_ok_cycle 排除 empty-string decision（防 R2-7 result.output='' 边界）。"""
    fixture_cycle = AgentCycle(
        session_id="test-empty-decision",
        cycle_id="empty01",
        triggered_by="scheduled",
        execution_status="ok",
        decision="",                       # empty string
        state_snapshot=json.dumps({"position": None}),
        tokens_consumed=100,
    )
    db_session.add(fixture_cycle)
    await db_session.commit()

    row = (await db_session.execute(text(
        "SELECT is_ok_cycle FROM v_cycle_metrics WHERE session_id='test-empty-decision'"
    ))).mappings().one()

    assert row["is_ok_cycle"] == 0    # length(decision)=0 → 不算 ok
```

- [ ] **Step 14.2: 跑测试确认 4 个 PASS**

```bash
pytest tests/test_v_cycle_metrics.py -v
```

Expected: 4 个全 PASS。

- [ ] **Step 14.3: Commit**

```bash
git add tests/test_v_cycle_metrics.py
git commit -m "feat(iter-w2r2-obs-phase1): T14 AC-5 v_cycle_metrics 字段断言测试"
```

---

## Task 15: alembic migration 加 v_alert_lifecycle view

**Files:**
- Modify: `alembic/versions/<rev>_phase1_observability.py` (填充 `_V_ALERT_LIFECYCLE_SQL`)

- [ ] **Step 15.1: 填充 _V_ALERT_LIFECYCLE_SQL 字符串**

Replace `_V_ALERT_LIFECYCLE_SQL = ""` with（保 spec §5.3.2 SQL 字面一致）:

```python
_V_ALERT_LIFECYCLE_SQL = """
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
  SELECT session_id,
         json_extract(trigger_context, '$.alert_id') AS alert_id,
         created_at AS triggered_at,
         CAST(json_extract(trigger_context, '$.current_price') AS REAL) AS triggered_price
  FROM agent_cycles
  WHERE triggered_by='alert'
    AND json_extract(trigger_context, '$.type')='price_level_alert'
    AND json_extract(trigger_context, '$.alert_id') IS NOT NULL
),
cancels AS (
  SELECT session_id, alert_id,
         created_at AS cancelled_at,
         reasoning AS cancel_reasoning
  FROM trade_actions
  WHERE action='cancel_price_level_alert' AND alert_id IS NOT NULL
),
cancel_attempts AS (
  SELECT session_id,
         json_extract(args, '$.alert_id') AS alert_id,
         COUNT(*) AS attempt_count,
         SUM(CASE WHEN status='biz_error' THEN 1 ELSE 0 END) AS attempt_failures
  FROM tool_calls
  WHERE tool_name='cancel_price_level_alert'
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
```

- [ ] **Step 15.2: 重 alembic upgrade 应用（含 backup）**

> **M1**: 同 T13 — 先 backup 防 dev 数据丢。

```bash
cp data/tradebot.db data/tradebot.db.bak-pre-t15
alembic downgrade -1
alembic upgrade head
sqlite3 data/tradebot.db "SELECT name FROM sqlite_master WHERE type='view' AND name LIKE 'v_%'"
```

Expected: 输出含 `v_cycle_metrics` 和 `v_alert_lifecycle` 两个 view 名。

- [ ] **Step 15.3: Commit**

```bash
git add alembic/versions/<rev>_phase1_observability.py
git commit -m "feat(iter-w2r2-obs-phase1): T15 alembic 加 v_alert_lifecycle view (4 CTE)"
```

---

## Task 16: AC-6 v_alert_lifecycle 三态测试

**Files:**
- Test: `tests/test_v_alert_lifecycle.py` (Create)

- [ ] **Step 16.1: 写 AC-6 测试**

Create `tests/test_v_alert_lifecycle.py`:

```python
"""AC-6: v_alert_lifecycle register/trigger/cancel 三态 + cancel_attempts 统计。"""
import json
from datetime import datetime, timezone, timedelta
import pytest
from sqlalchemy import text

from src.storage.models import TradeAction, AgentCycle, ToolCall


@pytest.mark.asyncio
async def test_alert_lifecycle_active_state(db_session):
    """T16.1: 仅 register 无 trigger/cancel → final_status='active'。"""
    db_session.add(TradeAction(
        session_id="test-lifecycle-active",
        cycle_id="cyc01",
        action="add_price_level_alert",
        alert_id="active01",
        symbol="BTC/USDT:USDT",
        price=80000.0,
        reasoning="above 80000.0 | resistance",
    ))
    await db_session.commit()

    row = (await db_session.execute(text(
        "SELECT final_status, target_price, cancel_attempt_count "
        "FROM v_alert_lifecycle WHERE alert_id='active01'"
    ))).mappings().one()

    assert row["final_status"] == "active"
    assert row["target_price"] == 80000.0
    assert row["cancel_attempt_count"] == 0


@pytest.mark.asyncio
async def test_alert_lifecycle_triggered_state(db_session):
    """T16.2: register + trigger cycle → final_status='triggered'。"""
    db_session.add(TradeAction(
        session_id="test-lifecycle-trig",
        cycle_id="cyc01", action="add_price_level_alert",
        alert_id="trig0001", symbol="BTC/USDT:USDT", price=80000.0,
        reasoning="above 80000",
    ))
    db_session.add(AgentCycle(
        session_id="test-lifecycle-trig",
        cycle_id="cyc02",
        triggered_by="alert",
        trigger_context=json.dumps({
            "type": "price_level_alert",
            "alert_id": "trig0001",
            "current_price": 80050.0,
            "target_price": 80000.0,
            "direction": "above",
        }),
        state_snapshot=json.dumps({"position": None}),
        decision="hold",
    ))
    await db_session.commit()

    row = (await db_session.execute(text(
        "SELECT final_status, triggered_at, triggered_price "
        "FROM v_alert_lifecycle WHERE alert_id='trig0001'"
    ))).mappings().one()

    assert row["final_status"] == "triggered"
    assert row["triggered_at"] is not None
    assert row["triggered_price"] == 80050.0


@pytest.mark.asyncio
async def test_alert_lifecycle_cancelled_state(db_session):
    """T16.3: register + cancel → final_status='cancelled'。"""
    db_session.add(TradeAction(
        session_id="test-lifecycle-cancel",
        cycle_id="cyc01", action="add_price_level_alert",
        alert_id="canc0001", symbol="BTC/USDT:USDT", price=80000.0,
        reasoning="above 80000",
    ))
    db_session.add(TradeAction(
        session_id="test-lifecycle-cancel",
        cycle_id="cyc02", action="cancel_price_level_alert",
        alert_id="canc0001", symbol="BTC/USDT:USDT",
        reasoning="invalidated",
    ))
    await db_session.commit()

    row = (await db_session.execute(text(
        "SELECT final_status, cancelled_at FROM v_alert_lifecycle WHERE alert_id='canc0001'"
    ))).mappings().one()

    assert row["final_status"] == "cancelled"
    assert row["cancelled_at"] is not None


@pytest.mark.asyncio
async def test_alert_lifecycle_cancel_attempts_aggregation(db_session):
    """T16.4: cancel_attempts 累计 tool_calls 调用数 + biz_error 失败数。"""
    db_session.add(TradeAction(
        session_id="test-lifecycle-attempts",
        cycle_id="cyc01", action="add_price_level_alert",
        alert_id="att00001", symbol="BTC/USDT:USDT", price=80000.0,
        reasoning="above",
    ))
    # 2 个 cancel attempt — 1 ok 1 biz_error
    db_session.add(ToolCall(
        session_id="test-lifecycle-attempts", cycle_id="cyc02",
        tool_name="cancel_price_level_alert", status="ok", duration_ms=100,
        args=json.dumps({"alert_id": "att00001", "reasoning": "invalidated"}),
    ))
    db_session.add(ToolCall(
        session_id="test-lifecycle-attempts", cycle_id="cyc03",
        tool_name="cancel_price_level_alert", status="biz_error",
        error_type="alert_not_found", duration_ms=50,
        args=json.dumps({"alert_id": "att00001", "reasoning": "retry"}),
    ))
    await db_session.commit()

    row = (await db_session.execute(text(
        "SELECT cancel_attempt_count, cancel_attempt_failures "
        "FROM v_alert_lifecycle WHERE alert_id='att00001'"
    ))).mappings().one()

    assert row["cancel_attempt_count"] == 2
    assert row["cancel_attempt_failures"] == 1


@pytest.mark.asyncio
async def test_alert_lifecycle_filters_null_alert_id(db_session):
    """T16.5: 历史数据 alert_id NULL 行被 view 自动过滤（不污染输出）。"""
    db_session.add(TradeAction(
        session_id="test-lifecycle-null",
        cycle_id="cyc01", action="add_price_level_alert",
        alert_id=None,                  # 历史 sim 数据模拟
        symbol="BTC/USDT:USDT", price=80000.0,
        reasoning="legacy row",
    ))
    await db_session.commit()

    rows = (await db_session.execute(text(
        "SELECT * FROM v_alert_lifecycle WHERE session_id='test-lifecycle-null'"
    ))).mappings().all()

    assert len(rows) == 0    # NULL alert_id 完全不进 view
```

- [ ] **Step 16.2: 跑测试确认 5 个 PASS**

```bash
pytest tests/test_v_alert_lifecycle.py -v
```

Expected: 5 个全 PASS。

- [ ] **Step 16.3: Commit**

```bash
git add tests/test_v_alert_lifecycle.py
git commit -m "feat(iter-w2r2-obs-phase1): T16 AC-6 v_alert_lifecycle 三态 + cancel_attempts test"
```

---

## Task 17: alembic migration 加 v_order_lifecycle view

**Files:**
- Modify: `alembic/versions/<rev>_phase1_observability.py` (填充 `_V_ORDER_LIFECYCLE_SQL`)

- [ ] **Step 17.1: 填充 _V_ORDER_LIFECYCLE_SQL 字符串**

Replace `_V_ORDER_LIFECYCLE_SQL = ""` with（保 spec §5.4 SQL 字面一致）:

```python
_V_ORDER_LIFECYCLE_SQL = """
CREATE VIEW v_order_lifecycle AS
SELECT
  so.session_id,
  so.order_id, so.symbol, so.side, so.position_side,
  so.order_type, so.amount,
  so.trigger_price, so.filled_price, so.fee, so.leverage, so.frozen_margin,
  so.created_at, so.filled_at, so.status,
  CASE
    WHEN so.filled_at IS NOT NULL
    THEN CAST((julianday(so.filled_at) - julianday(so.created_at)) * 86400 AS INTEGER)
  END AS lifetime_seconds,
  CASE
    WHEN so.order_type IN ('stop','take_profit')
     AND so.trigger_price IS NOT NULL AND so.filled_price IS NOT NULL
    THEN (so.filled_price - so.trigger_price) / so.trigger_price * 100.0
    ELSE NULL
  END AS trigger_drift_pct,
  (SELECT ta.cycle_id
   FROM trade_actions ta
   WHERE ta.order_id=so.order_id
     AND ta.action IN ('open_position','close_position','place_limit_order',
                       'set_stop_loss','set_take_profit')
   ORDER BY ta.created_at LIMIT 1) AS originated_cycle_id
FROM sim_orders so
"""
```

- [ ] **Step 17.2: 重 alembic upgrade 应用（含 backup）**

> **M1**: 同 T13/T15 — 先 backup 防 dev 数据丢。

```bash
cp data/tradebot.db data/tradebot.db.bak-pre-t17
alembic downgrade -1
alembic upgrade head
sqlite3 data/tradebot.db "SELECT name FROM sqlite_master WHERE type='view' AND name LIKE 'v_%'"
```

Expected: 输出含三个 view: `v_cycle_metrics`, `v_alert_lifecycle`, `v_order_lifecycle`。

- [ ] **Step 17.3: Commit**

```bash
git add alembic/versions/<rev>_phase1_observability.py
git commit -m "feat(iter-w2r2-obs-phase1): T17 alembic 加 v_order_lifecycle view"
```

---

## Task 18: AC-7 v_order_lifecycle 测试

**Files:**
- Test: `tests/test_v_order_lifecycle.py` (Create)

- [ ] **Step 18.1: 写 AC-7 测试**

Create `tests/test_v_order_lifecycle.py`:

```python
"""AC-7: v_order_lifecycle lifetime_seconds / trigger_drift_pct / originated_cycle_id 派生。"""
from datetime import datetime, timezone, timedelta
import pytest
from sqlalchemy import text

from src.storage.models import SimOrder, TradeAction


@pytest.mark.asyncio
async def test_order_lifecycle_lifetime_seconds(db_session):
    """T18.1: filled_at - created_at = lifetime_seconds（julianday 派生）。"""
    now = datetime.now(timezone.utc)
    db_session.add(SimOrder(
        session_id="test-lifetime",
        order_id="order00001",
        symbol="BTC/USDT:USDT", side="buy", position_side="long",
        order_type="market", amount=0.01,
        status="filled", filled_price=80000.0,
        created_at=now,
        filled_at=now + timedelta(seconds=15),
    ))
    await db_session.commit()

    row = (await db_session.execute(text(
        "SELECT lifetime_seconds FROM v_order_lifecycle WHERE order_id='order00001'"
    ))).mappings().one()

    assert row["lifetime_seconds"] == 15


@pytest.mark.asyncio
async def test_order_lifecycle_trigger_drift_pct_signed(db_session):
    """T18.2: stop order 的 trigger_drift_pct 是 signed 浮点（保正负号）。"""
    db_session.add(SimOrder(
        session_id="test-drift-stop",
        order_id="order00002",
        symbol="BTC/USDT:USDT", side="sell", position_side="long",
        order_type="stop", amount=0.01,
        trigger_price=80000.0, filled_price=79900.0,    # 滑点 -0.125%
        status="filled",
    ))
    await db_session.commit()

    row = (await db_session.execute(text(
        "SELECT trigger_drift_pct FROM v_order_lifecycle WHERE order_id='order00002'"
    ))).mappings().one()

    assert row["trigger_drift_pct"] == pytest.approx(-0.125, abs=1e-3)


@pytest.mark.asyncio
async def test_order_lifecycle_drift_pct_null_for_limit(db_session):
    """T18.3: limit order trigger_drift_pct = NULL（filter 掉结构性恒 0 噪音）。"""
    db_session.add(SimOrder(
        session_id="test-drift-limit",
        order_id="order00003",
        symbol="BTC/USDT:USDT", side="buy", position_side="long",
        order_type="limit", amount=0.01,
        trigger_price=80000.0, filled_price=80000.0,    # limit 单 fill = trigger
        status="filled",
    ))
    await db_session.commit()

    row = (await db_session.execute(text(
        "SELECT trigger_drift_pct FROM v_order_lifecycle WHERE order_id='order00003'"
    ))).mappings().one()

    assert row["trigger_drift_pct"] is None    # limit 单不计


@pytest.mark.asyncio
async def test_order_lifecycle_originated_cycle_id(db_session):
    """T18.4: originated_cycle_id 取最早创建 cycle（按 trade_actions.created_at LIMIT 1）。"""
    earlier = datetime(2026, 5, 1, tzinfo=timezone.utc)
    later = datetime(2026, 5, 2, tzinfo=timezone.utc)

    db_session.add(SimOrder(
        session_id="test-origin",
        order_id="order00004",
        symbol="BTC/USDT:USDT", side="buy", position_side="long",
        order_type="market", amount=0.01, status="filled",
        filled_price=80000.0,
    ))
    # 创建 cycle (earlier) + cancel cycle (later) 同 order_id
    db_session.add(TradeAction(
        session_id="test-origin",
        cycle_id="orig_cycle",
        action="open_position", order_id="order00004",
        symbol="BTC/USDT:USDT", side="long",
        created_at=earlier,
    ))
    db_session.add(TradeAction(
        session_id="test-origin",
        cycle_id="cancel_cycle",
        action="cancel_order", order_id="order00004",
        symbol="BTC/USDT:USDT",
        created_at=later,
    ))
    await db_session.commit()

    row = (await db_session.execute(text(
        "SELECT originated_cycle_id FROM v_order_lifecycle WHERE order_id='order00004'"
    ))).mappings().one()

    assert row["originated_cycle_id"] == "orig_cycle"   # 取最早，cancel_order 不影响
```

- [ ] **Step 18.2: 跑测试确认 4 个 PASS**

```bash
pytest tests/test_v_order_lifecycle.py -v
```

Expected: 4 个全 PASS。

- [ ] **Step 18.3: Commit**

```bash
git add tests/test_v_order_lifecycle.py
git commit -m "feat(iter-w2r2-obs-phase1): T18 AC-7 v_order_lifecycle 派生测试"
```

---

## Task 19: AC-8 历史 sim DB 兼容测试

**Files:**
- Test: `tests/test_view_historical_compat.py` (Create)

- [ ] **Step 19.1: 写 AC-8 兼容测试**

Create `tests/test_view_historical_compat.py`:

```python
"""AC-8: 历史 sim 数据兼容性 — sim #1-#8 在三个 view 上 SELECT * 不 raise。

历史数据特征：
- agent_cycles 8 新列全 NULL（P1+P2 列在本 iter 加；老数据没有源头）
- trade_actions.alert_id 全 NULL（X 方案前老 trade_actions 没此列）
- trigger_context JSON 全无 alert_id key（PriceLevelAlertInfo 加字段是本 iter）
- v_alert_lifecycle 完全过滤掉历史 alert（IS NOT NULL filter）
- v_cycle_metrics / v_order_lifecycle 应能完整 SELECT
"""
import pytest
from sqlalchemy import text


@pytest.mark.asyncio
async def test_v_cycle_metrics_select_on_historical(db_engine_with_real_db):
    """T19.1: 现有 data/tradebot.db SELECT * FROM v_cycle_metrics 不 raise。"""
    async with db_engine_with_real_db.connect() as conn:
        result = await conn.execute(text(
            "SELECT * FROM v_cycle_metrics LIMIT 10"
        ))
        rows = result.mappings().all()

    # 不要求 rows 非空（有可能 fresh DB），仅要求 SELECT 能跑完
    for row in rows:
        # 历史 cycle: 8 新列应允许 NULL（不 raise）
        assert "wall_time_ms" in row
        assert "cache_hit_rate_derived" in row


@pytest.mark.asyncio
async def test_v_alert_lifecycle_filters_historical(db_engine_with_real_db):
    """T19.2: v_alert_lifecycle 在历史 DB 上返回 0 行（NULL alert_id 全过滤）。

    sim #1-#8 历史 trade_actions 的 alert_id 列在本 iter 之前不存在；
    upgrade 后该列是 NULL，被 WHERE alert_id IS NOT NULL 过滤掉。
    """
    async with db_engine_with_real_db.connect() as conn:
        result = await conn.execute(text(
            "SELECT COUNT(*) AS c FROM v_alert_lifecycle"
        ))
        count = result.scalar_one()

    assert count == 0   # 历史数据 alert_id NULL → 全过滤


@pytest.mark.asyncio
async def test_v_order_lifecycle_select_on_historical(db_engine_with_real_db):
    """T19.3: 历史 sim_orders 在 v_order_lifecycle 上 SELECT 不 raise。"""
    async with db_engine_with_real_db.connect() as conn:
        result = await conn.execute(text(
            "SELECT * FROM v_order_lifecycle LIMIT 10"
        ))
        rows = result.mappings().all()

    for row in rows:
        # trigger_drift_pct 对历史 limit 单应为 NULL（T17 filter）
        if row["order_type"] == "limit":
            assert row["trigger_drift_pct"] is None


@pytest.mark.asyncio
async def test_v_cycle_metrics_historical_8_new_cols_null(db_engine_with_real_db):
    """T19.4: 历史 cycle 的 8 新列全 NULL（不破坏 view 但 cache_hit_rate_derived 也 NULL）。"""
    async with db_engine_with_real_db.connect() as conn:
        result = await conn.execute(text(
            "SELECT cycle_id, wall_time_ms, input_tokens, cache_hit_rate_derived "
            "FROM v_cycle_metrics "
            "WHERE created_at < '2026-05-09'"   # 本 iter land 前的数据
            "LIMIT 5"
        ))
        rows = result.mappings().all()

    for row in rows:
        assert row["wall_time_ms"] is None       # 老数据无 timing
        assert row["input_tokens"] is None       # 老数据无 token 拆分
        assert row["cache_hit_rate_derived"] is None  # NULLIF(input_tokens,0)
```

注：fixture `db_engine_with_real_db` 需在 conftest.py 提供——指向 `data/tradebot.db` 副本（避免污染主 DB）。如不存在，plan 实施时加：

```python
@pytest.fixture
async def db_engine_with_real_db(tmp_path):
    """Copy of data/tradebot.db for historical compat tests (read-only safe)."""
    import shutil
    from src.storage.database import get_engine
    src = "data/tradebot.db"
    dst = tmp_path / "compat_test.db"
    shutil.copy(src, dst)
    engine = get_engine(f"sqlite+aiosqlite:///{dst}")
    yield engine
    await engine.dispose()
```

- [ ] **Step 19.2: 跑测试确认 4 个 PASS**

```bash
pytest tests/test_view_historical_compat.py -v
```

Expected: 4 个全 PASS。

- [ ] **Step 19.3: Commit**

```bash
git add tests/test_view_historical_compat.py tests/conftest.py
git commit -m "feat(iter-w2r2-obs-phase1): T19 AC-8 历史 sim DB 兼容测试"
```

---

## Task 20: AC-9 5-field anchor drift-guard + baseline 实测

**Files:**
- Test: `tests/test_5field_anchor_drift_guard.py` (Create)
- 实测: 在 sim #8 archive DB 上跑 baseline → 回填常量

- [ ] **Step 20.1: 复用 T13 Step 13.3.5 实测 baseline（不重跑）**

> **#7 修订**：baseline 已在 T13 Step 13.3.5 view commit 前实测（避免 < 90% 时 view 已 commit 需 revert）；T20 复用同一数字，无需重跑 SQL。

如忘记记下，可重跑 SQL：

```bash
sqlite3 data/tradebot.db "
SELECT
  AVG(five_field_complete) AS hit_rate,
  COUNT(*) AS total_ok_cycles
FROM v_cycle_metrics
WHERE is_ok_cycle=1
  AND session_id='8f2ca0cb-1c2a-4dc7-8927-198e6c0d98d3'
"
```

**注**：到达本步意味着 T13.3.5 baseline 已 ≥ 0.90（否则 plan 已暂停回 spec），无需重新决策 gate。

- [ ] **Step 20.2: 写 AC-9 drift-guard 测试**

Create `tests/test_5field_anchor_drift_guard.py`:

```python
"""AC-9: 5-field anchor 联合命中率 drift-guard。

CI 行为：pytest.skip(reason="sim DB not present") 当 --sim-db 未提供。
W3 上线前手动:
    pytest tests/test_5field_anchor_drift_guard.py \\
        --sim-db data/tradebot.db \\
        --session-id 8f2ca0cb-1c2a-4dc7-8927-198e6c0d98d3
"""
import asyncio
import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

# T20 baseline 实测后回填（参 spec §6 AC-9 baseline 回填位置）
# L3: 强制 None — 防 Step 20.3 被跳过；测试函数顶部 assertion 把"未填"明确暴露
_BASELINE_HIT_RATE = None     # ← MUST set in Step 20.3 before running this test
_DRIFT_THRESHOLD = (_BASELINE_HIT_RATE - 0.05) if _BASELINE_HIT_RATE is not None else None


@pytest.fixture
def sim_db_path(request):
    p = request.config.getoption("--sim-db")
    if not p:
        pytest.skip("sim DB not present (use --sim-db <path> to run drift-guard)")
    return p


@pytest.fixture
def session_id_filter(request):
    sid = request.config.getoption("--session-id")
    if not sid:
        pytest.skip("--session-id required for drift-guard scoping")
    return sid


@pytest.mark.asyncio
async def test_5field_anchor_drift_guard(sim_db_path, session_id_filter):
    """T20.2: AVG(five_field_complete) WHERE is_ok_cycle=1 ≥ baseline - 5pp。"""
    if _BASELINE_HIT_RATE is None:
        pytest.fail(
            "_BASELINE_HIT_RATE not set — run T20 Step 20.1 to measure baseline "
            "on archived sim DB, then update Step 20.3 to fill the constant"
        )
    engine = create_async_engine(f"sqlite+aiosqlite:///{sim_db_path}")
    try:
        async with engine.connect() as conn:
            row = (await conn.execute(text(
                "SELECT AVG(five_field_complete) AS hit_rate, "
                "       COUNT(*) AS total "
                "FROM v_cycle_metrics "
                "WHERE is_ok_cycle=1 AND session_id=:sid"
            ), {"sid": session_id_filter})).mappings().one()
    finally:
        await engine.dispose()

    if row["total"] == 0:
        pytest.skip(f"no ok cycles for session {session_id_filter}")

    hit_rate = row["hit_rate"]
    assert hit_rate >= _DRIFT_THRESHOLD, (
        f"5-field anchor hit rate {hit_rate:.3f} < threshold {_DRIFT_THRESHOLD:.3f} "
        f"(baseline {_BASELINE_HIT_RATE:.3f}); persona LIKE pattern may have drifted, "
        f"see spec §6 AC-9 + §9 风险表 row 1"
    )
```

- [ ] **Step 20.3: 把 Step 20.1 实测的 baseline 替换 _BASELINE_HIT_RATE 常量**

如 Step 20.1 output 是 `hit_rate=0.94`，则把 `_BASELINE_HIT_RATE = None` 改成 `_BASELINE_HIT_RATE = 0.94`（向下取 2 位小数最稳）。

**未填则 fail**：测试函数顶部 assertion 会让未跑 Step 20.3 直接 pytest.fail 提示，避免假阈值通过 drift-guard。

- [ ] **Step 20.4: 跑 drift-guard 测试确认 PASS**

```bash
pytest tests/test_5field_anchor_drift_guard.py \
    --sim-db data/tradebot.db \
    --session-id 8f2ca0cb-1c2a-4dc7-8927-198e6c0d98d3 -v
```

Expected: 1 个 PASS（baseline 实测在 threshold 以上）。

- [ ] **Step 20.5: 跑无 --sim-db 验证 skip 行为**

```bash
pytest tests/test_5field_anchor_drift_guard.py -v
```

Expected: skip with reason="sim DB not present"（CI 默认行为）。

- [ ] **Step 20.6: Commit**

```bash
git add tests/test_5field_anchor_drift_guard.py
git commit -m "feat(iter-w2r2-obs-phase1): T20 AC-9 5-field drift-guard + baseline 实测 X.XX"
```

（commit message 中 X.XX 替换为 Step 20.1 实测的 baseline 数字。）

---

## Task 21: AC-12 forensic enum drift-guard

**Files:**
- Test: `tests/test_forensic_enum_completeness.py` (Create)

- [ ] **Step 21.1: 写 AC-12 测试**

Create `tests/test_forensic_enum_completeness.py`:

```python
"""AC-12: forensic enum drift-guard — 防 R2-Next-J 等加新 forensic enum 后 view 漏判。"""
import pytest
from sqlalchemy import text


@pytest.mark.asyncio
async def test_no_unknown_execution_status_enum(db_session):
    """T21.1: agent_cycles.execution_status ∈ {ok, retry_exhausted, usage_limit_exceeded}。

    如有新 enum 出现 → fail 提示需同步 v_cycle_metrics.is_forensic_cycle CASE 列举。
    与 spec §9 风险表 row 2 同源。
    """
    rows = (await db_session.execute(text(
        "SELECT DISTINCT execution_status "
        "FROM agent_cycles "
        "WHERE execution_status NOT IN ('ok','retry_exhausted','usage_limit_exceeded')"
    ))).mappings().all()

    unknown = [r["execution_status"] for r in rows]
    assert not unknown, (
        f"Unknown forensic enum(s) detected: {unknown}. "
        f"Update v_cycle_metrics.is_forensic_cycle CASE in alembic migration "
        f"and add the enum to this test's whitelist."
    )
```

- [ ] **Step 21.2: 跑测试确认 PASS**

```bash
pytest tests/test_forensic_enum_completeness.py -v
```

Expected: PASS（当前 DB 仅含 3 known enum）。

- [ ] **Step 21.3: Commit**

```bash
git add tests/test_forensic_enum_completeness.py
git commit -m "feat(iter-w2r2-obs-phase1): T21 AC-12 forensic enum drift-guard"
```

---

## Task 22: AC-10 view 性能 benchmark

**Files:**
- Create: `scripts/benchmark_view_phase1.py`
- Test: `tests/test_view_performance.py` (Create)

- [ ] **Step 22.1: 写 benchmark script**

Create `scripts/benchmark_view_phase1.py`:

```python
"""Phase 1 view 性能 benchmark.

跑 sim #8 archive DB 上 SELECT * FROM v_cycle_metrics / v_alert_lifecycle /
v_order_lifecycle 各 10 次取中位数时间。

Usage:
    python scripts/benchmark_view_phase1.py --db data/tradebot.db
"""
import argparse
import sqlite3
import statistics
import time
import sys


def bench(conn, query, n=10):
    times = []
    for _ in range(n):
        t0 = time.perf_counter()
        list(conn.execute(query))
        times.append((time.perf_counter() - t0) * 1000)
    return statistics.median(times), statistics.mean(times), max(times)


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--db", default="data/tradebot.db")
    args = p.parse_args()

    conn = sqlite3.connect(args.db)
    queries = {
        "v_cycle_metrics":     "SELECT * FROM v_cycle_metrics",
        "v_alert_lifecycle":   "SELECT * FROM v_alert_lifecycle",
        "v_order_lifecycle":   "SELECT * FROM v_order_lifecycle",
    }

    print(f"DB: {args.db}")
    print(f"{'view':<25} {'median_ms':>12} {'mean_ms':>12} {'max_ms':>12}")
    print("-" * 65)
    for view, q in queries.items():
        med, mean, mx = bench(conn, q)
        print(f"{view:<25} {med:>12.2f} {mean:>12.2f} {mx:>12.2f}")
        if med > 100:
            print(f"  ⚠️  {view} median > 100ms — see spec §8.3 future work")
            sys.exit(1)
    print("✓ All views < 100ms median")


if __name__ == "__main__":
    main()
```

- [ ] **Step 22.2: 写 unit test 调用 benchmark**

Create `tests/test_view_performance.py`:

```python
"""AC-10: view 性能 — sim #8 178 行级 SELECT < 100ms (offline benchmark)."""
import subprocess
import pytest


def test_benchmark_view_phase1(request):
    """T22.1: 跑 benchmark script 确认所有 view 中位 < 100ms。

    CI skip if --sim-db not present. W3 上线前 manual:
        pytest tests/test_view_performance.py --sim-db data/tradebot.db

    Hard fail by design — manual gate before W3 release; CI 通过 `--sim-db`
    缺失自动 skip 不阻拦 CI（与 spec AC-10 "non CI strict gate" 兼容）。
    sim #8 178 行级若实测 > 100ms 是真问题，应阻拦发布走 §8.3 future work
    （加 generated column index）。
    """
    db = request.config.getoption("--sim-db")
    if not db:
        pytest.skip("sim DB not present (use --sim-db <path> to run benchmark)")

    result = subprocess.run(
        ["python", "scripts/benchmark_view_phase1.py", "--db", db],
        capture_output=True, text=True, timeout=60,
    )
    assert result.returncode == 0, (
        f"benchmark failed:\nstdout={result.stdout}\nstderr={result.stderr}"
    )
    assert "✓ All views < 100ms median" in result.stdout
```

- [ ] **Step 22.3: 跑 benchmark 确认数据**

```bash
python scripts/benchmark_view_phase1.py --db data/tradebot.db
```

Expected: 三 view 全部 median < 100ms（sim #8 178 cycles 量级）。

- [ ] **Step 22.4: 跑 unit test 确认 PASS**

```bash
pytest tests/test_view_performance.py --sim-db data/tradebot.db -v
```

Expected: PASS。

- [ ] **Step 22.5: Commit**

```bash
git add scripts/benchmark_view_phase1.py tests/test_view_performance.py
git commit -m "feat(iter-w2r2-obs-phase1): T22 AC-10 view performance benchmark"
```

---

## Task 23: 既有测试 fixture migration（~3 test files）

**Files:**
- Modify: ~3 test files using legacy MagicMock-based usage construction

**Strategy**：grep 出所有使用 `MagicMock` 构造 usage 的测试，统一替换为 `make_usage` factory（T9 已加）。

> **#6 修订**：spec §7.2 原估 "30+ tests" 严重高估（grep 实测 ~3 文件: test_cycle_log.py / test_agent_cycle_injection.py / test_usage_limits.py）。整 iter 估算从 1300 行 → 1030 行（impl ~600 + test ~200 + SQL ~150 + alembic ~80）。

- [ ] **Step 23.1: 列出受影响测试文件（实测）**

```bash
grep -rln "MagicMock.*[Uu]sage\|usage\s*=\s*MagicMock\|mock_usage\|Mock.*spec=.*[Uu]sage" tests/ | grep -v __pycache__ > /tmp/usage_mock_files.txt
cat /tmp/usage_mock_files.txt
wc -l /tmp/usage_mock_files.txt
```

Expected: ~3 files (test_cycle_log.py / test_agent_cycle_injection.py / test_usage_limits.py)。如多 / 少：plan 实施时按实际数走。

- [ ] **Step 23.2: 逐文件迁移到 make_usage factory**

对每个文件：
1. 找出 `mock_usage = MagicMock(...)` 或 `usage = ...` 构造点
2. 替换为 `make_usage(input_tokens=..., output_tokens=..., cache_read_tokens=...)`
3. 测试函数签名加 `make_usage` 参数（fixture 注入）

示例转换：

**Before:**
```python
def test_xxx():
    mock_usage = MagicMock()
    mock_usage.total_tokens = 1000
    mock_usage.details = {"prompt_cache_hit_tokens": 700, "prompt_cache_miss_tokens": 300}
    # ...
```

**After:**
```python
def test_xxx(make_usage):
    mock_usage = make_usage(input_tokens=1000, cache_read_tokens=700)
    # ...
```

迁移过程中如发现某测试需要的字段不在 factory default → 显式 override 即可。

- [ ] **Step 23.3: 跑全部受影响 tests 确认 PASS**

```bash
pytest -v $(cat /tmp/usage_mock_files.txt | tr '\n' ' ')
```

Expected: 全 PASS（包括所有 Phase 1 既有测试）。

- [ ] **Step 23.4: Commit**

```bash
git add tests/
git commit -m "refactor(iter-w2r2-obs-phase1): T23 既有 tests migrate to make_usage factory"
```

---

## Task 24: 全 test suite green + alembic full roundtrip

**Files:**
- Run-only

- [ ] **Step 24.1: 跑全 test suite 确认 green**

```bash
pytest -v 2>&1 | tail -30
```

Expected: 全 PASS。检查测试数量与 spec §7.2 估算（~3 触及 + ~10 新增 test 文件，总 ~200 行新增）相符。

- [ ] **Step 24.2: 跑 alembic Phase 1 单步 roundtrip 验证**

> **H2**: 删除 `downgrade base` — 该命令会触发全 migration chain (R2-7 / R2-4 / Iter3) downgrade，drop 所有历史表数据；不必要且破坏性。AC-1 scope 仅是 Phase 1 single-step roundtrip（与 spec §8.2 一致），T5 已在 tmp_path 验证 fresh-init 完整路径。

```bash
# Backup（disaster recovery）
cp data/tradebot.db data/tradebot.db.bak-pre-t24

# Phase 1 单步对称 roundtrip (head ↔ R2-7)
alembic downgrade -1      # 回到 R2-7 (eeeee565cb36)
alembic upgrade head      # 重 upgrade 到 Phase 1 head
```

每步无 raise；最后 `sqlite3 data/tradebot.db "PRAGMA table_info(agent_cycles)"` 应含 8 新列；`SELECT name FROM sqlite_master WHERE type='view'` 应含 3 view。

- [ ] **Step 24.3: 跑 AC-9 / AC-10 manual gate**

```bash
pytest tests/test_5field_anchor_drift_guard.py tests/test_view_performance.py \
    --sim-db data/tradebot.db \
    --session-id 8f2ca0cb-1c2a-4dc7-8927-198e6c0d98d3 -v
```

Expected: 2 个 manual gate 测试 PASS。

- [ ] **Step 24.4: 撤销 backup（非必须，留作 disaster recovery）**

```bash
ls -lh data/tradebot.db.bak-pre-t24  # 留作回滚备份
```

无 commit 步骤（手动 verify 不进 git）。

---

## Task 25: 手动 spot-check + final commit

**Files:**
- Run-only + 可能的 minor cleanup

- [ ] **Step 25.1: 手动 SELECT 抽样验证三个 view 输出语义**

```bash
# v_cycle_metrics 抽样
sqlite3 data/tradebot.db "
SELECT cycle_id, triggered_by, wall_time_ms, llm_call_ms,
       cache_hit_rate_derived, five_field_complete, is_ok_cycle
FROM v_cycle_metrics
WHERE session_id='8f2ca0cb-1c2a-4dc7-8927-198e6c0d98d3'
ORDER BY created_at DESC LIMIT 5"

# v_alert_lifecycle 全表（W2 sim 可能 0 行因历史 alert_id NULL）
sqlite3 data/tradebot.db "SELECT COUNT(*) FROM v_alert_lifecycle"

# v_order_lifecycle 抽样
sqlite3 data/tradebot.db "
SELECT order_id, order_type, status, lifetime_seconds, trigger_drift_pct, originated_cycle_id
FROM v_order_lifecycle
WHERE session_id='8f2ca0cb-1c2a-4dc7-8927-198e6c0d98d3'
LIMIT 5"
```

每查询合理输出（不 raise，列存在，至少部分数据非 NULL 对 sim #9+ 数据）。

- [ ] **Step 25.2: 跑全 test suite 最终确认**

```bash
pytest -v --tb=short 2>&1 | tail -10
```

Expected: 全 PASS；测试总数 ≥ 旧数 + 30 (新加测试)。

- [ ] **Step 25.3: 检查 Git 状态 + log**

```bash
git status                          # 期望 clean
git log --oneline f29ee42^..HEAD    # 期望约 25 commits（spec + T1-T24 实施）
```

- [ ] **Step 25.4: 验证 spec / plan 一致性**

```bash
# 关键：所有 AC-1..AC-12 是否有对应 task
grep -E "^### Task [0-9]+:" docs/superpowers/plans/2026-05-08-iter-w2r2-obs-phase1.md | wc -l
grep -E "^\| AC-1[0-2]?" docs/superpowers/specs/2026-05-08-iter-w2r2-obs-phase1-design.md | wc -l
```

Expected: 25+ tasks / 12 ACs。

- [ ] **Step 25.5: （可选）push 分支**

⚠️ 仅在用户明确要求时 push（按 git safety protocol）。否则保留本地 commit 等用户决定。

```bash
# 等用户指令再:
# git push -u origin feature/iter-w2r2-obs-phase1
```

无 final commit 步骤（25.1-25.4 是 verify-only）。

---

## Self-Review Notes

**Spec coverage check**:
- AC-1 → T5 (alembic roundtrip) + T24 (final full roundtrip) ✓
- AC-2 → T10/T11 (写入路径) + T12 (三路径 unit test) ✓
- AC-3 → T7 (callers) + T8 (test) ✓
- AC-4 → T1 (dataclass + test) + T2 (trigger_context mirror + test) ✓
- AC-5 → T13 (view) + T14 (test) ✓
- AC-6 → T15 (view) + T16 (test) ✓
- AC-7 → T17 (view) + T18 (test) ✓
- AC-8 → T19 (历史兼容 test) ✓
- AC-9 → T20 (drift-guard + baseline 实测) ✓
- AC-10 → T22 (benchmark) ✓
- AC-11 → T0 (前置验证) ✓
- AC-12 → T21 (forensic enum drift-guard) ✓

**Type / signature consistency**:
- `_record_action(deps, action, order_id, alert_id, side, price, pnl, reasoning)` 在 T7 定义 + T8 测试一致
- `make_usage(input_tokens, output_tokens, cache_read_tokens, cache_write_tokens, details)` T9 定义 + T12/T23 使用一致
- `PriceLevelAlertInfo(symbol, target_price, direction, current_price, reasoning, timestamp, alert_id)` T1 定义 + T1.5/T1.6 fixture + T8 使用一致

**Placeholder scan**: 仅 alembic revision id `<auto_rev_id>` 和 `<rev>` placeholder（T4 实施时由 alembic 自动生成填入），其他无 TBD/TODO/XXX。

**Decomposition note**：alembic migration 单文件多 task 增量（T4 框架 → T13 v_cycle_metrics → T15 v_alert_lifecycle → T17 v_order_lifecycle）通过字符串常量占位避免 merge conflict；每个 view task 独立可回滚（downgrade -1 / upgrade head）。

**⚠️ Sequential execution constraints (subagent-driven-development 必读)**:

1. **T13 / T15 / T17 must be executed sequentially, not parallel** — 三 task 编辑同一 alembic 文件的不同字符串常量 placeholder（`_V_CYCLE_METRICS_SQL` / `_V_ALERT_LIFECYCLE_SQL` / `_V_ORDER_LIFECYCLE_SQL`），并行 dispatch 会 git merge 冲突。

2. **T9 must be dispatched before T7 / T8 / T12 / T14 / T16 / T18 / T19 / T21** — T9 在 conftest.py 添加 5 个 Phase 1 fixture（`db_engine` / `db_session` / `deps_factory` / `deps_with_sim_exchange` / `db_engine_with_real_db`），后续 task 的测试函数签名直接消费这些 fixture；如 T9 未先 dispatch，pytest 会 ERROR `fixture 'X' not found`。

3. **T13 Step 13.3.5 baseline gate 必须 view commit 之前** — 如 < 0.90 需暂停 plan 回 spec，view 不应 commit；commit 后再回 spec 需 revert 多个 commit 链。

4. **Task 编号不严格按 dispatch 顺序** — 推荐 dispatch 顺序：T0 → T9 → T1 → T2 → T3+T4 → T5 → T6 → T7 → T8 → T10 → T11 → T12 → T13 (sequential) → T14 → T15 → T16 → T17 → T18 → T19 → T20 → T21 → T22 → T23 → T24 → T25。

---

## Execution Handoff

Plan complete and saved to `docs/superpowers/plans/2026-05-08-iter-w2r2-obs-phase1.md`. Two execution options:

1. **Subagent-Driven (recommended)** - dispatch fresh subagent per task, review between tasks, fast iteration
2. **Inline Execution** - execute tasks in this session using executing-plans, batch execution with checkpoints

Which approach?

