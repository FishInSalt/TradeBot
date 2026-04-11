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


def test_alerts_config_defaults():
    """AlertsConfig 应有合理默认值。"""
    from src.config import AlertsConfig
    config = AlertsConfig()
    assert config.enabled is True
    assert config.window_minutes == 5
    assert config.threshold_pct == 3.0
    assert config.cooldown_minutes == 15


def test_settings_with_alerts(tmp_path: Path):
    """Settings 应能加载 alerts 配置段。"""
    settings_file = tmp_path / "settings.yaml"
    settings_file.write_text("""
exchange:
  name: okx
alerts:
  enabled: true
  window_minutes: 10
  threshold_pct: 5.0
  cooldown_minutes: 30
""")
    from src.config import load_settings
    settings = load_settings(settings_file, env_overrides={})
    assert settings.alerts.enabled is True
    assert settings.alerts.window_minutes == 10
    assert settings.alerts.threshold_pct == 5.0
    assert settings.alerts.cooldown_minutes == 30


def test_settings_without_alerts(tmp_path: Path):
    """不提供 alerts 配置段时应使用默认值。"""
    settings_file = tmp_path / "settings.yaml"
    settings_file.write_text("""
exchange:
  name: okx
""")
    from src.config import load_settings
    settings = load_settings(settings_file, env_overrides={})
    assert settings.alerts.enabled is True
    assert settings.alerts.window_minutes == 5


def test_settings_alerts_disabled(tmp_path: Path):
    """alerts.enabled=false 应正确加载。"""
    settings_file = tmp_path / "settings.yaml"
    settings_file.write_text("""
exchange:
  name: okx
alerts:
  enabled: false
""")
    from src.config import load_settings
    settings = load_settings(settings_file, env_overrides={})
    assert settings.alerts.enabled is False


def test_settings_models_optional(tmp_path: Path):
    """settings.yaml 中不提供 models 配置段时 Settings.models 应为 None。"""
    settings_file = tmp_path / "settings.yaml"
    settings_file.write_text("""
exchange:
  name: okx
""")
    from src.config import load_settings
    settings = load_settings(settings_file, env_overrides={})
    assert settings.models is None


def test_settings_models_still_works(tmp_path: Path):
    """settings.yaml 中提供 models 配置段时应正常加载（向后兼容）。"""
    settings_file = tmp_path / "settings.yaml"
    settings_file.write_text("""
exchange:
  name: okx
models:
  default: anthropic:claude-sonnet-4-20250514
  strong: anthropic:claude-opus-4-6
  weak: anthropic:claude-haiku-4-5-20251001
  routing:
    market_analysis: strong
    trade_decision: strong
    news_summary: weak
    review: weak
""")
    from src.config import load_settings
    settings = load_settings(settings_file, env_overrides={})
    assert settings.models is not None
    assert settings.models.strong == "anthropic:claude-opus-4-6"
