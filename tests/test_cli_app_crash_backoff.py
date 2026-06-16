"""半 A — 跨 cycle 崩溃退避重唤（spec §1）。"""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from sqlalchemy import select

from src.storage.models import AgentCycle


def test_trading_deps_has_scheduler_interval_min_field():
    """TradingDeps 暴露 scheduler_interval_min（退避封顶来源），默认 15，可覆写。"""
    from src.agent.trader import TradingDeps

    deps = TradingDeps(
        symbol="BTC/USDT:USDT", timeframe="15m",
        market_data=MagicMock(), exchange=MagicMock(), technical=MagicMock(),
        memory=MagicMock(), session_id="s",
    )
    assert deps.scheduler_interval_min == 15

    deps2 = TradingDeps(
        symbol="BTC/USDT:USDT", timeframe="15m",
        market_data=MagicMock(), exchange=MagicMock(), technical=MagicMock(),
        memory=MagicMock(), session_id="s", scheduler_interval_min=30,
    )
    assert deps2.scheduler_interval_min == 30
