"""一次性 smoke：真实 ccxt.okx() → SimulatedExchange.fetch_* → 工具渲染。

闭合本 iter 的 mock 保真盲区（所有单测都 mock _ccxt，无端到端真实拉取）。
用法：python scripts/smoke_sim_microstructure.py
"""
import asyncio
from unittest.mock import AsyncMock, MagicMock

import ccxt.async_support as ccxt

from src.integrations.exchange.simulated import SimulatedExchange
from src.agent.tools_perception import get_order_book, get_recent_trades

SYMBOL = "BTC/USDT:USDT"


def _make_sim() -> SimulatedExchange:
    config = MagicMock()
    config.fee_rate = 0.0005
    return SimulatedExchange(config=config, db_engine=None, session_id="smoke", symbol=SYMBOL)


def _deps(market_data) -> MagicMock:
    d = MagicMock()
    d.symbol = SYMBOL
    d.market_data = market_data
    return d


async def main():
    ex = _make_sim()
    ex._ccxt = ccxt.okx()
    try:
        # 真实拉取（sim 映射层）
        ob = await ex.fetch_order_book(SYMBOL, depth=15)
        trades = await ex.fetch_trades(SYMBOL, limit=500)
        print(f"[raw] order book: {len(ob.bids)} bids / {len(ob.asks)} asks; trades: {len(trades)}")

        # 工具渲染层
        md = MagicMock()
        md.get_order_book = AsyncMock(return_value=ob)
        md.get_recent_trades = AsyncMock(return_value=trades)

        print("\n========== get_order_book ==========")
        print(await get_order_book(_deps(md), depth=15))
        print("\n========== get_recent_trades ==========")
        print(await get_recent_trades(_deps(md)))
    finally:
        await ex._ccxt.close()


if __name__ == "__main__":
    asyncio.run(main())
