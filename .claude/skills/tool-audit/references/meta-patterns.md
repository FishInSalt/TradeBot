# Meta-pattern Scan Library

Phase 2 D6 强制：在全量 reasoning 上扫几类 ambient 现象。grep 命中率 ≥10% 的 pattern 即升级为**正式议题**，单点 outlier 不立。

每个 pattern 给：现象描述 + 含义 + regex（Python flavor，pcre 兼容）。在 reasoning_only 文本（已剔除工具输出）上跑。

## 1. Candle-close 时间戳推理（friction 指标）

**含义**：agent 在 reasoning 中做"哪根 bar 已收盘 / 何时收盘 / time 是 open 还是 close" 类心智翻译。命中率高 = 工具输出对 bar 状态的标注不足。

```python
patterns_candle_timing = [
    r"is this candle closed|candle should have closed|candle in progress|let me count.{0,15}candle",
    r"the .{2,8} candle.{0,30}closed at|hasn't closed yet|still in progress",
    r"last closed candle|latest closed.{0,5}bar|the candle that closed",
]
```

阈值经验：
- > 20% 的 reasoning blocks 命中 → P0 候选（agent 频繁做时间戳心智翻译）
- 5–20% → P1
- < 5% → 单点 outlier，不立议题

## 2. Ticker vs display-window 冗余（信号源混乱指标）

**含义**：tool 渲染同一信号在多个 section（如 `24h High/Low` ticker 段 vs `<N>-candle High-Low` market-context 段）。同 reasoning 同时引用两者 = agent 在做心智对账（违反原则 3）。

```python
patterns_window_conflict = {
    "ticker_24h": r"24h\s+(low|high)",
    "display_window_high_low": r"\d+-candle High-Low|\d+-candle range|candle High[- ]?Low",
}
# 各自命中数 + 两者并提的 blocks
```

阈值经验：display-window 命中数 / ticker-24h 命中数 < 5% → display-window 字段 dead，删除议题（P2）

## 3. Multi-call 同 Action 拼接（接口闭环 / 信号缺口指标）

**含义**：agent 在同一 Action 里多次调同一工具不同 args（如 GMD 同 action 调 5m + 15m）。说明该工具 default form 不够覆盖典型 multi-TF / multi-window 需求。

```python
# 在 (action_tools, reasoning) 配对后统计
def multi_call_count(records, target):
    return sum(1 for r in records if r["action_tools"].count(target) >= 2)
```

阈值经验：
- ≥ 5% actions multi-call → P1 候选（接口闭环议题，违反原则 5）
- < 5% → 观察，不立议题

## 4. Cross-cycle delta tracking（agent 记忆 / 历史依赖指标）

**含义**：agent 用"上 cycle X 是 a，本 cycle 是 b"的 delta 来推理。命中率高说明 agent **隐式依赖跨 cycle 记忆**。

```python
patterns_cross_cycle = [
    r"was \-?\d+(?:\.\d+)? .{0,5}(now|still)",
    r"from .{0,15}cycle|last cycle.{0,30}now",
    r"compared to .{0,15}(prior|last) cycle",
]
```

阈值经验：观察性指标，不直接立议题。但若 ≥ 20%，候选议题"工具可否 surface 跨 cycle delta"（如 OI delta 工具已做，本档案是其他工具的可借鉴范本）。

## 5. Manual per-bar hand-compute（输出粒度指标）

**含义**：agent 自己拿 High - Low / Close - Open / Open - 前 close 做手算。意味着工具没在便利位置提供该派生量。

```python
patterns_handcompute = [
    r"range was only|range of \d+|range \(\d+|range is \d+|range was \d+",
    r"\d{4,5}\.\d.{0,10}to \d{4,5}\.\d",
    r"~\d{2,4}\s*pts.{0,20}range|range.{0,10}~\d",
]
```

阈值经验：> 10% 命中 → P3 候选（工具可加 derived field）；不阻塞核心议题。

## 6. R:R / position-relative 手算

**含义**：agent 用 pts / ATR / equity ratio 做风控手算。多数情形是 position 工具的 scope 而非 perception 工具——但若 perception 工具被反复要求"配合"做风控（如 ATR-relative SL 距离），可能是 perception 输出格式可改进。

```python
patterns_rr = [
    r"R[:/]R\s*=|risk[/:]reward|reward[/:]risk",
    r"\d{2,4}\s*pts.{0,30}\d{2,4}\s*pts.{0,30}\d+(\.\d+)?[:/]1",
]
```

不直接立议题；用作交叉证据。

## 7. In-line 自纠正 / 阅读后困惑

**含义**：agent 在 reasoning 中显式自纠正读输出错误（"wait, that's the X candle vol, not Y" / "actually let me re-read"）。是 readability 议题的强信号。

```python
patterns_self_correct = [
    r"wait,? (let me|actually|the|that)",
    r"actually,? (let me|the|that)",
    r"re-?read|reread|let me look (again|at this more carefully)",
    r"hmm.{0,30}(wait|actually|let me)",
]
```

阈值经验：> 5% 是显著 readability 议题信号。但需 follow-up full read 看是不是因为该工具引起（也可能是 agent 在自纠正完全不相关的判断）。

## 使用注意

1. **在 reasoning_only 文件上跑**，不要含工具输出（会污染分母）
2. **regex 不是 ground truth**——命中后**必须**抽样完整 read 几个 block 验证是不是真的对应该含义
3. **不要为单次 audit 新增 regex**——若发现新 pattern，扩展本文件库后再跑（防止"为支持议题而构造 regex"反向操作）
4. **阈值是经验值**，不是金科玉律。具体工具可能有合理偏离（如 surveillance 工具的"延迟报告"模式天然有时间戳推理特征）
