# Batch 1 设计文档：R5 / R1 / R2

> **状态**: 三审修订后
> **日期**: 2026-04-12
> **范围**: 日志分离 (R5) → 交互式配置向导 (R1) → Session 管理 (R2)

---

## 项目背景

### TradeBot 是什么

TradeBot 是一个 AI 驱动的加密货币合约交易机器人。核心理念：LLM Agent 扮演交易员角色，自主分析市场、做出交易决策、管理仓位。

用户启动后，Agent 按固定间隔（如 15 分钟）被唤醒，或被价格波动/订单成交等事件打断唤醒。每次唤醒时，Agent 通过 tool 调用获取市场数据、查看持仓、回顾历史，然后决定操作（开仓/平仓/设止损/观望）。

### 项目阶段

- **Phase 1a** (已完成) — 最小 agent 循环：单模型、REST 轮询、定时唤醒、模拟交易所
- **Phase 1b** (已完成, PR #3 已合并) — 事件驱动闭环：多模型选择、WebSocket fill 推送、价格告警、Scheduler 事件队列化
- **当前** — 基础设施改造（本文档），改善开发体验和可维护性
- **Phase 2** (规划中) — 产品化：Web UI、多会话并行、多交易对

### 技术栈

| 组件 | 技术 |
|------|------|
| 语言 | Python 3.12+, asyncio |
| LLM 框架 | pydantic-ai（Agent + tool 定义） |
| 交易所接口 | ccxt / ccxt.pro（REST + WebSocket） |
| 数据库 | SQLite + SQLAlchemy async（aiosqlite） |
| 终端 UI | Rich（表格、面板、彩色输出、交互 prompt） |
| 测试 | pytest + pytest-asyncio，193 个测试 |

### 当前架构

```
main.py                          ← 入口，argparse --model
  └── src/cli/app.py::run()      ← 270 行巨函数，混杂配置/交互/初始化/主循环
        ├── src/config.py              ← Settings/PersonaConfig (Pydantic)，读 YAML
        ├── src/services/model_manager.py  ← ModelManager，models.json CRUD + 交互选择
        ├── src/storage/
        │     ├── database.py          ← init_db(), get_session(), SQLAlchemy async engine
        │     └── models.py            ← Session/TradeAction/DecisionLog/MemoryEntry/Sim* 表
        ├── src/integrations/
        │     ├── exchange/base.py     ← BaseExchange 抽象接口
        │     ├── exchange/simulated.py  ← SimulatedExchange（本地撮合引擎 + DB 状态持久化）
        │     ├── exchange/okx.py      ← OKXExchange（ccxt.pro WebSocket）
        │     └── market_data.py       ← MarketDataService（OHLCV via ccxt）
        ├── src/agent/
        │     ├── trader.py            ← create_trader_agent()，系统 prompt + tool 注册
        │     ├── tools_execution.py   ← 交易执行 tools（开仓/平仓/止损/止盈）
        │     ├── tools_perception.py  ← 感知 tools（市场数据/持仓/余额/日志）
        │     └── memory.py            ← MemoryService（短期/长期记忆 CRUD）
        ├── src/scheduler/scheduler.py ← Scheduler（事件队列 + 可中断 sleep）
        ├── src/services/
        │     ├── price_alert.py       ← PriceAlertService（滑动窗口 + 方向冷却）
        │     ├── metrics.py           ← MetricsService（收益率/胜率/回撤）
        │     └── technical.py         ← TechnicalAnalysisService（TA 指标计算）
        └── src/cli/
              ├── approval.py          ← ApprovalGate（人工审批门）
              └── display.py           ← 终端 metrics 展示
```

### 当前启动流程（改造前）

`app.py::run()` 当前的执行顺序（270 行，单一函数）：

```
1. logging.basicConfig + RichHandler         ← 日志初始化（仅终端，无文件）
2. load_settings(yaml) + load_trader_config  ← 读 YAML 配置
3. console.print(symbol, approval, persona)  ← 显示配置摘要
4. ModelManager 交互选择模型                 ← input() 散落在 run() 中
5. API 连通性测试                            ← input() 询问是否跳过
6. 保存新模型到 models.json
7. init_db()                                 ← SQLite 初始化
8. select Session where name="default"       ← 硬编码查找
9. 创建 Exchange (sim/okx)
10. 创建 MarketData/Technical/Memory/Metrics/Budget/Approval
11. create_trader_agent()
12. 价格告警交互配置                         ← 又一处 input() 散落
13. 注册 fill/alert handler
14. exchange.start()
15. 显示 metrics + scheduler info
16. 启动 Scheduler 主循环
17. 等待 shutdown_event
18. 清理退出
```

**痛点**：配置交互散落在步骤 3/4/5/12 中，Session 硬编码无法恢复，日志无文件持久化。

### 需求池与优先级

本文档涉及的 R1-R7 来自项目需求池，按主题分组：

| 编号 | 需求 | 类别 |
|------|------|------|
| R1 | 交互式 CLI 配置向导 | 基础设施 |
| R2 | Session 新建/恢复管理 | 基础设施 |
| R3 | 百分比告警重设计（触发后重置） | Agent 自主性 |
| R4 | 动态唤醒间隔（Agent 控制看盘节奏） | Agent 自主性 |
| R5 | 日志分离（会话/系统） | 基础设施 |
| R6 | SimExchange 与真实交易所行为对齐 | 引擎对齐 |
| R7 | 价位级别 Alert（Agent 设定关注价位） | Agent 自主性 |

**实施分批**：
- **第一批（本文档）**：R5 → R1 → R2 — 基础设施，后续所有需求依赖
- **第二批**：R4 + R3 + R7 — Agent 自主性，可并行
- **第三批**：R6 — 引擎对齐，改动量大且独立

**R5 排在 R1/R2 之前的原因**：R1/R2 会重写启动流程，重写过程中需要使用新的日志架构。如果先做 R1 再做 R5，R1 的代码要被 R5 改第二遍。

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
- 会话内容通过 `console.print()` 输出（app.py ~25 处 + display.py ~5 处调用）
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

当前 `console` 是 `app.py` 和 `display.py` 的模块级全局变量（合计约 30 处调用）。`SessionConsole` 在运行时才创建，不能替代全局变量。迁移方式：

- **阶段 1（session_id 未知）**：`setup_system_logging()` 返回临时 `Console()`，用于欢迎页、DB 初始化等启动信息
- **阶段 2（session_id 确定后）**：创建 `SessionConsole`，作为参数传入后续函数
- `run_agent_cycle()`、`build_services()` 等需要会话输出的函数增加 `console` 参数，不依赖全局变量
- `display.py` 的 `display_metrics()` / `log_trade()` 同样改为接受 `console` 参数（当前有独立的模块级 `console = Console()`）
- `app.py` 和 `display.py` 的模块级 `console = Console()` 均删除

### Known Limitation: 日志轮转

首版不做日志轮转。`system.log` 使用 `FileHandler`，长期运行会持续增长。Session 日志随 session 生命周期天然有限（每个 session 一个文件）。

后续优化：`system.log` 改为 `RotatingFileHandler`（如 10MB / 3 backup），在系统稳定后实施。

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
| `src/cli/display.py` | `display_metrics()` / `log_trade()` 增加 `console` 参数，删除模块级 `console` |
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
    alert_cooldown_min: int | None      # R3 将移除此字段（改为触发后重置）
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

> **增强项（非首版范围）**：实盘分支可在凭证输入后增加交易所连通性测试（如 `exchange.fetch_balance()`），避免配置完 5 步后才发现凭证无效。当前代码也没有此测试，首版保持一致，后续迭代加入。

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
- 价格告警开关 + 参数 (window [5min], threshold [3%], cooldown [15min])
  - 注：window/threshold 默认值将在 R3 中更新为 1h/5%；cooldown 字段将在 R3 中移除（改为触发后重置语义）
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
│ 价格告警    │ ON (5min / 3% / cd 15min)│
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

#### 模拟交易所精度处理

`SimulatedExchange.amount_to_precision()` 需要 symbol → 小数位数映射，当前从 `settings_sim.yaml` 的 `precision` 字段读取。向导不收集此参数（对用户无意义），由 `build_services()` 内置常见交易对的默认精度表：

```python
_DEFAULT_PRECISION = {
    "BTC/USDT:USDT": 3,
    "ETH/USDT:USDT": 2,
}
# 不在表中的 symbol 使用保守默认值 3
```

#### YAML 文件的新角色

```
读取优先级: Session DB (R2) > 向导用户输入 > YAML 默认值
```

YAML 从"用户编辑的配置源"变为"向导默认值模板"。高级用户仍可通过修改 YAML 调整默认值。

**`settings_sim.yaml` 废弃**：向导引入后，`settings.yaml` 作为唯一默认值源。模拟模式特有参数（`fee_rate`、`precision`）由向导 Step 1 提供默认值 / `build_services()` 内置。`settings_sim.yaml` 不再被读取，保留为参考但不影响运行。

**`load_settings()` 调用**：始终传入 `settings.yaml`，不再区分 sim/real 传不同文件。exchange type 由 `WizardResult.exchange_type` 决定。

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
approval_enabled: Mapped[bool]          # SQLAlchemy 自动处理 SQLite 0/1 映射
alert_config: Mapped[str | None]        # JSON: {"enabled":true,"window":5,"threshold":3.0,"cooldown":15}
fee_rate: Mapped[float | None]          # 模拟手续费率
token_budget: Mapped[int]              # 每日 token 预算
last_active_at: Mapped[datetime | None] # 最后 agent cycle 时间
```

#### WizardResult → Session 字段映射

新建 session 时，`WizardResult` 的字段写入 Session 记录。映射关系：

**复用已有字段**（无需改 schema）：

| WizardResult 字段 | Session 列 | 转换 |
|-------------------|------------|------|
| `session_name` | `name` | 直接写入 |
| `symbol` | `symbol` | 直接写入 |
| `persona` | `persona_config` | `json.dumps(persona.model_dump())` |
| `model_config` | `model_config` | `json.dumps({id, provider, model})` |
| `initial_balance` | `initial_balance` | 直接写入 |

**写入新增字段**：

| WizardResult 字段 | Session 列 | 转换 |
|-------------------|------------|------|
| `exchange_type` | `exchange_type` | 直接写入 |
| `timeframe` | `timeframe` | 直接写入 |
| `scheduler_interval_min` | `scheduler_interval_min` | 直接写入 |
| `approval_enabled` | `approval_enabled` | 直接写入 |
| `alert_enabled/window/threshold/cooldown` | `alert_config` | `json.dumps({enabled, window, threshold, cooldown})` |
| `fee_rate` | `fee_rate` | 直接写入 |
| `token_budget` | `token_budget` | 直接写入 |

**不存 DB**（从文件/环境读取）：

| WizardResult 字段 | 存储位置 | 说明 |
|-------------------|---------|------|
| `api_credentials` | `config/.credentials` (0o600) | 交易所 API 凭证 |
| `model_config.api_key` | `config/models.json` (0o600) | 模型 API key |
| `model` (pydantic-ai object) | 不持久化 | 运行时从 `model_config` 重建 |

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
        ("approval_enabled", "BOOLEAN DEFAULT 1"),
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
  │     └── alert_config 为 NULL → alerts disabled（迁移产生的旧 session）
  ├── 模型选择:
  │     ├── 有 --model X → 直接使用 X（显式 flag 优先于历史记录）
  │     └── 无 --model → "上次使用 claude-opus，继续？[Y/n]"
  │                        └── N → 进入 wizard Step 3（模型选择单步）
  ├── status → "active"
  └── 返回 WizardResult
```

### `--model` flag 行为

| 场景 | 行为 |
|------|------|
| 新建 session + `--model X` | 跳过 wizard Step 3，直接使用模型 X |
| 恢复 session + `--model X` | 跳过模型确认提示，直接使用模型 X（覆盖 session 记录） |
| 新建 session，无 `--model` | wizard Step 3 完整执行 |
| 恢复 session，无 `--model` | 提示确认是否继续使用上次模型 |

原则：显式 CLI flag 优先于 session 记录的历史值。

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
    engine, settings, trader_config, model_manager, model_id,
    console: Console,  # Phase 1 返回的 pre_console（SessionConsole 尚未创建）
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

## `run()` 重构后数据流

各阶段函数签名和参数传递：

```python
async def run(
    settings_path: Path = Path("config/settings.yaml"),
    trader_path: Path = Path("config/trader.yaml"),
    model_id: str | None = None,
    debug: bool = False,
):
    # ── Phase 1: 系统日志 ──
    log_dir = Path("logs")
    pre_console = setup_system_logging(debug, log_dir)
    #   → pre_console: Console（临时，用于启动阶段输出）

    # ── Phase 2: 数据库 ──
    settings = load_settings(settings_path)
    trader_config = load_trader_config(trader_path)
    engine = await init_db(db_url)
    #   → engine: AsyncEngine

    # ── Phase 3: Session 选择 / 向导 ──
    model_manager = ModelManager(config_path=...)
    result, session_id = await select_or_create_session(
        engine, settings, trader_config, model_manager, model_id,
        console=pre_console,
    )
    #   → result: WizardResult（新建走向导，恢复走 DB 加载）
    #   → session_id: str

    # ── Phase 4: 会话日志 ──
    sc = setup_session_logging(session_id, log_dir)
    #   → sc: SessionConsole（后续所有会话输出通过它）

    # ── Phase 5: 服务构建 ──
    exchange, deps, agent, budget = await build_services(
        result, engine, session_id, sc, settings,
    )
    #   → exchange: BaseExchange
    #   → deps: TradingDeps
    #   → agent: Agent[TradingDeps, str]
    #   → budget: TokenBudget
    #   注意: scheduler 不在此阶段创建（见下方说明）

    # ── Phase 6: 主循环 ──
    await run_main_loop(
        exchange, deps, agent, budget,
        engine, session_id, result, sc,
    )
    #   内部: 创建 scheduler → 注册 fill/alert handler → exchange.start()
    #         → scheduler.start() → shutdown_event.wait()
    #         → 清理 + session.status="paused"
```

**关键数据流**：
- Phase 3 输出 `(WizardResult, session_id)` → 后续所有阶段依赖
- Phase 4 输出 `SessionConsole` → Phase 5/6 的会话输出通道
- Phase 5 输出服务对象（exchange, deps, agent, budget）→ Phase 6 组装并运行

**Phase 5 `build_services()` 内部要点**：

```python
async def build_services(result: WizardResult, engine, session_id, sc, settings: Settings):
    # Exchange 构造
    if result.exchange_type == "simulated":
        precision = _DEFAULT_PRECISION.get(result.symbol, {result.symbol: 3})
        config = ExchangeConfig(
            name="simulated", fee_rate=result.fee_rate, precision=precision,
        )
        exchange = SimulatedExchange(config=config, db_engine=engine,
                                     session_id=session_id, symbol=result.symbol)
    else:
        # 实盘凭证从 WizardResult.api_credentials 获取
        # （向导已从 config/.credentials 或用户输入中读取）
        creds = result.api_credentials
        exchange = OKXExchange(api_key=creds["api_key"], secret=creds["secret"],
                               password=creds["password"], symbol=result.symbol)

    # ApprovalGate: timeout 使用 YAML 默认值（非 per-session 配置）
    approval_gate = ApprovalGate(
        enabled=result.approval_enabled,
        timeout_seconds=defaults.approval.timeout_seconds,  # 来自 settings.yaml
    )

    # ... MarketData, Technical, Memory, Metrics, Budget, Agent 构造
    return exchange, deps, agent, budget
```

注意：`approval_timeout_seconds` 不持久化到 Session 表，恢复 session 时从 `settings.yaml` 读取默认值（300s）。这是系统级常量，非 per-session 配置。

**Scheduler 为何在 Phase 6 而非 Phase 5 创建**：

Scheduler 构造需要 callback（`on_tick`），而 `on_tick` 内部需要调用 `handle_fill`，`handle_fill` 又需要引用 scheduler 本身（`scheduler.trigger("conditional", ...)`）。这是一个循环依赖。当前代码用闭包延迟绑定解决（`handle_fill = None` → 后赋值）。将 scheduler、callback、handler 统一在 `run_main_loop()` 内创建，闭包引用自然在同一作用域内完成，无需跨函数传递。

---

## 测试影响评估

### 现有测试概况

项目共 20 个测试文件，193 个测试。各 PR 对现有测试的影响：

### PR #1 (R5 日志分离)

| 测试文件 | 影响 | 说明 |
|---------|------|------|
| `test_cli.py` (22行) | 无 | 仅测试 `display.py::format_metrics`，不涉及 logging/console |
| 其余 19 个文件 | 无 | 不依赖 `app.py` 的日志配置 |

**新增测试**：`test_logging_config.py` — SessionConsole 双写、system.log 创建、--debug level 切换

### PR #2 (R1 配置向导)

| 测试文件 | 影响 | 说明 |
|---------|------|------|
| `test_cli.py` | 无 | 不测试 `app.py::run()` |
| `test_model_manager.py` | 无 | ModelManager 接口不变，wizard 复用其公开方法 |

**新增测试**：`test_wizard.py` — mock input 验证 WizardResult 完整性、默认值、Ctrl+C 处理、凭证文件读写

### PR #3 (R2 Session 管理)

| 测试文件 | 影响 | 说明 |
|---------|------|------|
| `test_storage.py` (174行) | **低风险** | 创建 Session 用 `Session(id=..., name=...)`，新增字段都有默认值，不破坏现有测试 |
| `test_simulated_exchange.py` (727行) | 无 | 通过 session_id FK 关联，Session schema 变化不影响 |

**新增测试**：`test_session_manager.py` — session 列表排序、残留 active 修复、恢复加载配置、--model 优先级

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
| `src/cli/display.py` | R5 | `display_metrics()` / `log_trade()` 增加 console 参数，删除模块级 console |
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
| `config/settings.yaml` | 内容不变，角色从"配置源"变为"唯一默认值模板" |
| `config/settings_sim.yaml` | 废弃，不再被读取（保留为参考） |
| `config/trader.yaml` | 内容不变 |

---

## 设计决策记录

在 brainstorming 阶段讨论并确认的关键决策：

### D1: `run()` 拆分方式 — 分阶段函数 vs Application 类

**决策**：分阶段函数

**备选方案**：
- (A) 分阶段函数 ✅ — 把 `run()` 拆为 6 个顺序调用的阶段，每个 30-50 行
- (B) `TradeBotApp` 类 — 封装整个生命周期为 start/run/shutdown 方法
- (C) 维持现状 — 只插入 wizard/session 调用，不重构

**理由**：方案 A 在结构清晰和改动量之间取得平衡。方案 B 引入新抽象层，当前阶段过度设计。方案 C 会让 270 行膨胀到 350+ 行，可读性进一步恶化。

### D2: API 凭证存储 — keyring vs 加密文件 vs 0o600 文件

**决策**：0o600 权限的 JSON 文件（`config/.credentials`）

**备选方案**：
- (A) `keyring` 库（OS 密钥链）— 最安全，但在无桌面服务器上不可用，新增依赖
- (B) `cryptography.Fernet` 加密文件 — 跨平台可靠，但需管理加密密钥
- (C) 0o600 JSON 文件 ✅ — 与现有 `models.json` 同模式，零新增依赖

**理由**：项目已有 `models.json` 以 0o600 存储 API key 的先例，保持一致。当前单用户本地运行，文件权限足够。

### D3: 数据库迁移 — Alembic vs ALTER TABLE vs 删库重建

**决策**：幂等 `ALTER TABLE ADD COLUMN`

**备选方案**：
- (A) 幂等 ALTER TABLE ✅ — ~20 行代码，在 `init_db()` 后执行，检查列存在性后按需添加
- (B) Alembic migration — 规范但增加配置复杂度（alembic.ini, versions 目录, 依赖）
- (C) 删旧库重建 — 最简单但会丢数据

**理由**：方案 C 在 R5/R1 实施期间用户可能已产生模拟交易数据，删库有风险。方案 B 对 pre-v1 单用户项目过重。方案 A 代码量极小且安全，Phase 2 产品化时再引入 Alembic。

### D4: 终端交互库 — Rich 内置 vs 外部 TUI 库

**决策**：Rich 内置 Prompt/Confirm/Table

**理由**：项目已依赖 Rich，其 `Prompt.ask(choices=...)` / `Confirm.ask()` / `IntPrompt` 覆盖了向导所需的全部交互类型。无需引入 `questionary`、`inquirer` 等外部库。

### D5: alert_config 存储 — JSON 字段 vs 独立列

**决策**：JSON 字符串字段

**理由**：R3（第二批）会重设计告警参数（去掉 cooldown，改默认值）。JSON 比独立列更灵活，schema 变化时不需要再次 ALTER TABLE。参数少量（当前 4 个，R3 后减为 3 个），查询需求不强。
