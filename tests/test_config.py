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


def test_exchange_config_simulated_fields():
    from src.config import ExchangeConfig
    config = ExchangeConfig(name="simulated", fee_rate=0.0005, precision={"BTC/USDT:USDT": 3})
    assert config.fee_rate == 0.0005
    assert config.precision["BTC/USDT:USDT"] == 3


def test_exchange_config_okx_ignores_sim_fields():
    from src.config import ExchangeConfig
    config = ExchangeConfig(name="okx")
    assert config.fee_rate is None
    assert config.precision is None
