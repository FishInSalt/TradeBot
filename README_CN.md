# TradeBot

基于 **pydantic-ai** 构建的加密货币永续期货自主交易 Agent。Agent 按计划定时唤醒，或由价格告警 / 订单成交事件触发，自主分析市场、管理仓位、记录推理过程。支持 OKX 实盘 / Demo 账户及零配置的本地模拟交易所。

> **状态**：MVP 阶段，核心功能已完整落地，正在观察期收集实测数据驱动后续迭代。

> ⚠️ **免责声明**：本软件**仅供研究与教育用途**，**不构成任何投资建议**。加密货币永续期货交易存在重大亏损风险。作者不声称本软件具备盈利能力，且对因使用本软件而产生的任何资金损失或其他损害**不承担任何责任**。使用风险完全自负。完整的"按现状（AS IS）提供、不附带任何担保"条款见 [LICENSE](LICENSE)。
>
> 本项目以 [Apache License 2.0](LICENSE) 授权。欢迎贡献——参见 [CONTRIBUTING.md](CONTRIBUTING.md)。

[English](README.md)

---

## 目录

- [快速开始](#快速开始)
- [运行效果](#运行效果)
- [功能特性](#功能特性)
- [架构概览](#架构概览)
- [Agent 能力](#agent-能力)
- [数据源](#数据源)
- [配置说明](#配置说明)
- [项目结构](#项目结构)
- [开发](#开发)
- [路线图](#路线图)

---

## 快速开始

### 环境要求

- Python 3.12+
- [uv](https://github.com/astral-sh/uv)（推荐）或 pip

### 安装

```bash
git clone <repo-url>
cd TradeBot

# 使用 uv（推荐）
uv sync

# 或使用 pip
pip install -e ".[dev]"
```

### 配置环境变量

在项目根目录创建 `.env`：

```dotenv
# LLM（选其一）
ANTHROPIC_API_KEY=your_key

# OKX 实盘（可选）
OKX_API_KEY=your_api_key
OKX_SECRET=your_secret
OKX_PASSWORD=your_passphrase

# OKX Demo 沙盒（可选）
OKX_DEMO_API_KEY=your_demo_key
OKX_DEMO_SECRET=your_demo_secret
OKX_DEMO_PASSWORD=your_demo_passphrase

# 宏观数据（可选，不配置则对应工具不可用）
FRED_API_KEY=your_fred_key
ALPHA_VANTAGE_API_KEY=your_av_key
COINGECKO_DEMO_API_KEY=your_cg_key
SOSOVALUE_API_KEY=your_sosovalue_key
```

### 初始化数据库

```bash
alembic upgrade head
```

### 启动

```bash
python main.py          # 交互式向导
python main.py --debug  # 显示完整系统日志
```

向导将引导完成交易对、交易所类型、LLM 模型、调度间隔等配置。

### 零配置模拟体验

无需任何 API Key：启动后在向导中选择 **Simulated** 交易所，即可在本地模拟环境中运行 Agent。

---

## 运行效果

以下片段来自真实模拟交易记录（BTC/USDT，2026-05-06）。[完整三 cycle 记录 →](docs/demo.md)

```
Exchange: simulated (local matching)
News: ON  |  Macro: ON  |  Crypto ETF: ON  |  Onchain: ON  |  Alerts: ON (60min / 5.0%)
╭──────────────────────── Performance ─────────────────────────╮
│ Return: +0.00% (+0.00 USDT)  |  Position: FLAT              │
╰──────────────────────────────────────────────────────────────╯
Scheduler: every 15 min  |  LLM Budget: 10,000,000 tokens/day
```

```
═══════════════════════════════════════════════════════════════════════════
  Cycle 48e7  •  08:59:56 UTC
───────────────────────────────────────────────────────────────────────────
  Trigger    CONDITIONAL — market long BTC 0.366 @ $81,879
  State      Long 0.366 @ $81,879 (15x) | PnL +0.03% | Balance $9,978
═══════════════════════════════════════════════════════════════════════════

▾ Action (3 tools)   get_position / get_market_data(1h) / get_price_pivots

  get_position →
    Side: Long | Contracts: 0.366 | Entry: 81,878.60 | Leverage: 15x
    Unrealized: +15.88 USDT | Margin: 1,997 USDT (20% of equity)
    Stop loss: not set  |  Take profit: not set

  get_market_data (1h) →
    RSI(14): 64.02  |  ATR(14): 417.90  |  Volume: 1.66x avg
    Daily MA200: 83,276 (-1.7%)  ← nearest major resistance above

▾ Reasoning
  Filled at 81,878.60 with a small unrealized gain. 1h ATR is 417 —
  stop placed at 81,450, just below the 81,453 swing low (~1× ATR,
  structurally anchored). TP at 82,750 gives 2:1 R:R and stays below
  the daily MA200 at 83,277 to avoid running into major resistance.

▾ Action (4 tools)
  ⚙ set_stop_loss          SL @ $81,450  (-0.47% from entry)
  ⚙ set_take_profit        TP @ $82,750  (+1.06% from entry)  R:R ~2.03:1
  ⚙ add_price_level_alert  above $82,000
  ⚙ set_next_wake          10min

▾ Decision
  (1) Stance — Holding long, breakout entry just filled, SL/TP set.
  (2) Active commitments: Long 0.366 BTC @ 81,878.60 · 15x
      SL: 81,450 (-429 pts)  |  TP: 82,750 (+871 pts)  |  Risk: ~$157 (1.6%)
  (3) This cycle delta — Fill confirmed. SL set below 81,453 swing low
      (~1× 1h ATR); TP at 82,750 (2:1 R:R, below daily MA200 at 83,277).
  (4) Thesis & invalidation — High-volume breakout (2.2× avg volume),
      1h RSI 64 with room to run. Target 82,500+; daily MA200 (83,277)
      is the primary magnet. Invalidation: close below 81,450.
  (5) Watch list — 81,972 (24h high)  |  82,000 (alert)  |  83,277 (MA200)

───────────────────────────────────────────────────────────────────────────
  Tokens   48,644 cycle  |  Session 110k (avg 55k/cycle, 2 cycles)
  Cache    90.9% hit rate
  Duration 99.1s  |  Ended 09:01:35 UTC
═══════════════════════════════════════════════════════════════════════════
```

---

## 功能特性

| 类别 | 说明 |
|------|------|
| **自主决策循环** | 按调度器间隔定时唤醒，或由价格告警 / 订单成交触发，独立完成分析 → 决策 → 执行 |
| **多时间周期分析** | 5m / 1h / 4h / 1d / 1w / 1M 全链路对齐：MA 方向、动量、波动率、结构锚点 |
| **六维市场感知** | 技术面 + 新闻情绪 + 衍生品结构（资金费率/OI/多空比）+ 宏观（DXY/VIX/美债）+ ETF 资金流 + 链上稳定币供应 |
| **跨 cycle 连续性** | 每次唤醒注入最近 3 个 cycle 的决策摘要；长期记忆按重要性检索，跨 session 持久化 |
| **人工审批门** | 执行交易前等待人工确认，超时自动跳过，可按需关闭 |
| **日 Token 预算** | 每日 LLM Token 上限，防止费用失控 |
| **全链路可观测性** | 每 cycle 记录：触发原因、状态快照、推理链路（thinking）、决策摘要、Token 用量、耗时、缓存命中率 |
| **本地模拟交易所** | 内存撮合引擎，支持市价单 / 限价单 / 止损 / 止盈，持久化到 SQLite，与 OKX 行为对齐 |
| **优雅关闭与续跑** | Ctrl+C 等待当前 cycle 完成后退出，会话置 `paused` 状态，下次启动可继续 |

---

## 架构概览

```
┌──────────────────────────────────────────────────┐
│  CLI  (Rich wizard · session manager · display)  │
└────────────────────┬─────────────────────────────┘
                     │ run_agent_cycle()
┌────────────────────▼─────────────────────────────┐
│  pydantic-ai Agent                               │
│  ┌────────────────┐ ┌────────────────┐           │
│  │ 感知工具 (×20) │ │ 执行工具 (×11) │  记忆 (×1)│
│  └───────┬────────┘ └───────┬────────┘           │
└──────────┼───────────────────┼───────────────────┘
           │                   │
┌──────────▼───────────────────▼───────────────────┐
│  Services                                        │
│  TechnicalAnalysis · Metrics · PriceAlert        │
│  CycleCapture · ToolCallRecorder                 │
└──────────┬───────────────────┬───────────────────┘
           │                   │
┌──────────▼──────┐  ┌─────────▼────────────────────┐
│  Integrations   │  │  Storage (SQLite + Alembic)   │
│  OKX / Sim      │  │  sessions · agent_cycles      │
│  News / Macro   │  │  trade_actions · tool_calls   │
│  ETF / Onchain  │  │  memory_entries               │
└─────────────────┘  └──────────────────────────────┘
```

Agent 由三类事件驱动：**定时调度**（APScheduler interval）、**成交回报**（OKX WebSocket fill push）、**价格告警**（波动率阈值 / 价格位触发）。每次唤醒独立完成一个完整的感知 → 推理 → 执行 → 记录 cycle。

**技术栈**：Python 3.12+ · pydantic-ai ≥1.78 · CCXT ≥4.0 · SQLAlchemy async · pandas-ta · Rich

---

## Agent 能力

Agent 拥有 **32 个工具**，分三类：

### 感知工具（20 个）

覆盖从微观到宏观的完整信息维度：

| 类别 | 工具 |
|------|------|
| **价格与技术** | `get_market_data`（单 tf：RSI/MACD/BB/ATR/OHLCV）· `get_multi_timeframe_snapshot`（跨 tf 对齐摘要）· `get_higher_timeframe_view`（长周期 MA/结构锚点）· `get_price_pivots`（关键价格结构位） |
| **持仓与账户** | `get_position`（持仓 + 风险敞口）· `get_account_balance`· `get_open_orders`· `get_order_book`· `get_recent_trades` |
| **市场情报** | `get_market_news`· `get_exchange_announcements`· `get_macro_calendar`· `get_derivatives_data`· `get_macro_context`· `get_etf_flows`· `get_stablecoin_supply` |
| **交易记录与记忆** | `get_trade_journal`· `get_performance`· `get_memories`· `get_active_alerts` |

### 执行工具（11 个）

`open_position` · `close_position` · `set_stop_loss` · `set_take_profit` · `place_limit_order` · `adjust_leverage` · `cancel_order` · `set_price_alert` · `add_price_level_alert` · `cancel_price_level_alert` · `set_next_wake`

### 记忆工具（1 个）

`save_memory`：将交易复盘 / 市场规律 / 教训按重要性权重持久化，下次唤醒时检索注入 prompt。

---

## 数据源

| 服务 | 数据内容 | 是否需要 Key |
|------|----------|-------------|
| OKX | 行情 / 持仓 / 订单 / 资金费率 / 公告 | 是（实盘/Demo） |
| CoinDesk | 加密货币新闻标题 | 否 |
| Alternative.me | Fear & Greed Index | 否 |
| ForexFactory | 宏观经济日历（FOMC / CPI / NFP） | 否 |
| DefiLlama | USDT + USDC 链上流通供应量 | 否 |
| FRED | 美联储经济数据（TW 指数 / 美债 / 通胀预期） | 是 |
| Alpha Vantage | SPY / QQQ 收盘报价 | 是 |
| CoinGecko | BTC/ETH 主导权 + 总市值 | 可选 |
| SoSoValue | BTC/ETH 现货 ETF 日净流入 + AUM | 是 |

宏观 / ETF / 链上数据源均可在 `config/settings.yaml` 中独立开关，Key 缺失时对应工具返回降级提示而不阻断 Agent 运行。

---

## 配置说明

### `config/settings.yaml`（核心配置）

```yaml
trading:
  symbol: BTC/USDT:USDT   # 交易对（CCXT 统一格式）
  timeframe: 15m           # 主时间周期

scheduler:
  interval_minutes: 15     # Agent 定时唤醒间隔

llm_budget:
  daily_max_tokens: 10000000  # 每日 Token 上限

approval:
  enabled: true            # 交易前是否等待人工审批
  timeout_seconds: 300

alerts:
  enabled: true
  window_minutes: 60       # 波动率检测窗口
  threshold_pct: 5.0       # 触发阈值（%）

# 各数据源开关
news:
  enabled: true
macro:
  enabled: true
crypto_etf:
  enabled: true
onchain:
  enabled: true
```

### `config/trader.yaml`（Persona）

```yaml
persona:
  # 留 null = Agent 自主选择；也可指定约束行为
  # personality: conservative | moderate | aggressive
  # trading_style: trend_following | swing | breakout
```

### `config/models.json`

定义可选 LLM 模型列表及强 / 弱模型路由规则（向导启动时展示选项）。

---

## 项目结构

```
TradeBot/
├── main.py                    # CLI 入口
├── config/                    # 配置文件（settings / trader / models）
├── src/
│   ├── agent/
│   │   ├── trader.py          # Agent 定义 + 32 个工具注册
│   │   ├── persona.py         # 系统提示词生成（三层结构）
│   │   ├── tools_perception.py
│   │   ├── tools_execution.py
│   │   ├── tools_memory.py
│   │   └── memory.py          # 长期记忆管理
│   ├── services/              # 技术分析 / 绩效指标 / 告警 / 可观测性
│   ├── integrations/
│   │   ├── exchange/          # OKX（CCXT + WebSocket）· Simulated
│   │   ├── news/              # CoinDesk · FGI · OKX 公告 · 宏观日历
│   │   ├── macro/             # FRED · Alpha Vantage · CoinGecko
│   │   ├── crypto_etf/        # SoSoValue
│   │   └── onchain/           # DefiLlama
│   ├── storage/               # SQLAlchemy ORM · Alembic 迁移
│   ├── scheduler/             # APScheduler 封装 + 动态唤醒间隔
│   └── cli/                   # Wizard · 审批门 · Rich 渲染 · 日志
├── alembic/                   # 数据库迁移脚本
├── scripts/                   # 观察期分析脚本（analyze_sim · diff_sim · tool_call_summary）
├── tests/                     # pytest 测试集（1525 个）
└── docs/superpowers/          # 设计 spec · 实施计划 · 工具设计原则
```

---

## 开发

### 测试

```bash
pytest                          # 全量
pytest tests/test_trader_agent.py -v
```

### 观察期分析脚本

```bash
python scripts/tool_call_summary.py --session <id>   # 工具调用统计
python scripts/analyze_sim.py --session <id>         # 会话绩效分析
python scripts/diff_sim.py <session_a> <session_b>   # 跨会话对比
python scripts/fetch_session_ohlcv.py --session <id> # 导出 OHLCV
```

### 开发约定

- **工具设计**：见 `docs/superpowers/principles/tool-design-principles.md`（7+1 核心原则）
- **Spec / Plan**：brainstorm 结论落 `docs/superpowers/specs/`，实施计划落 `docs/superpowers/plans/`，不直接动 source code
- **分支**：feature 分支开发，文档 commit 先于代码 commit；纯文档改动可直接 merge 到 main

---

## 路线图

### 当前阶段：观察期

六大感知通道（技术 / 新闻 / 衍生品 / 宏观 / ETF / 链上）已完整落地。Agent 正在模拟交易所 + OKX Demo 环境中持续运行，收集工具调用分布、决策质量、Token 消耗等基准数据，用于驱动后续迭代。

### 近期（观察期数据驱动）

- **决策纪律强化**：基于实测数据改进 Agent 在入场 / 止损 / 空仓决策上的一致性
- **信源可信度治理**：对可被操纵的软信号源（新闻聚合 / 情绪指数）加入 prompt 层的怀疑性引导
- **LLM 自纠错（ModelRetry 试点）**：工具返回错误时给 LLM 明确重试提示，而非让其把错误字符串当事实推理

### 中期（实盘前必做）

- **硬风控接入**：持仓比例上限 / 杠杆上限 / 止损距离从 prompt 软约束下沉为工具层硬约束
- **记忆系统升级**：将当前混合存储的记忆分层为事件流（Journal）/ 交易反思（Reflections）/ 可复用规律（Playbook），强化 discipline 而非单纯经验积累
- **OKX 实盘接入**：mark price 对齐验证、资金费率成本核算、WebSocket 断连补偿

### 远期（产品化演进）

- **Web UI**：替代 Rich CLI，提供会话管理、持仓监控、cycle 决策的可视化界面
- **多会话并行**：多交易对 / 多策略 / 多账户会话同时运行，共用调度器与数据库
- **多端通知**：Telegram / 飞书推送成交回报与异常告警
- **回测能力**：接入历史 K 线，离线回放 Agent 决策逻辑
- **执行层扩展**：从永续合约延伸至现货、杠杆现货
