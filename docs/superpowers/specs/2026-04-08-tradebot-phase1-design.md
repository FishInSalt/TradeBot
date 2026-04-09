# TradeBot Phase 1 — 设计文档

## 产品定位

AI 虚拟交易员委托平台。用户通过配置交易偏好，生成个性化的 AI 交易员 Agent，自动分析市场并执行交易。模拟真实世界中委托专业交易员的体验。

## Phase 1 目标

验证核心价值：**AI Agent 能否做出靠谱的交易决策**。

通过 CLI + OKX 真实账户（小资金）跑通完整交易链路，用收益指标衡量 AI 决策质量。

## Phase 1 范围拆分

### Phase 1a — 最小验证环（当前目标）

- Agent 决策引擎（单 Agent，强模型模式）
- 交易所数据采集（REST）
- 技术指标计算
- LLM 集成层（多模型可配置路由）
- 交易执行（OKX 真实账户，USDT 永续合约，小资金验证）
- 定时触发唤醒
- 简易长期记忆（最近 N 条复盘摘要注入 prompt）
- CLI 交互界面
- 审批门控（默认开启）
- 核心收益指标展示（CLI）
- 运营护栏（唤醒冷却、LLM 每日 token 预算）

### Phase 1b — 增强

- 价格条件触发（WebSocket 行情流 + AlertWatcher）
- 新闻/消息面数据采集（CryptoPanic API）
- 弱模型子 Agent 路由（市场分析 Agent、交易决策 Agent、复盘总结 Agent）
- 新闻数据 prompt injection 防护

### 不包含（Phase 2+）

- Web UI 界面
- 引导式偏好采集（LLM 对话）
- 多交易会话并行管理
- 金融助手会话
- 多端通知（Telegram 等）
- 宏观经济数据源
- 硬性风控机制（代码层面）
- 回测能力

## 技术决策

| 项目 | 决策 | 理由 |
|------|------|------|
| 编程语言 | Python + asyncio + type hints + pydantic | AI 生态 + 量化交易生态最强，中频交易场景性能足够 |
| 交易所 | OKX 真实账户（通过 ccxt 抽象层） | 抽象层支持未来扩展其他交易所 |
| 交易对 | BTC/USDT | 流动性好、数据丰富 |
| 交易类型 | USDT 永续合约 | 支持多空双向，策略空间大 |
| 交易频率 | 中频（15min - 1H 级别） | 匹配 LLM 推理延迟（秒级到半分钟） |
| AI 模型 | 多模型可配置（LLM Router） | 按业务环节分配模型，兼顾效果和成本 |
| Agent 框架 | Pydantic AI | 轻量不侵入、多模型路由原生支持、async-first、类型安全 |
| 数据库 | SQLite（WAL 模式）+ SQLAlchemy ORM | 零运维，WAL 模式解决并发读写，ORM 抽象支持后续迁移 PostgreSQL |
| 数据源 | Phase 1a 纯技术面；Phase 1b 加消息面 | 分阶段降低复杂度 |
| 技术指标 | pandas-ta | 纯 Python，零环境依赖，无需编译安装 |
| 自主程度 | 全自动/半自动可切换 | 审批门控开关，真实资金下默认开启 |
| 风控 | 由 AI 软性管控，无代码层面硬性约束 | 小资金验证，让 AI 自主决策以验证真实能力 |
| 密钥管理 | 环境变量 + `.env` 文件 | 不在配置文件中存储明文密钥 |
| WebSocket | ccxt Pro `watchTicker`（Phase 1b） | Phase 1a 仅用 REST 轮询 |

## 系统架构

### 分层架构

```
┌─────────────────────────────────────────────┐
│           表现层 — CLI Interface             │
│  交易偏好配置 │ 收益指标展示 │ 审批交互 │ 日志 │
├─────────────────────────────────────────────┤
│           决策层 — Agent Engine（核心）       │
│  交易员 Agent（主 Agent）                    │
│  ├── 感知：通过 Tools 获取市场数据           │
│  ├── 规划：LLM 推理分析与决策               │
│  ├── 执行：下单 / 委派子 Agent (1b)         │
│  ├── 记忆：短期上下文 + 长期经验            │
│  └── 唤醒：定时触发 (1a) + 条件触发 (1b)   │
│  子 Agent（Phase 1b，按需调用）              │
│  ├── 市场分析 Agent                         │
│  ├── 交易决策 Agent                         │
│  └── 复盘总结 Agent                         │
│  审批门控（可配置，默认开启）                │
│  调度器 + 事件监听引擎 (1b)                  │
│  运营护栏（唤醒冷却 + LLM 预算）            │
├─────────────────────────────────────────────┤
│           服务层 — Services                  │
│  LLM Router │ 技术分析服务 │ 收益统计服务    │
├─────────────────────────────────────────────┤
│           集成层 — Integrations              │
│  交易所适配器(ccxt) │ 行情数据源             │
│  新闻数据源 (1b, CryptoPanic API)           │
├─────────────────────────────────────────────┤
│           存储层 — Storage                   │
│  交易记录 │ 历史行情 │ AI决策日志 │ 策略配置  │
│  SQLite (WAL mode) + SQLAlchemy ORM         │
└─────────────────────────────────────────────┘
```

### Agent 核心设计

#### 交易员 Agent（主 Agent）

交易员 Agent 是系统的核心主体，具备四大能力：

**感知能力** — 通过 Tools 感知环境：
- `get_market_data` — 获取 K 线、Ticker 数据 + 技术指标
- `get_news` — 获取新闻和市场情绪（Phase 1b）
- `get_position` — 查看当前持仓
- `get_account_balance` — 查看账户余额
- `get_trade_history` — 查看交易记录和记忆

**规划能力** — LLM 推理 + Chain of Thought：
- 分析当前市场状态
- 评估多空力量对比
- 制定交易计划（入场/出场/仓位/杠杆）
- 决定是否委派子 Agent（Phase 1b）

**执行能力** — 通过 Tools 执行操作：
- `open_position` — 开仓（多/空、仓位、杠杆）
- `close_position` — 平仓
- `set_stop_loss` — 设置止损
- `set_take_profit` — 设置止盈
- `adjust_leverage` — 调整杠杆
- `set_alert` / `cancel_alert` / `list_alerts` — 管理唤醒条件（Phase 1b）
- `delegate_analysis` — 委派子 Agent 执行分析（Phase 1b）

**记忆能力** — 短期记忆 + 长期记忆：
- 短期记忆：当前分析周期的上下文（对话历史、最近数据）
- 长期记忆：按 relevance_score 取 top-N（默认 10 条）注入 prompt，包括历史交易复盘总结、市场规律认知积累、成功/失败模式记录
- 跨唤醒周期记忆：每次分析结束时将关键上下文序列化存储，下次唤醒时注入

#### 交易员人格

用户的交易偏好通过配置转化为 Agent 的 System Prompt，形成独特的"交易员人格"。偏好包括但不限于：
- 风险偏好（激进/稳健/保守）
- 交易风格（趋势跟踪/波段/突破）
- 仓位管理倾向
- 止损/止盈策略偏好

System Prompt 中同时包含**软性操作边界**（基于偏好配置自动生成），防止 AI 做出无意义的极端操作导致实验数据失效：
- "杠杆不超过 {preferred_leverage} 倍"
- "单笔仓位不超过 {max_position_pct}% 的可用余额"
- "严禁一次性全仓操作"
- "每笔交易必须设置止损"

这些约束在 prompt 层面生效，非代码强制。

#### 模型能力路由

根据配置的模型能力走不同执行路径：

- **Phase 1a — 强模型模式**：交易员 Agent 独立完成全部分析和决策，信息无损，决策一致性高
- **Phase 1b — 弱模型模式**：主 Agent 委派子 Agent 分工——市场分析 Agent → 交易决策 Agent → 主 Agent 审核

通过 Agent 配置中的模型能力等级决定执行策略，不硬编码。

#### 可委派的子 Agent（Phase 1b）

- **市场分析 Agent**：综合技术面和消息面，输出市场分析报告
- **交易决策 Agent**：基于分析报告，输出具体交易指令
- **复盘总结 Agent**：回顾交易结果，提炼经验，更新长期记忆

### Agent 唤醒机制

模拟真实交易员的"盯盘"行为：

#### Phase 1a — 定时触发

类比交易员每天固定时间看盘。用户可配置分析周期（如每 15min / 1H / 4H），定时唤醒 Agent 做全面市场分析。

运营护栏：最短唤醒间隔 60 秒（冷却时间），防止异常循环。

实现：asyncio 定时调度器。

#### Phase 1b — 条件触发

类比交易员设了价格提醒后去忙别的。Agent 每次分析结束后，通过 `set_alert` tool 自主设定唤醒条件，条件满足时立即唤醒。

MVP 仅支持价格条件（价格 ≤/≥ 某个值），技术指标条件和新闻事件条件推到后续迭代。

唤醒相关 Tools：
- `set_alert` — 设定唤醒条件
- `cancel_alert` — 取消已设定的唤醒条件
- `list_alerts` — 查看当前所有活跃的唤醒条件

实现：ccxt Pro `watchTicker` WebSocket 持续监听行情流，匹配 Agent 设定的价格条件。

### Agent 决策循环

```
⏰ 定时器到期 (1a) 或 🎯 价格条件满足 (1b)
  ↓ 唤醒（受冷却时间约束）
👁 感知 → 调用 tools 获取最新行情、持仓
  ↓
💾 回忆 → 加载 top-N 长期记忆 + 短期上下文
  ↓
📋 规划 → LLM 推理：分析市场 → 形成观点 → 制定计划
  │       └─ 1a: 强模型自己完成 ｜ 1b: 可委派子 Agent
  ↓
⚡ 执行 → 调用交易 tools 下单（或经审批门控）
  ↓
🎯 设定唤醒条件 (1b) → Agent 告诉系统"在什么情况下再叫我"
  ↓
📝 反思 → 记录决策理由，更新长期记忆
  ↓
😴 休眠 → 等待下次触发
  ↻ 循环
```

### 审批门控

通过配置开关控制是否启用人类审批（真实资金下默认开启）：

- **开启时**：Agent 生成交易指令后暂停执行，通过 CLI 展示交易详情（方向、仓位、杠杆、止损止盈、决策理由），等待用户确认/拒绝。超时 5 分钟自动跳过该交易。
- **关闭时**：Agent 自动执行交易指令，仅在 CLI 中展示日志

### 运营护栏

非交易风控，而是保证系统稳定运行的护栏：

- **唤醒冷却时间**：两次 Agent 唤醒之间最短间隔 60 秒，防止异常循环
- **LLM 每日 token 预算**：可配置每日最大 token 消耗量，超出后 Agent 休眠至次日，防止 API 费用爆炸
- **优雅关停**：收到终止信号时，完成当前决策周期，记录状态后退出；不在交易执行中途中断
- **重启恢复**：启动时检查当前持仓状态，与本地记录对账

### 核心收益指标

CLI 中实时展示以下指标，用于衡量 AI 交易员表现：

- **总收益率** — 累计收益 / 初始资金
- **胜率** — 盈利交易次数 / 总交易次数
- **最大回撤** — 最大峰谷跌幅
- **盈亏比** — 平均盈利金额 / 平均亏损金额
- **交易次数** — 已完成的交易总数
- **当前持仓** — 多头/空头/空仓 + 仓位大小

### 错误处理

基础容错策略（Phase 1a 范围）：

- **LLM API 失败**：指数退避重试（最多 3 次），全部失败则跳过本次决策周期，等待下次触发
- **交易所 API 超时**：重试 2 次，失败则记录日志，不执行交易
- **WebSocket 断连（Phase 1b）**：自动重连，退避间隔 5 秒起

## 项目结构（Phase 1a）

```
TradeBot/
├── pyproject.toml               # 项目依赖管理
├── .env.example                 # 环境变量模板（不含真实密钥）
├── .gitignore                   # 含 .env、data/、.superpowers/
├── config/
│   ├── settings.yaml            # 全局配置（引用环境变量）
│   └── trader.yaml              # 交易员偏好配置
├── src/
│   ├── __init__.py
│   ├── config.py                # Pydantic 配置模型 + YAML 加载
│   ├── storage/
│   │   ├── __init__.py
│   │   ├── models.py            # SQLAlchemy ORM 模型
│   │   └── database.py          # 异步引擎 + 会话管理（WAL 模式）
│   ├── integrations/
│   │   ├── __init__.py
│   │   ├── exchange/
│   │   │   ├── __init__.py
│   │   │   ├── base.py          # 抽象交易所接口
│   │   │   └── okx.py           # OKX 真实账户实现
│   │   └── market_data.py       # REST 行情数据服务
│   ├── services/
│   │   ├── __init__.py
│   │   ├── llm_router.py        # 多模型 LLM 路由
│   │   ├── technical.py         # 技术指标计算（pandas-ta）
│   │   └── metrics.py           # 收益统计服务
│   ├── agent/
│   │   ├── __init__.py
│   │   ├── persona.py           # 交易偏好 → System Prompt
│   │   ├── memory.py            # 记忆系统（短期 + 长期，top-N 检索）
│   │   ├── tools_perception.py  # 感知工具
│   │   ├── tools_execution.py   # 执行工具（含 set_stop_loss 等）
│   │   └── trader.py            # 主 Trader Agent（Pydantic AI）
│   ├── scheduler/
│   │   ├── __init__.py
│   │   └── scheduler.py         # 定时调度器
│   └── cli/
│       ├── __init__.py
│       ├── app.py               # CLI 主入口 + 组件装配
│       ├── display.py           # Rich 收益指标展示
│       └── approval.py          # 审批门控（含超时机制）
├── tests/                       # 测试
├── main.py                      # 程序入口
└── README.md
```

## 核心依赖

| 依赖 | 用途 |
|------|------|
| pydantic-ai | Agent 框架（agent 循环、tool calling、结构化输出） |
| ccxt | 交易所统一 API（OKX 合约交易） |
| anthropic / openai | LLM API 调用（由 pydantic-ai 内部调用） |
| pandas | 金融数据处理 |
| pandas-ta | 技术指标计算（纯 Python，无编译依赖） |
| sqlalchemy[asyncio] + aiosqlite | 异步 ORM 数据库抽象 |
| pydantic | 数据模型验证（pydantic-ai 依赖） |
| rich | CLI 美化输出（表格、进度条、颜色） |
| python-dotenv | 环境变量加载（.env 文件） |
| pyyaml | 配置文件解析 |
| httpx | 异步 HTTP 请求 |

## 密钥管理

所有敏感信息通过环境变量管理，不在代码或配置文件中存储。

**真实账户安全要求（必须）：**
- OKX API Key 必须设置 **IP 白名单**，仅允许运行 TradeBot 的机器 IP 访问
- OKX API Key 必须 **禁用提币权限**，仅开启交易权限
- `.env` 文件不得提交到 git（已在 `.gitignore` 中排除）

`.env.example`（提交到 git，不含真实值）：
```
OKX_API_KEY=your_api_key_here
OKX_SECRET=your_secret_here
OKX_PASSWORD=your_password_here
ANTHROPIC_API_KEY=your_anthropic_key_here
OPENAI_API_KEY=your_openai_key_here
```

`config/settings.yaml` 中引用：
```yaml
exchange:
  name: okx
  api_key: ${OKX_API_KEY}
  secret: ${OKX_SECRET}
  password: ${OKX_PASSWORD}
```

## 产品路线图

### Phase 1a（当前）— 最小验证环
- 定时触发 + 单 Agent（强模型）
- 纯技术面 + 交易执行
- OKX 真实账户，小资金验证
- CLI 指标 + 审批门控
- 简易长期记忆

### Phase 1b — 增强
- 价格条件触发（WebSocket）
- 新闻/消息面数据源
- 弱模型子 Agent 路由
- Prompt injection 防护

### Phase 2 — 产品化
- Web UI + 引导式偏好采集
- 多交易会话并行管理
- 多交易对支持
- 现货/杠杆现货执行层扩展

### Phase 3 — 完善与扩展
- 多端通知 + 审批（Telegram/飞书）
- 金融助手会话
- 宏观经济数据源
- 硬性风控机制
- 回测能力
