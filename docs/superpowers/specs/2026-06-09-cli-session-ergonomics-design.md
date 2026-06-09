# CLI Session Ergonomics — Design Spec

**Date:** 2026-06-09
**Iter:** `iter-cli-session-ergonomics` (mini-iter, direct-merge candidate)
**Scope:** 两项独立的 CLI 人机工程改动，源于用户使用反馈。均为 CLI ergonomics，零算法、零 exchange 行为改动。

---

## 1. 背景与动机

用户提出两个使用痛点：

1. **session log 文件开头无会话元数据** —— 文件名只有 `session_{uuid}.log`，要知道"哪个会话、何时开始"必须把 uuid 反查 DB；且 `SessionConsole` 以 **append 模式**打开，多次启动/resume 追加到同一文件，run 之间无分隔无时间戳，无法切分。
2. **wizard 会话菜单"创建新会话"恒在末尾** —— 编号 `len(sessions)+1` 随会话数增长，每次要扫表找那个越来越大的数字，易误操作。

两项都属 `src/cli/` 人机交互层，不在 CLAUDE.md 的 sim/scheduler 焦点内，但为低风险 QoL，按 mini-iter 推进。

---

## 2. 实证依据（决定 §4 的默认值）

查 `data/tradebot.db` + 15 个 session log（共 16 次启动 / header；用 `build_services` 每启动打印的 `^Exchange:` header 行数当"菜单启动次数"标记）：

| 动作 | 次数 | 占比 |
|---|---|---|
| 新建（new） | 15 | **~94%** |
| resume（菜单恢复） | 1（仅 #1，第二个 header 在第 3791 行） | ~6% |

- 排除史上首个会话（DB 空时直接进 wizard、不经菜单）后，**菜单展示过的 15 次启动里 14 次选 new、1 次 resume（93% = 14/15）**，且这是在当前 `default=1`（默认 resume）下发生的——即默认就是 resume，用户仍压倒性手动选 new。账目：15 文件/session、16 总 header、1 resume → 1 首会话（空库非菜单 new）+ 14 菜单 new + 1 菜单 resume。
- 长 sim（sim#10/#15，9MB/6.7MB log）header 都只有 1：macOS 睡眠是**冻结进程再唤醒**、不走菜单，故"睡醒续跑"不产生 resume 事件。
- 结论：**new 是常态，菜单默认应为 new**。

> caveat：此为单用户 sim 收集期的历史模式（大量短实验 + 少数长跑靠 OS-freeze 续命）。若日后转向"刻意 resume 长跑会话"，默认值需回看。

---

## 3. 改动一：session log 启动 header

### 行为
每次启动、在 `setup_session_logging()` 创建 `SessionConsole` 之后、`build_services()` 打印 config 行之前，向 session log 写一个 header block。字段：

- 会话名（`result.session_name`）
- 会话 id（`session_id`）
- symbol / mode（`result.exchange_type`）/ timeframe / scheduler interval
- 启动时间（UTC）
- `new` vs `resumed` 标记

示意（终端 + 文件双写，文件为 no-color）：

```
──────────────── Session: BTC sim #1 (resumed) ────────────────
ID:       38b29943-e37d-44c4-a1dc-2fee3f33d9b7
Symbol:   BTC/USDT:USDT   Mode: simulated   TF: 15m   Interval: 15m
Started:  2026-06-09 14:32:07 UTC
```

### 价值
- 让 forensic artifact **自包含**：不再 uuid↔DB 对账（tool-audit / W3-BUG forensic 都靠 session log）。与最近 iter `187873c`（session-log 自包含唤醒时间）方向一致。
- **顺带切分 append 边界**：每次启动写一遍 header，天然成为 run 与 run 之间的分隔符——覆盖**痛点①**的"何时开始 / run 边界"子项。注：append 拼接歧义本就**只在"同一文件多次启动"（resumed）时存在**（数据中 1/15），header 恰好在这些场景**全覆盖**——故此收益是"频率窄、但在问题发生处全覆盖"，非夸大；而"文件头自包含元数据"才是普适收益。

### 设计决定
- **解耦**：header 写入做成 helper（建议放 `logging_config.py`），**接受原始字段**（name/id/symbol/mode/tf/interval/is_new/started_at），**不依赖 `WizardResult`**，避免 logging 层耦合 wizard 类型。`app.py` 从 `result` 取字段后调用。
- **可测**：`started_at` 由调用方注入（`app.py` 传 `datetime.now(timezone.utc)`），单测传固定时间断言输出。
- **new/resumed 标记来源**：`select_or_create_session` 知道分支，需回传该标记（见 §4 plumbing）。
- **Mode 用全称**：header 写 `result.exchange_type` 原值（`simulated` / `okx`），**刻意**区别于 session list 走 `_EXCHANGE_DISPLAY`（`session_manager.py:245`）的缩写（`sim`）——self-contained forensic artifact 求精确不求省空间。
- **caveat（header 名 vs DB 名）**：header 取 `result.session_name`（去重前）；`_create_session`（`:208-219`）建会话时还会对 live DB 再去重追加 ` #N`，故理论上 header 显示名可能 ≠ DB 存储名。该名快照（`:342-344`）与建会话同处一次 `select_or_create_session` 调用、其间无其他写入，单用户 CLI 无并发写 → 实际几乎不可达，仅此记录、不额外 plumbing 规避。

---

## 4. 改动二：会话菜单 `0` = 新建且默认

### 行为
`_display_session_list` + `select_or_create_session`：

- **`0 = + New Session`** 固定键，置于表格**顶部**（不再是 `len(sessions)+1` 末尾）。
- 已有会话 `1..N` 按 `last_active_at DESC`（最近→最远，维持现状）。
- `IntPrompt.ask("Select session", default=0)` —— **默认 = 新建**（依据 §2）。
- 校验：`0 <= choice <= len(sessions)`，越界提示 `Please enter a number between 0 and {N}`。
- dispatch：`choice == 0` → 新建（`run_wizard`）；否则 `selected = sessions[choice - 1]` → resume。
- 无历史分支（当前 `:346`）不变：DB 空直接进 wizard。

### plumbing（同时服务 §3 的 new/resumed 标记）
`select_or_create_session` 当前返回 `(result, session_id)`，扩为 **`(result, session_id, is_new)`**：
- 新建路径（无历史 `:362` / 菜单 `:391`）→ `is_new=True`
- resume 路径（`:402`）→ `is_new=False`
- caller `app.py:1085` 解包三元组，把 `is_new` 传给 §3 的 header helper。
- 同步更新签名注解 `:327` `tuple[WizardResult, str]` → `tuple[WizardResult, str, bool]` 及 docstring `:328-329`「Returns (WizardResult, session_id)」。

---

## 5. 明确 defer（成痛点后统一做，不在本 iter）

以下为一组**耦合的触发型候选**，本 iter 不做：

- **wizard 通用 step-back（"b" 返回上一步）** —— 需把 `run_wizard` 线性流程重构为带 step index 的状态机、每个 prompt 接受 "b"（`IntPrompt`/`FloatPrompt` 会先拒掉非数字，须换裸 `Prompt` + 手动校验）、处理多 prompt 步与 real/sim 条件分支、前进/后退保留已填值。非 mini-iter，须单独 medium iter 走 PR。
- **Ctrl+C 两级 back-out**（wizard → 菜单；菜单 → 退出）。
- **wizard 取消优雅返回菜单**（现 `:387` 等取消是 `sys.exit(0)` 退程序）。

### 为何 `default=new`（§4）不需要先做上述护栏
- wizard **走完前零副作用**：`_create_session()` 只在 `run_wizard` 返回非 None（走完 Summary+确认+命名）后才执行。误按 Enter → 进 wizard → Ctrl+C → 返回 None → **不建任何会话**，代价仅"程序退出、重启一次"，且是罕见的 6% 误触。
- 反向更糟：若 `default=resume`，误按 Enter 会直接 restore 并把 agent loop 跑起来（真 live）。
- 故 `default=new` 的唯一毛刺（中途放弃 wizard 会退出程序、需重启）被定性为**罕见 + 无副作用 + 可延后**，等做 step-back 时一并抹平。

---

## 6. 实施 checklist（mini-iter，内嵌代替独立 plan 文档）

TDD：先写/改测试断言，再改实现。

1. `logging_config.py`：新增 `write_session_header(sc, *, name, session_id, symbol, mode, timeframe, interval_min, is_new, started_at)`，双写 header block。注：`interval_min` 是 `int`（分钟，来自 `result.scheduler_interval_min`），helper 内需 `f"{interval_min}m"` 格式化，勿把 int 直接拼进字符串。
2. `session_manager.py::_display_session_list`：New 行移至顶部、编号 `0`。
3. `session_manager.py::select_or_create_session`：返回扩为三元组 `(result, session_id, is_new)`；同步签名注解 `:327`→`tuple[WizardResult, str, bool]` + docstring `:328-329`；`default=0`；校验 `0..N`；dispatch `choice==0`。
4. `app.py`：`:1085` 解包三元组；`setup_session_logging` 后调用 `write_session_header(...)`（`started_at=datetime.now(timezone.utc)`）。
5. 测试：
   - `test_logging_config.py`：header 含各字段、`new`/`resumed` 文案、Mode 全称、固定 `started_at` 格式断言。
   - `test_session_manager.py`：菜单含顶部 `0 + New Session`、`default=0`、`choice=0`→新建、`choice=k`→`sessions[k-1]`、越界提示 `0 and N`、返回三元组。`is_new` 两分支显式断言：restore 测试（`:364`，IntPrompt=1）断 `is_new is False`；new 测试（`:471`）断 `is_new is True`。
   - **现存测试同步**（plumbing 破坏点）：解 2 元组处 `:320 / :366 / :473` 改解 3 元组；`:471`「new via menu」测试 mock `IntPrompt.ask` 从 `N+1`(=2) 改为 `0`。
6. 全套 `pytest` 绿（基线 29 passed in 两文件）。

## 7. 合并与纪律
- mini-iter **直 merge**（feature 分支 → main，不开 GitHub PR）。
- 守三纪律：spec + diff 用户审阅 / 测试全绿 / memory anchor。
- commit 顺序：本 spec 先作独立 commit，其后 impl commits。
