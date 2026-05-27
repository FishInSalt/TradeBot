# Correctness Checks

判断工具**算得对不对**。这是底线（原则 1.4 / 主档 §1.4），不是设计议题。

正确性议题一律 P0 或 P1。能 spot-check 就 spot-check，能用 invariant 就用 invariant，不要靠"看起来不对"。

## 1. 时间对齐 / closed-bar 检测

加密期货是 24/7，bar 的 "current bar" 含义敏感。memory `project_g_calc_audit_closure` G-1/G-2 议题都是因为算到了未收盘 bar。

### Checklist

- [ ] 工具的窗口型计算（ATR / RSI / MA / BB / pivots / 高低范围 / 期间统计）用的是 **closed bars only**，还是把当前未收盘 bar 也算进去？
- [ ] 看源码：fetch 出的 bar 列表是 `[:-1]` 切片掉最后一根，还是全用？
- [ ] 输出里"最近 N 根 candle"是不是真的是 N 根**完整收盘**的？标题文字 vs 实际切片有没有 drift？
- [ ] 跨工具一致：`get_market_data` 和 `get_higher_timeframe_view` 对"当前 bar"的定义相同吗？

### Spot-check 思路

从 session log 抽一个具体时刻：例如 `13:39:02 UTC`，对 `15m` timeframe。
- 该时刻最近一根 *未收盘* bar 是 13:30 open（13:30–13:45 区间，未关）
- 最近一根 *已收盘* bar 是 13:15 open（13:15–13:30 区间）
- 工具输出说 "Last bar vol: 1705.3" → 这是 13:15 还是 13:30 的？看 source 切片决定

## 2. NaN / 边界 / 早期数据不足

### Checklist

- [ ] 数据点不够（N < window）时，工具返回什么？NaN / 0 / 抛错 / 缩窗？哪个是正确语义？
- [ ] 除零：BB middle = lower 时 position % 怎么算？vol SMA = 0 时 ratio 怎么算？
- [ ] 负值合法吗？百分比应该 [0, 100] 还是 (-∞, +∞)？session log 里出现过越界吗？
- [ ] `None` 字段被渲染成什么？`null` / `—` / 空字符串 / 省略整行？跨字段一致吗？

## 3. 单位 / scale / 数量级

### Checklist

- [ ] 价格 / notional / pnl 单位一致用 USDT？没混进 BTC 计价？
- [ ] 百分比是 `0.05` 还是 `5.0`？docstring 与渲染一致吗？
- [ ] 时延是 ms 还是 s？
- [ ] 数量单位是合约张数（contracts）还是底层币（BTC）？OKX 永续 1 contract = 0.001 BTC（contract_size），fee 计算时易错
- [ ] ratio 字段后缀是不是带 `×`（区别于乘号），且方向（>1 表"高于均值"）是 agent 直觉对应的方向？

## 4. 一致性 invariants

### Checklist

- [ ] 同一工具内：`p50 ≤ p95`、`min ≤ avg ≤ max`、`upper ≥ middle ≥ lower`、`high ≥ open / close / low ≥ low`
- [ ] 跨工具：`get_position()` 的 PnL 和 `get_account_balance()` 的 unrealized 数值一致吗？`get_market_data()` 的 ticker 和 `get_multi_timeframe_snapshot()` 的 `Last (ticker @ ...)` 一致吗？
- [ ] 跨 cycle：同一时间点附近两次调用结果应该相近（除非市场真的剧烈变化）。session log 里能看到吗？
- [ ] state-delta 字段（`(was X)`、`X → Y`）：当前值和上次调用 / DB 记录一致吗？

## 5. Phase shift / lookahead

memory `project_g_calc_audit_closure` G-6 就是 OI delta 的 phase shift 议题。

### Checklist

- [ ] "delta" / "change" / "Δ" 类字段：比较的两个时点是不是同步采样的？比如 OI 当前快照 vs 1h 前快照，能确保 1h 前那个真的是 1h 前而不是上次 cycle 的偏差？
- [ ] "since last cycle" 类聚合：上 cycle 的 timestamp 准吗？跨进程重启后还对吗？
- [ ] 没有 lookahead：算 5m ATR 的时候用到的 5m bar 不应包含 1m bar 的实时信息

## 6. Fee / margin / liquidation（execution 类工具特有）

memory `project_sim_alignment` / `project_iter2_mock_fidelity_lesson` 是这个领域翻车的经验来源。

### Checklist

- [ ] taker / maker fee rate 写对了？（OKX 永续 default taker 0.05%）
- [ ] Breakeven 公式：`entry × (1 + 2 × fee_rate)` 对 long round-trip taker，short 反向
- [ ] Notional / margin / leverage 三者关系：`notional = price × contracts × contract_size`、`margin = notional / leverage`
- [ ] partial close 时 entry / leverage / fee 计算是否破坏？memory `project_pr57_followups` 提到 partial close → is_full_close 解耦的 OKX live blocker

## 7. 如何下结论

**P0 = agent 已经被错的数字误导做了错决策**。session log 里能找到证据链：错值 → reasoning 引用 → 决策 → 不利结果。

**P1 = 计算实证错，但 adoption 低 / agent 没用到**。仍是 bug，但优先级让位给 agent 真在用的 P0。

**P2 = 边界情形可能错**（NaN 越界 / scale 罕见）但本次 session 没复现。

**P3 = 算法等价改进**（如更精准的 ATR 算法），现版本不是 bug。

不能 spot-check 的就用 invariant；invariant 也用不上的就老实说"未能在本次 audit 验证"，不要瞎下结论。
