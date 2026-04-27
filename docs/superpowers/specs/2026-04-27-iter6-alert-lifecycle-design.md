# Iter 6 — Alert 生命周期工具暴露 + Close 路径 Batch 清理

**Date**: 2026-04-27
**Branch**: `feature/iter-t2-1-alert-lifecycle`
**Source todo**: `.working/pre-next-observation-todos.md` §T2-1 PR-A
**预估工作量**: 1.5 ~ 2 天（含 FillEvent 契约扩展 + OKX 对齐 + 全量 fixture 工厂迁移）

---

## 1. 背景与动机

> **术语图例**:
> - **P0-5** = alert 槽位耗尽 / stale alert 不清理（本 PR 主诉求）
> - **P0-6** = scheduler cross-tick 排序（Iter 7 已解 deque 内时序，本 PR 减少 alert 入队总量，两者联合闭环）

### 1.1 W1 观察期暴露的问题

W1 观察期（2026-04-26 18:14 → 2026-04-27 11:30 UTC，13.6h，105 cycles）暴露 **P0-5 alert 槽位耗尽**：

- Agent 自标 #1 operational risk
- 触发占比 67% 是 alert，其中相当部分是 stale（仓位已平但 alert 未撤）；W1 #6 实测 09:08:16 close → 09:24:29 conditional cycle，**16 分 13 秒 FIFO 排队，中间 7 个 alert cycle**（详见 `.working/observation-issues-for-review-2026-04-27.md §P0-6`）
- 每 cycle 烧 ~70k tokens 响应 stale alert
- `_price_level_alerts` 槽位上限 20 多次满槽

DB 实测真实根因（非 reviewer 推测的并发抢先）：close fill 后 stale alerts 仍挂在 `BaseExchange._price_level_alerts` list 中，价格继续波动持续触发 alert，agent 每次 cycle 都要响应"早该撤掉的"alert。详见 `.working/observation-issues-for-review-2026-04-27.md §P0-6` + memory `project_w2_prep_progress §P0-6 真实根因校准`。

### 1.2 现有 API gap

`base.py` 已有：
- `add_price_level_alert(price, direction, ...)` — line 167
- `get_price_level_alerts() -> list[dict]` — line 159
- `remove_price_level_alert(alert_id) -> bool` — line 179
- `_check_price_levels(current_price, ts)` — line 186-200，触发后自动从 list 移除

缺失：
- Agent 层无包装工具调 `remove_price_level_alert` — agent 想主动撤单也撤不了
- Close fill / SL fill / liquidation 路径不调批量清理 — stale alert 无人清

### 1.3 与 Iter 7 的关系

Iter 7 (commit `4318e10`，feature commit `a7bc226`) 已做 scheduler priority queue（heapq + `_PRIORITY_MAP {conditional:0, alert:1, scheduled:2}`），解 deque 内时序。**单 Iter 7 不闭环 P0-6** — 它解的是已入队事件的消费顺序，但 alert 总量不减少。

Iter 6 是源头修复：减少 stale alert 进入 deque 的数量。**Iter 6 + Iter 7 联合才完整解决 P0-6 cross-tick**。

---

## 2. 设计目标

### 2.1 In-scope

- **G1**: 新增 agent tool `cancel_price_level_alert(alert_id, reasoning)` — agent 可主动撤单
- **G2**: 新增 base 层 helper `clear_level_alerts_by_symbol(symbol) -> int` — 按 symbol 批量清理
- **G3**: Close fill 触发自动清理：simulated 与 OKX 行为对齐（SimExchange alignment 契约）
- **G4**: FillEvent 契约扩展 `is_full_close: bool` — producer 显式标注（"完全清仓"语义），下游不靠隐式推断

### 2.2 Out-of-scope（明牌不做）

- ❌ Alert 槽位上限调整（保持 20）— P0-5 根因是不清理而非容量
- ❌ Alert 自动过期（TTL）— 超 W1 数据支撑范围，留观察期
- ❌ Cross-tick scheduler 时序修复 — 已由 Iter 7 priority queue 解决
- ❌ Alert 与 SL/TP 订单生命周期联动（"SL 撤单 → 自动撤对应 alert"）— 当前 alert 是独立观察工具，不与订单 ID 绑定
- ❌ 开仓 fill 时清 alert — 仅 close fill 触发清理（开仓时 alert 仍有意义）
- ❌ AlertManager 架构抽离（见 §6 架构债务说明）— 长远候选议题，非 Iter 6 scope

---

## 3. 架构

### 3.1 核心契约提升

把 "close fill → 触发 alert 清理" 从隐式（消费方推断）提升为 **base 层显式契约**。两层修订：

1. **FillEvent +`is_full_close: bool`**：producer 在 `_parse_fill_event`（OKX）/ FillEvent 构造点（sim）显式标注
2. **base 层新增 `_dispatch_fill_event(fill)` wrapper**：所有 exchange 的 fill 都通过该 wrapper 上抛 callback；wrapper 内置 alert hygiene

**Layer 1 — 类继承结构**:

```
        BaseExchange (abstract)
        ├── state: _price_level_alerts (existing)
        ├── state: _fill_callback (NEW, 上提自子类)
        ├── on_fill(cb) (NEW, 上提非空实现)
        ├── add/get/remove_price_level_alert (existing)
        ├── clear_level_alerts_by_symbol(sym) (NEW)
        ├── _dispatch_fill_event(fill) (NEW, entry)
        │     ├── _clear_stale_alerts_for_full_close(fill) (NEW, SRP-1)
        │     └── _invoke_fill_callback(fill) (NEW, SRP-2)
                     ▲
                     │ inherit
        ┌────────────┴────────────┐
   SimExchange              OKXExchange
   - is_full_close 由         - is_full_close 由
     position state 推断         三源融合推断
```

**Layer 2 — Fill 处理时序（运行时调用）**:

```
[Sim/OKX 内部 close fill 触发]
         │
         ▼
  构造 FillEvent(is_full_close=...)
         │
         ▼
  await self._dispatch_fill_event(fill)  ← 子类调 base inherited 方法
         │
         ▼
  ┌──────────────────────────────────┐
  │  base._dispatch_fill_event       │
  │  1. _clear_stale_alerts_for_     │
  │     full_close(fill)              │
  │     └─ if fill.is_full_close:    │
  │        clear_level_alerts_by_    │
  │        symbol(fill.symbol)       │
  │  2. _invoke_fill_callback(fill)  │
  │     └─ try await _fill_callback  │
  │        except: log不传播          │
  └──────────────────────────────────┘
         │
         ▼
  cli/app.py:510 handle_fill
  → _record_action_from_fill (TradeAction)
  → scheduler.trigger("conditional", fill)
```

注：OKX 端三源融合在当前项目 convention 下等价于 full close（项目无 partial close 工具，所有 close 路径 `amount = pos.contracts`）。partial close 工具落地时 OKX 端需补强（见 §6.3）。

**FillEvent 消费方盘点**: 当前唯一 production 消费方是 `cli/app.py:81 _record_action_from_fill`（写 TradeAction 表），读 7 字段 `order_id / symbol / position_side / trigger_reason / fill_price / pnl / fee`，**不读 `is_full_close`** → 加新字段对此消费方零影响。`exchange.on_fill(handle_fill)` 在 cli/app.py:520 唯一注册。

### 3.2 设计权衡

**为什么把 `_dispatch_fill_event` 放在 base 层？**

承认这是**在已有设计债之上的局部最优**，非严格 SOLID 最佳实践：

| 视角 | 评估 |
|---|---|
| SRP | ✓ method-level split 修复 — `_dispatch_fill_event` 是 entry，`_clear_stale_alerts_for_full_close` + `_invoke_fill_callback` 各自单职责（详见 §4.4） |
| 抽象边界 | ✗ "close fill 必清 alert" 是应用业务规则，不该感知于 exchange 协议层 |
| 行业最佳实践 | ✗ 正解是 EventBus + 独立 `PriceLevelAlertManager`（CCXT/Binance/OKX 都不在协议层管客户端 alert） |
| 项目当前约束 | ✓ `_price_level_alerts` 已在 base 是既成事实（PR R7 时期决定）—— 重新拆抽象 = 比 Iter 6 大一个量级的重构 |
| W2 prep 纪律 | ✓ "不在观察期内做行为改造"，大重构污染下轮观察 baseline |
| 局部维护性 | ✓ base-dispatch 方案（base 包揽）vs 散调方案（4 处散调）—— 加新 exchange 自动获得 hygiene，少漏 |
| 测试性 | ✓ method split 后 dispatch entry / clear / invoke 三个独立单元测试，定位回归更精确 |

**结论**: Iter 6 接受 base-dispatch 方案作为局部最优；架构抽离（`PriceLevelAlertManager` + EventBus）作为长远候选议题（见 §6）。

**前置改动声明**（防 review 误判 scope creep）: §4.4 的 `import logging` / `__init__._fill_callback` 字段 / `on_fill` 上提非空实现 / 子类清理 4 项**不是**独立 DRY 重构，而是 `_dispatch_fill_event` 在 base 层引用 `self._fill_callback` 的强制前置依赖（base 层不能引用子类 attribute），属于本 PR scope 内不可分。

### 3.3 范围澄清：两套独立 alert 体系

项目内有 **两套互不相干的 alert**，本 PR 仅触达后者。明示二分避免混淆：

| 体系 | 类型 | 位置 | 触发条件 | 本 PR 影响 |
|---|---|---|---|---|
| **系统配置 alert** | `PriceAlertService` (object) | `src/services/price_alert.py`，via `base.py:144 set_alert_service` 注入 | 用户在 wizard 配的"价格 N 分钟内变动 ≥ X%"（默认 5%/60min） | ❌ 不动 |
| **Agent 价位 alert** | `_price_level_alerts` (list[dict]) | `base.py:89` 直接持有 | Agent 主动 `add_price_level_alert(price, direction, ...)` 设的具体结构性价位 | ✅ 本 PR 清理对象 |

ticker loop 中两者串行独立调用（`simulated.py:666-682` / `okx.py:303-311`）：
```python
if self._alert_service:
    alert_info = self._alert_service.check(ticker.last, ticker.timestamp)  # ① 系统 alert
level_alerts = self._check_price_levels(ticker.last, ticker.timestamp)    # ② agent alert
```

**语义差异**:
- **系统 alert** 是"市场剧烈波动唤醒"——独立于持仓/方向，无仓位时市场剧烈波动仍可能需要开仓决策。**仓位关闭不应触发清理**。
- **Agent 价位 alert** 通常关联 SL/TP/结构性价位——**仓位平了就 stale**。本 PR 清的就是这种。

`clear_level_alerts_by_symbol(symbol)` 仅 filter `self._price_level_alerts` list，**完全不触达** `self._alert_service`。

**alert 通道 vs fill 通道**: 两套 alert 在 ticker loop 共用 `_alert_callback` 通道上抛 trader（不是两套独立 dispatch 链），仅 state 存储分开。本 PR 新增的 `_dispatch_fill_event` 是 **fill 通道**的等价物（与 alert 通道独立），不影响任一 alert 的 callback 上抛路径。

### 3.4 范围澄清：partial close 处理

**当前项目无 partial close 工具** — 所有 close 路径（`close_position` / `set_stop_loss` / `set_take_profit` / sim conditional / liquidation / OKX algo）传入 `amount = pos.contracts`，fill 后 position 必清零。

为避免**未来加 partial close 工具时 silent corruption**（partial close 后还有仓位但 alert 被全清），FillEvent 字段命名采用更精确的 `is_full_close: bool`（不是 `is_close`），契约语义：

> `is_full_close == True` ⟺ 这个 fill 把该 symbol 的持仓清空到 0

仅 `is_full_close=True` 触发 `clear_level_alerts_by_symbol`。

**Sim 端**精确判定（基于 `_close_position_core` 后查 `self._positions.get(symbol)`）：partial close 工具落地后自动正确，无需改动。

**OKX 端**当前依赖 project convention（"all close = full"），三源融合实质判定的是"close 方向"。注释明示这一权衡 + §6.3 留 partial close 工具落地时的 OKX 补强候选。

### 3.5 拒绝的备选

> 注：以下方案命名故意避开 B1/B2/B3 数字标识 — 这些 ID 在 W2 prep memory `project_w2_prep_progress §brainstorm 决议` 已被占用（DecisionLog 双字段 / market_summary deprecated / cache hit rate）。

- **散调方案**: sim 三个 close 路径 + okx `_watch_orders_loop` 各自调 `clear_level_alerts_by_symbol`。否决：维护性差，加新 exchange / 新 close 路径必须记得调
- **trader-callback 方案**: trader 层 callback 内统一清理。否决：trader 反向触达 exchange 内部状态，违反层次；callback 失败时清理跳过
- **base-dispatch 方案 (采用)**: 见 §3.2 / §4.4

---

## 4. 详细设计

### 4.1 FillEvent 契约扩展

**改动**: `src/integrations/exchange/base.py:203` (FillEvent dataclass 起始行 `@dataclass`)

```python
@dataclass
class FillEvent:
    order_id: str
    symbol: str
    side: str
    position_side: str
    trigger_reason: str        # market / limit / stop / take_profit / liquidation
    fill_price: float
    amount: float
    fee: float
    pnl: float | None          # 已实现盈亏（开仓 None）
    timestamp: int
    is_full_close: bool        # NEW — True iff fill 把该 symbol 持仓清零（仅触发 alert 清理）
```

**字段不带默认值**: 故意不加 `is_full_close: bool = False` 默认，TDD 红期能让所有现有 fixture 立即报错，强制 fixture 工厂迁移（见 §5.4）。

**为什么不叫 `is_close`**: 见 §3.4 — 字段语义是"完全清仓"而非"减仓方向 fill"。命名清晰避免未来 partial close 工具落地时 silent corruption。

**消费方影响盘点**: 唯一现有 production 消费方 `cli/app.py:81 _record_action_from_fill` 读 7 字段（不含 `is_full_close`），加新字段 0 修改。新消费方仅 base 层 `_dispatch_fill_event`（本 PR 引入）。

### 4.2 Sim 端填值规则

5 处 `FillEvent(...)` 构造点全部显式填值（`src/integrations/exchange/simulated.py`）:

| Line | 函数 | 语义 | `is_full_close` 判定 |
|---|---|---|---|
| 335 | `_fill_market_open` | 市价开仓 | `False`（静态，开仓必为 False） |
| 367 | `_fill_market_close` | 市价平仓 | **动态**：`order.symbol not in self._positions` |
| 502 | `_execute_fill` (conditional SL/TP triggered) | algo 平仓触发 | **动态**：同上 |
| 561 | `_execute_limit_fill` | limit 开仓 fill | `False`（静态，项目无 limit close 工具） |
| 576 | `_force_liquidate` | liquidation | **动态**：同上 |

**动态判定模式**（line 367 / 502 / 576 三处统一）：
```python
# _close_position_core line 410-415 已显式区分：
# if amount >= pos.contracts: del self._positions[symbol]
# else: pos.contracts -= amount
# 所以 fill 后查 dict 是否还有该 symbol 即可判断
pnl, fee, _ = self._close_position_core(...)
is_full_close = order.symbol not in self._positions  # ← 同一行模式三处复用
return FillEvent(..., is_full_close=is_full_close)
```

**为什么三处都动态而非两处静态 True**:
- 若把 conditional/liquidation 写死 `True`，依赖项目当前 convention（`_create_conditional_order` 强制 `actual_amount = pos.contracts` / liquidation 必全仓）
- 未来加 partial conditional SL 工具（"50% 仓位的止损"完全合理）→ silent corruption（partial conditional 触发 → `is_full_close=True` → alert 全清 → 仓位仍存在）
- 三处统一动态判定 = §3.4 "partial close 工具落地后自动正确"承诺真正落地，liquidation 路径多花一次 dict 查询的边际成本可忽略

**`_force_liquidate` 边界确认**（line 568-583）: `_close_position_core(symbol, pos.side, pos.contracts, ...)` 调用 contracts 等于 position size，line 410 必删 dict → 动态判定结果恒为 True，与"liquidation 必全仓"语义一致 ✓

### 4.3 OKX 端填值规则

**改动**: `src/integrations/exchange/okx.py:322-386` (`_parse_fill_event` 内；FillEvent return 在 :375-386)

```python
async def _parse_fill_event(self, order_data: dict) -> FillEvent:
    # ... 现有 order_id / position_side / trigger_reason / fill_price / amount / fee / pnl ...

    info = order_data.get("info", {})
    is_full_close = self._infer_is_full_close(info, order_data["side"], trigger_reason)

    return FillEvent(
        # ... 现有字段 ...
        is_full_close=is_full_close,
    )

def _infer_is_full_close(self, info: dict, side: str, trigger_reason: str) -> bool:
    """OKX 平仓判定：三源融合，任一命中即认 close。

    NOTE: 当前项目 convention 下 ALL CLOSE FILLS ARE FULL CLOSE
    (close_position / set_stop_loss / set_take_profit 都传 amount=pos.contracts)。
    所以本判定实质是 "is close direction"，等价于 is_full_close。

    若未来加 partial close 工具（reduce_position(percent) 等），此判定会
    static-false-positive partial close (将 partial close 也判 True，
    导致 alert 被全清而仓位仍存在)。届时需改为基于 fetch_positions /
    in-memory position cache 的精确判定（见 §6.3）。
    """
    # 信号 1: reduceOnly 显式（OKX 强信号，最可靠）
    if info.get("reduceOnly") in (True, "true"):
        return True
    # 信号 2: trigger_reason 派生 close 类型。
    # 注意 "liquidation" 当前不可达 — _TRIGGER_REASON_MAP (okx.py:36-42)
    # 只映射 stop / stop_market / take_profit / take_profit_market / market，
    # 没有 liquidation key。OKX liquidation fill 实际靠信号 1 (reduceOnly) +
    # 信号 3 (posSide+side) 兜底。"liquidation" 留在 list 是防御性占位 —
    # 未来若 _TRIGGER_REASON_MAP 加入该映射，本判定无需改动。
    if trigger_reason in ("stop", "take_profit", "liquidation"):
        return True
    # 信号 3: posSide + side 反向（hedge mode + liquidation 的强信号）
    pos_side = info.get("posSide")
    if pos_side == "long" and side == "sell":
        return True
    if pos_side == "short" and side == "buy":
        return True
    return False
```

**为什么不用 `pnl is not None`**:
- OKX `_parse_fill_event` line 369-371 兜底 `pnl=None`（fetch 失败 logger.warning 但不抛）
- 用 pnl 推断会让"fetch 失败的真实 close"被误认 open，alert 漏清
- 三源融合（reduceOnly | trigger_reason | posSide）覆盖所有 OKX 平仓路径，pnl fetch 失败不影响判定

**net mode + 三源覆盖准确性校准**:

项目强制 `posMode == "net_mode"`（okx.py:183 startup 校验，错配 fail-fast）→ `info.posSide` 永远是 `"net"`（line 339 已处理）→ **信号 3 (posSide+side 反向) 在本项目永远不命中**。

实际命中矩阵：

| close 路径 | order_type | trigger_reason | 信号 1 (reduceOnly) | 信号 2 (trigger_reason) | 信号 3 (posSide) |
|---|---|---|---|---|---|
| `set_stop_loss` algo | "stop" | "stop" | ❓ 待实测 | ✅ 命中 | ❌ net mode |
| `set_take_profit` algo | "take_profit" | "take_profit" | ❓ 待实测 | ✅ 命中 | ❌ net mode |
| `close_position` market | "market" | "market" | ❓ 待实测 | ❌ 不命中 | ❌ net mode |
| Liquidation | (OKX 内部) | (映射不到) | ❓ 待实测 | ❌ "liquidation" 当前不可达 | ❌ net mode |

**关键盲点**: `close_position` 市价路径 + liquidation 路径**完全依赖 OKX 是否在 fill event 里自动回填 `info.reduceOnly`**。Algo 路径（SL/TP 触发）有信号 2 兜底相对安全。

附带证据：tests/fixtures/ 内 OKX raw response 全是 fetch_open_orders 路径（algo），**没有 watch_orders fill event 数据**。当前对 `info.reduceOnly` 在 fill event 里的存在性是未验证假设。

### 4.3.1 OKX market close 信号 1 验证（plan 阶段必跑）

**任务**: 用 OKX demo 账户 (Iter 2b 已 ready，credentials 见 `.env.example` `OKX_DEMO_*`) 实测 `_watch_orders_loop` 推送的 raw fill event，验证 `info.reduceOnly` 字段值。

**四场景**:

1. **Market close**: 开 swap 多仓 → market close → 抓 watch_orders 推送的 raw event JSON
2. **SL algo 触发**: 开多仓 + set_stop_loss → 价格触发 SL → 抓 fill event
3. **TP algo 触发**: 开多仓 + set_take_profit → 价格触发 TP → 抓 fill event
4. **Liquidation**: ⚠️ **demo 不可控触发**（要价格穿越 liq price，demo 调价权限有限）— 标记为已知盲区，**fixture 手工构造模拟**（基于场景 1-3 fill event 结构推测 + OKX API 文档 liquidation event schema）。实盘准备期或观察期意外触发时归档真实 fixture（见 §6.4）

参考脚本：`scripts/iter2b_smoke_test.py`（Iter 2b 已建立的实盘 smoke test 模式）。

**实测时间风险 mitigation**: 场景 2 (SL) / 场景 3 (TP) 触发依赖价格穿越，OKX demo 价格不可控可能延期 30 分钟~数小时。**触发距离设 ±0.1%**（如当前价 100 → SL=99.9 / TP=100.1），接受瞬时滑动触发；不要求 5%+ 触发距离的"真实交易"语义，仅验证 fill event 字段形态。场景 1 (market close) 即时触发，无延期风险。

**硬 timeout + escalation**:
- 场景 1 (market close) 即时，无 timeout
- 场景 2 (SL) **4 小时硬 timeout** — 超时直接走 **补救方案 A**（`close_position` 显式传 `params={"reduceOnly": True}` + abstract API 扩 signature，半天 implementation），不再等
- 场景 3 (TP) 同场景 2 的 4 小时硬 timeout + 补救方案 A escalation
- 整 Task 0 总 wall-time 上限 ≈ 4-5 小时（场景 1-3 可并行：开仓后同时挂 SL+TP，等任一触发）

**为什么 4 小时**: BTC ±0.1% 穿越通常分钟到小时级；4h 覆盖典型震荡 + 留 buffer；超时几乎肯定意味着 demo 价格异常静止，再等无意义。

**判定逻辑**（基于场景 1-3 实测结果）:
- ✅ 三场景 fill event 都有 `info.reduceOnly == True/"true"` → 信号 1 兜底成立，spec §4.3 设计可直接落地，无需补救（liquidation 沿用同假设，但标已知盲区）
- ⚠️ Algo 场景 OK 但 market close 缺失 → 走 **补救方案 A**
- ❌ 全部缺失 → 走 **补救方案 B**

**补救方案 A — 显式传 reduceOnly**:
- `tools_execution.py:close_position` 调 `create_order` 时传 `params={"reduceOnly": True}`
- `BaseExchange.create_order` abstract signature 扩 `params: dict | None = None` kwarg
- **传染面 4 处**：abstract method 签名 / sim override 透传忽略 / okx override 合并到 internal params / 所有 mock create_order 的测试同步
- **工作量**: ~半天，规模与 Task 4-5 相当
- **影响**: 改动 abstract API，是 base 层契约 break；plan 阶段需评估对 Iter 7 后续 / 实盘准备期工作的传染

**补救方案 B — OKX in-memory position cache**:
- 订阅 OKX positions WS channel，维护 `self._cached_positions: dict[symbol, Position]`
- `_parse_fill_event` 时比对 fill amount vs cached size，精确判 `is_full_close`
- **工作量**: 1-1.5 天（含 cache 同步逻辑 + race condition 处理 + 测试）
- **影响**: 仅 OKX 内部，不动 abstract API；但 cache 同步是新引入的复杂度

**不验证不落地原则**: §4.3 OKX 设计的接受度 100% 取决于本节实测结果。若实测显示需补救，plan 阶段把补救方案纳入 task 拆分（影响 §8 Task 5 + 后续 task 工作量）。

**实测产物归档**: 三场景 raw event JSON 存 `tests/fixtures/okx_watch_orders_market_close.json` / `okx_watch_orders_sl_fill.json` / `okx_watch_orders_tp_fill.json`，作为 §5.3 OKX 集成测试的真实 fixture（避免 Iter 2 R4 暴露的 mock fidelity 盲区，见 memory `project_iter2_mock_fidelity_lesson`）。

### 4.4 base 层新增 / 上提

**改动**: `src/integrations/exchange/base.py`

**前置改动 1 — 顶部 import**（base.py 当前无 logging）:
```python
import logging

logger = logging.getLogger(__name__)
```

**前置改动 2 — `__init__` 加 `_fill_callback` 字段**（base.py:88-91 当前只有 3 属性，子类各自维护 `_fill_callback`，spec 直接 `self._fill_callback` 是脆弱隐式契约）:
```python
def __init__(self):
    self._price_level_alerts: list[dict] = []
    self._latest_price: float | None = None
    self._alert_service: Any | None = None
    self._fill_callback: Callable[['FillEvent'], Awaitable[None]] | None = None  # NEW
```

**前置改动 3 — `on_fill` 上提非空实现**（base.py:136-138 当前是空 pass，子类 simulated.py / okx.py 各自 override 设置 `_fill_callback`，DRY 反模式）:
```python
def on_fill(self, callback: Callable[['FillEvent'], Awaitable[None]]) -> None:
    """注册 fill 回调。"""
    self._fill_callback = callback
```

**子类清理**（与上提同 commit 完成）:
- `simulated.py:79` 删 `self._fill_callback: ... = None` 字段
- `simulated.py:776-777` 删 `on_fill` override
- `okx.py:121` 删 `self._fill_callback: ... = None` 字段
- `okx.py:141-142` 删 `on_fill` override

**核心新增**:
```python
def clear_level_alerts_by_symbol(self, symbol: str) -> int:
    """Remove all price level alerts matching symbol. Returns count cleared.

    Used by _dispatch_fill_event on close fills. Also exposed as a
    standalone method for tests / future use.
    """
    before = len(self._price_level_alerts)
    self._price_level_alerts = [
        a for a in self._price_level_alerts if a["symbol"] != symbol
    ]
    return before - len(self._price_level_alerts)

async def _dispatch_fill_event(self, fill: FillEvent) -> None:
    """Entry point for fill event dispatch.

    Subclasses MUST route all FillEvent through this method, not call
    self._fill_callback directly. Internal split into two SRP units:
    alert hygiene (clear) and callback fan-out (invoke).

    Order semantics: clear-before-callback. The callback observes the
    final post-hygiene state (alert list already filtered). If a future
    callback needs to capture stale-alert context for diagnostic logging,
    either reorder the dispatch or add a pre-clear hook.
    """
    self._clear_stale_alerts_for_full_close(fill)
    await self._invoke_fill_callback(fill)

def _clear_stale_alerts_for_full_close(self, fill: FillEvent) -> None:
    """SRP unit 1: alert hygiene. Clear all level alerts for fill.symbol
    if and only if the fill closes the position fully (is_full_close).
    """
    if not fill.is_full_close:
        return
    cleared = self.clear_level_alerts_by_symbol(fill.symbol)
    if cleared > 0:
        logger.info(
            "Cleared %d stale price-level alert(s) on full close fill: "
            "symbol=%s order_id=%s",
            cleared, fill.symbol, fill.order_id,
        )

async def _invoke_fill_callback(self, fill: FillEvent) -> None:
    """SRP unit 2: callback fan-out with failure isolation.

    Callback exceptions are logged, not propagated, so one fill's
    callback failure does not block subsequent fill processing.
    """
    if self._fill_callback is None:
        return
    try:
        await self._fill_callback(fill)
    except Exception:
        logger.exception("Fill callback failed for order %s", fill.order_id)
```

**SRP method split 论证**: 早期版本 spec 把 alert hygiene + callback fan-out 合在 `_dispatch_fill_event` 里，违反 SRP（accepted as 局部最优 trade-off）。改为内部 split 后：
- `_dispatch_fill_event`：纯 entry / orchestration（一行 sync + 一行 async）
- `_clear_stale_alerts_for_full_close`：alert 业务（sync，可单独 patch / mock）
- `_invoke_fill_callback`：callback 派发（async，可单独 patch / mock）

成本：base 多两个 method（每个 ≤10 行）；收益：SRP 恢复 + 测试隔离 + 未来扩展点（pre-clear hook / post-callback hook）位置明确。

**try/except 上提**: OKX `_watch_orders_loop:264-265` 原本独立做 try/except，sim `_fill_callback:674-675` 没有 try/except —— 上提到 base 后两边行为一致（callback 失败不传播）。

**try/except 当前价值澄清**: 当前唯一注册 callback 是 `cli/app.py:510-516` handler，它**已自带** try/except + finally(scheduler.trigger)（详见 cli/app.py:511-516）。base 层 try/except 对此 handler 是冗余防护。**真正价值是契约保证**：base 层兜底失败隔离，未来其他 callback 注册者（如新 metric / 监控 / 测试 mock）即使忘写 try/except 也不会污染 dispatch 链。

### 4.5 Call site 替换

**Sim** — `src/integrations/exchange/simulated.py:673-675`:
```python
# before
for fill in triggered:
    if self._fill_callback:
        await self._fill_callback(fill)

# after
for fill in triggered:
    await self._dispatch_fill_event(fill)
```

**OKX** — `src/integrations/exchange/okx.py:260-265`:
```python
# before
fill_event = await self._parse_fill_event(order_data)
if self._fill_callback:
    try:
        await self._fill_callback(fill_event)
    except Exception:
        logger.exception("Fill callback failed for order %s", order_data.get("id"))

# after
fill_event = await self._parse_fill_event(order_data)
await self._dispatch_fill_event(fill_event)
```

### 4.6 Agent tool 新增

延续 `add_*` / `cancel_*` 命名 idiom（项目已有 `add_price_level_alert` 与 `cancel_order`，但二者非严格 add/cancel 配对；本 PR 引入 `cancel_price_level_alert` 是首个明确的 add/cancel 配对）。

**Step 1 — `src/agent/tools_execution.py` 新增 `_impl`**:

```python
async def cancel_price_level_alert(
    deps: TradingDeps,
    alert_id: str,
    reasoning: str,
) -> str:
    """Remove a price level alert by ID."""
    ok = deps.exchange.remove_price_level_alert(alert_id)
    if ok:
        await _record_action(
            deps, action="cancel_price_level_alert",
            reasoning=f"id={alert_id} | {reasoning}",
        )
        return f"Price level alert cancelled (id={alert_id})"
    return f"Alert {alert_id} not found (already triggered or never existed)"
```

**失败路径不 record 设计**: 与 `add_price_level_alert` (tools_execution.py:237-244) 同模式 — 业务拒绝（alert 不存在 / 已触发）不污染 TradeAction 表，避免 SQL 查询"哪些 alert 被 cancel"误统计 not-found 噪音。

**`_record_action` signature 兼容性**: `tools_execution.py:16-26` `_record_action(deps, action, order_id=None, side=None, price=None, pnl=None, reasoning=None)` — 所有非 action 参数均 keyword-only `default None`。cancel 仅传 `action` + `reasoning` 完全合法，无需扩 signature。

注：
- 与现有 `add_price_level_alert` line 227 同风格（`(deps, ...)` 签名 + 短 docstring）
- `remove_price_level_alert` 已是 base.py:179 sync 方法，不 await
- **不传 `order_id=alert_id`**：alert_id 是 8-char uuid，不是 exchange order ID。`TradeAction.order_id` 字段语义专属订单 ID，写 alert_id 进去会污染该字段（"哪些订单被 cancel" 的 SQL 查询会误统计）。改用 reasoning 嵌入，与 `add_price_level_alert` line 241-244 完全对齐

**Step 2 — `src/agent/trader.py` 包装注册**（紧邻 `add_price_level_alert` line 519-539 之后）:

```python
@tool
async def cancel_price_level_alert(
    ctx: RunContext[TradingDeps],
    alert_id: str,
    reasoning: str,
) -> str:
    """Cancel a previously-set price level alert by its ID.

    Use this when an alert is no longer relevant — for example, if the
    structural level it watched has been invalidated by a regime change
    or if the position context that motivated it has shifted in a way
    that the auto-clearing on close fill does not cover.

    Note: alerts at SL/TP levels are auto-cleared when a position closes;
    you usually do not need to call this for that case.

    Args:
        alert_id: the alert ID returned by add_price_level_alert.
        reasoning: brief description of why this alert is being cancelled.
    """
    from src.agent.tools_execution import cancel_price_level_alert as _impl

    return await _impl(ctx.deps, alert_id, reasoning=reasoning)
```

**docstring 约束**: trader.py 局部 `tool` 已包好 `docstring_format="google" + require_parameter_descriptions=True`（line 75-79）—— 缺 Args 描述会 startup fail。

**Step 3 — `src/cli/display.py` 注册 success prefix**:

```python
# display.py:251-262 _EXECUTION_SUCCESS_PREFIXES 内插入
_EXECUTION_SUCCESS_PREFIXES = {
    # ... 现有 10 项 ...
    "cancel_price_level_alert": "Price level alert cancelled",  # NEW
}
```

**为什么必加**: `is_tool_error` (display.py:273) 通过此白名单区分 success vs business rejection。机制是"工具名注册一组 success prefix，返回字符串以其中之一开头则判 success；否则 business rejection"。
- ✅ Success 返回 `"Price level alert cancelled (id={alert_id})"` 以 `"Price level alert cancelled"` prefix 开头 → `is_tool_error` 返回 False → UI 显示 success ✓
- ⚠️ Not-found 返回 `"Alert {alert_id} not found ..."` **不以 success prefix 开头** → `is_tool_error` 自动落 True 路径 → UI 正确显示 business rejection ✓

**不需要单独"注册" not-found 前缀**：display.py 机制是 "未匹配 success prefix 即业务拒绝"，与现有 `close_position` "No positions to close." 同模式（display.py:163 注释明确"业务拒绝由 is_tool_error 兜底"）。

**可选**: 不加 `_EXECUTION_PARSERS` 入口，cancel 操作 args 已自带 alert_id + reasoning，UI 默认渲染足够。

### 4.7 REGISTERED_TOOL_NAMES 更新

**改动**: `src/agent/trader.py` line 614-650 `REGISTERED_TOOL_NAMES`

- 总数：31 → 32
- 中文计数注释 line 637：`# --- 执行 (10) ---` → `# --- 执行 (11) ---`
- 在 line 645 `add_price_level_alert` 之后插入 `cancel_price_level_alert`（保持 add/cancel 配对相邻）

drift guard 测试已存在 (Iter 1 加，`tests/test_trader_agent.py::test_registered_tool_names_matches_agent_tools`)，加新工具忘更新 → CI 失败而非 silent drop。

**drift guard 硬编码同步**（必修，否则 CI 红）: `tests/test_trader_agent.py:85-86` 当前硬编码：
```python
assert len(REGISTERED_TOOL_NAMES) == 31, (
    f"Expected 31 tools (20+10+1), got {len(REGISTERED_TOOL_NAMES)}"
```
本 PR 同步改为：
```python
assert len(REGISTERED_TOOL_NAMES) == 32, (
    f"Expected 32 tools (20+11+1), got {len(REGISTERED_TOOL_NAMES)}"
```
错误消息中 `(20+10+1)` → `(20+11+1)` 同步。

---

## 5. 测试策略

### 5.1 新增测试文件

`tests/test_alert_lifecycle.py`

### 5.2 单元测试覆盖

| Test | 覆盖 |
|---|---|
| `test_cancel_price_level_alert_tool_success` | 工具调用 → `remove_price_level_alert` → 返回 cancelled |
| `test_cancel_price_level_alert_tool_not_found` | 不存在的 alert_id → 返回 not found 文案 |
| `test_clear_level_alerts_by_symbol_filters_correct_symbol` | 多 symbol 混合 → 仅清目标 symbol，返回正确 count |
| `test_clear_level_alerts_by_symbol_returns_zero_when_empty` | symbol 无 alert → 返回 0 |
| `test_dispatch_fill_event_clears_on_full_close` | sim：`is_full_close=True` → alert 清空 + callback 调用 |
| `test_dispatch_fill_event_skips_clear_when_not_full_close` | sim：`is_full_close=False` → alert 保留 + callback 调用 |
| `test_dispatch_fill_event_callback_failure_isolated` | callback 抛 → logger.exception 但 dispatch 不传播 |
| `test_dispatch_fill_event_no_callback_registered` | callback 未注册 → 仅清 alert，不抛 |
| `test_sim_partial_close_does_not_clear_alert` | 直接构造 `_close_position_core(amount < pos.contracts)` → fill 后 position 仍存在 → `is_full_close=False` → alert 保留（**未来 partial close 工具落地的契约保护**） |
| `test_is_tool_error_cancel_alert_success_returns_false` | display.py `is_tool_error("cancel_price_level_alert", "Price level alert cancelled (id=...)", "success")` → False（命中新注册 prefix） |
| `test_is_tool_error_cancel_alert_not_found_returns_true` | display.py `is_tool_error("cancel_price_level_alert", "Alert ... not found ...", "success")` → True（不命中 prefix，正确标 business rejection） |

### 5.3 集成测试覆盖（端到端）

| Test | 覆盖 |
|---|---|
| `test_sim_market_close_triggers_alert_clear` | 开仓 + add alert + market close → alert 清空 |
| `test_sim_conditional_fill_triggers_alert_clear` | SL 触发（conditional fill）→ alert 清空 |
| `test_sim_liquidation_triggers_alert_clear` | Liquidation → alert 清空 |
| `test_sim_open_fill_does_not_clear_alert` | 开仓 fill → alert 保留 |
| `test_okx_parse_fill_event_is_full_close_reduce_only` | OKX `info.reduceOnly=True` → `is_full_close=True` |
| `test_okx_parse_fill_event_is_full_close_trigger_reason_stop` | OKX trigger_reason="stop" → `is_full_close=True` |
| `test_okx_parse_fill_event_is_full_close_trigger_reason_tp` | OKX trigger_reason="take_profit" → `is_full_close=True` |
| `test_okx_parse_fill_event_is_full_close_trigger_reason_liq` | OKX trigger_reason="liquidation" → `is_full_close=True` |
| `test_okx_parse_fill_event_is_full_close_pos_side_long_sell` | OKX posSide="long"+side="sell" → `is_full_close=True`（**`@pytest.mark.skip(reason="hedge mode path, unreachable in net mode (okx.py:183)")` 标记**：当前项目强制 net mode → posSide="net"，本分支永不命中。保留信号 3 代码 + skip 测试，是防御未来 hedge mode 启用的设计；启用时去 skip 即可） |
| `test_okx_parse_fill_event_is_full_close_pos_side_short_buy` | OKX posSide="short"+side="buy" → `is_full_close=True`（**同上 skip 标记**） |
| `test_okx_parse_fill_event_is_full_close_net_mode_with_reduce_only` | OKX posSide="net"+reduceOnly=True → `is_full_close=True`（net mode 边界） |
| `test_okx_parse_fill_event_open_no_close_signals` | OKX 无任何 close 信号 → `is_full_close=False` |
| `test_okx_dispatch_fill_event_clears_via_loop` | OKX `_watch_orders_loop` mock close fill 推送 → alert 清空 |
| `test_registered_tool_names_includes_cancel_alert` | drift guard：注册名单含 cancel_price_level_alert (32 工具，执行 11) |

### 5.4 Fixture 迁移

**根本原因**: `FillEvent` 加 `is_full_close` 字段（无默认值），所有现存构造 `FillEvent(...)` 的测试需更新。

**实际规模盘点**（grep verified）:
- 现存测试 `FillEvent(...)` 构造点共 **2 处**：`tests/test_exchange.py:204` + `:212`
- 新增 26 个 Iter 6 测试也用工厂

**迁移方案**:
- 新建 `tests/_fixtures.py`（**当前不存在**），暴露 `make_fill_event(*, is_full_close=False, **overrides) -> FillEvent` 工厂
- TDD 红期：先 `FillEvent` 加字段（无默认）→ 跑测试 → 2 处 fixture 红
- 转绿：2 处替换为 `make_fill_event(...)` 调用 + 新增 26 测试用同工厂

（"工厂层默认 vs dataclass 层默认"的 silent corruption 防护理由见 §4.1，此处不复述）

### 5.5 测试规模预估

- 现有 baseline: 857 passed + 1 skipped
- baseline: **857 passed + 1 skipped**
- 新增: **26 tests**（单元 11 + 集成 15）
  - 单元 11（全 pass）：cancel tool ×2 / clear helper ×2 / dispatch ×4 / sim partial close 契约保护 ×1 / is_tool_error display.py ×2
  - 集成 15：sim 端到端 ×4 / OKX `_parse_fill_event` 三源融合 ×7 (含 reduceOnly bool ×1 + reduceOnly string 变体 ×1 + trigger_reason ×3 + posSide hedge ×2 skip) + net mode 边界 ×1 / OKX `_watch_orders_loop` mock ×1 / drift guard ×1
  - **2 hedge mode 测试 skipped**（net mode 项目下不可达，保留代码 + skip 标记防御未来）
- 预期 final: **881 passed + 3 skipped**（857+24 passed / 1+2 skipped）

---

## 6. 长远候选议题（Iter 6 之后）

### 6.1 AlertManager 架构抽离

**触发条件**（任一命中考虑启动）:
- 观察期暴露 alert 业务规则与 exchange 协议层进一步耦合
- Alert 与 SL/TP 订单生命周期联动需求出现
- 多 exchange 实例之间 alert 不一致（如：跨 sim/okx 切换 alert 状态丢失）
- 实盘准备期 / 多 exchange 并行接入期

**重构 scope**:
- 抽 `PriceLevelAlertManager` 服务（独立模块）
- 引入 EventBus（exchange emit `FillEvent` / `TickerEvent`）
- AlertManager 自行订阅 fill 与 ticker 事件，自管 state
- exchange 协议层完全不感知 alert 存在
- 测试从 `BaseExchange.test_*` 迁出到 `AlertManager.test_*`

**为什么 Iter 6 不做**:
- 比 Iter 6 大一个量级的重构
- W2 prep 纪律：不在观察期内做行为改造，重构污染 baseline
- 当前局部最优（B3）能满足 P0-5 闭环需求

### 6.2 Alert 自动 TTL

观察期数据若显示 stale alert 仍是问题（如：开仓后未 close 但市场结构变化使 alert 失效），考虑加 TTL 自动过期。

### 6.3 OKX `is_full_close` 精确判定（partial close 工具落地后）

**触发条件**: 项目加入 partial close 工具（如 `reduce_position(percent)` / `partial_close(amount)`）。

**问题**: 当前 OKX `_infer_is_full_close` 三源融合判定的实质是"close 方向"，依赖 project convention "all close = full" 才等价于 `is_full_close`。partial close 工具落地后，partial fill 会被误判 `is_full_close=True` → alert 全清 → 仓位仍存在但无 alert（silent corruption）。

**解决方向**:
- **选项 A**: `_parse_fill_event` 内 await `fetch_positions(symbol)` 看 contracts == 0 → 精确但额外 IO + race condition
- **选项 B**: 维护 in-memory position cache（订阅 OKX position channel WS push），fill 时比对 fill amount vs cached size
- **选项 C**: 同 sim 的 routing 层判定 — 但 OKX 没有 sim 那种"fill 之前先知道完整 position state"的 routing pattern

**为什么 Iter 6 不做**: 当前项目无 partial close 工具，提前实施是 YAGNI；W2 prep 不在观察期内做行为改造。Sim 端契约保护测试 (`test_sim_partial_close_does_not_clear_alert`) 已留 contract，partial close 工具落地时若忘补 OKX 端，集成测试会暴露 sim/okx 行为差异。

**roadmap 状态**: partial close 工具**不在已知 roadmap**。§3.4 / §6.3 是 hypothetical 防御性设计，预防 silent corruption。若 W3+ 仍无 partial close 需求，本节可整段移除（保留 §3.4 命名理由 + §5.2 契约保护测试即可）。

### 6.4 OKX liquidation fill event fixture 真实化

**触发条件**: 实盘准备期 / 观察期意外触发 OKX liquidation。

**问题**: §4.3.1 场景 4 demo 不可控，liquidation fill event 用手工构造 fixture（基于场景 1-3 + OKX API 文档推测）。真实 liquidation event 的 `info.reduceOnly` / `info.ordType` / 其他字段实际形态未验证。

**解决方向**: 实盘或观察期意外触发 liquidation 时，归档真实 fill event raw JSON 到 `tests/fixtures/okx_watch_orders_liquidation.json`，替换手工 fixture，跑回归测试验证 §4.3 信号 1 兜底假设。

**为什么 Iter 6 不强制**: demo 价格不可控（要触发 liq 需价格穿越，demo 调价权限有限）；强求实测会无限期阻塞 PR。手工 fixture + 已知盲区标注是务实折衷。

---

## 7. Acceptance Criteria

修后必须通过：

1. ✅ 集成测试：set alert → cancel via agent tool → 同价格波动不再触发
2. ✅ Sim 三种 full close 路径（market full close / conditional SL/TP / liquidation）→ 该 symbol 所有 stale alerts 被清空
3. ✅ OKX `_watch_orders_loop` mock close fill 推送（覆盖 reduceOnly / trigger_reason / posSide 三信号源）→ 同上
4. ✅ Sim 开仓 fill → alert **不** 清空（开仓 fill 时该 symbol 已存在的 alert 仍保留 — 仓位刚建立，无 stale 之说，alert 仍指向有意义的结构性价位）
5. ✅ Sim 直接构造 partial close（`_close_position_core` amount < pos.contracts）→ alert **不** 清空（契约保护，未来 partial close 工具落地的安全网）
6. ✅ Callback 失败 → logger.exception 但 dispatch 不传播，下一 fill 仍正常处理
7. ✅ `REGISTERED_TOOL_NAMES` drift guard 31 → 32 + 计数注释同步
8. ✅ 全套 857 baseline + 新增 26 tests (24 pass + 2 hedge skip) = **881 passed + 3 skipped**，零 regression
9. ✅ Iter 5 framework 合规保持：`cancel_price_level_alert` Args 描述齐全，startup 不 fail
10. ✅ §4.3.1 OKX market close 信号 1 实测通过 — 场景 1-3（market close / SL / TP）fill event raw JSON 已归档到 `tests/fixtures/`；`info.reduceOnly` 全部命中，或补救方案 A/B 已 implemented + 测试覆盖。场景 4 (liquidation) 用手工 fixture（demo 不可控，已知盲区，§6.4 长远候选真实化）
11. ✅ display.py prefix 注册 + `is_tool_error` 双测试覆盖（详见 §4.6 Step 3）

**不验证项（明牌跳过）**:
- ⚠️ "close fill 后第一个 cycle 是 conditional" — cross-tick 排序由 Iter 7 priority queue 解，本 PR 不验证
- 替代验证：close fill 后 30s 内 conditional cycle 已运行（即使不是第一个）

---

## 8. 实施顺序（Plan 阶段细化）

预期 task 拆分（writing-plans 阶段确认）:

1. **Task 0 (前置实测) — 🛑 HARD GATE**: §4.3.1 OKX market close 信号 1 实测 — 跑 demo **场景 1-3**（market close / SL / TP），归档真实 fixture 到 `tests/fixtures/okx_watch_orders_{market_close,sl_fill,tp_fill}.json`；**场景 4 (liquidation)** demo 不可控触发，手工构造 fixture `okx_watch_orders_liquidation.json`（基于场景 1-3 结构 + OKX API 文档推测，标记已知盲区，§6.4 长远候选真实化）。
    - **硬阻塞门**: 本 task 不完成（含场景 1-3 实测 OR 走补救方案 A/B 决策）**不进 Task 1+**，避免 implementation 假设破灭后大段返工
    - **超时 escalation**: 场景 2/3 各 4h 硬 timeout，超时直接走补救方案 A（§4.3.1）
    - **总 wall-time 上限**: ≈4-5h（场景 1-3 可并行）
    - **结果决定**: 场景 1-3 实测结果决定 §4.3 设计是否需补救方案 A/B（影响 Task 5 + 后续 task 拆分）
2. **Task 1 (base infra)**: base.py 加 `import logging` + logger 定义 + `__init__._fill_callback` 字段 + `on_fill` 上提非空实现 + 子类 (sim/okx) 删自有字段/override
3. **Task 2 (TDD red)**: FillEvent 加 `is_full_close` 字段（无默认）+ 新建 `tests/_fixtures.py` 暴露 `make_fill_event` 工厂 + 跑测试看红
4. **Task 3 (TDD green)**: 现存 2 处 (`tests/test_exchange.py:204/212`) 迁工厂 → 转绿
5. **Task 4**: Sim 5 处 FillEvent 构造点显式填 `is_full_close`（line 367/502/576 三处动态判定模式）+ 测试（含 partial close 契约保护）
6. **Task 5**: OKX `_infer_is_full_close` helper + `_parse_fill_event` 接入 + 7 个 OKX 单元测试 + 1 个 net mode 边界 + (条件性) Task 0 补救方案实施
7. **Task 6**: base 层 `clear_level_alerts_by_symbol` + `_dispatch_fill_event` + 单元测试
8. **Task 7**: Sim/OKX call site 替换 + 集成测试（4 sim 端到端 + 1 OKX `_watch_orders_loop` mock，用 Task 0 归档的真实 fixture）
9. **Task 8**: `cancel_price_level_alert` agent tool（tools_execution.py + trader.py）+ display.py `_EXECUTION_SUCCESS_PREFIXES` 注册 + REGISTERED_TOOL_NAMES 31→32 + **drift guard 测试 `test_trader_agent.py:85-86` 硬编码同步（31→32, "(20+10+1)"→"(20+11+1)"）** + `is_tool_error` 单元测试（success / not-found 双覆盖）
10. **Task 9**: Iter 5 framework 合规校验（Args 描述 + startup smoke）+ final review

---

## 9. 回滚策略

整 PR revert 即可。无 schema 改动 / 无 Alembic migration / 无配置项 / 无外部 API 调用。
