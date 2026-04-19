# Hardening Batch — 设计文档（PR B + PR C）

## 0. 背景

### 0.1 项目概述与当前状态

**TradeBot** 是一个 LLM 驱动的加密货币自动交易系统。Agent（Claude）通过工具调用感知市场、管理仓位、做出交易决策，在 USDT 保证金永续合约上自主交易。运行循环：每 15 分钟唤醒一次（也可被订单成交、价格警报等事件提前唤醒），通过工具获取数据并分析后决定是否操作。

**System Prompt 三层结构**（`src/agent/persona.py`）：
- Layer 1 — 身份 + 工具引导（介绍每个工具的用途和注意事项）
- Layer 2 — 通用交易思维框架（市场结构、信号确认、风险回报、仓位管理、自我复盘）
- Layer 3 — 人格 + 策略（可选注入 trading style 和 risk tolerance）

**工具库规模**：26 个（15 感知 [含 3 市场情报 + 4 宏观/ETF/链上] + 10 执行 + 1 memory）。

**当前状态（2026-04-19）**：N3 (PR#14, 2026-04-18) 合并后，Agent 感知层达到 6 类广度：技术面 / 消息面 / 衍生品结构 / 宏观环境 / 机构 ETF 资金流 / 链上稳定币。

- **主线**：14 个 feature PR 已 landed，647 测试通过
- **已完成的 hardening 前置步骤**：
  - PR #15（2026-04-19）— 全量信源风险矩阵（`docs/source-risk-matrix.md`），N4 audit 产出
  - PR #16（2026-04-19）— Dependabot 告警修复（authlib 1.7.0 + python-multipart 0.0.26）

### 0.2 为什么做这一批

**相关概念**：

- **观察期**：N3 landed 后让 agent 用当前工具集运行并采集实战决策数据的阶段（非 QA；agent 在模拟交易所自主交易）。目标是用实战数据驱动下一轮改进（观察期后可能启动 N6 HTF hardening、N7 Layer 1 组织、Toolkit Expansion 等议题）
- **N 编号**：Next-Iteration 改进候选议题，按识别时间顺序命名。PR A（#15）完成 N4 全量信源审视；本 spec 做 **N5 标签清理** + **N3 follow-up 批次**

观察期的核心目的是**评估 agent 的决策质量**。但如果当前工具层自身存在以下缺陷，会污染观察数据、让"工具问题"与"决策问题"难以区分：

1. **工具输出里残留"决策暗示"标签**（如 RSI `(bearish)`、MACD `(bullish)`）——agent 可能把标签当 hint 直接采用，而非独立分析 → **PR B**
2. **已识别但未修复的散点缺陷**（N3 PR#14 review 遗留）——如 DefiLlama schema 防御缺失、HTF df.empty 与 outage 语义混淆、AV daily-call 预算无可观测性 → **PR C**

**PR A 状态**：信源风险矩阵已合并（PR #15，`docs/source-risk-matrix.md`），作为本批 §3.6 API key audit 的依据（完整结论见 §3.6）。

### 0.3 与其他议题的边界

本 spec 范围**不包含**：

- **Toolkit Expansion**（下一轮大迭代）—— 代入 agent 视角盘出的 15 项工具 gap（订单簿、关键位、多时间框架 coherent、风险敞口等），本批不做
- **N6 HTF hardening**（volume + MA 斜率）—— 需观察期数据支持是否真需要
- **N7 Layer 1 组织重构**（19 bullet 分组 / 抽索引）—— 需观察期数据知道哪些工具被忽略
- **N4 prompt 级缓解方案**（skepticism 引导 / 可信度标注 / 多源交叉）—— 需观察期实例支撑（参见 `source-risk-matrix.md` §N4 Prompt 议题候选）
- **CoinDesk 是否删除** —— 观察期候选结论
- **硬性风控（P3）** —— 切实盘前再做

---

## 1. 目标与范围

### 1.1 功能目标

本批次按 2 个 PR 推进：

| PR | 议题 | 产出物 | 规模估算 |
|----|------|--------|---------|
| B | N5 工具输出标签清理 | `services/technical.py` + `tools_perception.py` fact-only 清理 | 净 ±0 行代码（-20 删标签 + 10-15 加 BB 三分支 + 注释）；4 个新增测试（3 BB 边界 + 1 5m ATR 对称）+ ~15 个断言更新（`test_technical.py` + `test_tool_enhancement.py` 真断言）+ 5 处 stale mock/fixture 清理（display_cycle + tool_enhancement mock） |
| C | N3 follow-up 批次 | 6 项散点修复（见 §3；§3.5 原 M4/M6 已移除） | ~80 行代码；12 个新增测试，无现有测试需修改 |

### 1.2 非功能目标

- **Fact-only 原则**（沿用 N3 §3.1）：工具输出 = 纯数字 + 事实换算 + 维度比较；不含"bullish / bearish / neutral / moderate / strong"等解读标签
- **三态降级契约**（沿用 N3 §3.5）：`list` / `[]` / `None` 分别表示"数据 / insufficient data / temporarily unavailable"；HTF 工具补齐这一契约
- **向后兼容**：两项数据模型字段变更（`StablecoinSnapshot.change_7d_pct` + `StablecoinTotal.total_change_7d_pct`: `float` → `float | None`，见 §3.5 M3）；无 config schema 变更；测试更新限于"旧标签 → 新 fact-only 输出"的断言变化，不影响调用契约
- **无新增运行时依赖**
- **API key 泄露防护**：PR C §3.6 复核所有外部源 HTTP client 的错误路径，确认 api_key 不会被序列化到日志（PR A 已初步确认仅 FRED/AV 有 URL query-param key，其他源用 header 或无 auth）

### 1.3 非目标（明确不做）

- 不引入新工具、新数据源、新感知能力
- 不改 Layer 1/Layer 2 prompt 的叙事内容（仅改 §3.4 的 "past 7 days" 事实性措辞）
- 不做 N4 的 prompt 级缓解（skepticism、可信度标注等）
- 不修改测试基础设施、不加新 dev 依赖
- **不改 ETF footer 视觉布局**：`tools_perception.py:876-877` 当前两行独立 `Note:` 段（数据范围说明 + 数据修订提示）性质不同，合并会降低可读性；保持现状（原 §3.5 M4，讨论后决定不改）

### 1.4 PR 切分策略与依赖

两个 PR 的**代码段独立**（两者均触及 `tools_perception.py` 但改不同行段），可并行开发但 rebase 时可能产生同文件冲突；推荐串行合并以简化 review：

```
PR B (N5 清理) → PR C (N3 follow-up)
```

- **B 触点**：`services/technical.py` 的 `format_for_llm` + `tools_perception.py:59-95`（`get_market_data` Market Context 段）
- **C 触点**：`alpha_vantage.py` / `onchain/service.py` / `tools_perception.py:625-632,655,675-676,820`（HTF + ETF 相关段；`:655` 是 HTF MA 格式对齐 PR B）/ `persona.py` / `crypto_etf/service.py`

---

## 2. PR B — N5 工具输出标签清理

### 2.1 清理原则

**核心判断标准**：工具输出是"事实查询接口"，Agent 应基于事实自主判断。任何**定性分类**（强弱、方向、优劣）都属于"决策暗示"，应删除。

**保留**（事实性）：
- 纯数字值（`RSI(14): 35.20`）
- 事实换算（`MA(20): 42800.00 (price +2.3%)`、`ATR: 1.5% of price`、`Volume: 1.20x avg`）
- 维度比较 / 位置定位（`BB position: 57% of band width`、`within range: 45%`）

**删除**（决策暗示）：
- 定性标签（`bearish / bullish / neutral / oversold / overbought`）
- 强度分类（`low / moderate / high / above normal / normal`）
- 方向性修饰（`price above — bullish`、`price in upper half`）

### 2.2 具体决策清单

基于 `src/services/technical.py:57-122` `format_for_llm` 方法和 `src/agent/tools_perception.py:59-95` Market Context 段的现状。

**清理点分布**：

- #1-4（RSI/MA/MACD/BB）：`services/technical.py:format_for_llm`（`:57-122`）
- #5-6（ATR/Volume）：`tools_perception.py:get_market_data` Market Context 段（ATR 在 `:63-71`，Volume 在 `:76-87`）

| # | 指标 | 当前输出 | 目标输出 | 理由 |
|---|------|---------|---------|------|
| 1 | RSI(14) | `RSI(14): 35.20 (bearish)` | `RSI(14): 35.20` | 删除 5 级定性标签（oversold/bearish/neutral/bullish/overbought） |
| 2 | MA(20/50) | `MA(20): 42800.00 (price above — bullish)` | `MA(20): 42800.00 (price vs MA: +2.3%)` | 删除 bullish/bearish 标签；above/below 改为带符号百分比 `(current - ma) / ma * 100`；"price vs MA:" 前缀消除"MA 自身动 +2.3%"的潜在歧义 |
| 3 | MACD | `MACD: 15.20 \| Signal: 12.30 \| Histogram: 2.90 (bullish)` | `MACD: 15.20 \| Signal: 12.30 \| Histogram: 2.90` | 删除 histogram 符号的定性标签 |
| 4 | BB | `BB: 43200 / 42800 / 42400 (price in upper half)` | 带内：`BB: 43200 / 42800 / 42400 (position: 72% of band width)`；脱带：`BB: 43200 / 42800 / 42400 (2.3% above upper band)` 或 `(1.5% below lower band)` | 替换 "upper/lower half" 为精确位置；脱带用 above/below 偏移量避免 ">100%" 歧义；handle edge case when `bb_u == bb_l` |
| 5 | ATR(14) | 5m: `ATR(14): 1.50 (0.35% of price — moderate)`；其他 timeframe: `ATR(14): 1.50 (0.35% of price, 1h candles)` | 所有 timeframe 统一为 `ATR(14): 1.50 (0.35% of price, {timeframe} candles)` | 删除 5m 分支的 low/moderate/high 标签；合并 `tools_perception.py:63-71` 的 `if/else` 双分支为单一 fact-only 输出 |
| 6 | Volume | `Volume: 1234.5 (1.20x avg — normal)` | `Volume: 1234.5 (1.20x avg)` | 删除 low/normal/above normal 标签；保留 "x avg" 倍数 |

### 2.3 边界决策说明

本节记录 spec 阶段的判断依据，供 review：

1. **#2 改 "price above/below" 为带符号百分比**：原表述保留了 above/below 方向性，但既然已经给了 MA 值和当前价，百分比形式信息更多且无方向词
2. **#4 BB 替换为 "% of band width" + 脱带偏移量**：三分支渲染，避免 "upper/lower half" 隐含方向暗示 *和* ">100%" 的语义歧义
   - 带内（`bb_lower ≤ current ≤ bb_upper`）：`position: N% of band width`，`N = (current - bb_lower) / (bb_upper - bb_lower) * 100`
   - 脱带上方（`current > bb_upper`）：`X% above upper band`，`X = (current - bb_upper) / bb_upper * 100`
   - 脱带下方（`current < bb_lower`）：`Y% below lower band`，`Y = (bb_lower - current) / bb_lower * 100`
   - 若 `bb_upper == bb_lower`（极罕见但可能发生在横盘极窄 BB）：显示 `N/A`（避免除零）
   - **脱带公式分母选 `bb_upper` / `bb_lower` 而非 `current`**：band 本身是参考系（anchor），agent 关心的是"价格偏离带边界的幅度相对于带位置"，不是"偏离相对于当前价"；与 ATR "% of price" 用 price 作分母不同是因为 ATR 的 anchor 是绝对波动相对价格规模，本质不同
   - **实施注记**：PR B 实施时在 `format_for_llm` 的 BB 渲染处加一行代码注释，说明 "带内 anchor=band width / 脱带 anchor=band edge"，帮助未来 reviewer 理解分母切换
3. **保留 ATR/Volume 的换算**：`% of price` 和 `x avg` 都是纯数学运算的事实，不涉及判断
4. **Layer 1 prompt 在本 PR (B) 不改**：本 PR 只改**工具输出**；Layer 1 工具描述中如有类似暗示（N5 记忆提到的 "X often signals Y"），属于 Layer 2 prompt 改动范畴，不在本 PR scope。**注**：PR C §3.4 对 `persona.py:43` 的事实化措辞改动（"past 7 days" → 动态描述）属于 fact-only 范畴（补齐参数能力描述，不涉及去决策标签），不在本排除之内
5. **Layer 1 工具描述不冲突**：grep 验证 `persona.py` 的 Layer 1 工具描述（`_build_layer1()`，L17-44）未承诺具体标签（bearish/bullish 等），N5 清理后的工具输出不会破坏 Layer 1 的语义契约。Layer 2 的 `_build_layer2()` 在 L56 提到 "technical indicators showing confluence"——这是决策思维框架而非工具契约，也不冲突

### 2.4 测试策略

**回归覆盖**（主要工作，完整清单见 §5.1）：

- `tests/test_technical.py`：~10 个断言更新（RSI/MA/MACD/BB/ATR 标签相关）。**`:103` 特别处理**：当前为正检查 `assert any(word in text.lower() for word in ("neutral","bullish","bearish","overbought","oversold"))`，N5 清理后必须**反转为 `assert not any(...)`**——仅删除字符串会让断言默认通过，丧失回归保护。同时**改测试函数名**：`test_format_for_llm_5m_annotations`（`:95`）→ `test_format_for_llm_is_fact_only`，因断言语义已从"有标签"反转为"无标签"，旧名与行为矛盾
- `tests/test_tool_enhancement.py`：~5 个 `get_market_data` Market Context 端到端断言
- **stale fixture/mock 清理 5 处**（实测 `src/cli/display.py:38-52` 只 regex 提数字，这些标签均为 fixture 输入非断言，**不破 CI** 但偏离新契约，应一并清理，详见 §5.1）

**新增测试**（BB 边界条件，加到 `tests/test_technical.py`）：

- `test_format_for_llm_bb_position_edge_case_equal_bands`：`bb_upper == bb_lower` 时不崩溃，**解析 BB 行括号内 position 段**（非整行——整行必含 `BB: 43200 / 42800 / 42400` 数字）并断言该段包含 `N/A` 且**不含 `%` 符号或数字字符**（防止未来实现误写 `N/A%` 折中）
- `test_format_for_llm_bb_position_at_lower_band`：当 price = bb_lower，position = 0%
- `test_format_for_llm_bb_position_at_upper_band`：当 price = bb_upper，position = 100%

**5m ATR 对称回归**（加到 `tests/test_tool_enhancement.py`）：

- `test_get_market_data_5m_atr_no_qualitative_label`：与既有 `test_get_market_data_1h_atr_no_qualitative_label`（`:356`）对称——断言 5m timeframe 输出不含 `low volatility` / `moderate` / `high volatility` 任一字符串。防止未来有人重新引入 5m 标签分支

### 2.5 预期产出规模

- 代码：-20 行（删除 RSI/MA/MACD/BB 标签 + ATR/Volume 标签） + 10-15 行（BB 三分支 + edge case + 注释）= **净 ±0 行**（原估"-10 行"偏乐观）
- 测试：~15 个断言更新（~10 `test_technical.py` + ~5 `test_tool_enhancement.py` 真断言）+ 1 个函数名修改（`:95` 重命名）+ 4 个新测试（3 BB 边界 + 1 5m ATR 对称）+ 5 处 stale mock/fixture 清理（`display_cycle.py:14-18` + `:376` + `test_tool_enhancement.py:313,348,375`——均为 fixture/mock 输入，非断言）

---

## 3. PR C — N3 follow-up 批次

### 3.1 AV daily-call counter metric

**背景**：Alpha Vantage 免费档 25 req/day（见 N3 spec §5.2 时段感知 TTL 就是为了省这个预算）。当前无可观测性——观察期如果预算透支、SPY/QQQ 频繁返回 RateLimitHit，事后才能从 log 推。

**设计**（决策 D3：选项 B — log warning at 80%）：

- `AlphaVantageClient` 增加 **instance-level** 计数器 `self._daily_count` + `self._daily_count_date`（按日期字符串 reset；**默认 UTC 时区**为设计定稿，观察期若发现偏差 > 1h 再调整——见 §7.2）
- **递增策略**（`alpha_vantage.py:62-118` 的 `fetch_quote`）：采用 `try/finally` 统一递增，避免逐路径枚举漏洞。**锚点**：任何 "请求已到达 AV 且 AV 已处理" 的路径都应递增（配额已消耗），涵盖：
  - 硬 429（`:82`）：AV 回了 429 表示配额已达上限，必须递增
  - 成功返回路径（`:113-118` 构造 `EquityQuote`）
  - 软 rate limit（`:104`，HTTP 200 + body 含 "Information" / "Note"）
  - 响应结构异常（`:109` ValueError：`quote` 字段缺失）
  - **`:113-118` 构造异常**（`quote["05. price"]` 缺键 → KeyError；`float("N/A")` → ValueError；`change_pct` rstrip 类型错 → AttributeError）
  - **`resp.json()` JSONDecodeError**（HTTP 200 + 非 JSON body，罕见但可能——AV 偶发返回错误页）：flag 必须在 `resp.json()` **之前** 置位（见下方伪代码）
- **不递增**：
  - HTTPStatusError（其他 4xx/5xx，`:94`）：AV 通常不对错误响应计费（行业假设）；观察期若发现错误响应仍消耗配额，扩展递增路径并**同步补测试** `test_daily_count_increments_on_http_error`
  - 网络错误（ConnectError/Timeout，请求未到达 AV）
- **推荐实现结构**（伪代码）：

  ```python
  consumed_quota = False  # flag 取代 try/finally 的"无差别递增"，配合 HTTP error 路径不递增
  try:
      resp = await self._http.get(...)
      if resp.status_code == 429:
          consumed_quota = True  # 硬 429 属于"配额已耗尽"信号
          raise RateLimitHit(...)
      if resp.is_error:
          raise HTTPStatusError(...)  # 不递增
      # 通过 is_error 检查即代表 AV 已返回 2xx/3xx，配额已消耗。
      # 在 resp.json() 之前置 flag 以覆盖 JSONDecodeError（AV 偶发返回
      # HTTP 200 + 非 JSON body，罕见但存在）。
      consumed_quota = True
      data = resp.json()
      if soft_msg := ...:
          raise RateLimitHit(...)
      quote = data.get("Global Quote")
      if not quote:
          raise ValueError(...)
      return EquityQuote(...)  # 成功路径
  finally:
      if consumed_quota:
          self._increment_daily_count()  # 封装递增 + date 切换 + warning 频控
  ```
- **AV 计费假设**：HTTP 4xx/5xx 不递增基于"AV 通常不对错误响应计费"的行业通用假设，AV 官方文档未明确。观察期若发现错误响应仍消耗配额（可通过 429 命中时点与 counter 值的偏差判断），将递增路径扩展到 `:94` HTTPStatusError，**同步补测试** `test_daily_count_increments_on_http_error` 形成回归闭环
- **Warning 频控**：引入 `self._warned_today: bool` flag。首次 `count >= 20` 触发 `logger.warning("AV daily budget at %d/25 (date %s UTC)", count, date)` 并置 flag；同日内再达阈值**不重复触发**（避免单日 5-25 次 log spam）。`_daily_count_date` 切换时一并 reset flag
- **时区**：`_daily_count_date` 用 `datetime.now(timezone.utc).strftime("%Y-%m-%d")`（默认 UTC；观察期验证，见 §7.2 Q2）
- **进程重启语义**：`_daily_count` 为 instance attribute，**进程重启后归零 acceptable**——此 counter 是 best-effort observability（帮助提前感知预算）；**真实 429 enforcement 在 AV 侧**执行，本 counter 失准也不会导致 over-quota。无需 durable 存储（如 sqlite / file）

**理由**：

- **不走 MetricsService（选项 A）的根本原因是语义边界**：`MetricsService`（`src/services/metrics.py`）聚焦 **trading performance metrics**（win rate / drawdown / avg_win/loss 等策略评估指标）；AV counter 是 **infrastructure observability**（外部 API 配额监控），两者性质不同。耦合会污染 MetricsService 的语义职责，未来加其他 infra metric（如 FRED 调用频率、OKX 限流次数）会进一步失焦。本批采用 logger.warning 方案（选项 B）即可；若未来需要 structured infra observability，应独立建 `ObservabilityService` 或接入 APM
- **instance-level**（而非 class-level）：AlphaVantageClient 在 `MacroService` 中为单例，instance 级足够跟踪单进程预算。避免 class-level 全局可变状态导致测试间互相污染（每测试要 reset class attribute）

**实现位置**：`src/integrations/macro/alpha_vantage.py`

**测试**：`tests/test_macro_clients.py`

- `test_daily_count_increments_on_success`
- `test_daily_count_warning_at_threshold_only_once`（mock logger，断言 count=20 首次触发 warning；同日 count=21/22 不再重复触发；next day 可再次触发）
- `test_daily_count_resets_on_new_date`（mock datetime，验证 UTC 日期字符串切换时 reset count + `_warned_today` flag）

### 3.2 DefiLlama symbol 归一化防御

**背景**：当前 `src/integrations/onchain/service.py:49` 使用字典推导 `{a.get("symbol"): a for a in raw if a.get("symbol")}` 去重。

**问题**（已在 `source-risk-matrix.md` §12 确认）：
- 若上游 Schema 变为"每条链单独一行"（如 `USDT` 同时在 Ethereum + Tron），**后者静默覆盖前者**，丢失数据
- 大小写/whitespace 敏感（上游若改为 `usdt` 或 `"USDT "` 会匹配失败）

**当前实测**：DefiLlama 返回单行干净 `"USDT"` / `"USDC"`，暂不触发问题。本改动是**防御性加固**。

**设计策略调整**（基于实测）：`defillama.py:16-17` 注释明确 top-level `circulating` 是 **"across every chain"**（全链合计），**非单链发行量**。因此"多行同 symbol 求和"方案**在当前 schema 下会重复计数**，必须改为 **"first occurrence wins + 归一化 + schema drift 告警"**：

**设计**（替换 `onchain/service.py:49-82` 的 `by_sym` 构造 + 后续组装）：

```python
# === Phase 1: 归一化 + first-occurrence dedup（保留原 dict 覆盖语义）===
# 原代码 {a.get("symbol"): a for a in raw if a.get("symbol")} 的问题：
#   1. 大小写/whitespace 敏感："USDT " / "usdt" 不匹配
#   2. 同 symbol 多行时后者静默覆盖前者（无 drift 感知）
# 修法：用显式循环做归一化 + 首次出现保留 + 多行出现时 log WARN
# 注意：DefiLlama top-level `circulating` 已是全链合计（见 defillama.py:16-17），
# 多行同 symbol 应视为 schema drift 信号，不求和（求和会在当前 schema 下重复计数）
by_sym: dict[str, dict] = {}
seen_duplicates: set[str] = set()
for asset in raw:
    sym_raw = asset.get("symbol")
    if not sym_raw:
        continue
    sym = sym_raw.strip().upper()  # 大小写 + whitespace 归一化
    if sym not in _TRACKED_SYMBOLS:
        # 仅对 tracked symbol（USDT/USDC）触发 drift 告警，避免其他
        # stablecoin 的 schema drift 产生噪音。原代码先建全 by_sym
        # 再过滤，现把过滤提前到循环内，行为等价但 WARN 范围收敛
        continue
    if sym in by_sym:
        seen_duplicates.add(sym)  # 记录 drift 信号
        continue  # first occurrence wins（保留原语义）
    by_sym[sym] = asset

if seen_duplicates:
    logger.warning(
        "DefiLlama schema drift: multiple rows for symbol(s) %s; "
        "using first occurrence. Review if aggregation semantics changed.",
        sorted(seen_duplicates),
    )

# === Phase 2: 提取 per-symbol 数据，组装 coins + totals ===
# 此段保持原逻辑结构，只是读取改用归一化后的 by_sym
coins: list[StablecoinSnapshot] = []
total_circ = 0.0
total_prev = 0.0
for sym in _TRACKED_SYMBOLS:
    asset = by_sym.get(sym)
    if asset is None:
        continue
    circulating = float(
        (asset.get("circulating") or {}).get("peggedUSD", 0.0)
    )
    prev_week = float(
        (asset.get("circulatingPrevWeek") or {}).get("peggedUSD", 0.0)
    )
    delta = circulating - prev_week
    # M3：prev_week == 0 时 pct 无意义（见 §3.5 M3），返回 None 让渲染层显示 "N/A"
    pct = (delta / prev_week * 100.0) if prev_week > 0 else None
    coins.append(StablecoinSnapshot(
        symbol=sym,
        circulating_usd=circulating,
        change_7d_usd=delta,
        change_7d_pct=pct,
    ))
    total_circ += circulating
    total_prev += prev_week

total_delta = total_circ - total_prev
total_pct = (total_delta / total_prev * 100.0) if total_prev > 0 else None  # M3 同理
total = StablecoinTotal(
    total_circulating_usd=total_circ,
    total_change_7d_usd=total_delta,
    total_change_7d_pct=total_pct,
)
```

**防御收益**：
- **当前 schema（实测单行全链合计）**：行为与原代码一致，无 regression
- **schema drift 情形**（未来若 DefiLlama 改为每链分条 + 新字段如 `peggedChains`）：log WARN 触发人工介入，而非盲目聚合导致静默 bug
- **大小写/whitespace 漂移**：归一化后匹配稳定

**后续迁移路径**：若确认 DefiLlama schema 变为"每链分条"，再单独 PR 实现 per-chain 聚合逻辑（届时需查 `peggedChains` 字段语义），不在本批 scope。

**测试**：`tests/test_onchain_service.py`

- `test_multi_row_same_symbol_first_occurrence_wins_with_warning`：输入两行 `{symbol: "USDT", circulating: 100}` 与 `{symbol: "USDT", circulating: 50}`，期望 `circulating_usd == 100`（首次保留），且 `logger.warning` 被调用、消息含 `"schema drift"` + `"USDT"`
- `test_symbol_normalization_whitespace`：输入 `"USDT "`、`" usdt"` 等变体，期望被识别为 USDT
- `test_unknown_symbol_ignored`：未跟踪的 symbol 被跳过

### 3.3 HTF `df.empty` 三态区分

**背景**：`src/agent/tools_perception.py:625-632` 当前实现：

```python
try:
    df = await deps.market_data.get_ohlcv_dataframe(symbol, timeframe, limit=250)
except Exception:
    logger.warning("HTF fetch failed for %s %s", symbol, timeframe, exc_info=True)
    return "Higher timeframe view: temporarily unavailable"

if df.empty:
    return "Higher timeframe view: temporarily unavailable"
```

**问题**：两种情况被混为一谈：
- `Exception` → 真 outage（网络 / API 错）
- `df.empty` → 请求成功但无数据（symbol/timeframe 组合没历史、极少见但不同于 outage）

这违反 N3 §3.5 三态契约。

**设计**（决策 D6）：

```python
try:
    df = await deps.market_data.get_ohlcv_dataframe(symbol, timeframe, limit=250)
except Exception:
    logger.warning("HTF fetch failed for %s %s", symbol, timeframe, exc_info=True)
    return f"Higher timeframe view ({timeframe}, {symbol}): temporarily unavailable"

if df.empty:
    return f"Higher timeframe view ({timeframe}, {symbol}): insufficient data"
```

**一致性**：
- outage 和 data-gap 两分支均带 `({timeframe}, {symbol})` context，让 agent 清楚"哪个 timeframe 的 HTF 暂不可达 / 数据不足"
- **措辞与现有工具对齐**："insufficient data" 与 ETF `crypto_etf/service.py:89-93` 的 "Insufficient data in requested window" + MA `tools_perception.py:651` 的 "insufficient data (need N candles)" 风格一致；不再引入新措辞 "no data available"

**契约**：

| 状态 | 返回 | 含义 |
|------|------|------|
| fetch 成功 + 数据 | 正常 HTF 视图 | 数据 |
| fetch 成功 + `df.empty` | `Higher timeframe view ({tf}, {sym}): insufficient data` | 数据缺口（symbol/timeframe 历史为空） |
| fetch 抛异常 | `Higher timeframe view ({timeframe}, {symbol}): temporarily unavailable` | outage |
| fetch 成功 + 部分 MA 数据不足 | 已有 `insufficient data (need N candles)` | 数据缺口（subset） |

**测试**：`tests/test_perception_tools_n3.py`（HTF 属 N3 perception tools 族）

- **新增** `test_htf_empty_dataframe_returns_insufficient_data`：mock `get_ohlcv_dataframe` 返回空 DataFrame，断言输出包含 `"insufficient data"`、**不包含** `"temporarily unavailable"`，并含 `({timeframe}, {symbol})` 上下文前缀
- **更新既有** `test_htf_view_upstream_failure_degrades`（`:154-162`，已断言 RuntimeError → "temporarily unavailable"）：追加断言输出含 `({timeframe}, {symbol}):` 前缀，匹配新 outage 措辞。不新增等价测试，避免测试重复

**附加：HTF MA 格式与 PR B 短周期 MA 统一**

当前 HTF（`tools_perception.py:655`）MA 输出为 `f"MA{period}: {ma:,.2f} (price {dist_pct:+.1f}%)"` —— 即 `(price +2.3%)` 无 `vs MA:` 前缀。PR B §2.2 #2 将短周期 MA 改为 `(price vs MA: +2.3%)` 消歧；agent 同时看到两种格式会混淆。

**改动**：HTF `:655` 改为 `f"MA{period}: {ma:,.2f} (price vs MA: {dist_pct:+.1f}%)"`，与短周期对齐。

**测试新增**：`test_htf_ma_format_includes_vs_ma_prefix`：断言 HTF 输出含 `"(price vs MA:"` 前缀；防止未来格式漂移。

### 3.4 persona.py ETF 措辞动态化

**背景**：`src/agent/persona.py:43` 硬编码 `"past 7 days"`，但 `get_etf_flows(days=...)` 工具支持 1-14 days（clamp 于 `crypto_etf/service.py:47`）。

**设计**：

```python
# Before
"- **ETF flows**: Use get_etf_flows for daily net flow data of US-traded BTC and ETH spot ETFs over the past 7 days, plus cumulative AUM. Today's value may be revised T+1."

# After
"- **ETF flows**: Use get_etf_flows for daily net flow data of US-traded BTC and ETH spot ETFs, plus cumulative AUM. Default lookback is 7 days; pass days parameter (1-14) to adjust. Today's value may be revised T+1."
```

**测试**：已实测 `tests/test_persona.py` 无 "past 7 days" 字串断言，**无需更新**。persona prompt 改动后若需补新断言在 PR 描述里说明。

### 3.5 Minor 修复与维护清单

**分类**：M1-M3 是格式/事实类输出清理；M5 是测试维护（容差检视）。放在同一节便于实施阶段一并处理，但性质不同，PR 描述需分别说明。

| # | 问题 | 位置 | 修改 |
|---|------|------|------|
| M1 | HTF `0 {unit} ago` 措辞 + 单复数瑕疵（当 hi/lo 为最新一根时，输出如 `0 1d ago` / `0 4h-bars ago`；另 `_UNIT_LABEL` 值是复数 `"days"`/`"weeks"`/`"months"`，导致 `1 days ago` grammar 瑕疵） | `tools_perception.py:610,675-676` | (a) 0 值改为 `"latest"`；(b) 引入 `_UNIT_LABEL_SINGULAR = {"4h": "4h-bar", "1d": "day", "1w": "week", "1M": "month"}`，当 `hi_ago == 1` / `lo_ago == 1` 时用 singular。两点一起修避免"latest + 1 days ago" 新反差 |
| M2 | `days` 参数双 clamp（tool + service 各一处） | `tools_perception.py:820` + `crypto_etf/service.py:47` | tool 删 clamp；footer 从 `btc` / `eth` 任一非空 list 推导实际天数：`days_rendered = len(next((f for f in (btc, eth) if f), []))`。footer 渲染**复用现有 `if btc or eth:` 守卫**（既有代码已用 `if btc or eth:`，`[]` 和 `None` 均 falsy，自然排除"两者均空"的场景，不会渲染 "Past 0 days"）。**不变式**：当 `btc` 和 `eth` 均非空时，`len(btc) == len(eth)`（由 `crypto_etf/service.py:47` 同一 clamp 值 + `BTC`/`ETH` 并行 fetch 保证）；未来若有人并行化 service 层，必须保留此不变式或重写 footer 逻辑 |
| M3 | `prev_week == 0` 时 pct 固定为 0.0% + 渲染层未适配 None | `onchain/service.py:65,76` + `tools_perception.py:914,920` | **服务层**：`change_7d_pct` / `total_change_7d_pct` 在 `prev_week == 0` 时返回 `None`；数据模型 `StablecoinSnapshot.change_7d_pct` / `StablecoinTotal.total_change_7d_pct` 类型改为 `float \| None`（具体计算见 §3.2 Phase 2）。**渲染层**：`tools_perception.py:914` 和 `:920` 必须同步改为条件渲染 `f"{v:+.2f}%"` if `v is not None` else `"N/A (no prior-week data)"`——否则 `None` 进 format spec 触发 `TypeError`。**现有测试兼容性**：`tests/test_onchain_client.py:91` 已有 `StablecoinSnapshot(change_7d_pct=1.27)` 实例化，类型从 `float` 改为 `float \| None` 后仍兼容（1.27 是 valid float），**无需更新该测试** |
| M5 | Throttle 测试容差 | `tests/test_macro_clients.py:376`（`test_av_throttles_consecutive_calls`，当前容差 `0.9 ≤ x ≤ 1.2` 对 `_MIN_INTERVAL=1.1`） | 实施前再次 grep 历史 CI 记录是否有该测试 flaky 实例；若无则**保持现状**（§6.2 已允许"审视后保持现状"，需在 PR 描述说明）。当前 0.3 的容差宽度对 monotonic-clock jitter 是合理的 |

**说明**：M1-M3 每项改动小（5-10 行）；M5 需先查看测试代码再决定是否需要。ETF footer 视觉布局（原 M4）已归入 §1.3 非目标（保持现状）；AV throttle 升级为 class-level（原 M6）已从本批移除——`AlphaVantageClient` 在 `MacroService` 中为单例，class-level 可变状态会让测试间互相污染，收益不足以抵消复杂度。

### 3.6 API key scrubber audit

**背景**：FRED (`fred.py:35-50`) 和 AV (`alpha_vantage.py:81-98`) 已手工构造 HTTPStatusError 避免 `raise_for_status` 把 URL (含 api_key) 序列化到 log。两文件 class 注释均已明确 "API key leakage boundary"：`str(exc)` 是 sanitized 的（stdlib traceback 安全），**真实风险 vector** 在 `exc.request.url` 上——若未来接入 Sentry / Datadog 等 APM，其会遍历 exception 属性从而触达原始 URL (含 api_key)。

**PR A 审计结论**（完整内容见 `docs/source-risk-matrix.md` §总结）：

- 未发现 N3 review 期间识别的 scope 以外的新 schema 漏洞或 key 泄露点
- 所有 🔴 直接可修项都已在本 spec §3 预定范围内
- SoSoValue (header auth)、CoinGecko (header auth)、DefiLlama (no auth)、CoinDesk/FGI/ForexFactory/OKX (no auth) 均不在 URL 中携带 key —— 风险原生不存在

**本节 audit 范围**（基于 PR A 收敛后的清单）：

1. **确认 FRED 防护完整**：grep `src/integrations/macro/` + `src/services/` + **`src/agent/tools_perception.py:731`**（MacroService 调用栈的 `exc_info=True` 传播点——该处 exc 若携带 FRED/AV 原始 URL 会跨层泄露），确保没有路径把 `request.url` 或 exception 直接打到 log。其他 `tools_perception.py` 的 `exc_info=True` 调用（`:263` fetch_order、`:628` HTF、`:894` stablecoin）不涉及含 api_key 的 URL，可不纳入本次 grep
2. **确认 AV 防护完整**：同上 scope；重点核查 `MacroService._fetch_av_all` 异常路径 + `tools_perception.py:731` 的 exc 传播
3. **新增测试**：对 FRED + AV 各新增一个 `test_http_error_does_not_leak_key`：mock 4xx/5xx response，断言 `exc_info=True` 路径下 log 不含 api_key 字符串

**测试 scope 声明**：本节 2 个测试（FRED + AV 各一）**只覆盖 "stdlib logging + exception traceback 路径的 sanitization 回归保护"**（即验证 `raise_for_status` 替换为手工 `HTTPStatusError` 的防护不会被后续改动回退）。**不覆盖** APM 风险 vector（如 Sentry 遍历 `exc.request.url`）—— 该风险属于未来接入 APM 时的 scrubber 配置议题（需做 URL/query-string scrubber 配置 + httpx transport 层 EventHook 测试），**不在本批 scope**，记入 `docs/source-risk-matrix.md` §9 FRED 和 §10 AV 的 "Agent-observable / 工程侧" 观察项。

**预期产出**：0 或极少代码改动（已防护），主要是**确认 + 测试覆盖补全**（2 个新测试）。

### 3.7 预期产出规模

- 代码：~80 行（3.1 + 3.2 + 3.3 + 3.4 + 3.5 M1/M2/M3/M5 + 3.6 补丁）
- 测试：13 个新增（详见 §5.2）+ 1 个既有 HTF 测试更新

---

## 4. 通用契约

### 4.1 Fact-only 原则（沿用 N3 §3.1）

**原则定义**：工具输出是"事实查询接口"。Agent 基于事实自主判断，工具不应预判 / 标签 / 暗示结论。禁止定性分类（bearish/bullish）、强度分级（low/moderate/high）、方向修饰（above—bullish）—— 它们构成决策暗示，让 agent 依赖标签而非独立推理，长期压缩 agent 的分析能力发展空间，且在被操纵数据源（如 CoinDesk News，见 `docs/source-risk-matrix.md` §5）出现时放大误导风险。

具体到本批：

- ✅ 允许：数字、百分比、倍数、位置定位（0-100%）、时间戳、数据源标识
- ❌ 禁止：方向标签（bullish/bearish）、强度分类（low/moderate/high）、因果暗示（"often precedes"、"signals"）、隐喻（"dry powder"）

### 4.2 三态降级契约（沿用 N3 §3.5）

**契约定义**：源的响应状态区分三类，分别渲染不同信息给 agent。目的是让 agent 清晰区分 "数据真的不存在"（可能意味着 symbol/timeframe 无历史、本窗口期上游无发布）与 "源暂时不可达"（重试可能恢复）—— 两者对 agent 的后续操作含义完全不同（前者"接受这是事实"，后者"稍后再试或用其他信号")。

本批次明确 HTF 工具加入这一契约（§3.3）：

| 状态 | 语义 | 渲染 |
|------|------|------|
| 数据 | 成功且有足够内容 | 正常输出 |
| `[]` / 空 / 部分可用 | 源响应成功但数据不足 | "insufficient data"（整响应空）/ "insufficient data (need N)"（subset 不足）等明确信息 |
| `None` / Exception | 源不可达 | "temporarily unavailable" |

**data-gap 措辞统一**：项目现有三种 data-gap 场景，本批**统一为 "insufficient data" 家族**措辞（避免 "no data available" / "insufficient data" / "Insufficient data in requested window" 三种措辞混用）：

- 整响应空（HTF `df.empty`，本批 §3.3 引入）：`"insufficient data"`（带 `({timeframe}, {symbol})` 上下文前缀）
- Subset 不足（MA `tools_perception.py:651`，现有）：`"insufficient data (need N candles)"`
- 窗口不足（ETF `crypto_etf/service.py:89-93`，现有）：`"Insufficient data in requested window."`

均为 data-gap（区别于 outage 和正常数据），差异仅在渲染文本 —— 这是对 N3 §3.5 契约的合理细化，不破坏三态。ETF 现有大写 "Insufficient" 保留不改（shipped 代码不做 cosmetic 修改）。

### 4.3 向后兼容

- **工具调用签名**：零变化（不新增/删除参数）
- **返回类型**：仍是 `str`；内容变化对 agent 是"更少噪音 + 更精确"，不会让既有 prompt 失效
- **模型变更**：`StablecoinSnapshot.change_7d_pct` + `StablecoinTotal.total_change_7d_pct` 从 `float` 改为 `float | None`（M3）——需同步更新 tool 渲染层（`tools_perception.py:914,920`）和测试，但不影响其他消费者

---

## 5. 测试策略

### 5.1 回归测试（PR B 重点）

N5 改动会破坏**现有**关于 `format_for_llm` 输出格式的断言。预计需更新：

- `tests/test_technical.py`：~10 个断言更新（RSI/MA/MACD/BB/ATR 标签相关）。**`:103` 必须反转为 `assert not any(...)`**（当前是正检查 `any(word in text.lower() for word in ("neutral","bullish","bearish","overbought","oversold"))`，仅删字符串会让断言默认通过失去保护）。函数名 `test_format_for_llm_5m_annotations`（`:95`）改为 `test_format_for_llm_is_fact_only`（反映新语义）
- `tests/test_tool_enhancement.py`：~5 个 `get_market_data` Market Context 相关断言（实施时 grep 定位；若发现分散在 `test_tools.py` 也要同步）
- **清理 5 处 stale mock/fixture**（实测均为 fixture/mock 输入非断言——`src/cli/display.py:38-52` 只 regex 提数字不解析标签；`test_display_cycle.py:28-30` 断言仅查数字——**不破 CI** 但偏离新契约，应一并更新避免维护债）：
  - `tests/test_display_cycle.py:14-18`：`test_summarize_get_market_data` 的 `content` 输入 fixture 含 5 行标签
  - `tests/test_display_cycle.py:376`：`test_format_cycle_output_basic` 构造的假输出仍含 `(neutral)`
  - `tests/test_tool_enhancement.py:313, :348, :375`：三处 `deps.technical.format_for_llm.return_value = "...(neutral)..."` mock 返回值

执行方式：`pytest` 先跑全量，统计 failure，按断言类型批量更新。

### 5.2 新增测试（PR C 重点）

| PR | 新增测试类 | 约数量 |
|----|-----------|--------|
| B §2.4 BB 边界 | `test_format_for_llm_bb_position_edge_case_equal_bands`, `test_format_for_llm_bb_position_at_lower_band`, `test_format_for_llm_bb_position_at_upper_band` | 3 |
| B §2.4 5m ATR 对称 | `test_get_market_data_5m_atr_no_qualitative_label`（对称既有 1h 版，防止标签回潮） | 1 |
| C §3.5 M1 latest 分支 | `test_htf_range_latest_when_zero_ago`（`tests/test_perception_tools_n3.py`，断言 `hi_ago=0` / `lo_ago=0` 时输出 `"latest"` 而非 `"0 days ago"` / `"0 4h-bars ago"`） | 1 |
| C §3.1 AV counter | `test_daily_count_increments_on_success`, `test_daily_count_warning_at_threshold`, `test_daily_count_resets_on_new_date` | 3 |
| C §3.2 DefiLlama 归一化 | `test_multi_row_same_symbol_first_occurrence_wins_with_warning`, `test_symbol_normalization_whitespace`, `test_unknown_symbol_ignored` | 3 |
| C §3.3 HTF 三态 + MA 格式 | 新增 `test_htf_empty_dataframe_returns_insufficient_data` + `test_htf_ma_format_includes_vs_ma_prefix`（MA 格式对齐）；更新既有 `test_htf_view_upstream_failure_degrades`（追加前缀断言，非新增） | 2 新增 |
| C §3.5 M3 `prev_week == 0` | `test_prev_week_zero_returns_none_pct`（`tests/test_onchain_service.py`，服务层），`test_stablecoin_render_handles_none_pct`（`tests/test_perception_tools_n3.py`，渲染层不触发 TypeError） | 2 |
| C §3.6 API key 泄露 | `test_fred_http_error_does_not_leak_key`, `test_av_http_error_does_not_leak_key` | 2 |

总计：17 个新增测试（B 4 + C 13）+ 1 个既有测试更新（HTF upstream failure）。

### 5.3 测试基线

- 本批前：647 测试全绿（当前 main）
- PR B 合并后：约 651 测试全绿（+4 新增：3 BB 边界 + 1 5m ATR 对称；~15 断言内容更新 + 5 处 stale fixture 清理 + 1 函数重命名，但计数不变）
- PR C 合并后：约 664 测试全绿（+13 新增：AV 3 + DefiLlama 3 + HTF 2 + M3 2 + API key 2 + M1 latest 1；1 既有测试更新，计数不变）
- 每个 PR 合并前都要跑全量 `uv run pytest -q` 绿，并在 PR 描述中给出具体数字

---

## 6. 验收标准

### 6.1 PR B — N5 工具输出标签清理

- [ ] `services/technical.py::format_for_llm` 输出不包含下列字符串（#1-4 RSI/MA/MACD/BB 的标签）：`bearish`、`bullish`、`neutral`、`oversold`、`overbought`、`upper half`、`lower half`、`price above — bullish`、`price below — bearish`
- [ ] `tools_perception.py::get_market_data` Market Context 段（ATR `:63-71` + Volume `:76-87`）输出不包含下列字符串（#5-6 的标签）：`low volatility`、`moderate`、`high volatility`、`low`（作 Volume 标签）、`normal`、`above normal`
- [ ] BB 输出按价格位置分叉渲染：带内（`bb_lower ≤ current ≤ bb_upper`）用 `position: N% of band width`；脱带上方用 `X% above upper band`；脱带下方用 `Y% below lower band`
- [ ] `bb_upper == bb_lower` 时 BB 行括号内 position 段包含 `N/A` 且**不含 `%` 符号或数字字符**（整行仍含 band 值数字，断言范围限定在括号内段）
- [ ] 现有测试全部更新、全绿
- [ ] §2.4 的 4 个新增测试通过（3 BB 边界 + 1 5m ATR 对称）
- [ ] 测试总数约 651（647 + 4 新增）

### 6.2 PR C — N3 follow-up 批次

- [ ] AV counter 使用 UTC 时区：`_daily_count_date` 用 `datetime.now(timezone.utc).strftime("%Y-%m-%d")`；warning 消息含 `(date %s UTC)` 标注；`alpha_vantage.py` 类注释写明"默认 UTC reset"。观察期验证 follow-up 已记入 `project_tradebot_status.md`（见 §7.2）
- [ ] §3.1 AV 达 80% 阈值时 log warning（`test_daily_count_warning_at_threshold` 通过）
- [ ] §3.2 DefiLlama 归一化的 3 个防御测试通过
- [ ] §3.3 HTF 三态契约的 2 个测试通过
- [ ] §3.4 `persona.py:43` 表述已动态化
- [ ] §3.5 M1/M2/M3/M5 每项已应用（M5 允许"审视后保持现状"，需在 PR 描述说明；原 M4 已归入 §1.3 非目标；原 M6 已移除）
- [ ] §3.5 M3 的渲染层同步改动：`tools_perception.py:914` 和 `:920` 已条件渲染 None，测试验证 pct=None 时不触发 TypeError
- [ ] §3.5 M3 传染性 grep：全仓 grep `StablecoinSnapshot(` 和 `StablecoinTotal(` 消费点，确认无 `isinstance(x.change_7d_pct, float)` 或 `x.change_7d_pct > 0` 等断言式依赖（`float | None` 类型下这类用法会破）
- [ ] §3.6 FRED + AV 新增 `test_http_error_does_not_leak_key` 通过
- [ ] 测试总数约 664（651 + 13 新增 + 1 既有更新）

---

## 7. Open Questions / Risks

### 7.1 Q1: M3（`prev_week == 0`）类型变更影响

类型变更（`float` → `float | None`）会影响调用方和所有测试。实际场景 `prev_week == 0` 是否会发生？

**考察**：`prev_week == 0` 只会在以下情况发生：
- 上游 Schema drift（字段改名或被剔除）
- 新 stablecoin 刚上线（7 天前供应为 0）—— USDT/USDC 不可能
- 上游返回 `null` 被 `get(..., 0.0)` 默认为 0

**倾向**：改为 `float | None` + 渲染 `"N/A (no prior-week data)"`。成本中等但契约更健壮。spec 评审确认。

### 7.2 Risk/Follow-up: AV reset 时区观察期验证

**设计决策已定**（见 §3.1）：`_daily_count_date` 默认用 UTC（`datetime.now(timezone.utc).strftime("%Y-%m-%d")`）。

**Risk**：Alpha Vantage 官方文档未明确 25 req/day 的实际 reset 时点（empirical 验证需真跑 25 次撞墙），若实际 reset 不是 UTC 而是 ET 或其他时区，warning 触发时点会与实际 429 命中偏差 4-5 小时。

**观察期验证任务**：
- Warning 消息含 `(date %s UTC)` 时区标注，便于对照日志分析
- 监控指标：若 warning 触发时点与实际 429 命中时点偏差 > 1h，说明实际 reset 时区非 UTC
- 阈值触发后：调整时区源 + 补测试 + 更新 `alpha_vantage.py` 类注释
- Follow-up 条目已记入 `project_tradebot_status.md`

### 7.3 Risk/Follow-up: AV counter 重启归零影响

§3.1 的 AV counter 为 instance-level，进程重启归零（接受为 best-effort observability）。但观察期本身是 "频繁重启 + 调 prompt + 重跑" 的开发周期，若重启频繁到 counter 持续失准（warning 永远触不到，或过早触发），则 80% warning 的告警价值丧失。

**观察期监控**：记录一天内的重启次数 + 实际 warning 触发次数；若 counter 明显跟不上实际 AV 调用量，评估升级为 durable 存储（file / sqlite 本地缓存）。不在本批 scope，视观察期数据决定是否启动独立 PR。

### 7.4 Risk: PR C §3.6 audit 可能发现新泄露点

PR A 已确认仅 FRED / AV 在 URL 携带 api_key，其他源用 header 或无 auth。若 §3.6 grep 发现 FRED/AV 以外的路径打印 `request.url`，拆出独立 PR。

---

## 8. 设计决定（待评审确认）

以下决策在 spec 阶段由我提出，需在评审中确认或推翻：

| # | 决策 | 来源 | 需确认 |
|---|------|------|--------|
| D1 | N5 的 MA 输出改 `price vs MA: +X%`（完全事实化 + 消歧：避免 `price +X%` 被误读为 "MA 自身动 +X%"） | §2.2 #2 | 是否接受 |
| D2 | N5 的 BB 输出：带内用 "position: N% of band width"，脱带用 "X% above upper band" / "Y% below lower band" | §2.2 #4 + §2.3 | 是否接受 |
| D3 | AV counter 暴露采用选项 B（log warning at 80%） | §3.1 | 已决（`MetricsService` 语义边界理由已在 §3.1 展开，不走选项 A）；本条保留仅供溯源 |
| D4 | M2 双 clamp 的处置方案（删 tool clamp + 从 service 返回长度推断 footer） | §3.5 M2 | 方向确认 |
| D5 | M3 `change_7d_pct` 改 `float \| None` | §3.5 M3 + §7.1 Q1 | 是否接受类型变更 |
| D6 | HTF 三态字符串选择 `"insufficient data"`（与 ETF / MA 现有措辞家族对齐，避免引入新词） | §3.3 + §4.2 | 措辞是否合适 |
| D7 | 整体 PR 顺序 B → C 串行合并 | §1.4 | 是否允许并行 |

---

## 9. 参考

- N2 spec: `docs/superpowers/specs/2026-04-16-n2-market-news-design.md`
- N3 spec: `docs/superpowers/specs/2026-04-18-n3-macro-context-design.md`
- N3 plan: `docs/superpowers/plans/2026-04-18-n3-implementation-plan.md`
- **PR A 产出**：`docs/source-risk-matrix.md`（PR #15, 2026-04-19）
- 记忆：`project_n5_label_cleanup`、`project_tradebot_status`、`project_next_iteration_toolkit_expansion`

**文档 drift 说明**：`docs/source-risk-matrix.md` 是 PR A 产出（早于本 spec 定稿），与本 spec 存在以下 drift：

- L488/L512/L537 仍描述 "多行同 symbol 聚合求和"——本 spec §3.2 基于 `defillama.py:16-17` 实测改为 "first occurrence wins + drift 告警"（top-level `circulating` 已是 across-every-chain 合计，求和会重复计数）
- L539 提及 "M6 class-level throttle"——本 spec 已移除 M6
- 使用 "PR C §4.x" 编号——本 spec 实际为 §3.x

**回填策略**：**PR C 的第一个 commit 就回填 risk-matrix**（与代码改动同 PR），避免 drift 持续整个 PR C 开发周期。不拆独立 PR（避免小 PR 过多）；也不拖到 PR C 合并时（文档 drift 窗口过长）。
