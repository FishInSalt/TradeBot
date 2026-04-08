from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from src.agent.trader import TradingDeps


async def save_memory(
    deps: TradingDeps, category: str, content: str, importance: float = 0.5
) -> str:
    """Save a learning or observation to long-term memory.
    category: trade_review / market_pattern / lesson
    importance: 0.0-1.0, higher = more important, will be recalled first"""
    importance = max(0.0, min(1.0, importance))
    await deps.memory.save_long_term(category, content, relevance_score=importance)
    return f"Memory saved [{category}] (importance={importance:.1f}): {content[:80]}"
