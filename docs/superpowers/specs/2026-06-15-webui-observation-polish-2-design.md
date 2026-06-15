# WebUI 观察台 mini-polish #2 设计

## 背景

PR #81 落地观察台一轮打磨后，用户在 sim #21（会话 `d10b0442`）实际观察中暴露三处可读性缺口。本迭代为一组相互独立的小打磨：两项各加 1 个后端只读聚合字段 + 前端 1 处展示，一项纯前端布局。

**无表结构变更、无迁移**——只动读路径 query（`queries.py`）与前端视图组件。

## 范围

| 项 | 内容 | 触及 |
|---|---|---|
| ① | 会话状态栏显示会话累计 token 消耗 | 后端聚合 + 前端 1 chip |
| ② | cycle header 显示距上一 cycle 的空闲间隔 | 后端窗口字段 + header 1 段 |
| ③ | 告警区多条由平铺改竖排逐条 | 纯前端布局 |

### 不做（已评估否决）

- **「告警」改名「价格告警」**：`CycleDetailPanel.vue:101-113` 的告警区已是「价格 + 波动」伞形容器（PR #81 B3 落地 `volatility_alert`），内部已用「价格」「波动」子标签区分。当前 sim 只见价格是因 sim #21 早于 B3 capture（`volatility_alert` 键缺失——snapshot 无此键，0/1702 cycle），换新 sim 后波动会出现。改外层名会错标这个同时装波动告警的容器。
- **告警计数硬截断（max-20 尾部）**：实测全库告警数量多数 ≤2（约 77%）、长尾到 8（峰值，id=328）、从未接近系统上限 20。③ 的竖排布局已让「告警变多」自然退化为可滚动列表，无需单独加截断（YAGNI）。

## ① 会话累计 token

### 数据流

`tokens_consumed` 为每 cycle 字段（`AgentCycle.tokens_consumed`）。会话累计 = 该会话所有 cycle 之和。

- **后端**：`get_live_status`（`queries.py:276`）内加一次 `SELECT coalesce(sum(tokens_consumed), 0) FROM agent_cycles WHERE session_id=:sid`；`LiveStatus` schema（`schemas.py:164`）加字段 `tokens_consumed_total: int`（非 Optional）。**必须 `func.coalesce(func.sum(...), 0)`**——SQLite `SUM` 对空集返回 `NULL`，全新会话（0 cycle、status active）若直接 `sum` 会让 `int` 字段触发 pydantic 校验失败。
- **前端**：`LiveStatusCard.vue` 末尾加一个 chip `累计 {{ fmtTokensCompact(live.tokens_consumed_total) }} tok`（该文件当前未 import format，需补 `import { fmtTokensCompact } from "@/utils/format"`）。

### 量纲约定

只显示**累计绝对值**，不做 `/预算 %`。`token_budget` 是**每天**预算，累计跨多天，二者对齐即错值。

### token 格式

新增 `fmtTokensCompact(n)`：以千为单位、千分位、`K` 后缀，无小数。

- `75795 → "76K"`
- `6778612 → "6,779K"`
- `31500513 → "31,501K"`（全库最坏情形）
- `null → "—"`

（实测全库 21 个会话累计 token 区间 **~73K–31.5M**；统一用 K，4–5 位数可接受——最坏 `31,501K` 为 5 位，正落在边界上、结论成立。单轮 token 的 `CycleRowHeader` 展示保持 `fmtTokens` 千分位不变，不在本项范围。）

## ② cycle 间隔

### 语义

**空闲间隔** = 本轮 start − 上轮 end，即 `set_next_wake` 真正控制的休眠时长。

```
gap_since_prev_ms = (created_at − wall_time_ms) − prev.created_at
```

其中 `created_at` 为 cycle 结束时刻、`prev.created_at` 为上一 cycle 结束时刻。

### 数据流

- **后端**：`get_cycles` 子查询（`queries.py:28-31`）在 `row_number` 旁加 `func.lag(AgentCycle.created_at, type_=DateTime(timezone=True)).over(order_by=AgentCycle.id.asc()).label("prev_created_at")`。窗口须与 `seq` 一样开在【游标过滤之前】的全量 session 子查询里，否则翻页会从游标处重启、上一行错位。
  - **`type_=DateTime(timezone=True)` 不可省**：`func.lag(...)` 默认 `NullType`，SQLAlchemy 不套 DateTime result processor，而 SQLite 把 datetime 存为 text → 窗口列回读为**裸字符串**；与 ORM 实体 `c.created_at`（真 datetime）相减会 `TypeError`，每个非首行都触发 → feed 接口 500（已本地复现）。`seq` 用 `row_number` 返回 int 无需 processor，故先例未暴露此坑。给 `type_` 后回读为 datetime（与 `c.created_at` 同为 naive，相减安全）。需 `from sqlalchemy import DateTime`。
  - **外层须投影该列**：当前外层 `select(ac, inner.c.seq)`（`queries.py:34`）、装配循环 `for c, seq in result`（`queries.py:62-68`）。须改为 `select(ac, inner.c.seq, inner.c.prev_created_at)` 并解包 `for c, seq, prev_created_at in result`，否则 lag 列取不到（SQLAlchemy 子查询常见 footgun）。
  - **`CycleRow` schema 加字段 `gap_since_prev_ms: int | None`，装配时算**（量纲：datetime 差转 ms 再减 wall）：
    ```python
    # 先守卫、后计算（lazy）：首轮 prev_created_at is None，若先算 datetime - None 会 TypeError
    if prev_created_at is None or c.wall_time_ms is None:
        gap_since_prev_ms = None
    else:
        gap_ms = (c.created_at - prev_created_at).total_seconds() * 1000 - c.wall_time_ms
        gap_since_prev_ms = max(0, round(gap_ms))
    ```
- **边界**：
  - 首轮（无上轮）→ `null`
  - `wall_time_ms` 为 null（forensic 行，推不出 start）→ `null`
  - 算出负值（时钟抖动 / 重叠）→ 归 `0`（经 `fmtGap` 显示 `<1m`，即「无可观测空闲」，可接受、不特判 0）
- **前端**：`CycleRowHeader.vue` 时间段后加一节 `· 间隔 {{ fmtGap(cycle.gap_since_prev_ms) }}`；`gap_since_prev_ms == null` 时整节不渲染。

### 间隔格式

新增 `fmtGap(ms)`：

- `null → "—"`（前端用 `v-if` 不渲染，formatter 仍给占位）
- `< 60000 → "<1m"`
- `< 3600000 → "{m}m"`（如 `600000 → "10m"`）
- `≥ 3600000 → "{h}h{m}m"`（如 `3720000 → "1h2m"`；整点 `3600000 → "1h"`）

## ③ 告警竖排

### 现状

`CycleDetailPanel.vue:101-113` 告警单元用内联平铺（`.snap-item { display: inline-block }`），多条告警 + 长 `reasoning` 被挤成连续文字流。实测 sim #21 第 51 轮（id=1807）有 3 条价格告警，关键 `direction @price` 淹没在叙述里、无法快速扫读。

### 修法

告警单元改为**逐条竖排块**：

```
告警  价格
      ↓ @64,400  · Early warning: price approaching SL at 64,320…
      ↓ @64,636  · Failed reclaim warning: if price falls back…
      ↓ @64,780  · Updated to just below new SL (64,800)…
      波动
      ±2.0% / 30min
```

**杠杆在 flex 容器方向，不在 `.snap-item`。** `.alert-grp` 现为 `display: inline-flex`（`CycleDetailPanel.vue:177`），其子元素（label + 各 `.snap-item`）是 flex item 沿主轴**横排**；把 `.snap-item` 由 `inline-block` 改 `block` 不会竖排——flex item 的 `display` 会被 blockify 但仍沿 row 主轴排列。且 `.snap-item` 与「挂单」（`line 99`）共用，全局改有副作用。因此：

- **组内竖排**：`.alert-grp` → `flex-direction: column; align-items: flex-start`（label 在上、各告警条逐条在下）。
- **组间竖排**：价格组与波动组当前是值单元格 `<span>`（`line 103-112`，无类）里的 inline 兄弟、横排。给该单元格加类并 `display: flex; flex-direction: column; align-items: flex-start`（+ 纵向 gap），使「波动」组落到「价格」组**下方**，与 mockup 一致。
- **`.snap-item` 保持不动**——column 容器内每条自动独占一行；挂单不受影响。
- 每行内容：方向 glyph（`below → ↓` / `above → ↑`）+ 价位（`@{{ fmtNum(a.price) }}`，加重）+ `reasoning`（弱化 `--ob-text-muted`）。
- `reasoning` **整段换行不截断**（零信息丢失；每行以价位 glyph 起首，仍可扫读）。

竖排后告警变多 = 纵向列表加长，天然消化 max-20 尾部场景。

## 测试

- **后端**（`tests/test_webui_queries.py`）：
  - `tokens_consumed_total`：多 cycle 求和 / 无 cycle → 0。
  - `gap_since_prev_ms`：正常值（两轮间隔）/ 首轮 → null / `wall_time_ms` null → null / 负值 → 归 0。
- **前端**：
  - `LiveStatusCard`：断言累计 chip 渲染 `fmtTokensCompact` 结果。
  - `CycleRowHeader`：`gap_since_prev_ms` 有值渲染「间隔」节、null 不渲染。
  - `CycleDetailPanel`：多条价格告警逐条成行（`.alert-grp` column 布局，断言各告警条独立成行/纵向排列，而非内联）。
  - **波动子组无真实数据**：全库 `volatility_alert` 0 条（含 sim #21）→ 该子组的结构/布局只能用**构造 `volatility_alert` 的 mock snapshot** 在组件测试里验证（价格+波动同时存在时两组纵向堆叠），真实数据需等新 sim。
  - `fmtTokensCompact` / `fmtGap` 纯函数单测覆盖边界。
- **类型同步**：后端 schema 变更后重新生成 OpenAPI → `npm run gen:types`，保持 `frontend/src/api/types.ts` 与后端一致。

## 单元边界

- `fmtTokensCompact` / `fmtGap`：纯函数，落 `frontend/src/utils/format.ts`，独立单测。
- ① ② 后端聚合各自独立，互不依赖；③ 不触后端。
- 三项可独立实现、独立测试、独立提交。
