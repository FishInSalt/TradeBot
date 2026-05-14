"""Iter tool-opt-mark-vs-last tests.

Spec: docs/superpowers/specs/2026-05-14-iter-tool-opt-mark-vs-last-design.md

Test pattern:
- OKX-side: mock `_client` (CCXT) with MagicMock; mark endpoint returns full V5
  envelope `{"code": "0", "msg": "", "data": [{"instId", "instType", "markPx",
  "ts"}]}` per project_iter2_mock_fidelity_lesson.
- Sim-side: direct attribute set on `_latest_ticker`.
- Byte-equal for full lines with fully fixture-controlled values; substring for
  lines carrying variable order IDs / amounts / contracts.
"""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest


# ============ Task 1: BaseExchange attribute + abstract method ============

def test_base_algo_trigger_reference_default_last():
    """Spec §3.1: BaseExchange.algo_trigger_reference is a class attribute
    defaulting to "last". OKXExchange and SimulatedExchange inherit unchanged.
    """
    from src.integrations.exchange.base import BaseExchange
    from src.integrations.exchange.okx import OKXExchange
    from src.integrations.exchange.simulated import SimulatedExchange

    assert BaseExchange.algo_trigger_reference == "last"
    assert OKXExchange.algo_trigger_reference == "last"
    assert SimulatedExchange.algo_trigger_reference == "last"
