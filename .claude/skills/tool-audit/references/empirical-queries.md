# Empirical Queries

session log + DB + scripts/ 的具体查询模板。复制粘贴改 `<tool_name>` / `<session_id>` 就能跑。

## 0. 通用路径常量

```bash
SLOG=$(ls -t logs/session_*.log | head -1)        # 最新 session log
DB=data/tradebot.db
TOOL=<tool_name>                                   # 改这里
SESSION_ID=$(basename "$SLOG" .log | sed 's/^session_//')
```

## 0.5 Schema 侦察（每次 audit 起手必做）

`scripts/` 下的辅助脚本写于过去某个时间点，列变动 / 新表 / 字段语义微调都可能让脚本 silently 漏数据。**每次 audit 第一件事先确认 DB 当前 schema**：

```bash
# tool_calls 表当前列（脚本如果还在用旧列名 / 没读新列就过时了）
sqlite3 "$DB" "PRAGMA table_info(tool_calls);"
sqlite3 "$DB" "PRAGMA table_info(agent_cycles);"

# 抽一行 raw 看实际数据形状（特别是 args / state_snapshot 等 JSON Text 列）
sqlite3 "$DB" "SELECT * FROM tool_calls WHERE session_id='$SESSION_ID' LIMIT 1;"
sqlite3 "$DB" "SELECT * FROM agent_cycles WHERE session_id='$SESSION_ID' LIMIT 1;"

# 看本 session 总共有哪些表非空（防遗漏新增观察表）
sqlite3 "$DB" "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name;"
```

**对账纪律**：

- 跑完 `scripts/tool_call_summary.py` 之后，**至少**用一次 raw `SELECT COUNT(*), SUM(status!='ok'), AVG(duration_ms) FROM tool_calls WHERE session_id=? AND tool_name=?` 对账。
- 数字不一致 → 信 DB raw，把脚本标为**疑似过时**记入报告"附录: out-of-scope findings"段。
- 发现 DB 有列脚本完全没读 → 同样进附录段。
- 严重 / 反复发生 → 对话告诉用户"建议立 backlog 修脚本"，让用户决定。

## 1. session log 端

### 总览

```bash
# 工具被调多少次
grep -c "⚙ $TOOL(" "$SLOG"

# 涉及的 cycle 数（每 cycle 第一次出现 ⚙ <tool>）
grep -nE "(Cycle [a-f0-9]+|⚙ $TOOL\()" "$SLOG" | awk '/Cycle/{c=$0} /⚙/{if(c){print c; c=""}}' | wc -l

# 所有 unique args 串
grep -oE "⚙ $TOOL\([^)]*\)" "$SLOG" | sort -u
```

### 抽样

```bash
# 随机抽 3 次调用的行号
grep -n "⚙ $TOOL(" "$SLOG" | shuf -n 3 | cut -d: -f1

# 看第 N 次调用 + 紧随 reasoning（END = 下一个 ⚙ 或 ▾ Reasoning/Decision）
N=1
START=$(grep -n "⚙ $TOOL(" "$SLOG" | sed -n "${N}p" | cut -d: -f1)
END=$(awk -v s=$START 'NR>s && /^(  ⚙|▾)/ {print NR; exit}' "$SLOG")
sed -n "${START},${END}p" "$SLOG"
```

### Adoption — verbatim 字段 grep

填 `<UNIQUE_LABEL>` 为工具输出的独有字段名（如 `Range pos`、`Last bar vol`、`Breakeven`）：

```bash
# adoption 上界估计：reasoning 提到该字段的次数
grep -c "<UNIQUE_LABEL>" "$SLOG"

# 调用比
CALLS=$(grep -c "⚙ $TOOL(" "$SLOG")
HITS=$(grep -c "<UNIQUE_LABEL>" "$SLOG")
echo "adoption proxy: $HITS hits / $CALLS calls"
```

### 找紧随 reasoning 块（用 awk 抽 "▾ Reasoning" 块）

```bash
# 工具调用之后的下一个 reasoning 块全文
awk -v tool="⚙ $TOOL(" '
  index($0, tool) {flag=1; next}
  flag && /^▾ Reasoning/ {capture=1; next}
  capture && /^▾/ {capture=0; flag=0}
  capture {print}
' "$SLOG"
```

## 2. DB 端（sqlite3 直查）

### args 分布（去重 + 计数）

```bash
sqlite3 "$DB" <<SQL
SELECT args, COUNT(*) AS n
FROM tool_calls
WHERE session_id = '$SESSION_ID' AND tool_name = '$TOOL'
GROUP BY args
ORDER BY n DESC
LIMIT 20;
SQL
```

### 时延 / 错误率

```bash
sqlite3 "$DB" <<SQL
SELECT
  COUNT(*) AS calls,
  SUM(CASE WHEN status != 'ok' THEN 1 ELSE 0 END) AS errors,
  ROUND(AVG(duration_ms), 1) AS avg_ms,
  MIN(duration_ms) AS min_ms,
  MAX(duration_ms) AS max_ms
FROM tool_calls
WHERE session_id = '$SESSION_ID' AND tool_name = '$TOOL';
SQL
```

### 跨 cycle 频率（每个 cycle 调多少次）

```bash
sqlite3 "$DB" <<SQL
SELECT cycle_id, COUNT(*) AS n
FROM tool_calls
WHERE session_id = '$SESSION_ID' AND tool_name = '$TOOL'
GROUP BY cycle_id
ORDER BY n DESC
LIMIT 10;
SQL
```

> 高 `n_per_cycle` (>3) 是设计缺口信号 — 原则 5。

### 错误明细

```bash
sqlite3 "$DB" <<SQL
SELECT cycle_id, args, error_type, created_at
FROM tool_calls
WHERE session_id = '$SESSION_ID'
  AND tool_name = '$TOOL'
  AND status != 'ok'
ORDER BY created_at;
SQL
```

### Note

- `tool_calls.args` 是 JSON 字符串，4000 char cap，`reasoning` key 被 strip（详见 `models.py`）
- `tool_calls` 表**不存输出**，输出只在 session log——这是 audit 必须双源的原因
- session_id 是 UUID；如果只知道 `name`，先查 `SELECT id FROM sessions WHERE name = '<name>';`

## 3. scripts/ 端

### tool_call_summary.py — per-tool 统计（推荐起手）

```bash
uv run python scripts/tool_call_summary.py --session "$SESSION_ID" --tool "$TOOL"
```

输出包含：calls、errors、p50/p95 时延、distinct args 数、last seen 时间。

### analyze_sim.py — 整 session 报告（看大局，不专门为某个工具）

```bash
uv run python scripts/analyze_sim.py --session "$SESSION_ID" --out /tmp/sim_report.md
```

慢，但能看到 PnL / 决策分布 / cost / 每工具 top10。

### diff_sim.py — 跨 sim 对比（如果用户给两个 session id）

```bash
uv run python scripts/diff_sim.py --base <id1> --head <id2>
```

适合 "本次 PR 改了 docstring，看看跨 sim 的 adoption 变化"。

## 4. 源码端

### 找工具定义

```bash
grep -rn "def $TOOL\b\|name=\"$TOOL\"" src/agent/
```

### 找关联 service / 计算实现

```bash
# 工具往往 import + 调 src/services/<x>.py 的函数
grep -rnE "from src\.services|import .*service" src/agent/tools_perception.py | head -10
# 工具内调用哪些 service 函数
grep -A2 "def $TOOL" src/agent/tools_perception.py | head -50
```

### 找 docstring 完整文本（含 Returns 块）

```bash
# 拿到 def 行号 LSTART，再看到下一个 def 之前
LSTART=$(grep -n "def $TOOL\b" src/agent/tools_perception.py | cut -d: -f1)
LEND=$(awk -v s=$LSTART 'NR>s && /^(async )?def / {print NR-1; exit}' src/agent/tools_perception.py)
sed -n "${LSTART},${LEND}p" src/agent/tools_perception.py
```

## 5. 组合 — 一次性 dump

如果要快速给用户看一份证据档案，可以一次跑完：

```bash
OUT=/tmp/tool_audit_$TOOL.md
{
  echo "# $TOOL — raw evidence"
  echo
  echo "## Calls"
  grep -c "⚙ $TOOL(" "$SLOG"
  echo
  echo "## Args distribution"
  sqlite3 "$DB" "SELECT args, COUNT(*) FROM tool_calls WHERE session_id='$SESSION_ID' AND tool_name='$TOOL' GROUP BY args ORDER BY 2 DESC LIMIT 10;"
  echo
  echo "## Sample call + reasoning"
  N=1
  START=$(grep -n "⚙ $TOOL(" "$SLOG" | sed -n "${N}p" | cut -d: -f1)
  END=$(awk -v s=$START 'NR>s && /^(  ⚙|▾)/ {print NR; exit}' "$SLOG")
  sed -n "${START},${END}p" "$SLOG"
} > "$OUT"
echo "wrote $OUT"
```

把 `$OUT` 当 working 文件用，整理完再落正式报告到 `.working/tool-audits/`。
