# iter-taker-flow-audit-remediation Design

## Context

`get_taker_flow`（PR #65 新增的 order-flow 感知工具）经两轮独立 tool-audit
（`.working/tool-audits/2026-06-07-get_taker_flow.md` + `2026-06-09-get_taker_flow.md`，
均基于 sim #15 `f670abe1`）。审计确认工具 **adoption 真实健康**（91.2% cycle 级独占信号
grounding，#2 高频工具）、**计算正确性 0 议题**（rubik 列序经 OKX 官方文档逐字核对、
CVD/invariant spot-check 全过）。本 iter 收口审计浮出的 docstring / 接口 / render 打磨议题。

议题分流：

- ✅ **I-1**（session-log per-bar 折叠）已由 `1310e73`（iter-taker-flow-render, 2026-06-07，
  现已在 main，仅改 `display.py`）修复，**不在本 scope**。
- 👁 **I-4**（CVD 跨调用比较）observe / wontfix-by-design：CVD 设计上 window-relative
  （零点 = 窗口最老 bar，每 call 滚动），docstring 已警告且实测实质有效（仅 2.3% block 误比，
  agent 多数仍 ground 在窗口内 per-bar 证据）；按原则 8 不加重 prompt nudge，真修 = 锚定/绝对
  累积 CVD 新特性 redesign，**不在本 scope**。
- 🔧 本 iter 收口 **7 条**：I-5 / I-9 / I-2 / I-3 / I-6 / I-7 / I-8。

## Goals / Non-goals

**Goals**：① period 集补 15m 成连贯阶梯；② docstring 时点语义自洽（Example 反映 closed 主流 +
default 对齐实测主流）；③ render 消歧（rubik publish-lag 滞后、in-progress partial）；
④ doc 准确性（anchor 列表去枚举防漂移）。

**Non-goals**：不动 `_render_taker_flow` 核心算法（计算已验证正确）；不动 `display.py`
（I-1 已修）；不改 CVD 语义（I-4 observe）；不新增工具；不动 4h/1d（阶梯档位 + anchor 来源，保留）。

## §1 Period 集补 15m（I-5 + I-9）

**决策（brainstorm 2026-06-09）**：tool period 集 `{5m,1h,4h,1d}` → `{5m,15m,1h,4h,1d}`
（`1w` 仍 anchor-only）。理由（实证为主、阶梯为辅）：① **实证**——sim #15 agent 主时间框就是 15m（62% reasoning
引用、2 次 15m 调用被拒），而 30m/2H/6H/12H **零实证需求**；② **阶梯**——15m 填掉 `5m→1h`
的 ×12 空洞，加 30m 会把底部步长压成 ×2、破坏连贯（原则 4：选项数是 agent 选择延迟的物理约束）。
注：现有阶梯本就不完全均匀（4h→1d 6×、1d→1w 7×），故"均匀步长"是次要论据，主驱动是实证 +
排除项零需求。OKX `taker-volume-contract` 端点支持 15m 已经
context7 官方文档核实（`period` 枚举 `5m/15m/30m/1H/2H/4H/...`；区别于 `taker-volume`
非 contract 端点只支持 `5m/1H/1D`）。

**Anchor 决策**：宽错 anchor `{5m→1h, 15m→1h, 1h→4h, 4h→1d, 1d→1w}`——保留主力 5m
（97%）现有的 1h anchor（不回归），15m 也接 1h（"细粒度帧都看小时级上下文"）；非严格 +1 档。

**改动**：

1. `src/integrations/exchange/base.py:23` `_TAKER_VOLUME_PERIOD`：加 `"15m": "15m"`
   （OKX 该端点 15m 小写，与 5m 一致）。
2. `src/agent/tools_perception.py:1124` `_TAKER_FLOW_PERIOD_MS`：加 `"15m": 15 * 60_000`。
3. `src/agent/tools_perception.py:1130` `_TAKER_FLOW_ANCHOR`：
   `{"5m": "1h", "15m": "1h", "1h": "4h", "4h": "1d", "1d": "1w"}`。
4. `src/agent/tools_perception.py:1284` reject 消息：硬编码 `"5m, 1h, 4h, 1d"` →
   派生自 `_TAKER_FLOW_ANCHOR` keys（`f"... must be one of: {', '.join(_TAKER_FLOW_ANCHOR)}"`），
   防再漂移（同时根治 I-9 的枚举漂移病根）。
5. `fetch_taker_flow` 的 `period: Literal[...]` 三处加 `"15m"`：`base.py:181`（抽象契约）/
   `simulated.py:1042`（sim 活跃路径，**必加**）/ `okx.py:856`（live 路径一致性）。现为
   `["5m","1h","4h","1d","1w"]`（含 anchor-only 的 1w、无 15m）；加 15m 后
   `fetch_taker_flow("15m")` 会被实际调用（get_taker_flow→market_data→exchange 链路），
   否则 Literal 变 stale enum，与 I-9 去枚举防漂移自相矛盾。⚠️ 仅类型注解，项目无 mypy/CI 类型
   gate（已核 pyproject.toml）→ 不破运行/CI，但为一致性须同步。`market_data.get_taker_flow`
   用 `str` 非 Literal，无需改。
6. **两处 stale 注释同步去枚举（F1 · I-9 同病灶）**：`tools_perception.py:1128-1129`
   （"the exact set of valid *tool* periods ({5m,1h,4h,1d})"）与 `:1283` 行内注释
   （`# valid tool periods == {5m,1h,4h,1d}`）——加 15m 后硬编码的 `{5m,1h,4h,1d}` 变 stale，
   正是 I-9 要根治的枚举漂移病（且 :1283 紧贴改动 4 的 :1284）。改为**不列具体档位**
   （指向"`_TAKER_FLOW_ANCHOR` 的 keys"）。grep 全库已确认这是 taker-flow period 集硬枚举的
   **最后两处**（其余 `5m,1h,4h,1d` 命中均属 MTS/get_multi_timeframe_snapshot 别工具）。

**I-9 docstring anchor 描述**：summary 现 `"A same-period 1h/4h/1d context-anchor line"`
措辞不准（漏 1d→1w；"same-period" 误导）→ **去枚举化**为
`"a coarser-tier context-anchor line shows the larger bar's current direction"`
（不列具体档位，根除未来漂移）。

**无需改（已核验）**：valid-period 校验（`:1283 if period not in _TAKER_FLOW_ANCHOR` 自动纳入
15m）；OHLCV Close join（`get_ohlcv_dataframe(symbol,"15m",…)` 已支持，ts 按 15m 边界对齐）；
anchor 上层 fetch（15m→1h，1h 在 `_TAKER_VOLUME_PERIOD` 内）；render `up_ms` 取 1h（已在表内）。

## §2 Docstring Example → closed 主流 + default limit（I-2 + I-3）

**I-3 default limit 6→12**：`limit: int = 6` → `12`（wrapper `trader.py:431` + impl
`tools_perception.py:1271` 两处签名 + Args 文字）。理由：sim #15 实测 limit=12 占 65.1%
（终段稳定 T1 62.5/T2 68.0/T3 64.8%），default=6 仅 10.4%，偏离 2×（原则 5 "default 反映
实测主流"）；agent 推理一致用 60min 窗口（"CVD over the last 60min"）。**token 影响 = 零**
（DB 核实 sim #15 `limit_omitted=0/384`：agent 100% 显式传 limit、真·default 调用为 0；那
10.4% 的 limit=6 全是**显式**传 6，改 default 不影响它们）。I-3 本质纯 cosmetic——把**声明的**
default 对齐实测主流 + docstring 诚实，**非省 token**。

**I-2 Example 改 closed 主流**：wrapper docstring `Returns:` 块 Example（实测 LLM 收到，
griffe 不剥）现演示 in-progress，但 sim #15 实测 91–98% 是 closed。改：

- `Now (current 5m, 4.0/5min formed)` → `Now (current 5m, closed)`
- 去 `04:30*` 星标 → `04:30`
- `row 1 = current in-progress` → `row 1 = latest closed bar`（**Example 须与 §3 I-6 最终
  渲染一致**：I-6 选 option (a) 则改 row1 内联文案；选 option (b) 则 Example **须同步加那行独立
  caveat**）
- 删 `[* row 1 = current bar still forming (4.0/5min)]` 脚注行
- `Window (6 bars = 30min)` → `Window (12 bars = 60min)`（与 I-3 default=12 同步）
- per-bar 用 `... (older bars) ...` 省略，避免 12 行撑大 docstring
- summary 两态陈述把 closed 提到主导位（"...the latest closed bar; or the current
  in-progress bar, labeled with how far it has formed, when still open"）
- **F3：重写后的 Example 用代表性值**——原 `vol 0.3×` 是 in-progress 偏低量能，转 closed 后
  非典型；closed 主流示例宜用 ~1.0× 典型 RVol + 内部自洽的 net/buy%（Example 职责是教读格式，
  不宜把边缘值当范本）

## §3 Render：rubik publish-lag row1 标注（I-6, P2 · agent-facing）

**问题**：rubik 发布滞后使 taker_flow 的 row1（"latest closed bar"）常比 GMD OHLCV
最新已收盘 bar 晚 ~1 根；Close 列邀请 agent 跨源比对 → 误判"timestamp / join bug"
（sim #15: 9/1802 blocks，L37769 "Something is off — maybe... a timing issue" /
L72185 "a timestamp alignment issue between the two sources"，皆误报——join 按 ts 正确）。

**改动（`tools_perception.py:1232` 附近）**：让 row1 在 closed 时显式提示 rubik 滞后。
**意图明确，确切文案/落点交 plan 定**，约束：① 简洁、② 避免嵌套括号（现 row1_state 已被
`Per-bar (...; row 1 = {row1_state}):` 包一层括号，故 I-6 文案不宜再带括号）。建议落点二选一
（plan 决）：(a) row1_state closed 值改 `"latest closed bar — rubik may lag candle/ticker
by ~1 bar"`（em-dash 无嵌套括号）；(b) 在 Per-bar 表头下加一行独立 caveat（仅 closed 时）。
**仅 closed 分支**；in-progress 分支与 Close 列均不动。§2 Example 的 row1 行须与最终选定文案
一致。**F5 注**：保留 Close 列（而非审计另提的"重评 Close 是否值得"）属保守默认 + 原则 8 轻触；
Close 列 adoption **未单独量化**（仅零散引用证据如 audit correctness 流 L37770 "close 70228…
matches the OHLCV table"），故选择"不动而非删"——若 plan 想更扎实可补 Close 引用取证。

## §4 Render：in-progress partial 标注（I-8, P3）

**问题**：in-progress bar 的 RVol = 部分成交量 ÷ 20 根整 bar 均值，bar 早期机械偏低
（如 4.2/5min formed → 0.4×，非真缩量）；现仅 `*` 脚注说"still forming"，未点明 RVol 是部分量。

**改动**：复用 GMD 的 **"partial bar" 术语**（`tools_perception.py:212` 用了该词；只借术语
做跨工具一致，**不照搬 GMD 整句**——GMD 的 "excluded from all indicators" 对 taker_flow
不适用，因 taker_flow 仍展示在制 bar）。在 **"Now" 行**（`:1198 rvol_now`）的 in-progress
分支给 RVol 加提示：`vol 0.3× (vs 20-bar avg)` → in-progress 时
`vol 0.3× (vs 20-bar avg; partial bar)`。仅 `is_in_progress` 为真时生效（closed 不变）。
低频（实测 3.3%）但便宜，消除潜在误读。

## §5 I-7 anchor $K/$M scale —— 检视后不硬修 + 测试 pin

**审计发现**：anchor 行用独立 `_pick_usd_scale([up_net])`（`:1262`），同一 render 内可能
出现 $M 主表 + $K anchor 混排（强制读后缀才不误读 1000×）。

**决策（brainstorm 2026-06-09）：不硬修**。理由：anchor 是 1h/4h bar，量级常比 5m bar 大
~10×；若强制共享主表 scale，anchor 会渲染成 `-62000.0$K` 等难读形态——独立 scale 恰是为
各行各自可读。readability 流亦标 P3 + 注明"每值带显式 `$K`/`$M` 后缀，非严格歧义"。
→ 独立 scale 可辩护，后缀已是缓解。

**改动**：0 渲染改动；新增 1 个守护测试 pin 住"anchor 行始终带显式 `$K`/`$M` 单位后缀"，
把"隐患"转成"已审定 + 守护"。

## §6 Impact surface

| 文件 | 改动 | ~行 |
|---|---|---|
| `src/integrations/exchange/base.py` | `_TAKER_VOLUME_PERIOD` +15m · `fetch_taker_flow` Literal +15m | 2 |
| `src/integrations/exchange/{simulated,okx}.py` | `fetch_taker_flow` Literal +15m（各 1 行）| 2 |
| `src/agent/tools_perception.py` | `_TAKER_FLOW_PERIOD_MS` +15m / `_TAKER_FLOW_ANCHOR` 重写 / reject 派生 / :1128-1129·:1283 注释去枚举(F1) / row1 标签(I-6) / Now-行 partial(I-8) | ~8 |
| `src/agent/trader.py` | docstring（period 枚举 +15m / Example closed 化 / anchor 去枚举）+ 签名 default 6→12 | ~16 |
| `tests/test_taker_flow.py`（+ 必要时 drift-guard） | period/anchor/reject 派生/default/docstring-drift/I-6 标签/I-8 partial/I-7 anchor-suffix pin | 测试若干 |

src 净改动 **~28 行（< 30）**，符合 mini-iter direct-merge（per `feedback_docs_only_direct_merge`）。

## §7 Testing

- `_TAKER_FLOW_ANCHOR`：`["15m"]=="1h"`、`["5m"]=="1h"` 不变；keys == `{5m,15m,1h,4h,1d}`。
- `_TAKER_FLOW_PERIOD_MS["15m"] == 900_000`；`_TAKER_VOLUME_PERIOD["15m"]=="15m"`。
- period=15m 不再 reject（走正常渲染路径）；period=30m 仍 reject。
- reject 消息派生自 keys（含 15m，与 `_TAKER_FLOW_ANCHOR` 同步——drift guard，防硬编码再漂移）。
- default limit==12（wrapper + impl 两签名一致）。
- docstring drift-guard / `test_get_taker_flow_returns_example_*`：LLM-visible description
  period 枚举含 15m；Example 为 closed 形态（无 `*`、无 "still forming"、含
  `row 1 = latest closed bar`、含 `Window (12 bars`）。
- I-6：closed 分支 row1_state 含 publish-lag 标注子串。
- I-8：in-progress 渲染的 "Now" 行 RVol 后缀含 "partial bar"；closed 不含。
- I-7 pin：anchor 行 regex 始终匹配 `\$[KM]` 单位后缀。
- **F2 Literal 防漂移 pin（可选，补全 I-9 对 Literal 层覆盖）**：drift 测试断言三处
  `fetch_taker_flow` 的 period Literal 与单一来源同步——
  `set(get_args(<period Literal>)) == set(_TAKER_FLOW_ANCHOR) | set(_TAKER_FLOW_ANCHOR.values())`
  （= 工具档 {5m,15m,1h,4h,1d} ∪ anchor 值 {1h,4h,1d,1w} = 可 fetch 集 {5m,15m,1h,4h,1d,1w}），
  把手工维护的 Literal 也纳入防漂移网。
- round-trip：用真实 `_render_taker_flow` 生成 content 做断言，防 fixture drift（沿用既有 pattern）。
- 全量 `pytest -q` 0 回归。

**【Pre-merge gate · 必跑、非 unit】15m 真实端点 smoke**：merge 前对真实 OKX 跑一次
`fetch_taker_flow("BTC/USDT:USDT", "15m", 12)`，确认 ① 返回非空、② 行形状 `[ts, sellVol,
buyVol]` 正确、③ ts 按 15m 边界对齐。理由：§1 整体 load-bearing 在"OKX rubik 真对该 instId
返回 15m 数据"这一**外部假设**上；context7 文档枚举 ≠ 端点实际行为（可能稀疏/形状异/该 instId
无 15m），unit 全绿但端点不返回 → 下一轮 sim 静默失败。sim 用真实 `_ccxt` 抓 rubik（per
`feedback_sim_real_data_except_order_mgmt`），且 order-flow 系列既定纪律即"启动新 session
前置 smoke"（per `project_sim_market_data_fidelity`）。**由用户在 merge 前跑 + 贴结果**（理由：它碰
live OKX / env-side 凭证 —— 是只读 rubik 端点的 ~2s 快调，**非** ">10min 长实验"，故不援引
`feedback_long_walltime_experiments`；指派给用户是因 live/env 侧而非耗时）；smoke 不过则 §1 整组回炉。

## §8 Out of scope

- **I-1** session-log per-bar 折叠 — ✅ 已修（`1310e73`, display.py）。
- **I-4** CVD 跨调用 — observe / wontfix-by-design（见 Context；真修 = 锚定 CVD 新特性，另立）。
- 附录 backlog（非 taker_flow 工具议题）：session-log completeness（9 调用未渲染 tool body）/
  `scripts/fetch_session_ohlcv.py` 重抓当前 OKX 数据与 sim-era 漂移 / `scripts/tool_call_summary.py`
  default `--since 1d` 截断长 sim。

## §9 Risks / edge cases

- **15m anchor=1h**：1h 必在 `_TAKER_VOLUME_PERIOD` —— 成立。15m 的 OHLCV Close join 走 15m
  timeframe；sim 用真实 `_ccxt` 抓真实 rubik 15m + 真实 15m OHLCV，ts 按 15m 边界对齐。
- **default 12 使 docstring Example 表更长** —— 用 `... (older bars) ...` 省略控制 docstring 体积。
- **reject 消息派生自 dict** —— Python dict 保插入序，输出 `5m, 15m, 1h, 4h, 1d` 顺序自然。
- **I-6 标签加长 row1_state** —— 仅 closed 分支文本，不影响 in-progress；session-log per-bar
  现已全保留（`1310e73`），故新标签在存档中可见。
- **I-8 partial 仅 in-progress** —— 与 `*` 脚注/"X/Ymin formed"协同，不污染 closed 主流渲染。
