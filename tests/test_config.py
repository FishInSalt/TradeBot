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
  personality: aggressive
  trading_style: swing
  position_sizing: percentage
  max_position_pct: 50
  preferred_leverage: 5
  stop_loss_pct: 2.0
  take_profit_pct: 8.0
""")
    from src.config import load_trader_config
    config = load_trader_config(trader_file)
    assert config.persona.personality == "aggressive"
    assert config.persona.trading_style == "swing"
    assert config.persona.preferred_leverage == 5


def test_load_trader_config_both_optional(tmp_path: Path):
    """personality and trading_style omitted should both default to None."""
    trader_file = tmp_path / "trader.yaml"
    trader_file.write_text("""
persona:
  position_sizing: percentage
""")
    from src.config import load_trader_config
    config = load_trader_config(trader_file)
    assert config.persona.personality is None
    assert config.persona.trading_style is None


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


def test_news_config_defaults():
    from src.config import NewsConfig
    config = NewsConfig()
    assert config.enabled is True


def test_settings_with_news(tmp_path: Path):
    """news.enabled=false disables NewsService initialization."""
    settings_file = tmp_path / "settings.yaml"
    settings_file.write_text("""
exchange:
  name: okx
news:
  enabled: false
""")
    from src.config import load_settings
    settings = load_settings(settings_file, env_overrides={})
    assert settings.news.enabled is False


def test_settings_without_news(tmp_path: Path):
    """news section is optional and defaults to enabled."""
    settings_file = tmp_path / "settings.yaml"
    settings_file.write_text("exchange:\n  name: okx\n")
    from src.config import load_settings
    settings = load_settings(settings_file, env_overrides={})
    assert settings.news.enabled is True


# --- N3 config ---

def test_macro_config_defaults():
    from src.config import MacroConfig
    cfg = MacroConfig()
    assert cfg.enabled is True
    assert cfg.fred_api_key == ""
    assert cfg.alpha_vantage_api_key == ""
    assert cfg.coingecko_demo_api_key == ""


def test_crypto_etf_config_defaults():
    from src.config import CryptoEtfConfig
    cfg = CryptoEtfConfig()
    assert cfg.enabled is True
    assert cfg.sosovalue_api_key == ""


def test_onchain_config_defaults():
    from src.config import OnchainConfig
    cfg = OnchainConfig()
    assert cfg.enabled is True


def test_settings_includes_n3_configs():
    from src.config import Settings
    s = Settings()
    assert s.macro.enabled is True
    assert s.crypto_etf.enabled is True
    assert s.onchain.enabled is True


def test_load_settings_env_overrides_n3_keys(tmp_path):
    """4 new env vars should populate config when the YAML leaves them blank."""
    from src.config import load_settings
    yaml_path = tmp_path / "settings.yaml"
    yaml_path.write_text("trading:\n  symbol: 'BTC/USDT:USDT'\n")
    env = {
        "FRED_API_KEY": "fred-test",
        "ALPHA_VANTAGE_API_KEY": "av-test",
        "COINGECKO_DEMO_API_KEY": "cg-test",
        "SOSOVALUE_API_KEY": "soso-test",
    }
    settings = load_settings(path=yaml_path, env_overrides=env)
    assert settings.macro.fred_api_key == "fred-test"
    assert settings.macro.alpha_vantage_api_key == "av-test"
    assert settings.macro.coingecko_demo_api_key == "cg-test"
    assert settings.crypto_etf.sosovalue_api_key == "soso-test"


def test_load_settings_yaml_overrides_n3_keys_wins(tmp_path):
    """YAML values take precedence over env vars."""
    from src.config import load_settings
    yaml_path = tmp_path / "settings.yaml"
    yaml_path.write_text(
        "macro:\n"
        "  fred_api_key: 'yaml-fred'\n"
    )
    env = {"FRED_API_KEY": "env-fred"}
    settings = load_settings(path=yaml_path, env_overrides=env)
    assert settings.macro.fred_api_key == "yaml-fred"


# --- Iter 2b T1: sandbox + OKX_DEMO_* credentials split ---

import tempfile
from pathlib import Path
from src.config import load_settings


def _write_yaml_settings(content: str = "") -> Path:
    """Helper: write a minimal settings.yaml to a temp file and return path."""
    tmp = tempfile.NamedTemporaryFile(
        mode="w", suffix=".yaml", delete=False, encoding="utf-8",
    )
    tmp.write(content)
    tmp.close()
    return Path(tmp.name)


def test_load_settings_sandbox_true_reads_demo_credentials():
    path = _write_yaml_settings("")
    env = {
        "OKX_SANDBOX": "true",
        "OKX_DEMO_API_KEY": "demo_key",
        "OKX_DEMO_SECRET": "demo_secret",
        "OKX_DEMO_PASSWORD": "demo_pwd",
        "OKX_API_KEY": "live_key",
        "OKX_SECRET": "live_secret",
        "OKX_PASSWORD": "live_pwd",
    }
    settings = load_settings(path, env_overrides=env)
    assert settings.exchange.api_key == "demo_key"
    assert settings.exchange.secret == "demo_secret"
    assert settings.exchange.password == "demo_pwd"
    assert settings.exchange.sandbox is True


def test_load_settings_sandbox_false_reads_live_credentials():
    path = _write_yaml_settings("")
    env = {
        "OKX_SANDBOX": "false",
        "OKX_DEMO_API_KEY": "demo_key",
        "OKX_API_KEY": "live_key",
        "OKX_SECRET": "live_secret",
        "OKX_PASSWORD": "live_pwd",
    }
    settings = load_settings(path, env_overrides=env)
    assert settings.exchange.api_key == "live_key"
    assert settings.exchange.sandbox is False


def test_load_settings_missing_sandbox_defaults_live():
    path = _write_yaml_settings("")
    env = {"OKX_API_KEY": "live_key", "OKX_SECRET": "live_s", "OKX_PASSWORD": "live_p"}
    settings = load_settings(path, env_overrides=env)
    assert settings.exchange.sandbox is False
    assert settings.exchange.api_key == "live_key"


def test_load_settings_yaml_sandbox_true_wins_over_env_missing():
    path = _write_yaml_settings("exchange:\n  sandbox: true\n")
    env = {"OKX_DEMO_API_KEY": "demo_k", "OKX_DEMO_SECRET": "d_s", "OKX_DEMO_PASSWORD": "d_p"}
    settings = load_settings(path, env_overrides=env)
    assert settings.exchange.sandbox is True
    assert settings.exchange.api_key == "demo_k"


def test_load_settings_yaml_sandbox_false_overrides_env_true():
    """YAML 显式 sandbox=false 必须覆盖 OKX_SANDBOX=true env — final_sandbox 单一 SoT 关键分支.

    若 final_sandbox 错用 env-derived sandbox_env (非 exchange["sandbox"]),
    此场景会错走 demo credentials 路径 → demo endpoint + 空 live credentials
    或 demo credentials 的 live 标签, auth 失败时 error message 误导.
    """
    path = _write_yaml_settings("exchange:\n  sandbox: false\n")
    env = {
        "OKX_SANDBOX": "true",
        "OKX_API_KEY": "live_key", "OKX_SECRET": "live_s", "OKX_PASSWORD": "live_p",
        "OKX_DEMO_API_KEY": "demo_k",
    }
    settings = load_settings(path, env_overrides=env)
    assert settings.exchange.sandbox is False
    assert settings.exchange.api_key == "live_key"


def test_load_settings_empty_env_dict_defaults_to_live_empty_credentials():
    path = _write_yaml_settings("")
    settings = load_settings(path, env_overrides={})
    assert settings.exchange.sandbox is False
    assert settings.exchange.api_key == ""
    assert settings.exchange.secret == ""
    assert settings.exchange.password == ""
