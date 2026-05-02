"""SessionStats — session-level cycle tracker, decoupled from daily TokenBudget."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone


def test_session_stats_initial_state():
    from src.cli.session_state import SessionStats
    stats = SessionStats()
    assert stats.cycle_count == 0
    assert stats.total_tokens == 0
    assert stats.avg_tokens_per_cycle == 0
    assert stats.last_cycle_ended_at is None


def test_session_stats_record_single_cycle():
    from src.cli.session_state import SessionStats
    stats = SessionStats()
    end_ts = datetime(2026, 5, 2, 18, 14, 23, tzinfo=timezone.utc)
    stats.record_cycle(cycle_tokens=46_500, cycle_ended_at=end_ts)
    assert stats.cycle_count == 1
    assert stats.total_tokens == 46_500
    assert stats.avg_tokens_per_cycle == 46_500
    assert stats.last_cycle_ended_at == end_ts


def test_session_stats_record_multiple_cycles_avg():
    from src.cli.session_state import SessionStats
    stats = SessionStats()
    base = datetime(2026, 5, 2, 18, 14, 23, tzinfo=timezone.utc)
    for i, tokens in enumerate([40_000, 50_000, 30_000]):
        stats.record_cycle(tokens, base + timedelta(minutes=i * 5))
    assert stats.cycle_count == 3
    assert stats.total_tokens == 120_000
    assert stats.avg_tokens_per_cycle == 40_000  # 120000 // 3
    assert stats.last_cycle_ended_at == base + timedelta(minutes=10)


def test_session_stats_avg_zero_when_no_cycles():
    """Defensive: avg accessor on empty stats should return 0, not divide by zero."""
    from src.cli.session_state import SessionStats
    stats = SessionStats()
    assert stats.avg_tokens_per_cycle == 0


def test_session_stats_forensic_cycle_increments_count_but_not_tokens():
    """spec §4.5.3 lifecycle: forensic / retry-exhausted cycles 调 record_cycle(0, ts).
    cycle_count 计入但 total_tokens 不增 — avg 反映 trigger 容量浪费."""
    from src.cli.session_state import SessionStats
    stats = SessionStats()
    base = datetime(2026, 5, 2, 18, 14, 23, tzinfo=timezone.utc)
    stats.record_cycle(50_000, base)
    stats.record_cycle(0, base + timedelta(minutes=5))   # forensic
    stats.record_cycle(0, base + timedelta(minutes=10))  # retry-exhausted
    assert stats.cycle_count == 3
    assert stats.total_tokens == 50_000
    assert stats.avg_tokens_per_cycle == 16_666  # 50000 // 3


def test_session_stats_last_cycle_ended_at_overwrites_each_record():
    """T-INT-8 / T-INT-9 spec invariant: last_cycle_ended_at 跨日不重置（lifecycle bound to session
    not daily budget）—— record 调用每次覆盖到 latest cycle 的 end_ts."""
    from src.cli.session_state import SessionStats
    stats = SessionStats()
    day1 = datetime(2026, 5, 2, 23, 55, 0, tzinfo=timezone.utc)
    day2 = datetime(2026, 5, 3, 3, 55, 0, tzinfo=timezone.utc)
    stats.record_cycle(40_000, day1)
    stats.record_cycle(35_000, day2)
    assert stats.last_cycle_ended_at == day2
    # 跨日不归零（不显式 reset 调用）
