# Iter W2-R2-3 — system.log 轮转

**Date**: 2026-04-30
**Branch**: `feature/iter-w2r2-3-system-log-rotation`
**Source**: `.working/sim4-issues-inventory.md §P1-6` / `.working/all-pending-needs.md` Tier 1 R2-3
**前置依赖**: 无（独立 PR，cli 模块单点改动，与 R2-1/R2-2/R2-4+ 物理零冲突）
**预估工作量**: 40-50 min（含 spec / plan / impl / TDD / review-before-commit / merge）

---

## 1. 背景与动机

### 1.1 现象

`logs/system.log` 持续增长无轮转：

- inventory snapshot (2026-04-30 早段)：**~69 MB**（4 sessions 累计）
- 写 spec 时实测：**~69 MB**（`ls -la` 字节读数；`du -h` 报 80M 是块分配 rounding，文件内容稳定在 68-72 MB 区间）
- sim #4 单 session（2h12min）≈ 22MB 增长率（≈ 167KB/min）

DEBUG 级别 + 跨 session append + 单文件 → 三重叠加。

### 1.2 当前实现

```python
# src/cli/logging_config.py:41
file_handler = logging.FileHandler(log_dir / "system.log")
file_handler.setLevel(logging.DEBUG)
```

`FileHandler` 默认 append mode (`"a"`)，无轮转策略。

### 1.3 问题维度（inventory §P1-6 已记录）

| 维度 | 现状 |
|---|---|
| 单文件大小 | ~69 MB（持续增长） |
| 增长率 | sim #4 单 session 2h12min ≈ 22MB |
| 轮转策略 | 无 |
| Session 边界 | logger name 不含 session_id，跨 session 数据混在同一文件 |
| Append mode | 是（`"a"` default）→ 测试运行也累积进同一文件 |
| 日志级别 | DEBUG → LLM 完整 request body 全部写入 |

### 1.4 衍生问题（W2 启动核心风险 = #3）

1. `scripts/observation_token_audit.py` 整文件 grep DEBUG body，文件越大越慢
2. 跨 session 分析必须时间窗口 filter，W2 数据混进 W1/sim#3
3. **W2 启动风险**：W2 24-48h × 22MB/2h ≈ **264-528 MB** 单文件，单次 grep 抢占 IO，分析痛苦
4. **实盘准备风险**: DEBUG + 无轮转 + 无 redaction → 第三方 SDK 行为未审计前可能写敏感数据（**redaction 是独立议题，本次只解决轮转**）

### 1.5 R2-3 修法选择（重新评估后切到 size-based + timestamped naming）

inventory §P1-6 给出两个候选：

- **(A)** Size-based: `RotatingFileHandler(maxBytes, backupCount)`
- **(B)** Time-based: `TimedRotatingFileHandler(when='midnight', backupCount=7)`

**初版 spec 沿用 (B)，重新评估后改采 (A) + 自定义 subclass 加时间戳归档名 + regex 过滤外来文件**——核心理由：

#### 1.5.1 为什么不用 (B)

(B) 的 4 条 inventory 论据再核：

| inventory 论据 | 重新评估 |
|---|---|
| "观察期友好：W1/sim#3/sim#4/W2 各属不同日 → 按日分隔" | ❌ 实际不成立。W1 13.6h 单 session 跨午夜，会被切成 `system.log.04-26` + `system.log.04-27`，**不是按 session 分**。每条日志已带 `%(asctime)s`，(A) 下 `grep '2026-04-27' system.log*` 同样能按日 filter。|
| "文件名稳定 `system.log`" | ✅ 平 — (A) 同样保留 `system.log` 当活跃文件名 |
| "触发可预期 0:00" | ⚠️ 仅当 bot 此刻在写。Bot 间歇运行（sim 之间空闲数日）时，midnight 被 defer 到下次 emit，文件名跟实际轮转时间错位 |
| "7 天保留直观" | 平 — (A) 用 `backupCount=N` 同样直观 |

**致命问题**：**`TimedRotatingFileHandler` 不 cap 单文件大小**——它只触发时间节点。W2 24h 连跑单日文件可达 264MB，**正是 §1.4 衍生问题 #3 的核心 W2 启动风险，(B) 不解决**。

#### 1.5.2 为什么 (A)

按 §1.4 衍生问题逐项核：

| 衍生问题 | (A) 解决度 |
|---|---|
| #1 audit 脚本整文件 grep 慢 | ✅ 严格 cap，单文件始终小 |
| #2 跨 session 数据混 | ⚠️ 部分（按 size 切，session 边界丢；与 (B) 一致）|
| #3 W2 24-48h 264-528MB 单文件 | ✅ 直接解决 |

#### 1.5.3 为什么自定义 subclass 加时间戳归档名 + regex filter

stock `RotatingFileHandler` 归档命名是**纯数字** `system.log.1` ... `system.log.30`——文件名不含时间信息。定位时多一步 `ls -la --time-style=long-iso` / `head -1 system.log.N` 才能识别归档时间窗。

按"问题定位便利"优先（用户在 spec 阶段反复强调），引入 `TimestampedRotatingFileHandler` 自定义 subclass：

- 归档名 `system.log.YYYYMMDD-HHMMSS-ffffff`（微秒精度）—— 直接 `ls logs/` 一眼看清
- 时间戳语义 = "本归档被 rotate-out 时刻" = 该归档数据窗口的**结束**
- 微秒精度（`%Y%m%d-%H%M%S-%f`）spec 阶段已实测 5 次连续 rollover 全部 distinct（间隔 ~150-300 微秒），实际碰撞概率 ≈ 0，无需 PID fallback 分支
- 修剪策略：`glob` + **regex filter（仅匹配本 handler 时间戳格式归档）** + 按 mtime 排序 + 删最旧（保持 backupCount 上限）

**为什么加 regex filter**: stock 行为下 `glob "<base>.*"` 会把用户手动放置的 `system.log.bak` / `system.log.old` 等同前缀文件**误匹配进修剪队列**——当归档总数超 backupCount 时这些备份按 mtime 顺序被静默删。用户做 `cp system.log system.log.bak` 临时备份是常见运维行为，无 filter 等于 foot-gun。filter 只匹配 `<base>.\d{8}-\d{6}-\d{6}$` 严格时间戳归档，物理消除该风险，成本仅 ~3 行 source + 1 测试。

**实现复杂度**: ~18 行 source 自定义代码 + 4 个 test (drift guard / 单 rollover / 修剪 / 外来文件不被卷入修剪)，仍属"极小 PR"档位。

#### 1.5.4 参数选型（按"问题定位便利"排）

实际定位单位 vs 文件容量：

| 投资单位 | 时间跨度 | 数据量 |
|---|---|---|
| 单 cycle LLM round-trip | 5-15 min | 1-2 MB |
| 单 P0 事件链 (e.g. P0-6 16min) | 15-30 min | 3-5 MB |
| 完整 sim/smoke (e.g. sim#4) | 2-3h | ~25 MB |
| 完整 W1 长观察 (13.6h) | 13.6h | ~135 MB |
| W2 24h | 24h | ~240 MB |
| W2 48h | 48h | ~480 MB |

候选档对比（grep 速度按 SSD 估）：

| maxBytes | sim 1 文件 | W1 1 文件 | W2 24h | grep 速度 | 编辑器可读性 |
|---|---|---|---|---|---|
| 50 MB | ✅ | 切 3 | ~5 | <0.5s | ✅ |
| **100 MB** | ✅ | 1-2 | **3** | **<1s** | ✅ |
| 200 MB | ✅ | ✅ | 1-2 | ~1-2s | ⚠️ 编辑器警告线 |
| 500 MB | ✅ | ✅ | ✅ | ~3s | ❌ 编辑器卡 |

**采用 `maxBytes=100MB, backupCount=30`** (cap = 3 GB)：

- 100 MB **覆盖每一类高频定位单位**（单 cycle / 单 P0 事件 / 完整 sim / 完整 W1）1 文件命中
- W2 24h 切 3 段 acceptable：实战定位永远是 `grep` 不是顺序读；3 段成本 = 一次 `grep system.log*` 通配 < 1s
- grep <1s + VSCode/less/vim 无警告 + audit 脚本 `--last N` 与 size 无关
- 30 backups × 100 MB = 3 GB ≈ sim#4 rate 270 小时活跃运行 ≈ 1 个月历史窗口（用户存储空间充足前提下不需扣值）

**唯一显式 trade-off**：W2 24h 整段不在 1 文件。但 sim#4/W1/各种复盘从未发生"整段顺序读"，都是 grep 切片，损失成本 ≈ 0。

---

## 2. 设计目标

### 2.1 In-scope

| # | 改动 |
|---|---|
| **G1** | `src/cli/logging_config.py` 新增 `TimestampedRotatingFileHandler` 自定义 subclass（继承 `RotatingFileHandler`，override `doRollover()`，归档名带微秒精度时间戳，修剪逻辑用 regex filter 仅卷入本 handler 时间戳格式归档）|
| **G2** | `src/cli/logging_config.py:41` 把 `logging.FileHandler(...)` 替换为 `TimestampedRotatingFileHandler(filename, maxBytes=100*1024*1024, backupCount=30, encoding='utf-8')` |
| **G3** | 顶部 import 新增 `import glob` / `import os` / `import re` / `from datetime import datetime` / `from logging.handlers import RotatingFileHandler` |
| **G4** | drift guard 测试（T1）：assert root logger 的 file handler 是 `TimestampedRotatingFileHandler` 实例 + `maxBytes==100*1024*1024` + `backupCount==30` |
| **G5** | rotation 单次行为测试（T2）：用 `handler.doRollover()` 直接触发，verify 旧文件被加微秒时间戳后缀（regex `\d{8}-\d{6}-\d{6}`）+ 活跃 `system.log` 是干净新文件 |
| **G6** | pruning 行为测试（T3）：backupCount=2 + 3 次连续 doRollover → 只剩最近 2 个归档（最旧被删，最近 2 个内容正确）|
| **G7** | regex filter 测试（T4）：`logs/` 下放 `system.log.bak` 外来文件 + N+1 次 rollover 让归档总数超 backupCount → 验证 `.bak` **未**被卷入修剪（即使其 mtime 最旧）|
| **G8** | `setup_system_logging` 内部 line 36-38 的 `for h in root.handlers: h.close()` 清理循环对子类仍正确：`TimestampedRotatingFileHandler` 不 override `.close()`，继承自 `FileHandler.close()`（关闭底层 stream）。spec 阶段已 REPL verify MRO `TimestampedRotatingFileHandler → RotatingFileHandler → BaseRotatingHandler → FileHandler → StreamHandler → Handler`。`tests/test_logging_config.py:14-22` 的 `_restore_root_logger` fixture 走 save/restore 模式（保存 `root.handlers[:]` 引用，yield 后回放），不直接调 `.close()`，与本议题正交不需变更。|

### 2.2 Out-of-scope（不做项 + 何时做）

| 议题 | 不做理由 | 何时做 |
|---|---|---|
| `--debug` 设计问题（默认 DEBUG / 终端洪水 / file 与 terminal level 耦合）| inventory §P1-6 已声明独立议题；与轮转独立的设计层 brainstorm | log 改造 round 2（数据触发）|
| system.log 默认 level 从 DEBUG 调到 INFO | inventory §P1-6 已声明独立议题；与轮转独立 | 同上 |
| `session_id` 进 log formatter | inventory §P1-6 已声明独立议题；formatter 改造与 rotation 正交；按 session 分文件无法靠 stdlib handler 直接实现 | 跨 session 分析频率高时 |
| **redaction**（敏感信息脱敏，OKX/DefiLlama SDK header）| 实盘前必修；与轮转无耦合 | Tier 3 实盘准备期 batch |
| 改 `scripts/observation_token_audit.py` 兼容轮转后的多档案聚合 | 当日 `system.log` 文件名不变 → 默认路径不破；多日聚合是 grep `system.log*` 的脚本工具改造，非本议题 | 跨日 token audit 需求 ≥ 1 例 |
| 清理已有的 ~69 MB `logs/system.log` | 用户运维操作，不属代码改动 | 用户自行 `> logs/system.log` 截断或删除 |
| time-based 轮转（候选 B `TimedRotatingFileHandler`）| §1.5 评估后选 (A)；不双轮转 | 永不（除非 (A) 实证不够用 + 时间维度需求出现）|
| `maxBytes` / `backupCount` 调整 | §1.5.4 已定 100MB / 30；存储充足前提下不必扣 | W2 实证后单文件需更大或更小，独立 mini-PR |
| PID fallback / 多级碰撞处理 | 微秒精度已实测无碰撞；同微秒概率 ≈ 0 | 永不（除非真出现碰撞实例 ≥ 1）|
| 把 `TimestampedRotatingFileHandler` 抽出独立模块文件 | 当前唯一消费者是 `logging_config.py`，inline 简洁；项目无 cli/ 子目录的 handler 模块惯例 | 出现第二个 logger 配置文件需要复用时 |

---

## 3. 设计详情

### 3.1 改动 A — imports（logging_config.py 顶部）

```python
# 改前
import logging
from pathlib import Path

# 改后
import glob
import logging
import os
import re
from datetime import datetime
from logging.handlers import RotatingFileHandler
from pathlib import Path
```

### 3.2 改动 B — `TimestampedRotatingFileHandler` 自定义 subclass（新增）

放在 `logging_config.py` 文件顶部 imports 之后、`SessionConsole` 之前：

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
    collisions practically impossible (verified in spec REPL with 5 rapid
    rollovers, all distinct).

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

**关键设计点**：

- `datetime.now().strftime("%Y%m%d-%H%M%S-%f")` 用 `datetime` 不用 `time.strftime` —— 后者无 `%f` 微秒支持
- glob `<baseFilename>.*` 收集候选 → regex `_ARCHIVE_SUFFIX_RE.fullmatch` 二次 filter，确保只把本 handler 产出的时间戳归档纳入修剪；用户的 `system.log.bak` / `system.log.old` / `system.log.1` (stdlib stock 数字模式残留) 等任何不符 `\d{8}-\d{6}-\d{6}` 的文件都被排除
- mtime 排序 + 删最旧：`os.rename` 在 POSIX 不更新 mtime（保留原 active 文件最后写入的 mtime），所以最新 rollover 的归档总是 mtime 最大；按升序排序后 pop(0) 删最旧，正确
- 不传 `mode='a'`（default）/ `delay=False` / `errors=None`：保持 stdlib 默认

### 3.3 改动 C — handler 实例化（logging_config.py:41）

```python
# 改前
file_handler = logging.FileHandler(log_dir / "system.log")
file_handler.setLevel(logging.DEBUG)

# 改后
file_handler = TimestampedRotatingFileHandler(
    log_dir / "system.log",
    maxBytes=100 * 1024 * 1024,  # 100 MB per file
    backupCount=30,              # ~30 archives → 3 GB cap, ~1 month at sim rate
    encoding="utf-8",
)
file_handler.setLevel(logging.DEBUG)
```

**参数说明**：

- `maxBytes=100 * 1024 * 1024`：100 MB（用 `100 * 1024 * 1024` 而非 `100_000_000` 显式表 MiB 二进制）
- `backupCount=30`：保留最近 30 个轮转文件
- `encoding="utf-8"`：显式指定，避免 Python locale 影响（原 `FileHandler` 未指定，依赖系统默认；改造同时显式化）

### 3.4 改动 D — `setLevel` / `setFormatter` 调用保持

```python
# logging_config.py:42-46（不动）
file_handler.setLevel(logging.DEBUG)
file_handler.setFormatter(logging.Formatter(
    "%(asctime)s [%(levelname)-7s] %(name)s: %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
))
```

`TimestampedRotatingFileHandler` 经 `RotatingFileHandler → FileHandler → StreamHandler` 继承链，`.setLevel()` / `.setFormatter()` API 一致，无需改动。

### 3.5 测试改动

#### T1 — drift guard（新增 1 测试）

文件：`tests/test_logging_config.py`（与现有 `test_setup_system_logging_*` 同文件）

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

#### T2 — rotation 单次行为测试（新增 1 测试）

```python
def test_setup_system_logging_rotation_creates_timestamped_archive(tmp_path: Path):
    """R2-3: doRollover() renames active log to a microsecond-stamped archive
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

#### T3 — pruning 行为测试（新增 1 测试）

```python
def test_setup_system_logging_rotation_prunes_oldest_beyond_backup_count(tmp_path: Path):
    """R2-3: when archive count exceeds backupCount, oldest (by mtime) is pruned.
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

#### T4 — regex filter 测试（新增 1 测试）

```python
def test_setup_system_logging_rotation_ignores_unrelated_files(tmp_path: Path):
    """R2-3: pruning regex filter excludes user-placed files like system.log.bak,
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

#### T5 — 现有测试兼容性（不改）

现有 4 个 `test_setup_system_logging_*` 测试均不依赖 `FileHandler` 具体类型，仅 verify 行为：

| 测试 | 现状 | R2-3 后 |
|---|---|---|
| `test_setup_system_logging_creates_log_dir` | assert `(log_dir / "system.log").exists()` | ✓ 仍通过（轮转 handler 同样写 `system.log`）|
| `test_setup_system_logging_writes_to_system_log` | assert "system info message" in `system.log` content | ✓ 仍通过（写入路径未变）|
| `test_setup_system_logging_debug_mode` | assert RichHandler level | ✓ 仍通过（不涉及 file handler）|
| `test_setup_system_logging_non_debug_filters_info` | assert RichHandler level >= WARNING | ✓ 仍通过（同上）|

测试隔离机制兼容性两层独立分析：

1. **`setup_system_logging` 内部 `cli/logging_config.py:36-38` 的 `for h in root.handlers: h.close(); root.handlers.clear()`**：下次 setup 调用时清理上次遗留 handler 的路径。`TimestampedRotatingFileHandler` 不 override `.close()`，继承自 `FileHandler.close()`（关闭底层 stream）→ 该路径对子类仍正确。spec 阶段已 REPL verify MRO 完整链：`TimestampedRotatingFileHandler → RotatingFileHandler → BaseRotatingHandler → FileHandler → StreamHandler → Handler`。
2. **`tests/test_logging_config.py:14-22` 的 `_restore_root_logger` fixture**：走 save/restore 模式（`original_handlers = root.handlers[:]; yield; root.handlers = original_handlers`），**不直接调 `.close()`**，只是回放 handlers list 引用。与本议题的 handler 子类化正交，无需变更。

**测试总计**：T1 (+1) + T2 (+1) + T3 (+1) + T4 (+1) = **+4 net**。

预期：936 collected → 940 collected, +4 passed, ±0 skipped/failed。

---

## 4. Acceptance Criteria

| # | 验收项 | 验证方式 |
|---|---|---|
| AC1 | imports 新增 `glob` / `os` / `re` / `datetime` / `RotatingFileHandler` | `git diff src/cli/logging_config.py` 顶部 |
| AC2 | `TimestampedRotatingFileHandler` 类定义存在，override `doRollover()`，归档名格式 `<base>.YYYYMMDD-HHMMSS-ffffff`，修剪走 `_ARCHIVE_SUFFIX_RE.fullmatch` filter | 同上 + 类源码核对 |
| AC3 | `FileHandler(...)` 替换为 `TimestampedRotatingFileHandler(maxBytes=100*1024*1024, backupCount=30, encoding="utf-8")` | 同上 |
| AC4 | drift guard 测试通过（T1）| `pytest tests/test_logging_config.py -v -k uses_timestamped_rotating` |
| AC5 | rotation 单次行为测试通过（T2，含 regex 断言）| `pytest tests/test_logging_config.py -v -k rotation_creates_timestamped` |
| AC6 | pruning 测试通过（T3）| `pytest tests/test_logging_config.py -v -k prunes_oldest` |
| AC7 | regex filter 测试通过（T4）：user-placed `system.log.bak` 在 backupCount 越界时仍存活 | `pytest tests/test_logging_config.py -v -k ignores_unrelated_files` |
| AC8 | 现有 4 个 `test_setup_system_logging_*` 全绿 | `pytest tests/test_logging_config.py -v` |
| AC9 | 全套 regression 0 回归 | `pytest`：936 → 940 passed, ±0 skipped/failed |
| AC10 | **未**改 `--debug` / DEBUG level / formatter / `_restore_root_logger` fixture / observation_token_audit 脚本 | `git diff` 整体扫描，文件清单仅 `src/cli/logging_config.py` + `tests/test_logging_config.py` + `docs/superpowers/specs/...` + `docs/superpowers/plans/...` |
| AC11 | spec §2.2 Out-of-scope 表完整列出 redaction / `--debug` / level / formatter / time-based 候选 (B) / 历史 ~69 MB 清理 / 参数调整 / PID fallback / 抽独立模块 | spec self-review 时已 verify |

---

## 5. 风险与回滚

### 5.1 风险

| 风险 | 概率 | 影响 | 缓解 |
|---|---|---|---|
| **轮转触发期间日志丢失**：size 越界瞬间正在写入的 record 被吞 | 极低 | 无（`RotatingFileHandler.shouldRollover()` 在 `emit()` 内判定，logging 模块全局 lock 保证原子）| 不缓解（标准库保证）|
| **微秒级别归档名碰撞**：同进程 1 微秒内 2 次 rollover → `os.rename` 覆盖 | 极低 | 数据丢一档 | spec 阶段实测 5 次连续 rollover 间隔 ~150-300 微秒，碰撞需 < 1μs 内同时触发 size 阈值，物理上几乎不可能；实证 ≥ 1 例后再加 PID fallback |
| **glob 误匹配 `logs/` 下用户手动放置文件**（`system.log.bak` / `system.log.old`） | ~~低~~ → ✅ **已物理消除** | ~~错误删文件~~ | regex filter `_ARCHIVE_SUFFIX_RE` 仅匹配 `\d{8}-\d{6}-\d{6}` 严格时间戳，外来文件不入修剪队列；T4 测试覆盖 |
| **测试中 `doRollover()` 与 fixture 交互**：fixture 退出时 handlers 被回放，可能与 doRollover 后的 stream state 冲突 | 低 | 测试 flaky | T2/T3/T4 显式只在 fixture 内做完整生命周期（setup → doRollover → write → assert）；fixture 走 save/restore 不主动 close handler，回放后下次 setup 时由 cli `:36-38` 处理 close，标准库 close() 对已 rotate handler 安全 |
| **logs/system.log 已存在 ~69 MB**：升级后第一次启动**不立即轮转**——69 MB < 100 MB 阈值，继续 append 到达 100 MB 才首次 rollover | 中 | 已有 ~69 MB 仍占用单文件直到 +31 MB 后切；W2 启动后约 ~3h 触发首次轮转 | 不缓解（用户可手动 `mv logs/system.log logs/system.log.<ts>` 提前轮转；不强求代码做 first-run 兜底）|
| **W2 24h 跨 3 文件**：单事件可能恰好被切到边界 | 低 | 跨 2 文件 grep 同一事件 | 用 `grep ... logs/system.log*` 通配；§1.5.4 已显式接受此 trade-off |
| **mtime 排序在 coarse-grained 文件系统不稳**（如 FAT32 mtime 仅秒级精度）| 极低 | 修剪时删错档 | 项目目标平台是 macOS APFS / Linux ext4/btrfs 全部纳秒精度；T3 测试加 `time.sleep(0.01)` 显式降级到毫秒粒度仍可分 |
| **encoding 行为变更**：原 `FileHandler` 未指定 encoding，新 handler 显式 `encoding="utf-8"` | 极低 | macOS/Linux 默认 locale 通常 UTF-8，行为一致；Windows locale 可能不同 | 项目 `pyproject.toml` 未声明 Windows 支持，且显式 utf-8 是更稳的默认 |
| **Pytest CWD 下 logs/ 状态污染**：测试用 `tmp_path` 隔离，与项目根 `logs/` 无关 | 低 | 无 | 现有测试已用 `tmp_path` pattern，T1/T2/T3/T4 沿用 |

### 5.2 回滚

R2-3 是纯 cli 模块单文件改动，无 schema / 数据 / 协议改变。回滚 = `git revert <merge-commit>` 单步完成，无 data fix。已轮转的 `system.log.<ts>` 档案保留（无副作用，用户自决保留/删除）；如完全回退到 stock，stdlib `RotatingFileHandler` 不会读这些时间戳归档（它按 `.1` `.2` 数字模式管理），相当于自然孤儿，可手动删除。

---

## 6. 与 R2 议题的关系

| 关联议题 | 关系 |
|---|---|
| **R2-1** (set_price_alert 阈值放宽) | ✅ 已 landed PR #30；模块完全独立（cli vs agent）|
| **R2-2** (cancel_alert 协议) | ✅ 已 landed PR #31；同上 |
| **R2-4** (P0-1 业务失败 metrics) | 物理零冲突；R2-4 改 schema/Alembic，不动 cli logging |
| **R2-5** (P0-5 scheduler 30min 兜底) | 物理零冲突 |
| **R2-6** (P0-2 max_position_pct 风控) | 物理零冲突 |
| **R2-7** (P0-4 N9 limit-order 派生盲区) | 物理零冲突 |
| **R2-8** (P1-7 session log MVP + N10 注入) | 物理零冲突；session log 与 system log 是不同 handler 系（`SessionConsole` vs root logger）|
| **R2-9** (Iter 10 重跑 smoke W2 启动验证) | R2-3 在 W2 启动前必须落，R2-9 跑 24-48h 期间必触发首次 size-based 轮转，验证维度：(a) `logs/system.log.YYYYMMDD-HHMMSS-ffffff` 在 size 阈值后出现 (b) 当日 `system.log` 文件继续接收日志无中断 (c) audit 脚本 `--last N` 仍正确解析当前 `system.log` (d) `ls -la logs/` 时间戳归档名直观可读 |
| **Tier 3 redaction**（实盘前 batch）| R2-3 是其前置基础（轮转后 redaction 可针对单个 100MB 时间戳归档做 sanitize 而非整 ~69 MB）|

---

## 7. 估算

- **Spec**: 已完成（本文档）
- **Plan**: ~10 min（writing-plans skill）
- **Impl**: ~30 min（imports + 自定义 subclass ~18 行 + handler 替换 + 4 新增测试）
- **Review (self + user) + commit + merge**: ~10 min
- **总计**: ~40-50 min

改动量预估：

| 类型 | 行数 |
|---|---|
| Source (`src/cli/logging_config.py`) | ~28 行（5 行 import + ~18 行 subclass class def + module-level regex constant + ~6 行 handler 实例化） |
| Tests (`tests/test_logging_config.py`) | ~95 行（T1 ~13 / T2 ~30 / T3 ~25 / T4 ~25 + 空行） |
| Docs (`docs/superpowers/specs/...` + `docs/superpowers/plans/...`) | 本 spec ~ 410 行 + plan ~ 60 行 |
| **总计** | **~28 行 source + ~95 行 tests + 文档** |

仍属"极小 PR"档位（与 inventory §P1-6 估算 5-10 行的差距来自自定义 subclass + regex filter 决议；net behavior change 仍极小）。
