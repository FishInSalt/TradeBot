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
    fee_rate: float | None = None
    precision: dict[str, int] | None = None
    sandbox: bool = False


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


class LLMBudgetConfig(BaseModel):
    daily_max_tokens: int = 10000000


class DatabaseConfig(BaseModel):
    url: str = "sqlite+aiosqlite:///data/tradebot.db"


class ApprovalConfig(BaseModel):
    enabled: bool = True
    timeout_seconds: int = 300


class NewsConfig(BaseModel):
    enabled: bool = True


class MacroConfig(BaseModel):
    enabled: bool = True
    fred_api_key: str = ""              # env FRED_API_KEY
    alpha_vantage_api_key: str = ""     # env ALPHA_VANTAGE_API_KEY
    coingecko_demo_api_key: str = ""    # env COINGECKO_DEMO_API_KEY


class CryptoEtfConfig(BaseModel):
    enabled: bool = True
    sosovalue_api_key: str = ""         # env SOSOVALUE_API_KEY


class OnchainConfig(BaseModel):
    enabled: bool = True


class Settings(BaseModel):
    exchange: ExchangeConfig = ExchangeConfig()
    trading: TradingConfig = TradingConfig()
    models: ModelsConfig | None = None
    scheduler: SchedulerConfig = SchedulerConfig()
    llm_budget: LLMBudgetConfig = LLMBudgetConfig()
    database: DatabaseConfig = DatabaseConfig()
    approval: ApprovalConfig = ApprovalConfig()
    news: NewsConfig = NewsConfig()
    macro: MacroConfig = MacroConfig()
    crypto_etf: CryptoEtfConfig = CryptoEtfConfig()
    onchain: OnchainConfig = OnchainConfig()


class PersonaConfig(BaseModel):
    """Trader persona configuration.

    Two field categories with very different runtime status:

    Active (consumed by src/agent/persona.py to build the system prompt):
    - personality: trader temperament (conservative / moderate / aggressive)
    - trading_style: strategy preference (trend_following / swing / breakout)

    P3 placeholders (kept in code for re-introduction at hard-risk-control
    time before live trading; currently NOT injected into the prompt and
    NOT validated at tool entry, by A1 design 2026-04-15 — see
    docs/superpowers/specs/2026-04-15-agent-experience-design.md):
    - position_sizing: only the "percentage" path is wired through
      open_position / place_limit_order; "fixed" is reserved
    - max_position_pct, preferred_leverage, stop_loss_pct, take_profit_pct:
      numeric anchors deliberately withheld from the prompt so the
      observation period can reveal the agent's true decision boundary

    R2-6 (2026-05-01) reaffirmed wontfix-by-design after sim #4 P0-2
    (39.94% effective vs 30% configured) traced back to this A1 decision.
    Drift-guards in tests/test_persona.py and tests/test_wizard.py lock
    the placeholder behavior — do not relax without revisiting A1.
    """
    personality: Literal["conservative", "moderate", "aggressive"] | None = None
    trading_style: Literal["trend_following", "swing", "breakout"] | None = None
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
    # Env-derived sandbox flag — seed for setdefault
    sandbox_env = env_overrides.get("OKX_SANDBOX", "").lower() == "true"
    exchange.setdefault("sandbox", sandbox_env)
    # Final sandbox = YAML-set value (if any) else env-derived; single source of truth.
    final_sandbox = bool(exchange["sandbox"])

    if final_sandbox:
        exchange.setdefault("api_key", env_overrides.get("OKX_DEMO_API_KEY", ""))
        exchange.setdefault("secret", env_overrides.get("OKX_DEMO_SECRET", ""))
        exchange.setdefault("password", env_overrides.get("OKX_DEMO_PASSWORD", ""))
    else:
        exchange.setdefault("api_key", env_overrides.get("OKX_API_KEY", ""))
        exchange.setdefault("secret", env_overrides.get("OKX_SECRET", ""))
        exchange.setdefault("password", env_overrides.get("OKX_PASSWORD", ""))
    data["exchange"] = exchange

    # N3: macro + crypto_etf env overrides (YAML values take precedence)
    macro = data.get("macro", {})
    macro.setdefault("fred_api_key", env_overrides.get("FRED_API_KEY", ""))
    macro.setdefault("alpha_vantage_api_key",
                     env_overrides.get("ALPHA_VANTAGE_API_KEY", ""))
    macro.setdefault("coingecko_demo_api_key",
                     env_overrides.get("COINGECKO_DEMO_API_KEY", ""))
    data["macro"] = macro

    crypto_etf = data.get("crypto_etf", {})
    crypto_etf.setdefault("sosovalue_api_key",
                          env_overrides.get("SOSOVALUE_API_KEY", ""))
    data["crypto_etf"] = crypto_etf

    return Settings(**data)


def load_trader_config(path: Path = Path("config/trader.yaml")) -> TraderConfig:
    with open(path) as f:
        data = yaml.safe_load(f) or {}
    return TraderConfig(**data)
