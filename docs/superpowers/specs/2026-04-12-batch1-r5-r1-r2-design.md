# Batch 1 设计文档：R5 / R1 / R2

> **状态**: 审阅修订后
> **日期**: 2026-04-12
> **范围**: 日志分离 (R5) → 交互式配置向导 (R1) → Session 管理 (R2)

---

## 概述

第一批需求是基础设施改造，为后续 Agent 自主性（R3/R4/R7）和引擎对齐（R6）打地基。依赖链：

```
R5(日志分离) → R1(配置向导) → R2(Session 管理)
```

- R5 先行：后续启动流程重写依赖新日志架构
- R1 次之：向导产出的配置数据需持久化到 Session 记录
- R2 最后：Session 选择/恢复建立在向导和扩展后的 Session 表之上

**核心架构决策**：采用分阶段函数重构 `app.py` 的 `run()`（当前 270 行巨函数），拆为 6 个阶段：

```
run()
  ├── Phase 1: setup_system_logging()       → 系统日志
  ├── Phase 2: init_db()                    → 数据库
  ├── Phase 3: select_or_create_session()   → session 选择 / wizard
  ├── Phase 4: setup_session_logging()      → 会话日志
  ├── Phase 5: build_services()             → exchange, deps, agent, scheduler
  └── Phase 6: run_main_loop()              → 事件绑定 + 主循环 + shutdown
```

---

## R5: 日志分离

### 目标

将当前仅有的终端输出拆分为三个流：

```
终端显示
├── 会话内容（Agent 输出、交易决策、fill、alert）  ← 主要内容
└── 系统 WARNING/ERROR                              ← 仅异常情况

日志文件
├── logs/session_{session_id}.log  ← 会话内容（纯文本，同终端会话部分）
└── logs/system.log                ← 系统全量（DEBUG 及以上）

--debug 模式
└── 终端额外显示系统 DEBUG/INFO
```

### 现状

- `app.py:167-171`: `logging.basicConfig` + `RichHandler`，仅终端输出
- 会话内容通过 `console.print()` 输出（~20 处调用）
- 系统日志通过 `logger.info/warning/error()` 输出
- 两者已形成自然分离，设计利用这一现有分离

### 两阶段初始化

session_id 在 DB 初始化后才确定，因此日志分两阶段：

**阶段 1（session_id 未知）**：
- 配置 root logger → `system.log` FileHandler（全量）+ terminal RichHandler（WARNING+，`--debug` 时 DEBUG）
- 返回临时 `Console()` 供启动信息输出

**阶段 2（session_id 确定后）**：
- 创建 `SessionConsole`，后续会话内容通过它输出
- 双写：终端（Rich 格式）+ `logs/session_{id}.log`（纯文本）

### 新模块 `src/cli/logging_config.py`

```python
def setup_system_logging(debug: bool, log_dir: Path) -> Console:
    """阶段 1 — 创建 log_dir，配置 root logger，返回临时 Console。"""

class SessionConsole:
    """阶段 2 — 终端 + 会话文件双写。"""
    def __init__(self, session_id: str, log_dir: Path): ...
    def print(self, *args, **kwargs): ...  # 接口兼容 Console.print
    def close(self): ...

def setup_session_logging(session_id: str, log_dir: Path) -> SessionConsole:
    """创建 SessionConsole 并返回。"""
```

### SessionConsole 实现要点

- 内部两个 Rich Console：终端输出 + 文件输出（`no_color=True, width=120`）
- 文件 append 模式 — 恢复 session 时日志续写
- 每次 `print()` 后对文件执行 `flush()`，防止非正常退出（SIGKILL）时丢尾部日志
- `close()` 在 graceful shutdown 时调用

### console.print 迁移策略

当前 `console` 是 `app.py` 的模块级全局变量（~32 处调用）。`SessionConsole` 在运行时才创建，不能替代全局变量。迁移方式：

- **阶段 1（session_id 未知）**：`setup_system_logging()` 返回临时 `Console()`，用于欢迎页、DB 初始化等启动信息
- **阶段 2（session_id 确定后）**：创建 `SessionConsole`，作为参数传入后续函数
- `run_agent_cycle()`、`build_services()` 等需要会话输出的函数增加 `console` 参数，不依赖全局变量
- 模块级 `console = Console()` 删除

### Root Logger 配置

```python
file_handler = logging.FileHandler(log_dir / "system.log")
file_handler.setLevel(logging.DEBUG)
file_handler.setFormatter(logging.Formatter(
    "%(asctime)s [%(levelname)-7s] %(name)s: %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
))

terminal_handler = RichHandler(console=Console(), rich_tracebacks=True)
terminal_handler.setLevel(logging.DEBUG if debug else logging.WARNING)

logging.basicConfig(level=logging.DEBUG, handlers=[file_handler, terminal_handler])
```

各模块的 `logger = logging.getLogger(__name__)` 无需改动，root logger 配置自动生效。

### CLI 变化

`main.py` 新增 `--debug` flag → `run(debug=args.debug)`

### 文件变化

| 文件 | 变化 |
|------|------|
| `src/cli/logging_config.py` | 新建 |
| `src/cli/app.py` | 删除 `logging.basicConfig` + 模块级 `console`，用 `SessionConsole` 替换 |
| `main.py` | 新增 `--debug` |
| `.gitignore` | 新增 `logs/` |

---

## R1: 交互式 CLI 配置向导

### 目标

启动后通过统一的问答流完成全部配置，不再需要手动编辑 YAML。

### 现状

- 配置分散在 4 个文件：`settings.yaml` / `settings_sim.yaml` / `trader.yaml` / `models.json`
- 交互逻辑散落在 `app.py`：模型选择 (L202-224)、alert 参数 (L341-365)、`_interactive_add_model` (L436-466)
- 交易所选择、交易对、persona、scheduler 间隔均需手动编辑 YAML

### 新模块 `src/cli/wizard.py`

#### 输出数据结构

```python
@dataclass
class WizardResult:
    # 交易所
    exchange_type: str              # "simulated" / "okx"
    fee_rate: float | None          # 模拟模式
    initial_balance: float
    api_credentials: dict | None    # 实盘: {api_key, secret, password}

    # 交易对
    symbol: str
    timeframe: str

    # 模型
    model_config: ModelConfig       # from ModelManager
    model: Any                      # pydantic-ai Model object

    # 风控与调度
    scheduler_interval_min: int
    approval_enabled: bool
    alert_enabled: bool
    alert_window_min: int | None
    alert_threshold_pct: float | None
    token_budget: int

    # Persona
    persona: PersonaConfig

    # Session
    session_name: str               # 如 "BTC sim #1"，向导最后一步生成，用户可改
```

这是向导与后续启动流程之间的唯一接口。

#### 入口函数

```python
async def run_wizard(
    model_manager: ModelManager,
    defaults: Settings,
    trader_defaults: TraderConfig,
    model_id: str | None = None,
) -> WizardResult | None:
    """运行交互式配置向导。返回 None 表示用户中途退出（含 Ctrl+C）。"""
```

向导整体用 `try/except KeyboardInterrupt` 包裹，中途 Ctrl+C 返回 `None`，由调用方决定退出行为。

#### 向导流程（5 步）

**Step 1: 交易所模式**
- "模拟 / 实盘？" → `Prompt.ask(choices=["sim","real"])`
- (sim) 手续费率 [0.05%], 初始余额 [100 USDT]
- (real) 交易所 [okx], API 凭证（`password=True` 隐藏输入）
  - 检测 `config/.credentials` → 有则提示复用

**Step 2: 交易对**
- Symbol [BTC/USDT:USDT]
- Timeframe → `Prompt.ask(choices=["1m","5m","15m","1H","4H"])`

**Step 3: 大模型**
- 复用 `ModelManager.load_models()` 列出已有
- 选择已有 / 添加新模型（现有 `_interactive_add_model` 逻辑移入）
- 连通性测试
- `--model <id>` flag 跳过此步

**Step 4: 风控与调度**
- 唤醒间隔 [15 min]
- 审批开关 — 模拟默认 OFF, 实盘默认 ON
- 价格告警开关 + 参数 (window [5min], threshold [3%])  ← 注：此默认值将在 R3 中更新为 1h/5%
- Token 预算 [500000/day]

**Step 5: 交易员人设**
- 风险偏好 → `choices=["conservative","moderate","aggressive"]`
- 交易风格 → `choices=["trend_following","swing","breakout"]`
- 最大仓位% [30], 杠杆 [3x], 止损% [3], 止盈% [6]

每步 `[]` 内为从 YAML 读取的默认值，用户直接回车采用。

#### 最后确认

Rich Table 汇总全部配置，用户确认或返回修改：

```
┌────────────┬──────────────────────────┐
│ 交易所      │ simulated (fee: 0.05%)   │
│ 交易对      │ BTC/USDT:USDT / 15m     │
│ 模型        │ claude-opus (anthropic)  │
│ 唤醒间隔    │ 15 min                   │
│ 审批        │ OFF                      │
│ 价格告警    │ ON (5min / 3%)           │
│ Persona    │ moderate / trend_following│
└────────────┴──────────────────────────┘
确认启动？ [Y/n]
```

#### 交互组件

全部使用 Rich 内置，不引入外部 TUI 库：

| 类型 | 实现 |
|------|------|
| 二选一 | `Confirm.ask()` |
| 多选一 | `Prompt.ask(choices=[...])` |
| 数值 | `IntPrompt.ask()` / `FloatPrompt.ask()` |
| 文本 | `Prompt.ask()` |
| 密码 | `Prompt.ask(password=True)` |
| 汇总 | `Table` |

#### 凭证存储

实盘 API 凭证保存到 `config/.credentials`（JSON, 0o600 权限），与 `models.json` 同模式：

```json
{
  "okx": {"api_key": "...", "secret": "...", "password": "..."}
}
```

下次向导检测到该文件时提示复用。`config/.credentials` 加入 `.gitignore`。

#### YAML 文件的新角色

```
读取优先级: Session DB (R2) > 向导用户输入 > YAML 默认值
```

YAML 从"用户编辑的配置源"变为"向导默认值模板"。高级用户仍可通过修改 YAML 调整默认值。

### 对 `app.py` 的影响

- 删除 `_interactive_add_model()` — 逻辑移入 wizard
- 删除 `run()` 中所有散落的 `input()` 调用
- `run()` Phase 3 调用 wizard 或 session 恢复

### 文件变化

| 文件 | 变化 |
|------|------|
| `src/cli/wizard.py` | 新建 |
| `src/cli/app.py` | 删除散落交互逻辑，调用 wizard |
| `.gitignore` | 新增 `config/.credentials` |

---

## R2: Session 管理

### 目标

支持多 session 新建/恢复，终端关闭后重启无需重新配置。

### 现状

- 硬编码 `Session.name == "default"` 查找 (`app.py:261`)
- Session 表缺少 exchange_type, timeframe, alert 参数, scheduler 间隔等字段
- `status` 字段从未被更新（始终 "active"）
- SimExchange 状态恢复已实现（balance/positions/orders）

### Session 表扩展

`src/storage/models.py` Session 模型新增字段：

```python
# --- 新增字段 ---
exchange_type: Mapped[str]              # "simulated" / "okx"
timeframe: Mapped[str]                  # "15m", "1H" 等
scheduler_interval_min: Mapped[int]     # 唤醒间隔（分钟）
approval_enabled: Mapped[int]           # SQLite 无 Boolean → 0/1
alert_config: Mapped[str | None]        # JSON: {"enabled":true,"window":5,"threshold":3.0}
fee_rate: Mapped[float | None]          # 模拟手续费率
token_budget: Mapped[int]              # 每日 token 预算
last_active_at: Mapped[datetime | None] # 最后 agent cycle 时间
```

**不存 DB**：API 凭证（`config/.credentials`）、模型 API key（`models.json`）。

**迁移策略**：幂等 `ALTER TABLE ADD COLUMN`。在 `init_db()` 中 `create_all()` 之后调用：

```python
async def _migrate_session_table(conn):
    """检查并添加 Session 表新增字段。幂等，可重复执行。"""
    result = await conn.execute(text("PRAGMA table_info(sessions)"))
    existing = {row[1] for row in result}
    migrations = [
        ("exchange_type", "TEXT DEFAULT 'simulated'"),
        ("timeframe", "TEXT DEFAULT '15m'"),
        ("scheduler_interval_min", "INTEGER DEFAULT 15"),
        ("approval_enabled", "INTEGER DEFAULT 1"),
        ("alert_config", "TEXT"),
        ("fee_rate", "REAL"),
        ("token_budget", "INTEGER DEFAULT 500000"),
        ("last_active_at", "TIMESTAMP"),
    ]
    for col, defn in migrations:
        if col not in existing:
            await conn.execute(text(f"ALTER TABLE sessions ADD COLUMN {col} {defn}"))
```

这比删库重建更安全（不丢 R5/R1 期间可能产生的交易数据），比 Alembic 更轻量。

### 启动流程

```
启动 → init_db
  │
  ├── 有历史 session → 显示列表 → 用户选择
  │                                ├── 已有 → 恢复流程
  │                                └── "新建" → wizard (R1)
  │
  └── 无历史 → 直接 wizard (R1)
```

### Session 列表

```
 TradeBot Sessions
 ──────────────────────────────────────────────────────────────────
  #  Name              Mode   Status     Position        Last Active
 ──────────────────────────────────────────────────────────────────
  1  BTC sim #1        sim    ▶ active   long 0.5 BTC    2 hours ago
  2  ETH sim #2        sim    ⏸ paused   —               3 days ago
 ──────────────────────────────────────────────────────────────────
  3  + New Session
```

- 按 `last_active_at` 降序
- 仅显示 `status in ("active", "paused")`
- 时间显示为相对时间
- 持仓摘要：模拟模式从 `SimPosition` 表直接查询；实盘模式显示 "—"（exchange 未连接，无法获取实时持仓）

### 恢复流程

从 DB 加载全部配置，构造 `WizardResult`，跳过 wizard。唯一可重选：模型。

```
恢复 "BTC sim #1"
  ├── 从 DB 加载: exchange/symbol/timeframe/persona/scheduler/alert
  ├── 模型: "上次使用 claude-opus，继续？[Y/n]"
  │         └── N → 进入 wizard Step 3（模型选择单步）
  ├── status → "active"
  └── 返回 WizardResult
```

### Session 生命周期

```
新建 → active ──┬── Ctrl+C / 退出 → paused
                │
paused ─────────┼── 恢复选择 → active
                │
active/paused ──┴── 用户主动结束 → stopped（首版不实现 UI）
```

| 事件 | 状态变化 | 代码位置 |
|------|---------|---------|
| wizard 完成 | → active | `session_manager.py` |
| 恢复 session | paused → active | `session_manager.py` |
| 正常退出 Ctrl+C | active → paused | `run()` shutdown |
| agent cycle 完成 | 更新 `last_active_at` | `run_agent_cycle()` |
| 启动发现残留 active | → paused | `select_or_create_session()` 入口 |

### Session 命名

自动生成，用户可改：`"{symbol_short} {exchange_type} #{count+1}"` — 如 "BTC sim #1"

### 新模块 `src/cli/session_manager.py`

```python
async def select_or_create_session(
    engine, settings, trader_config, model_manager, model_id
) -> tuple[WizardResult, str]:
    """入口。返回 (WizardResult, session_id)。"""
```

职责：修复残留 active → paused、显示列表、路由恢复/wizard、返回 `(WizardResult, session_id)`。

### 文件变化

| 文件 | 变化 |
|------|------|
| `src/storage/models.py` | Session 新增 8 字段 |
| `src/cli/session_manager.py` | 新建 |
| `src/cli/wizard.py` | 完成时创建 Session 记录 |
| `src/cli/app.py` | 调用 session_manager，shutdown 更新状态 |

---

## 实施顺序

```
PR #1: R5 日志分离
  新建 src/cli/logging_config.py
  改造 src/cli/app.py + main.py
  更新 .gitignore

PR #2: R1 配置向导
  新建 src/cli/wizard.py
  重构 src/cli/app.py（删除散落交互）
  更新 .gitignore

PR #3: R2 Session 管理
  扩展 src/storage/models.py
  新建 src/cli/session_manager.py
  集成到 src/cli/app.py
```

每个 PR 可独立测试和合并，但有先后依赖。

---

## 全量文件变化汇总

### 新建文件

| 文件 | 需求 | 用途 |
|------|------|------|
| `src/cli/logging_config.py` | R5 | 日志配置 + SessionConsole |
| `src/cli/wizard.py` | R1 | 配置向导 + WizardResult |
| `src/cli/session_manager.py` | R2 | Session 列表/选择/恢复 |

### 修改文件

| 文件 | 需求 | 改动 |
|------|------|------|
| `src/cli/app.py` | R5+R1+R2 | 重构 run() 为 6 阶段，删除 logging.basicConfig/console/scattered input |
| `src/storage/models.py` | R2 | Session 表新增 8 字段 |
| `main.py` | R5 | 新增 `--debug` flag |
| `.gitignore` | R5+R1 | 新增 `logs/`, `config/.credentials` |

### 不改动

| 文件 | 原因 |
|------|------|
| `src/config.py` | Settings/PersonaConfig 不变，仍作 YAML 解析载体 |
| `src/services/model_manager.py` | wizard 复用其公开方法，无需修改 |
| `src/scheduler/scheduler.py` | 接口不变 |
| `src/integrations/exchange/*` | 接口不变 |
| 各模块 `logger = logging.getLogger(__name__)` | root logger 配置自动生效 |
| `config/*.yaml` | 内容不变，角色从"配置源"变为"默认值模板" |
