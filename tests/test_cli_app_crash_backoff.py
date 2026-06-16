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


@pytest.mark.parametrize("n, fallback, expected", [
    # fallback=1 → floor=min(2,1)=1 → 恒 1（no-op，本就每分钟巡检）
    (1, 1, 1), (5, 1, 1),
    # fallback=60 → 2,4,8,16,32,60(封顶),60…
    (1, 60, 2), (2, 60, 4), (3, 60, 8), (4, 60, 16),
    (5, 60, 32), (6, 60, 60), (7, 60, 60),
    # fallback=180 → 2,4,…,128,180(封顶)
    (1, 180, 2), (7, 180, 128), (8, 180, 180), (12, 180, 180),
])
def test_backoff_min_curve(n, fallback, expected):
    from src.cli.app import backoff_min
    assert backoff_min(n, fallback) == expected
