from __future__ import annotations

from sqlalchemy import delete, desc, select
from sqlalchemy.ext.asyncio import AsyncEngine

from src.storage.database import get_session
from src.storage.models import MemoryEntry


class MemoryService:
    def __init__(self, engine: AsyncEngine, session_id: str = "default"):
        self.engine = engine
        self.session_id = session_id

    async def save_long_term(
        self, category: str, content: str, relevance_score: float = 0.5
    ) -> None:
        """Save a long-term memory entry."""
        memory_entry = MemoryEntry(
            session_id=self.session_id,
            memory_type="long_term",
            category=category,
            content=content,
            relevance_score=relevance_score,
        )
        async with get_session(self.engine) as session:
            session.add(memory_entry)
            await session.commit()

    async def save_short_term(self, content: str) -> None:
        """Save a short-term memory entry."""
        memory_entry = MemoryEntry(
            session_id=self.session_id,
            memory_type="short_term",
            category="context",
            content=content,
            relevance_score=1.0,
        )
        async with get_session(self.engine) as session:
            session.add(memory_entry)
            await session.commit()

    async def get_relevant_memories(
        self, category: str | None = None, limit: int = 10
    ) -> list[MemoryEntry]:
        """Get long-term memories ordered by relevance score, optionally filtered by category."""
        async with get_session(self.engine) as session:
            query = select(MemoryEntry).where(
                MemoryEntry.session_id == self.session_id,
                MemoryEntry.memory_type == "long_term",
            )
            if category is not None:
                query = query.where(MemoryEntry.category == category)
            query = query.order_by(desc(MemoryEntry.relevance_score)).limit(limit)
            result = await session.execute(query)
            return result.scalars().all()

    async def get_short_term_context(self) -> list[MemoryEntry]:
        """Get short-term memory entries ordered by created_at DESC."""
        async with get_session(self.engine) as session:
            query = (
                select(MemoryEntry)
                .where(MemoryEntry.session_id == self.session_id)
                .where(MemoryEntry.memory_type == "short_term")
                .order_by(desc(MemoryEntry.created_at))
            )
            result = await session.execute(query)
            return result.scalars().all()

    async def clear_short_term(self) -> None:
        """Delete all short-term memory entries."""
        async with get_session(self.engine) as session:
            query = delete(MemoryEntry).where(
                MemoryEntry.session_id == self.session_id,
                MemoryEntry.memory_type == "short_term",
            )
            await session.execute(query)
            await session.commit()

    async def format_for_prompt(self) -> str:
        """Format memories for inclusion in a prompt."""
        long_term = await self.get_relevant_memories(limit=10)
        short_term = await self.get_short_term_context()

        if not long_term and not short_term:
            return "No relevant memories."

        lines = []

        if long_term:
            lines.append("=== Long-term Memory ===")
            for entry in long_term:
                lines.append(f"- [{entry.category}] {entry.content}")
            lines.append("")

        if short_term:
            lines.append("=== Recent Context ===")
            for entry in short_term:
                lines.append(f"- {entry.content}")

        return "\n".join(lines)
