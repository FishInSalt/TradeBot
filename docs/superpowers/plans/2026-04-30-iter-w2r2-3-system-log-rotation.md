# R2-3 system.log Rotation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 把 `src/cli/logging_config.py` 的 `logging.FileHandler` 替换为自定义 `TimestampedRotatingFileHandler(maxBytes=100MB, backupCount=30, encoding='utf-8')`，bound 单文件大小（解决 W2 24-48h × 22MB/2h ≈ 264-528 MB 单文件 grep 痛点），归档名带微秒精度时间戳（直接 `ls logs/` 看清时间窗口），regex filter 物理消除"用户手动 cp `system.log.bak` 被静默删"的 foot-gun。

**Architecture:** cli 层单点改动 + 一个自定义 handler subclass：
- **Subclass** (`logging_config.py` 顶部，`SessionConsole` 之前)：`TimestampedRotatingFileHandler` 继承 `RotatingFileHandler`，仅 override `doRollover()` —— 时间戳归档命名 + glob+regex+mtime 修剪。其他逻辑（size 检测 / setLevel / setFormatter / close）全部走父类 stdlib 实现。
- **Module-level constant** `_ARCHIVE_SUFFIX_RE = re.compile(r"\d{8}-\d{6}-\d{6}$")` —— 修剪 filter 用，仅匹配本 handler 产出的微秒时间戳后缀。
- **Handler swap** (`logging_config.py:41`)：`logging.FileHandler(log_dir / "system.log")` → `TimestampedRotatingFileHandler(log_dir / "system.log", maxBytes=100*1024*1024, backupCount=30, encoding="utf-8")`。其余 `setLevel(DEBUG)` / `setFormatter(...)` 不动（API 经继承链一致）。

**Tech Stack:** Python 3.13 stdlib 唯一 (`logging.handlers.RotatingFileHandler` / `glob` / `os` / `re` / `datetime.datetime.strftime("%Y%m%d-%H%M%S-%f")`) / pytest 8.x。无新外部依赖。

**Spec reference:** `docs/superpowers/specs/2026-04-30-iter-w2r2-3-system-log-rotation-design.md`（已 commit `e9516f8`）

**Baseline (locked 2026-04-30):** **936 tests collected** via `uv run pytest --collect-only -q`（933 pass + 3 skip）。Target after R2-3: **+4 net = 940 tests collected**, 937 pass + 3 skip, 0 failed。

**净增准确计数**:

| 测试名 | 状态 |
|---|---|
| `test_setup_system_logging_uses_timestamped_rotating_file_handler` | 新增 (Task 1, drift guard) |
| `test_setup_system_logging_rotation_creates_timestamped_archive` | 新增 (Task 2, T2 rotation 行为) |
| `test_setup_system_logging_rotation_prunes_oldest_beyond_backup_count` | 新增 (Task 3, T3 pruning) |
| `test_setup_system_logging_rotation_ignores_unrelated_files` | 新增 (Task 4, T4 regex filter) |
| **净增** | **+4** |

**Branch:** `feature/iter-w2r2-3-system-log-rotation`（已建于 main `cc43c86`，含 spec commit `e9516f8`）

---

## File Touch Summary

| File | Change | Where |
|---|---|---|
| `src/cli/logging_config.py` | +5 imports / +20 行 module-level regex + subclass / 替换 6 行 handler 实例化 | 顶部 imports + class 前 + L41 |
| `tests/test_logging_config.py` | +4 测试 append at end | 末尾 (~95 行) |

**净增测试**：4 新增 - 0 删除 = **+4 净增 → 940 collected**。

---

## Task 1: Source impl + T1 drift guard (atomic source change)

**Files:**
- Modify: `src/cli/logging_config.py` (imports + new subclass + handler swap)
- Test: `tests/test_logging_config.py` (new test appended at end)

**Note:** R2-3 的 source 是不可分割的原子块（subclass 必须有完整 `doRollover` 才能让任何测试有意义）。因此 Task 1 走 TDD red-green：先写 T1 让它因 `ImportError` 失败，然后一次性提交完整 source 让它通过。Tasks 2-4 是覆盖测试，源码已就位时只做"绿色补充"。

- [ ] **Step 1: Write the failing T1 drift guard test**

Append to `tests/test_logging_config.py` end of file:

```python
def test_setup_system_logging_uses_timestamped_rotating_file_handler(tmp_path: Path):
    """R2-3 drift guard: file handler must be TimestampedRotatingFileHandler with
    maxBytes=100MB and backupCount=30.
    """
    from src.cli.logging_config import TimestampedRotatingFileHandler

    log_dir = tmp_path / "logs"
    setup_system_logging(debug=False, log_dir=log_dir)

    root = logging.getLogger()
    file_handlers = [h for h in root.handlers if isinstance(h, TimestampedRotatingFileHandler)]
    assert len(file_handlers) == 1, (
        f"expected exactly 1 TimestampedRotatingFileHandler, got {len(file_handlers)} "
        f"(all handlers: {[type(h).__name__ for h in root.handlers]})"
    )
    fh = file_handlers[0]
    assert fh.maxBytes == 100 * 1024 * 1024, (
        f"expected maxBytes=100MB ({100 * 1024 * 1024}), got {fh.maxBytes}"
    )
    assert fh.backupCount == 30, f"expected backupCount=30, got {fh.backupCount}"
```

**Imports note**: file top L4-11 已 import `logging` / `from pathlib import Path` / `import pytest` / `from src.cli.logging_config import SessionConsole, setup_system_logging, setup_session_logging`，无需新增。

- [ ] **Step 2: Run T1 to verify it fails**

Run: `uv run pytest tests/test_logging_config.py::test_setup_system_logging_uses_timestamped_rotating_file_handler -v`

Expected: **FAIL** with `ImportError: cannot import name 'TimestampedRotatingFileHandler' from 'src.cli.logging_config'`。这是 RED 状态：subclass 尚未定义。

- [ ] **Step 3: Modify source — add imports + subclass + handler swap**

Edit `src/cli/logging_config.py`:

**3a. Replace imports (file top, lines 1-7)**:

```python
# Before
# src/cli/logging_config.py
from __future__ import annotations

import logging
from pathlib import Path

from rich.console import Console
from rich.logging import RichHandler

# After
# src/cli/logging_config.py
from __future__ import annotations

import glob
import logging
import os
import re
from datetime import datetime
from logging.handlers import RotatingFileHandler
from pathlib import Path

from rich.console import Console
from rich.logging import RichHandler
```

**3b. Insert module-level constant + subclass class (between rich imports and `class SessionConsole`)**:

```python
# Module-level constant: archive suffix must match this exact pattern to be
# considered a rotation artifact (vs. user-placed files like system.log.bak).
_ARCHIVE_SUFFIX_RE = re.compile(r"\d{8}-\d{6}-\d{6}$")  # YYYYMMDD-HHMMSS-ffffff


class TimestampedRotatingFileHandler(RotatingFileHandler):
    """RotatingFileHandler whose archive files carry a microsecond-precision
    timestamp suffix instead of stdlib's sequential .1/.2/... numbering.

    Archive name format: ``<baseFilename>.YYYYMMDD-HHMMSS-ffffff``
    (e.g., ``system.log.20260430-160027-747099``).

    The timestamp marks when the file was rotated out (i.e., the END of the
    archive's data window). Microsecond resolution makes intra-process
    collisions practically impossible.

    Pruning keeps the newest ``backupCount`` archives by mtime; only files
    matching the strict timestamp suffix are eligible — user-placed files
    like ``system.log.bak`` are ignored (preserved across rotations).
    """

    def doRollover(self) -> None:
        if self.stream:
            self.stream.close()
            self.stream = None
        ts = datetime.now().strftime("%Y%m%d-%H%M%S-%f")
        dfn = f"{self.baseFilename}.{ts}"
        os.rename(self.baseFilename, dfn)
        if self.backupCount > 0:
            base_prefix_len = len(self.baseFilename) + 1  # +1 for the '.'
            archives = sorted(
                (
                    p for p in glob.glob(f"{self.baseFilename}.*")
                    if _ARCHIVE_SUFFIX_RE.fullmatch(p[base_prefix_len:])
                ),
                key=os.path.getmtime,
            )
            while len(archives) > self.backupCount:
                os.remove(archives.pop(0))
        if not self.delay:
            self.stream = self._open()
```

**3c. Replace handler instantiation (line 41)**:

```python
# Before
    # System log file — all levels
    file_handler = logging.FileHandler(log_dir / "system.log")
    file_handler.setLevel(logging.DEBUG)

# After
    # System log file — all levels (rotated by size with microsecond-stamped archives)
    file_handler = TimestampedRotatingFileHandler(
        log_dir / "system.log",
        maxBytes=100 * 1024 * 1024,  # 100 MB per file
        backupCount=30,              # ~30 archives → 3 GB cap, ~1 month at sim rate
        encoding="utf-8",
    )
    file_handler.setLevel(logging.DEBUG)
```

**Verify**: `setFormatter(...)` 调用（原 line 43-46）保持不变，紧跟 `setLevel`。`file_handler.close()` 不需要任何子类侧改动（继承自 `FileHandler.close()`）。

- [ ] **Step 4: Run T1 to verify it passes**

Run: `uv run pytest tests/test_logging_config.py::test_setup_system_logging_uses_timestamped_rotating_file_handler -v`

Expected: **PASS**.

- [ ] **Step 5: Run full logging_config test file (现有 6 测试 + T1 = 7 都应绿)**

Run: `uv run pytest tests/test_logging_config.py -v`

Expected:
- `test_session_console_writes_to_file PASSED`
- `test_session_console_appends PASSED`
- `test_session_console_no_ansi_in_file PASSED`
- `test_session_console_flush_on_print PASSED`
- `test_session_console_double_close PASSED`
- `test_setup_system_logging_creates_log_dir PASSED`
- `test_setup_system_logging_writes_to_system_log PASSED`
- `test_setup_system_logging_debug_mode PASSED`
- `test_setup_system_logging_non_debug_filters_info PASSED`
- `test_setup_session_logging_returns_session_console PASSED`
- `test_setup_system_logging_uses_timestamped_rotating_file_handler PASSED` ← new

11 tests passed (= file pre-baseline 10 + T1)。

- [ ] **Step 6: Commit Task 1**

```bash
git add src/cli/logging_config.py tests/test_logging_config.py
git commit -m "$(cat <<'EOF'
feat(iter-w2r2-3): TimestampedRotatingFileHandler + handler swap (T1)

Replace `logging.FileHandler(system.log)` with custom subclass that:
- Caps single-file size at 100 MB (RotatingFileHandler base class)
- Renames active log to `system.log.YYYYMMDD-HHMMSS-ffffff` on rollover
- Retains last 30 archives (3 GB cap, ~1 month at sim rate)
- Filters pruning via module-level _ARCHIVE_SUFFIX_RE — user-placed
  files like system.log.bak survive across rotations

Microsecond timestamp resolution makes intra-process collisions
practically impossible (spec REPL verified 5 rapid rollovers all
distinct, intervals 150-300μs).

Drift guard (T1): asserts handler is TimestampedRotatingFileHandler
with maxBytes=100MB and backupCount=30. Tasks 2-4 will append
behavior tests (rotation / pruning / regex filter) on this base.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 2: T2 rotation 单次行为测试

**Files:**
- Test: `tests/test_logging_config.py` (new test appended at end)

**Note:** Task 2-4 是行为覆盖测试 — Task 1 的 source 已实现完整 doRollover，这些测试理论上写完即绿。每个测试聚焦一个独立行为维度，单独 commit 便于回看。

- [ ] **Step 1: Append T2 rotation behavior test**

Append to `tests/test_logging_config.py` end of file:

```python
def test_setup_system_logging_rotation_creates_timestamped_archive(tmp_path: Path):
    """R2-3 T2: doRollover() renames active log to a microsecond-stamped archive
    (system.log.YYYYMMDD-HHMMSS-ffffff) and creates fresh system.log.
    """
    import re
    from src.cli.logging_config import TimestampedRotatingFileHandler

    log_dir = tmp_path / "logs"
    setup_system_logging(debug=False, log_dir=log_dir)

    test_logger = logging.getLogger("test.r2_3.rotation")
    test_logger.info("before rollover")

    fh = next(
        h for h in logging.getLogger().handlers
        if isinstance(h, TimestampedRotatingFileHandler)
    )
    fh.doRollover()
    test_logger.info("after rollover")

    # Active file exists, contains only post-rollover content
    active = log_dir / "system.log"
    assert active.exists()
    active_content = active.read_text()
    assert "after rollover" in active_content
    assert "before rollover" not in active_content

    # Exactly 1 archive with timestamped suffix
    archives = sorted(log_dir.glob("system.log.*"))
    assert len(archives) == 1, (
        f"expected 1 archive, got {[a.name for a in archives]}"
    )
    suffix = archives[0].name[len("system.log."):]
    assert re.fullmatch(r"\d{8}-\d{6}-\d{6}", suffix), (
        f"archive suffix {suffix!r} does not match YYYYMMDD-HHMMSS-ffffff"
    )
    assert "before rollover" in archives[0].read_text()
```

- [ ] **Step 2: Run T2 to verify it passes**

Run: `uv run pytest tests/test_logging_config.py::test_setup_system_logging_rotation_creates_timestamped_archive -v`

Expected: **PASS**（Task 1 source 已含 doRollover 完整实现）。

- [ ] **Step 3: Commit Task 2**

```bash
git add tests/test_logging_config.py
git commit -m "$(cat <<'EOF'
test(iter-w2r2-3): T2 rotation 单次行为 — 时间戳归档名 + 干净新活跃文件

doRollover() 行为锁定：
- 旧 active 被重命名为 system.log.YYYYMMDD-HHMMSS-ffffff (regex 锁格式)
- 新 active system.log 是干净文件（rollover 前后内容分流）
- 归档保留 rollover 前内容，活跃文件保留 rollover 后内容

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 3: T3 pruning 行为测试

**Files:**
- Test: `tests/test_logging_config.py` (new test appended at end)

- [ ] **Step 1: Append T3 pruning test**

Append to `tests/test_logging_config.py` end of file:

```python
def test_setup_system_logging_rotation_prunes_oldest_beyond_backup_count(tmp_path: Path):
    """R2-3 T3: when archive count exceeds backupCount, oldest (by mtime) is pruned.
    """
    import time
    from src.cli.logging_config import TimestampedRotatingFileHandler

    log_dir = tmp_path / "logs"
    setup_system_logging(debug=False, log_dir=log_dir)

    fh = next(
        h for h in logging.getLogger().handlers
        if isinstance(h, TimestampedRotatingFileHandler)
    )
    # Shrink backupCount for fast test (production is 30)
    fh.backupCount = 2

    test_logger = logging.getLogger("test.r2_3.prune")
    contents = ["v1", "v2", "v3"]
    for msg in contents:
        test_logger.info(msg)
        # Sleep to ensure distinct mtimes on coarse-grained filesystems
        time.sleep(0.01)
        fh.doRollover()

    archives = sorted(log_dir.glob("system.log.*"))
    assert len(archives) == 2, (
        f"expected 2 archives after 3 rollovers with backupCount=2, "
        f"got {[a.name for a in archives]}"
    )
    # Oldest content ("v1") should be pruned; newest two retained
    surviving = "\n".join(a.read_text() for a in archives)
    assert "v1" not in surviving, f"oldest content not pruned: {surviving!r}"
    assert "v2" in surviving and "v3" in surviving
```

- [ ] **Step 2: Run T3 to verify it passes**

Run: `uv run pytest tests/test_logging_config.py::test_setup_system_logging_rotation_prunes_oldest_beyond_backup_count -v`

Expected: **PASS**.

- [ ] **Step 3: Commit Task 3**

```bash
git add tests/test_logging_config.py
git commit -m "$(cat <<'EOF'
test(iter-w2r2-3): T3 pruning — backupCount 越界时按 mtime 删最旧

设 backupCount=2 + 3 次连续 doRollover：
- 总归档数应严格 = 2（最旧被删）
- 最旧内容 "v1" 被删，"v2"/"v3" 保留
- 防回退：未来若 doRollover 漏写 prune loop，本测试立即失败

time.sleep(0.01) 在每次 rollover 间确保 mtime distinct（即使
coarse-grained FS 也能分），单测耗时 < 100ms。

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 4: T4 regex filter 测试（外来文件不被卷入修剪）

**Files:**
- Test: `tests/test_logging_config.py` (new test appended at end)

- [ ] **Step 1: Append T4 regex filter test**

Append to `tests/test_logging_config.py` end of file:

```python
def test_setup_system_logging_rotation_ignores_unrelated_files(tmp_path: Path):
    """R2-3 T4: pruning regex filter excludes user-placed files like system.log.bak,
    even when their mtime is older than rotation archives.
    """
    import time
    from src.cli.logging_config import TimestampedRotatingFileHandler

    log_dir = tmp_path / "logs"
    setup_system_logging(debug=False, log_dir=log_dir)

    # Drop a user-placed backup BEFORE any rotation, so its mtime is oldest
    bak = log_dir / "system.log.bak"
    bak.write_text("user manual backup")
    time.sleep(0.01)  # ensure distinct mtime vs upcoming archives

    fh = next(
        h for h in logging.getLogger().handlers
        if isinstance(h, TimestampedRotatingFileHandler)
    )
    fh.backupCount = 2

    test_logger = logging.getLogger("test.r2_3.unrelated")
    # 3 rollovers > backupCount=2, would prune oldest if .bak were eligible
    for msg in ["v1", "v2", "v3"]:
        test_logger.info(msg)
        time.sleep(0.01)
        fh.doRollover()

    # .bak must survive (regex filter excludes non-timestamp suffixes)
    assert bak.exists(), "user-placed system.log.bak was incorrectly pruned"
    assert bak.read_text() == "user manual backup", "bak content corrupted"

    # Timestamped archives still capped at 2
    timestamped = [
        p for p in log_dir.glob("system.log.*")
        if p.name != "system.log.bak"
    ]
    assert len(timestamped) == 2, (
        f"expected 2 timestamped archives, got {[p.name for p in timestamped]}"
    )
```

- [ ] **Step 2: Run T4 to verify it passes**

Run: `uv run pytest tests/test_logging_config.py::test_setup_system_logging_rotation_ignores_unrelated_files -v`

Expected: **PASS**（spec §3.2 设计的 `_ARCHIVE_SUFFIX_RE.fullmatch` filter 物理消除该 foot-gun，spec REPL 已实测）。

- [ ] **Step 3: Commit Task 4**

```bash
git add tests/test_logging_config.py
git commit -m "$(cat <<'EOF'
test(iter-w2r2-3): T4 regex filter — 外来文件不被卷入修剪

用户运维场景：cp system.log system.log.bak 临时备份后任由 bot
继续跑。stock RotatingFileHandler 的 glob "<base>.*" 修剪会把
.bak / .old / 旧 stock 数字归档全部按 mtime 卷入 → 默默删除。

修法：_ARCHIVE_SUFFIX_RE.fullmatch(r"\d{8}-\d{6}-\d{6}$") 仅纳入
本 handler 产出的微秒时间戳归档，外来文件不入修剪队列。

测试：pre-place system.log.bak（mtime 最旧）+ 3 rollovers
(backupCount=2) → .bak 内容完好 + 时间戳归档恰 2 个。

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 5: Regression sanity + AC verification

**Files:** （只跑测试，不改代码）

- [ ] **Step 1: Full pytest sweep**

Run: `uv run pytest -q 2>&1 | tail -10`

Expected: `937 passed, 3 skipped` (= 940 collected, +4 net vs baseline 936)。

净增计数详见 plan 头部 Baseline 段下表（4 新增 + 0 删除 = +4）。

- [ ] **Step 2: 测试文件级覆盖（AC4-AC8）**

Run: `uv run pytest tests/test_logging_config.py -v 2>&1 | tail -20`

Expected 14 tests passed:
- 既有 10 (`test_session_console_*` × 5 + `test_setup_*` × 4 + `test_setup_session_logging_returns_session_console`)
- 新增 4 (`test_setup_system_logging_uses_timestamped_rotating_file_handler` / `test_setup_system_logging_rotation_creates_timestamped_archive` / `test_setup_system_logging_rotation_prunes_oldest_beyond_backup_count` / `test_setup_system_logging_rotation_ignores_unrelated_files`)

- [ ] **Step 3: 文件 diff 边界 verify（AC10）**

Run: `git diff main --stat`

Expected: 仅以下 4 文件变更：

```
docs/superpowers/specs/2026-04-30-iter-w2r2-3-system-log-rotation-design.md  | + ~410 行
docs/superpowers/plans/2026-04-30-iter-w2r2-3-system-log-rotation.md         | + ~440 行
src/cli/logging_config.py                                                    | + ~28 行
tests/test_logging_config.py                                                 | + ~95 行
```

`scripts/observation_token_audit.py` / 其他 cli 文件 / agent 任何文件**未**变更（AC10 显式断言）。

- [ ] **Step 4: 与 R2-9 smoke 对照点（informational）**

R2-3 落地后 R2-9 跑 smoke 时验证维度（不在本 task 内做，仅记录到 PR description）：

(a) `logs/system.log.YYYYMMDD-HHMMSS-ffffff` 在 size 阈值后出现 — 需 smoke 跑到 100 MB 触发首次 rollover  
(b) 当日 `system.log` 文件继续接收日志无中断  
(c) audit 脚本 `--last N` 仍正确解析当前 `system.log`（默认路径不破）  
(d) `ls -la logs/` 时间戳归档名直观可读

注：smoke 期间触发 rollover 需 logging 量到 100 MB（按 sim#4 rate 约 9 小时活跃），R2-9 24-48h 必触发。如 smoke 耗时不够 9 小时，可临时 monkey-patch maxBytes 验证（不进 PR，仅 R2-9 verification 用）。

- [ ] **Step 5: Self-review 清单（commit 前最后一次）**

人工核对：

- [ ] 4 个新测试全部含 `R2-3` docstring tag
- [ ] commit 序列：spec doc → plan doc → Task 1 source+T1 → Task 2 T2 → Task 3 T3 → Task 4 T4，共 6 个 commits（plan doc 在下一步落，Task 5 不 commit）
- [ ] 没有调试 `print` / `breakpoint` / 临时注释
- [ ] `_ARCHIVE_SUFFIX_RE` regex 与测试断言 `\d{8}-\d{6}-\d{6}` 字符串一致（grep 同时核对 spec / source / 4 测试 4 处）
- [ ] commit messages 不含 spec 的 W2 阻塞 P0 列表（避免 scope creep 印象）

- [ ] **Step 6: Task 5 不需要单独 commit**

Task 5 仅验证，无代码改动。Verification log 写进 PR description。

---

## Self-Review

**1. Spec coverage**:

| Spec §2.1 In-scope 项 | Plan 覆盖 |
|---|---|
| G1 subclass class def + override doRollover 含 regex filter | Task 1 Step 3b ✅ |
| G2 handler swap | Task 1 Step 3c ✅ |
| G3 imports (glob/os/re/datetime/RotatingFileHandler) | Task 1 Step 3a ✅ |
| G4 drift guard test (T1) | Task 1 Step 1 ✅ |
| G5 rotation 单次测试 (T2) | Task 2 Step 1 ✅ |
| G6 pruning 测试 (T3) | Task 3 Step 1 ✅ |
| G7 regex filter 测试 (T4) | Task 4 Step 1 ✅ |
| G8 setup 内部 close 循环兼容性（描述性）| spec §3.5 T5 段 + 实跑 verify by Task 5 Step 2 现有 10 测试全绿 ✅ |

**2. Placeholder scan**: 无 TBD / TODO / "implement later"。所有代码块完整，所有命令含 expected output。

**3. Type / 字符串一致性**:

- `_ARCHIVE_SUFFIX_RE = re.compile(r"\d{8}-\d{6}-\d{6}$")` —— 在 source `logging_config.py` 顶部定义一处
- 测试断言 regex `r"\d{8}-\d{6}-\d{6}"` —— Task 2 T2 一处（含 fullmatch 隐式 `^...$`）
- `datetime.now().strftime("%Y%m%d-%H%M%S-%f")` —— source 一处
- `100 * 1024 * 1024` (= 104857600) —— source `maxBytes=` 一处 + 测试 T1 断言一处
- `30` (backupCount) —— source 一处 + 测试 T1 断言一处
- 所有数值在 source 与 test 间字面值一致 ✅

**4. Net test count 校验**:

- Task 1: +1 (T1 drift guard)
- Task 2: +1 (T2 rotation)
- Task 3: +1 (T3 pruning)
- Task 4: +1 (T4 regex filter)
- Task 5: 0
- **总净增 = +4 → 936 + 4 = 940 collected**（plan 头部 / File Touch Summary / Task 5 Step 1 三处一致）✅

**5. Risk awareness（spec §5.1 风险表已 cover）**:

- ✅ 微秒级别归档名碰撞：spec REPL 已 verify 间隔 150-300μs，T2 单次 rollover 测试不会触发；T3/T4 用 `time.sleep(0.01)` 显式分割
- ✅ glob 误匹配用户文件：T4 直接 verify `.bak` 存活
- ✅ `_restore_root_logger` fixture 兼容性：spec §3.5 T5 段已论证 fixture 走 save/restore 不调 `.close()`，与子类化正交；Task 5 Step 2 跑 14 测试全绿即 verify
- ✅ first-run 不立即轮转：spec §5.1 已记录 ~69 MB 现状 + 不缓解决议；运维行为不进代码

---

## Execution Handoff

Plan complete and saved to `docs/superpowers/plans/2026-04-30-iter-w2r2-3-system-log-rotation.md`.

**两个执行选项**:

**1. Subagent-Driven (recommended)** — 每 Task 派一个 fresh subagent，task 间 review，快速迭代

**2. Inline Execution** — 本 session 内执行 Tasks，checkpoint review

**纪律**: 用户审阅 plan 后才进 execution（memory `feedback_review_before_commit`）。
