"""Session-level cycle tracking — independent of daily token budget reset.

Decoupled from TokenBudget (which has its own daily lifecycle)：cycle 时序
metric 是 session 语义（不跨日重置 last_cycle_ended_at），与 TokenBudget._used
归零节奏不同。R2-8a §4.5.3.
"""
from __future__ import annotations

from datetime import datetime


class SessionStats:
    """Session-level cycle tracker. 1 instance per cli session, lives from
    session start to shutdown. NOT reset on daily token budget reset
    (跨夜 wake interval 仍可见 → "+540 min from prev"）."""

    def __init__(self) -> None:
        self._cycle_count = 0
        self._total_tokens = 0
        self._last_cycle_ended_at: datetime | None = None

    def record_cycle(self, cycle_tokens: int, cycle_ended_at: datetime) -> None:
        """Called once per cycle, after format_cycle_output renders.

        forensic / retry-exhausted cycles 也调用此 (cycle_tokens=0)，
        消耗 trigger 容量但无 token 产出 — avg 反映容量浪费。
        """
        self._cycle_count += 1
        self._total_tokens += cycle_tokens
        self._last_cycle_ended_at = cycle_ended_at

    @property
    def cycle_count(self) -> int:
        return self._cycle_count

    @property
    def total_tokens(self) -> int:
        return self._total_tokens

    @property
    def avg_tokens_per_cycle(self) -> int:
        if self._cycle_count == 0:
            return 0
        return self._total_tokens // self._cycle_count

    @property
    def last_cycle_ended_at(self) -> datetime | None:
        return self._last_cycle_ended_at
