"""§2 MidCycleEventInjector capability + TradingDeps 注入字段单测。"""
from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock

import pytest


def make_deps(**overrides):
    """最小 TradingDeps（仿 test_tool_call_recorder.make_deps，注入字段可覆写）。"""
    from src.agent.trader import TradingDeps
    kwargs = dict(
        symbol="BTC/USDT:USDT",
        timeframe="15m",
        market_data=MagicMock(),
        exchange=MagicMock(),
        technical=MagicMock(),
        memory=AsyncMock(),
        session_id="sess-test",
        cycle_id="cyc-test",
    )
    kwargs.update(overrides)
    return TradingDeps(**kwargs)


def test_trading_deps_injection_fields_default_off():
    """新字段默认值 = 注入关闭：fn 双 None、累积器空、cycle_started_at None。"""
    deps = make_deps()
    assert deps.drain_pending_events_fn is None
    assert deps.requeue_events_fn is None
    assert deps.injected_events_log == []
    assert deps.cycle_started_at is None


def test_trading_deps_log_not_shared_between_instances():
    """default_factory 隔离：两实例不共享累积器 list。"""
    d1, d2 = make_deps(), make_deps()
    d1.injected_events_log.append({"x": 1})
    assert d2.injected_events_log == []
