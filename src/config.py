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


class AlertsConfig(BaseModel):
    enabled: bool = True
    window_minutes: int = 60
    threshold_pct: float = 5.0


class Settings(BaseModel):
    exchange: ExchangeConfig = ExchangeConfig()
    trading: TradingConfig = TradingConfig()
    models: ModelsConfig | None = None
    scheduler: SchedulerConfig = SchedulerConfig()
    llm_budget: LLMBudgetConfig = LLMBudgetConfig()
    database: DatabaseConfig = DatabaseConfig()
    approval: ApprovalConfig = ApprovalConfig()
    alerts: AlertsConfig = AlertsConfig()


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
