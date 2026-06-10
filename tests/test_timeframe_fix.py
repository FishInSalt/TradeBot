"""Integration tests for the 2026-06-09 uppercase-timeframe fix.

Root cause: session sim #17 was created with primary timeframe "1H" (uppercase,
a value the config comment advertised). That value flowed unvalidated into
deps.timeframe → the wake-up prompt ("Timeframe: 1H") → get_market_data →
ccxt.parse_timeframe, which rejects uppercase "H" and crashed 7 cycles.

Defense at three boundaries, all via src.utils.timeframe.normalize_timeframe:
  - Layer 2b: TradingConfig.timeframe validator (config-file boundary)
  - Layer 2a: WizardResult.__post_init__ (session-creation carrier → deps + DB)
  - Layer 3:  get_market_data graceful handling (agent-supplied arg boundary)
"""
import pytest
from unittest.mock import AsyncMock, MagicMock

from tests.fixtures.multi_tf_ohlcv import df_1h_250bars, fake_ticker_81870


# === Layer 2b: TradingConfig validates/normalizes at the config boundary ===

class TestTradingConfigTimeframe:
    def test_folds_uppercase_hour_to_lowercase(self):
        from src.config import TradingConfig
        assert TradingConfig(timeframe="1H").timeframe == "1h"

    def test_default_is_already_canonical(self):
        from src.config import TradingConfig
        assert TradingConfig().timeframe == "15m"

    def test_rejects_unsupported_timeframe(self):
        from pydantic import ValidationError
        from src.config import TradingConfig
        with pytest.raises(ValidationError):
            TradingConfig(timeframe="banana")


# === Layer 2a: WizardResult normalizes on construction (covers wizard + cfg) ===

def _make_wizard_result(timeframe):
    from src.cli.wizard import WizardResult
    from src.config import PersonaConfig
    from src.services.model_manager import ModelConfig
    return WizardResult(
        exchange_type="simulated",
        fee_rate=0.001,
        initial_balance=10000.0,
        api_credentials=None,
        symbol="BTC/USDT:USDT",
        timeframe=timeframe,
        model_config=ModelConfig(
            id="x", provider="deepseek", model="deepseek-v4-pro",
            api_key="", base_url=None,
        ),
        model=None,
        scheduler_interval_min=60,
        approval_enabled=False,
        token_budget=0,
        persona=PersonaConfig(),
        session_name="test",
    )


class TestWizardResultTimeframe:
    def test_folds_uppercase_hour_to_lowercase(self):
        assert _make_wizard_result("1H").timeframe == "1h"

    def test_lowercase_passes_through(self):
        assert _make_wizard_result("15m").timeframe == "15m"

    def test_rejects_unsupported_timeframe(self):
        with pytest.raises(ValueError):
            _make_wizard_result("banana")


# === Layer 3: get_market_data degrades gracefully, never crashes the cycle ===

def _build_gmd_deps(ticker, ohlcv_by_tf, tf="1h"):
    from src.services.technical import TechnicalAnalysisService
    deps = MagicMock()
    deps.symbol = "BTC/USDT:USDT"
    deps.timeframe = tf
    deps.technical = TechnicalAnalysisService()
    deps.market_data = MagicMock()
    deps.market_data.get_ticker = AsyncMock(return_value=ticker)

    async def _ohlcv(sym, t, limit):
        if t not in ohlcv_by_tf:
            raise RuntimeError(f"no fixture for {t}")
        return ohlcv_by_tf[t]

    deps.market_data.get_ohlcv_dataframe = AsyncMock(side_effect=_ohlcv)
    return deps


# === Layer 1: shipped config defaults must stay canonical (no uppercase) ===

class TestShippedConfigTimeframeIsCanonical:
    """Regression guard for the misleading config comment that advertised "1H":
    the shipped default timeframe must already be canonical (folding a no-op),
    so a fresh session never inherits an uppercase-unit timeframe."""

    @pytest.mark.parametrize("path", ["config/settings.yaml", "config/settings_sim.yaml"])
    def test_default_timeframe_is_already_canonical(self, path):
        import yaml
        from src.utils.timeframe import normalize_timeframe
        with open(path) as f:
            raw_tf = yaml.safe_load(f)["trading"]["timeframe"]
        assert normalize_timeframe(raw_tf) == raw_tf, (
            f"{path} ships a non-canonical timeframe {raw_tf!r} "
            f"(would be folded to {normalize_timeframe(raw_tf)!r})"
        )


class TestGetMarketDataTimeframeNormalization:
    @pytest.mark.asyncio
    async def test_uppercase_1H_is_normalized_and_fetches_1h(
        self, fake_ticker_81870, df_1h_250bars,
    ):
        # Agent passes "1H"; the tool must fetch "1h" (fixture only has "1h", so
        # an un-normalized "1H" would RuntimeError on the missing fixture).
        from src.agent.tools_perception import get_market_data
        deps = _build_gmd_deps(fake_ticker_81870, {"1h": df_1h_250bars})
        out = await get_market_data(deps, timeframe="1H")
        assert "=== Ticker" in out
        args = deps.market_data.get_ohlcv_dataframe.await_args
        assert args.args[1] == "1h", f"expected normalized '1h', got {args.args[1]!r}"

    @pytest.mark.asyncio
    async def test_unsupported_timeframe_returns_error_string_not_raise(
        self, fake_ticker_81870, df_1h_250bars,
    ):
        from src.agent.tools_perception import get_market_data
        deps = _build_gmd_deps(fake_ticker_81870, {"1h": df_1h_250bars})
        out = await get_market_data(deps, timeframe="banana")
        assert out.startswith("Error:"), f"expected graceful Error string, got {out[:80]!r}"
        # Must short-circuit before hitting the exchange.
        deps.market_data.get_ohlcv_dataframe.assert_not_awaited()
