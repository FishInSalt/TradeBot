# Adoption Checks

判断 agent **真的在用**工具的输出做推理，还是只调不用 / 跨工具手算 / fabricate 信号。

这是三维度里**最容易被 session log 量化**的维度。主档原则 2 / 3 / 8 都在这里落地。

## 1. 基本判定：调用 → reasoning 引用

session log 的格式：
```
▾ Action (<n> tools)
  ⚙ <tool_name>(<args>)
    <output...>

▾ Reasoning (<n> chars total)
  <agent narrative>
```

一次调用的 adoption 信号 = **紧接其后的 `▾ Reasoning` 块**里有没有引用该工具输出的**具体值 / 标签 / 字段名**。

### 三档信号

| 档位 | 表现 | 解读 |
|---|---|---|
| **强** | reasoning 引用 ≥ 2 个 verbatim 数值 + 显式 framing（"Range pos 73% 在 4h tf 已经偏高"） | agent 把工具输出 internalize 进推理链 |
| **弱** | 只提到工具的存在或一两个标签，没引数值（"checked alerts; nothing fired"） | 工具被当成存在性 check，输出价值未释放 |
| **零** | reasoning 完全不提该工具的任何输出 | 调用浪费；或 agent 把工具调用当 ritual |

## 2. grep 模板

### a. 找所有调用位置

```bash
SLOG=logs/session_<uuid>.log
grep -n "⚙ <tool_name>(" "$SLOG"
```

### b. 看一次完整调用 + 紧随 reasoning

```bash
# 拿第 N 次调用的行号 START，下一个 ⚙ 或 ▾ 的行号 END
awk '/⚙ <tool_name>\(/{i++; if(i==<N>){print NR; exit}}' "$SLOG"
sed -n '<START>,<END>p' "$SLOG"
```

### c. 量化 adoption — 反向：先抽 reasoning 块，再 grep 字段名

工具的输出里有哪些**独有的标签 / 字段名**？（"Range pos" 是 MTS 独有；"Last bar vol" + "× SMA" 是 GMD 独有；"breakeven" 是 get_position 独有）

```bash
# reasoning 里提到 "Range pos" 的次数
grep -c "Range pos\|range pos" "$SLOG"
# 工具调用次数
grep -c "⚙ get_multi_timeframe_snapshot(" "$SLOG"
# 比率 ≈ 字段级 adoption
```

### d. 反向：agent 是不是从 *别的* 工具拿了本该这里给的信号？（违反原则 3）

工具 A 输出字段 X。grep 看 agent reasoning 里出现 X 的语境，是不是经常出现在调用工具 B 之后？说明信号源混乱。

## 3. 检测 fabricate / 凭空信号

agent 有时会引用一个"看起来精确"但**实际工具没给**的数值。这是 fabricate，比 adoption 低更危险。

### 检测思路

抽几个 reasoning 中的具体数字（"buy taker 74%"、"MA200 in 4h at 78484"），反查紧前的工具输出有没有这个数。如果**没有**：
- 检查别的工具有没有？（信号源混乱）
- 检查是不是 agent 从前几个 cycle 记忆 / 计算 / 联想出来的？（fabricate 风险）
- 如果跨 cycle 引用，confidence interval 多宽？（原则 3 信号唯一权威来源被违反）

## 4. 检测跨工具手算（对账）

memory `project_tool_design_principles` 原则 5 + 8：高频 multi-call 拼凑是设计缺陷。

### 模式 1：工具 A 调完接着调工具 B，然后 reasoning 把两者数值相加 / 相减 / 比较

```
⚙ get_position()
  ... Unrealized: -123 USDT ...
⚙ get_account_balance()
  ... Free: 9876 USDT ...
▾ Reasoning
  My position is -123 against 9876 free = ~1.2% drawdown
```

这种"手算 X / Y"出现 ≥ 3 次（across cycles）就是议题：要么 A 没给 percentage（应给），要么需要新 derived field。

### 模式 2：同一信号在多个工具里都出现，agent 反复对账

`get_market_data()` 给一个 last price，`get_multi_timeframe_snapshot()` 也给一个 `Last (ticker @ ...)`。看 agent 是不是因为时间戳不同而手动确认它们一致——多余的 friction。

## 5. 量化报告 metric 建议

报告 Adoption 段落给这些数：

- **N_calls**: 本 session 总调用次数
- **N_cycles**: 涉及多少个不同 cycle
- **call_per_cycle**: N_calls / 总 cycle 数（多调指数）
- **adoption_rate**（强信号）= 紧随 reasoning 块至少引用 1 个 verbatim 数值 / 标签的次数 ÷ N_calls
- **fabricate_count**: reasoning 引用了工具应该给但**没给**的数值的次数（dangerous 信号）
- **manual_compute_count**: reasoning 手算了 X / Y / X - Y 的次数（multi-call 设计缺口信号）

不要追求精确——量级对就够。"adoption 8/27 = ~30%" 已经能说明问题。

## 6. 假阴性提防

agent 不引用不等于 adoption 低：

- 工具是 surveillance（如 `get_active_alerts`）：没新事件不报告是 *正常* 的；判定要看 alert 触发时是否被消化
- 工具是被动信息（如 `get_account_balance`）：余额不变时 reasoning 不会提，但**变化时**会。判定要看 *delta 时刻*
- 工具是周期性 sanity check：低 adoption 是设计预期，写报告时要说明

## 7. 假阳性提防

agent 引用具体数值不等于 adoption 高：

- 复述（"the volume was 1705"）≠ 推理依据（"the 1.84× SMA volume confirms the breakdown"）
- 报告时区分**复述** vs **推理依据**——后者才是工具真的进了决策链
- 引用同一数值多次不算多次——只算一次 adoption

## 总结

报告里 adoption 段落不是堆 grep 结果，是回答：**这工具被调了 N 次，有多少次它的输出真的影响了 agent 的决策？哪些字段从来没被引用？哪些信号 agent 不信任、自己绕去别处算了？** —— 这是工具能不能继续 justify 存在的依据。
