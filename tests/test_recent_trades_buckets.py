"""Tests for the get_recent_trades count-bucket refactor + fetch_trades 张->base
unit normalization. Spec docs/superpowers/specs/2026-05-30-order-flow-tools-redesign-design.md
§3.4 (count buckets), §4.2 (Option B adapter), §5 ④⑤.
"""
import pytest
from unittest.mock import AsyncMock, MagicMock


@pytest.mark.asyncio
async def test_sim_fetch_trades_normalizes_contracts_to_base():
    """§4.2/④: raw ccxt amount is OKX contracts (张); multiply by real market
    contractSize so Trade.amount is base-currency. Mock-fidelity (⑤): include
    info.sz + contractSize != 1 (BTC swap 0.01)."""
    from src.integrations.exchange.simulated import SimulatedExchange
    ex = SimulatedExchange.__new__(SimulatedExchange)
    ex._ccxt = MagicMock()
    ex._ccxt.market.return_value = {"id": "BTC-USDT-SWAP", "contractSize": 0.01}
    ex._ccxt.fetch_trades = AsyncMock(return_value=[
        {"timestamp": 1, "side": "buy", "price": 70000.0, "amount": 5.0,
         "id": "a", "info": {"sz": "5"}},   # 5 张 * 0.01 = 0.05 base
        {"timestamp": 2, "side": "sell", "price": 70010.0, "amount": 2.0,
         "id": "b", "info": {"sz": "2"}},
    ])
    ex._validate_symbol = lambda s: None
    trades = await ex.fetch_trades("BTC/USDT:USDT", limit=500)
    assert trades[0].amount == pytest.approx(0.05)
    assert trades[1].amount == pytest.approx(0.02)


@pytest.mark.asyncio
async def test_sim_fetch_trades_contractsize_missing_defaults_to_one():
    from src.integrations.exchange.simulated import SimulatedExchange
    ex = SimulatedExchange.__new__(SimulatedExchange)
    ex._ccxt = MagicMock()
    ex._ccxt.market.return_value = {"id": "X"}  # no contractSize key
    ex._ccxt.fetch_trades = AsyncMock(return_value=[
        {"timestamp": 1, "side": "buy", "price": 100.0, "amount": 3.0, "id": "a"}])
    ex._validate_symbol = lambda s: None
    trades = await ex.fetch_trades("X/USDT:USDT", limit=500)
    assert trades[0].amount == pytest.approx(3.0)  # cs defaults to 1.0


@pytest.mark.asyncio
async def test_okx_fetch_trades_normalizes_contracts_to_base():
    from src.integrations.exchange.okx import OKXExchange
    ex = OKXExchange.__new__(OKXExchange)
    ex._client = MagicMock()
    ex._client.markets = {"ETH/USDT:USDT": {"contractSize": 0.1}}
    ex._client.market.return_value = {"contractSize": 0.1}
    ex._client.fetch_trades = AsyncMock(return_value=[
        {"timestamp": 1, "side": "buy", "price": 3000.0, "amount": 4.0, "id": "a",
         "info": {"sz": "4"}}])  # 4 张 * 0.1 = 0.4 base
    trades = await ex.fetch_trades("ETH/USDT:USDT", limit=500)
    assert trades[0].amount == pytest.approx(0.4)


def _mk_trades(specs):
    """specs: list of (ts_ms, side, price, base_amount)."""
    from src.integrations.exchange.base import Trade
    return [Trade(timestamp=ts, side=s, price=p, amount=a, trade_id=str(i))
            for i, (ts, s, p, a) in enumerate(specs)]


def _deps(trades):
    deps = MagicMock()
    deps.symbol = "BTC/USDT:USDT"
    deps.market_data.get_recent_trades = AsyncMock(return_value=trades)
    return deps


@pytest.mark.asyncio
async def test_recent_trades_count_buckets_5x100():
    from src.agent.tools_perception import get_recent_trades
    specs = [(1_000_000 + i * 1000, "buy" if i % 2 == 0 else "sell", 70000.0, 0.01)
             for i in range(500)]
    out = await get_recent_trades(_deps(_mk_trades(specs)))
    assert "last 500 ·" in out
    assert "Per 100-trade slice (newest first):" in out
    assert "1 (new)" in out and "5 (old)" in out
    assert "by count" in out and "by volume" in out


@pytest.mark.asyncio
async def test_recent_trades_usd_is_amount_times_price():
    from src.agent.tools_perception import get_recent_trades
    specs = [(1_000_000 + i * 1000, "buy", 70000.0, 0.001) for i in range(99)]
    specs.append((1_000_000 + 99_000, "sell", 70000.0, 1.0))  # 1.0 base * 70000 = $70K
    out = await get_recent_trades(_deps(_mk_trades(specs)))
    assert "$70.0K SELL" in out


@pytest.mark.asyncio
async def test_recent_trades_count_vs_volume_buy_pct_divergence():
    from src.agent.tools_perception import get_recent_trades
    specs = [(1_000_000 + i * 1000, "buy", 70000.0, 0.0001) for i in range(90)]
    specs += [(1_000_000 + (90 + i) * 1000, "sell", 70000.0, 1.0) for i in range(10)]
    out = await get_recent_trades(_deps(_mk_trades(specs)))
    assert "90% by count" in out
    assert "by volume" in out


@pytest.mark.asyncio
async def test_recent_trades_under_100_single_aggregate_no_table():
    from src.agent.tools_perception import get_recent_trades
    specs = [(1_000_000 + i * 1000, "buy", 70000.0, 0.01) for i in range(40)]
    out = await get_recent_trades(_deps(_mk_trades(specs)))
    assert "last 40 ·" in out
    assert "Per 100-trade slice" not in out


@pytest.mark.asyncio
async def test_recent_trades_partial_fewer_slices_with_real_counts():
    from src.agent.tools_perception import get_recent_trades
    specs = [(1_000_000 + i * 1000, "buy", 70000.0, 0.01) for i in range(250)]
    out = await get_recent_trades(_deps(_mk_trades(specs)))
    assert "last 250 ·" in out
    assert "[50 tr]" in out


@pytest.mark.asyncio
async def test_recent_trades_empty_and_failure():
    from src.agent.tools_perception import get_recent_trades
    out_empty = await get_recent_trades(_deps([]))
    assert "No recent trades." in out_empty
    deps = MagicMock(); deps.symbol = "BTC/USDT:USDT"
    deps.market_data.get_recent_trades = AsyncMock(side_effect=Exception("timeout"))
    out_fail = await get_recent_trades(deps)
    assert "Recent trades temporarily unavailable" in out_fail


@pytest.mark.asyncio
async def test_recent_trades_tr_per_s_na_on_zero_span():
    """Imp 3 (PR #65 review): all trades share one timestamp → window span 0 → tr/s
    renders 'n/a', not a 1e11 sentinel (fact-provider honesty; was `span_s or 1e-9`)."""
    from src.agent.tools_perception import get_recent_trades
    specs = [(1_000_000, "buy" if i % 2 == 0 else "sell", 70000.0, 0.01) for i in range(120)]
    out = await get_recent_trades(_deps(_mk_trades(specs)))
    assert "n/a tr/s" in out
    assert "100000000000" not in out   # no sentinel explosion
    assert "0.0s" in out               # span legitimately renders 0.0s


def test_get_recent_trades_returns_example_not_mangled_into_pseudo_type():
    """Imp 1 (PR #65 review): colon-free Returns first line keeps the call→output
    example out of a griffe pseudo-<type> (memory project_griffe_example_section_stripped;
    mirrors PR #64 get_order_book). Regression:
    '<type>A trades micro-report. Example for get_recent_trades(</type>'."""
    import re
    from src.agent.trader import create_trader_agent
    from src.config import PersonaConfig
    agent = create_trader_agent(model="test", persona_config=PersonaConfig())
    desc = agent._function_toolset.tools["get_recent_trades"].tool_def.description or ""
    assert "=== Recent Trades (BTC-USDT-SWAP · last 500" in desc  # example reaches LLM
    assert not re.search(r"<type>[^<]*Example", desc), \
        f"Returns example leaked into pseudo-<type> (griffe colon-split):\n{desc!r}"
