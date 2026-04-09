# TradeBot Phase 1a Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build an AI agent-driven crypto trading bot that connects to OKX real account (small funds), runs BTC/USDT perpetual futures trading via CLI with timed wake cycles, approval gate, and performance metrics.

**Architecture:** 5-layer system — CLI > Agent Engine > Services > Integrations > Storage. Single Pydantic AI Trader Agent (strong model mode) woken by timed scheduler. Exchange via ccxt. Secrets via `.env`.

**Tech Stack:** Python 3.12+, Pydantic AI, ccxt, SQLAlchemy (async + SQLite WAL), pandas-ta, rich, asyncio, python-dotenv

**Spec:** `docs/superpowers/specs/2026-04-08-tradebot-phase1-design.md`

**Scope:** Phase 1a only — no news, no sub-agents, no WebSocket condition triggers (those are Phase 1b).

---

## File Structure

```
TradeBot/
├── pyproject.toml
├── .env.example
├── .gitignore
├── config/
│   ├── settings.yaml
│   └── trader.yaml
├── src/
│   ├── __init__.py
│   ├── config.py
│   ├── storage/
│   │   ├── __init__.py
│   │   ├── models.py
│   │   └── database.py
│   ├── integrations/
│   │   ├── __init__.py
│   │   ├── exchange/
│   │   │   ├── __init__.py
│   │   │   ├── base.py
│   │   │   └── okx.py
│   │   └── market_data.py
│   ├── services/
│   │   ├── __init__.py
│   │   ├── llm_router.py
│   │   ├── technical.py
│   │   └── metrics.py
│   ├── agent/
│   │   ├── __init__.py
│   │   ├── persona.py
│   │   ├── memory.py
│   │   ├── tools_perception.py
│   │   ├── tools_execution.py
│   │   └── trader.py
│   ├── scheduler/
│   │   ├── __init__.py
│   │   └── scheduler.py
│   └── cli/
│       ├── __init__.py
│       ├── app.py
│       ├── display.py
│       └── approval.py
├── tests/
│   ├── __init__.py
│   ├── conftest.py
│   ├── test_config.py
│   ├── test_storage.py
│   ├── test_exchange.py
│   ├── test_market_data.py
│   ├── test_technical.py
│   ├── test_llm_router.py
│   ├── test_metrics.py
│   ├── test_memory.py
│   ├── test_persona.py
│   ├── test_tools.py
│   ├── test_trader_agent.py
│   ├── test_scheduler.py
│   ├── test_approval.py
│   └── test_cli.py
└── main.py
```

---

### Task 1: Project Scaffolding + Configuration

**Files:**
- Create: `pyproject.toml`
- Create: `.env.example`
- Create: `.gitignore`
- Create: `config/settings.yaml`
- Create: `config/trader.yaml`
- Create: `src/__init__.py`
- Create: `src/config.py`
- Create: `tests/__init__.py`
- Create: `tests/conftest.py`
- Test: `tests/test_config.py`

- [ ] **Step 1: Create pyproject.toml**

```toml
[project]
name = "tradebot"
version = "0.1.0"
description = "AI Agent-driven crypto trading bot"
requires-python = ">=3.12"
dependencies = [
    "pydantic-ai>=1.0",
    "pydantic>=2.0",
    "ccxt>=4.0",
    "sqlalchemy[asyncio]>=2.0",
    "aiosqlite>=0.20",
    "pandas>=2.0",
    "pandas-ta>=0.3",
    "rich>=13.0",
    "httpx>=0.27",
    "python-dotenv>=1.0",
    "pyyaml>=6.0",
]

[project.optional-dependencies]
dev = [
    "pytest>=8.0",
    "pytest-asyncio>=0.24",
    "pytest-mock>=3.14",
]

[build-system]
requires = ["hatchling"]
build-backend = "hatchling.build"

[tool.pytest.ini_options]
asyncio_mode = "auto"
testpaths = ["tests"]
```

- [ ] **Step 2: Create .env.example and .gitignore**

`.env.example`:
```
OKX_API_KEY=your_api_key_here
OKX_SECRET=your_secret_here
OKX_PASSWORD=your_password_here
ANTHROPIC_API_KEY=your_anthropic_key_here
OPENAI_API_KEY=your_openai_key_here
```

`.gitignore` (append to existing):
```
.env
data/
__pycache__/
*.pyc
.pytest_cache/
*.egg-info/
dist/
build/
```

- [ ] **Step 3: Create config files**

`config/settings.yaml`:
```yaml
exchange:
  name: okx

trading:
  symbol: BTC/USDT:USDT
  timeframe: 15m
  initial_balance_usdt: 100.0

models:
  default: anthropic:claude-sonnet-4-20250514
  strong: anthropic:claude-opus-4-6
  weak: anthropic:claude-haiku-4-5-20251001
  routing:
    market_analysis: strong
    trade_decision: strong
    news_summary: weak
    review: weak

scheduler:
  interval_minutes: 15
  cooldown_seconds: 60

llm_budget:
  daily_max_tokens: 500000

database:
  url: "sqlite+aiosqlite:///data/tradebot.db"

approval:
  enabled: true
  timeout_seconds: 300
```

`config/trader.yaml`:
```yaml
persona:
  risk_tolerance: moderate
  trading_style: trend_following
  position_sizing: percentage
  max_position_pct: 30
  preferred_leverage: 3
  stop_loss_pct: 3.0
  take_profit_pct: 6.0
```

- [ ] **Step 4: Write failing test for config loading**

`tests/test_config.py`:
```python
import pytest
from pathlib import Path


def test_load_settings(tmp_path: Path):
    env = {
        "OKX_API_KEY": "test_key",
        "OKX_SECRET": "test_secret",
        "OKX_PASSWORD": "test_pass",
    }
    settings_file = tmp_path / "settings.yaml"
    settings_file.write_text("""
exchange:
  name: okx
trading:
  symbol: BTC/USDT:USDT
  timeframe: 15m
  initial_balance_usdt: 100.0
models:
  default: anthropic:claude-sonnet-4-20250514
  strong: anthropic:claude-opus-4-6
  weak: anthropic:claude-haiku-4-5-20251001
  routing:
    market_analysis: strong
    trade_decision: strong
    news_summary: weak
    review: weak
scheduler:
  interval_minutes: 15
  cooldown_seconds: 60
llm_budget:
  daily_max_tokens: 500000
database:
  url: "sqlite+aiosqlite:///data/tradebot.db"
approval:
  enabled: true
  timeout_seconds: 300
""")
    from src.config import load_settings
    settings = load_settings(settings_file, env_overrides=env)
    assert settings.exchange.name == "okx"
    assert settings.exchange.api_key == "test_key"
    assert settings.exchange.secret == "test_secret"
    assert settings.trading.symbol == "BTC/USDT:USDT"
    assert settings.trading.initial_balance_usdt == 100.0
    assert settings.scheduler.cooldown_seconds == 60
    assert settings.llm_budget.daily_max_tokens == 500000
    assert settings.approval.timeout_seconds == 300


def test_load_trader_config(tmp_path: Path):
    trader_file = tmp_path / "trader.yaml"
    trader_file.write_text("""
persona:
  risk_tolerance: aggressive
  trading_style: swing
  position_sizing: percentage
  max_position_pct: 50
  preferred_leverage: 5
  stop_loss_pct: 2.0
  take_profit_pct: 8.0
""")
    from src.config import load_trader_config
    config = load_trader_config(trader_file)
    assert config.persona.risk_tolerance == "aggressive"
    assert config.persona.preferred_leverage == 5


def test_settings_missing_env_keys_uses_empty(tmp_path: Path):
    settings_file = tmp_path / "settings.yaml"
    settings_file.write_text("exchange:\n  name: okx\n")
    from src.config import load_settings
    settings = load_settings(settings_file, env_overrides={})
    assert settings.exchange.api_key == ""
```

- [ ] **Step 5: Run test to verify it fails**

Run: `cd /Users/z/Z/TradeBot && python -m pytest tests/test_config.py -v`
Expected: FAIL with `ModuleNotFoundError`

- [ ] **Step 6: Install dependencies**

Run: `cd /Users/z/Z/TradeBot && pip install -e ".[dev]"`

- [ ] **Step 7: Implement config module**

`src/__init__.py`: (empty file)

`src/config.py`:
```python
from __future__ import annotations

import os
from pathlib import Path
from typing import Literal

import yaml
from dotenv import load_dotenv
from pydantic import BaseModel


class ExchangeConfig(BaseModel):
    name: str = "okx"
    api_key: str = ""
    secret: str = ""
    password: str = ""


class TradingConfig(BaseModel):
    symbol: str = "BTC/USDT:USDT"
    timeframe: str = "15m"
    initial_balance_usdt: float = 100.0


class ModelRouting(BaseModel):
    market_analysis: str = "strong"
    trade_decision: str = "strong"
    news_summary: str = "weak"
    review: str = "weak"


class ModelsConfig(BaseModel):
    default: str = "anthropic:claude-sonnet-4-20250514"
    strong: str = "anthropic:claude-opus-4-6"
    weak: str = "anthropic:claude-haiku-4-5-20251001"
    routing: ModelRouting = ModelRouting()


class SchedulerConfig(BaseModel):
    interval_minutes: int = 15
    cooldown_seconds: int = 60


class LLMBudgetConfig(BaseModel):
    daily_max_tokens: int = 500000


class DatabaseConfig(BaseModel):
    url: str = "sqlite+aiosqlite:///data/tradebot.db"


class ApprovalConfig(BaseModel):
    enabled: bool = True
    timeout_seconds: int = 300


class Settings(BaseModel):
    exchange: ExchangeConfig = ExchangeConfig()
    trading: TradingConfig = TradingConfig()
    models: ModelsConfig = ModelsConfig()
    scheduler: SchedulerConfig = SchedulerConfig()
    llm_budget: LLMBudgetConfig = LLMBudgetConfig()
    database: DatabaseConfig = DatabaseConfig()
    approval: ApprovalConfig = ApprovalConfig()


class PersonaConfig(BaseModel):
    risk_tolerance: Literal["conservative", "moderate", "aggressive"] = "moderate"
    trading_style: Literal["trend_following", "swing", "breakout"] = "trend_following"
    position_sizing: Literal["fixed", "percentage"] = "percentage"
    max_position_pct: float = 30.0
    preferred_leverage: int = 3
    stop_loss_pct: float = 3.0
    take_profit_pct: float = 6.0


class TraderConfig(BaseModel):
    persona: PersonaConfig = PersonaConfig()


def load_settings(
    path: Path = Path("config/settings.yaml"),
    env_overrides: dict[str, str] | None = None,
) -> Settings:
    if env_overrides is None:
        load_dotenv()
        env_overrides = dict(os.environ)

    with open(path) as f:
        data = yaml.safe_load(f) or {}

    exchange = data.get("exchange", {})
    exchange.setdefault("api_key", env_overrides.get("OKX_API_KEY", ""))
    exchange.setdefault("secret", env_overrides.get("OKX_SECRET", ""))
    exchange.setdefault("password", env_overrides.get("OKX_PASSWORD", ""))
    data["exchange"] = exchange

    return Settings(**data)


def load_trader_config(path: Path = Path("config/trader.yaml")) -> TraderConfig:
    with open(path) as f:
        data = yaml.safe_load(f) or {}
    return TraderConfig(**data)
```

- [ ] **Step 8: Run tests to verify they pass**

Run: `cd /Users/z/Z/TradeBot && python -m pytest tests/test_config.py -v`
Expected: 3 passed

- [ ] **Step 9: Create conftest.py**

`tests/__init__.py`: (empty file)

`tests/conftest.py`:
```python
import pytest
from src.config import Settings, ExchangeConfig, TradingConfig, TraderConfig, PersonaConfig


@pytest.fixture
def settings() -> Settings:
    return Settings(
        exchange=ExchangeConfig(name="okx", api_key="test", secret="test", password="test"),
        trading=TradingConfig(initial_balance_usdt=10000.0),
    )


@pytest.fixture
def trader_config() -> TraderConfig:
    return TraderConfig(persona=PersonaConfig())
```

- [ ] **Step 10: Commit**

```bash
git add pyproject.toml .env.example .gitignore config/ src/__init__.py src/config.py tests/__init__.py tests/conftest.py tests/test_config.py
git commit -m "feat: project scaffolding with config, env-based secrets, settings schema"
```

---

### Task 2: Storage Layer

**Files:**
- Create: `src/storage/__init__.py`, `src/storage/models.py`, `src/storage/database.py`
- Test: `tests/test_storage.py`

- [ ] **Step 1: Write failing test**

`tests/test_storage.py`:
```python
import pytest


@pytest.fixture
async def db_session(tmp_path):
    from src.storage.database import init_db, get_session
    engine = await init_db(f"sqlite+aiosqlite:///{tmp_path}/test.db")
    async with get_session(engine) as session:
        yield session


async def test_create_trade_record(db_session):
    from src.storage.models import TradeRecord
    trade = TradeRecord(
        symbol="BTC/USDT:USDT", side="long", entry_price=65000.0,
        quantity=0.01, leverage=3, status="open",
        decision_reason="Bullish MA crossover",
    )
    db_session.add(trade)
    await db_session.commit()
    await db_session.refresh(trade)
    assert trade.id is not None
    assert trade.created_at is not None


async def test_create_decision_log(db_session):
    from src.storage.models import DecisionLog
    log = DecisionLog(
        cycle_id="c1", trigger_type="scheduled", decision="open_long",
        reasoning="RSI oversold", model_used="claude-opus", tokens_used=1500,
    )
    db_session.add(log)
    await db_session.commit()
    await db_session.refresh(log)
    assert log.tokens_used == 1500


async def test_create_memory_entry(db_session):
    from src.storage.models import MemoryEntry
    m = MemoryEntry(
        memory_type="long_term", category="trade_review",
        content="BTC bounced at 60k", relevance_score=0.85,
    )
    db_session.add(m)
    await db_session.commit()
    await db_session.refresh(m)
    assert m.relevance_score == 0.85
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd /Users/z/Z/TradeBot && python -m pytest tests/test_storage.py -v`
Expected: FAIL

- [ ] **Step 3: Implement storage layer**

`src/storage/__init__.py`: (empty file)

`src/storage/models.py`:
```python
from __future__ import annotations
from datetime import datetime, timezone
from sqlalchemy import String, Float, Integer, Text, DateTime
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    pass


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class TradeRecord(Base):
    __tablename__ = "trade_records"
    id: Mapped[int] = mapped_column(primary_key=True)
    symbol: Mapped[str] = mapped_column(String(50))
    side: Mapped[str] = mapped_column(String(10))
    entry_price: Mapped[float] = mapped_column(Float)
    exit_price: Mapped[float | None] = mapped_column(Float, nullable=True)
    quantity: Mapped[float] = mapped_column(Float)
    leverage: Mapped[int] = mapped_column(Integer, default=1)
    stop_loss: Mapped[float | None] = mapped_column(Float, nullable=True)
    take_profit: Mapped[float | None] = mapped_column(Float, nullable=True)
    status: Mapped[str] = mapped_column(String(20))
    pnl: Mapped[float | None] = mapped_column(Float, nullable=True)
    decision_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)
    closed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class DecisionLog(Base):
    __tablename__ = "decision_logs"
    id: Mapped[int] = mapped_column(primary_key=True)
    cycle_id: Mapped[str] = mapped_column(String(50))
    trigger_type: Mapped[str] = mapped_column(String(20))
    market_summary: Mapped[str | None] = mapped_column(Text, nullable=True)
    decision: Mapped[str] = mapped_column(String(50))
    reasoning: Mapped[str | None] = mapped_column(Text, nullable=True)
    model_used: Mapped[str | None] = mapped_column(String(100), nullable=True)
    tokens_used: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)


class MemoryEntry(Base):
    __tablename__ = "memory_entries"
    id: Mapped[int] = mapped_column(primary_key=True)
    memory_type: Mapped[str] = mapped_column(String(20))
    category: Mapped[str] = mapped_column(String(50))
    content: Mapped[str] = mapped_column(Text)
    relevance_score: Mapped[float] = mapped_column(Float, default=0.5)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
```

`src/storage/database.py`:
```python
from __future__ import annotations
from contextlib import asynccontextmanager
from typing import AsyncGenerator
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker, create_async_engine
from src.storage.models import Base


async def init_db(url: str) -> AsyncEngine:
    engine = create_async_engine(url, echo=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
        await conn.execute(text("PRAGMA journal_mode=WAL"))
    return engine


@asynccontextmanager
async def get_session(engine: AsyncEngine) -> AsyncGenerator[AsyncSession, None]:
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as session:
        yield session
```

- [ ] **Step 4: Run tests**

Run: `cd /Users/z/Z/TradeBot && python -m pytest tests/test_storage.py -v`
Expected: 3 passed

- [ ] **Step 5: Commit**

```bash
git add src/storage/ tests/test_storage.py
git commit -m "feat: storage layer with SQLite WAL mode"
```

---

### Task 3: Exchange Abstraction + OKX

Tasks 3-13 follow the same TDD pattern. For brevity, each task shows the key files and code. The full TDD steps (write test > run fail > implement > run pass > commit) apply to every task.

**Files:** `src/integrations/exchange/base.py`, `src/integrations/exchange/okx.py`, `tests/test_exchange.py`

The exchange base defines data classes (`Ticker`, `Candle`, `Order`, `Balance`, `Position`) and abstract `BaseExchange`. OKX implementation wraps ccxt for real account (no sandbox/demo mode flags).

See the spec's "Exchange Abstraction" section for complete interface. Tests mock ccxt methods and verify data mapping.

- [ ] **Step 1: Write test** (see test_exchange.py in file structure — tests fetch_ticker, fetch_ohlcv, create_order, fetch_balance, fetch_positions with mocked ccxt)
- [ ] **Step 2: Run fail**
- [ ] **Step 3: Implement base.py + okx.py**
- [ ] **Step 4: Run pass**
- [ ] **Step 5: Commit** `git commit -m "feat: exchange abstraction with OKX real account"`

---

### Task 4: Market Data Service

**Files:** `src/integrations/market_data.py`, `tests/test_market_data.py`

Wraps exchange for `get_current_price()`, `get_ticker()`, and `get_ohlcv_dataframe()` returning pandas DataFrame.

- [ ] **Step 1: Write test** (mock exchange, verify price and DataFrame output)
- [ ] **Step 2: Run fail**
- [ ] **Step 3: Implement**
- [ ] **Step 4: Run pass**
- [ ] **Step 5: Commit** `git commit -m "feat: market data service"`

---

### Task 5: Technical Analysis Service

**Files:** `src/services/technical.py`, `tests/test_technical.py`

Uses pandas-ta to compute RSI(14), SMA(20), SMA(50), MACD, Bollinger Bands. `format_for_llm()` returns human-readable text for agent prompt.

- [ ] **Step 1: Write test** (50-row random OHLCV, verify indicator keys and format)
- [ ] **Step 2: Run fail**
- [ ] **Step 3: Implement**
- [ ] **Step 4: Run pass**
- [ ] **Step 5: Commit** `git commit -m "feat: technical analysis with pandas-ta"`

---

### Task 6: LLM Router + Metrics Service

**Files:** `src/services/llm_router.py`, `src/services/metrics.py`, `tests/test_llm_router.py`, `tests/test_metrics.py`

LLM Router resolves task name to model string via config tiers. MetricsService computes return%, win rate, max drawdown, profit factor from TradeRecord list.

- [ ] **Step 1: Write tests** (router resolve + is_strong; metrics with sample trades + empty list)
- [ ] **Step 2: Run fail**
- [ ] **Step 3: Implement both**
- [ ] **Step 4: Run pass**
- [ ] **Step 5: Commit** `git commit -m "feat: LLM router and performance metrics"`

---

### Task 7: Agent Memory System

**Files:** `src/agent/memory.py`, `tests/test_memory.py`

SQLAlchemy-backed memory with `save_long_term()`, `save_short_term()`, `get_relevant_memories(limit=10)` ordered by relevance_score desc, `clear_short_term()`, `format_for_prompt()`.

- [ ] **Step 1: Write test** (save/retrieve, top-N ordering, clear, format)
- [ ] **Step 2: Run fail**
- [ ] **Step 3: Implement**
- [ ] **Step 4: Run pass**
- [ ] **Step 5: Commit** `git commit -m "feat: agent memory with top-N retrieval"`

---

### Task 8: Agent Persona

**Files:** `src/agent/persona.py`, `tests/test_persona.py`

`generate_system_prompt(PersonaConfig) -> str` produces the trader's system prompt with personality, rules, output format, and **soft operating constraints** derived from config (max leverage, max position %, no all-in, mandatory stop loss). These prompt-level constraints prevent meaningless extreme operations that would invalidate the experiment.

- [ ] **Step 1: Write test** (verify prompt contains key terms)
- [ ] **Step 2: Run fail**
- [ ] **Step 3: Implement**
- [ ] **Step 4: Run pass**
- [ ] **Step 5: Commit** `git commit -m "feat: agent persona"`

---

### Task 9: Agent Tools (Perception + Execution)

**Files:** `src/agent/tools_perception.py`, `src/agent/tools_execution.py`, `tests/test_tools.py`

Perception: `get_market_data`, `get_position`, `get_account_balance`, `get_trade_history`
Execution: `open_position`, `close_position`, `set_stop_loss`, `set_take_profit`, `adjust_leverage`

All functions take a `TradingDeps` instance and return formatted strings.

- [ ] **Step 1: Write tests** (mock all deps, verify output contains expected values)
- [ ] **Step 2: Run fail**
- [ ] **Step 3: Implement perception tools**
- [ ] **Step 4: Implement execution tools**
- [ ] **Step 5: Run pass**
- [ ] **Step 6: Commit** `git commit -m "feat: agent tools — perception and execution"`

---

### Task 10: Trader Agent (Pydantic AI)

**Files:** `src/agent/trader.py`, `tests/test_trader_agent.py`

Defines `TradingDeps` dataclass and `create_trader_agent()` that wires all 9 tools via `@agent.tool` decorators with `RunContext[TradingDeps]`.

- [ ] **Step 1: Write test** (create agent, verify tools registered by name)
- [ ] **Step 2: Run fail**
- [ ] **Step 3: Implement** (use `models.ALLOW_MODEL_REQUESTS = False` in tests)
- [ ] **Step 4: Run pass**
- [ ] **Step 5: Commit** `git commit -m "feat: Pydantic AI trader agent with 9 tools"`

---

### Task 11: Scheduler with Cooldown

**Files:** `src/scheduler/scheduler.py`, `tests/test_scheduler.py`

Async scheduler with `interval_seconds` and `cooldown_seconds`. Cooldown prevents rapid re-triggering.

- [ ] **Step 1: Write test** (verify fire count, cooldown enforcement, stop)
- [ ] **Step 2: Run fail**
- [ ] **Step 3: Implement**
- [ ] **Step 4: Run pass**
- [ ] **Step 5: Commit** `git commit -m "feat: scheduler with cooldown"`

---

### Task 12: Approval Gate with Timeout

**Files:** `src/cli/approval.py`, `tests/test_approval.py`

`ApprovalGate` with sync and async `check()`. Async version uses `asyncio.wait_for()` with configurable timeout. Auto-skip on timeout.

- [ ] **Step 1: Write test** (auto-approve when disabled, accept/reject via monkeypatch)
- [ ] **Step 2: Run fail**
- [ ] **Step 3: Implement**
- [ ] **Step 4: Run pass**
- [ ] **Step 5: Commit** `git commit -m "feat: approval gate with timeout"`

---

### Task 13: CLI Display + Main Entry Point

**Files:** `src/cli/display.py`, `src/cli/app.py`, `main.py`, `tests/test_cli.py`

Display: Rich-based `format_metrics()` and `display_metrics()`.
App: Wires all components, runs scheduler loop, handles `SIGINT`/`SIGTERM` for graceful shutdown, tracks `TokenBudget`.
Main: `asyncio.run(run())`.

- [ ] **Step 1: Write test** (format_metrics output)
- [ ] **Step 2: Implement display.py**
- [ ] **Step 3: Implement app.py** (includes `TokenBudget` class, `run_agent_cycle()`, graceful shutdown via `asyncio.Event` + signal handlers)
- [ ] **Step 4: Create main.py**
- [ ] **Step 5: Run all tests** `python -m pytest tests/ -v`
- [ ] **Step 6: Smoke test** `python -c "from src.cli.app import run; print('OK')"`
- [ ] **Step 7: Commit** `git commit -m "feat: CLI display, main app with graceful shutdown and token budget"`

---

## Post-Implementation Checklist

- [ ] `python -m pytest tests/ -v` — All tests pass
- [ ] `python -c "from src.cli.app import run"` — Imports OK
- [ ] Fill `.env` with real OKX API keys + Anthropic API key
- [ ] Review `config/settings.yaml` — verify symbol, timeframe, approval ON
- [ ] Review `config/trader.yaml` — configure persona
- [ ] Fund OKX with small USDT (e.g. 100 USDT)
- [ ] `python main.py` — Agent starts, connects, first analysis cycle runs
- [ ] Verify approval gate — approve or reject first trade
- [ ] Check `data/tradebot.db` — decision logs recorded
