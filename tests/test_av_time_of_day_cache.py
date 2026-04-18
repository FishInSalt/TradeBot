# tests/test_av_time_of_day_cache.py
"""Tests for Alpha Vantage time-of-day TTL helper (spec §5.2)."""
from datetime import datetime
from zoneinfo import ZoneInfo


def _patch_now(monkeypatch, et_dt: datetime) -> None:
    """Replace datetime.now inside alpha_vantage module with a fixed ET time."""
    import src.integrations.macro.alpha_vantage as mod

    class FakeDateTime(datetime):
        @classmethod
        def now(cls, tz=None):
            return et_dt.astimezone(tz) if tz else et_dt

    monkeypatch.setattr(mod, "datetime", FakeDateTime)


def test_ttl_weekend_saturday(monkeypatch):
    from src.integrations.macro.alpha_vantage import alpha_vantage_ttl_seconds
    # 2026-04-18 is Saturday
    _patch_now(monkeypatch, datetime(2026, 4, 18, 14, 0, tzinfo=ZoneInfo("America/New_York")))
    assert alpha_vantage_ttl_seconds() == 12 * 3600.0


def test_ttl_weekend_sunday(monkeypatch):
    from src.integrations.macro.alpha_vantage import alpha_vantage_ttl_seconds
    _patch_now(monkeypatch, datetime(2026, 4, 19, 10, 0, tzinfo=ZoneInfo("America/New_York")))
    assert alpha_vantage_ttl_seconds() == 12 * 3600.0


def test_ttl_weekday_market_hours(monkeypatch):
    """Weekday 9:30-16:00 ET → 30min TTL."""
    from src.integrations.macro.alpha_vantage import alpha_vantage_ttl_seconds
    # Monday 10:00 AM ET
    _patch_now(monkeypatch, datetime(2026, 4, 20, 10, 0, tzinfo=ZoneInfo("America/New_York")))
    assert alpha_vantage_ttl_seconds() == 30 * 60.0


def test_ttl_weekday_market_open_edge(monkeypatch):
    """9:30 AM inclusive → market hours."""
    from src.integrations.macro.alpha_vantage import alpha_vantage_ttl_seconds
    _patch_now(monkeypatch, datetime(2026, 4, 20, 9, 30, tzinfo=ZoneInfo("America/New_York")))
    assert alpha_vantage_ttl_seconds() == 30 * 60.0


def test_ttl_weekday_just_before_open(monkeypatch):
    """9:29 AM → pre-market 4h TTL."""
    from src.integrations.macro.alpha_vantage import alpha_vantage_ttl_seconds
    _patch_now(monkeypatch, datetime(2026, 4, 20, 9, 29, tzinfo=ZoneInfo("America/New_York")))
    assert alpha_vantage_ttl_seconds() == 4 * 3600.0


def test_ttl_weekday_market_close_edge(monkeypatch):
    """16:00 exclusive → after-market 4h TTL."""
    from src.integrations.macro.alpha_vantage import alpha_vantage_ttl_seconds
    _patch_now(monkeypatch, datetime(2026, 4, 20, 16, 0, tzinfo=ZoneInfo("America/New_York")))
    assert alpha_vantage_ttl_seconds() == 4 * 3600.0


def test_ttl_weekday_just_before_close(monkeypatch):
    """15:59 → still market hours."""
    from src.integrations.macro.alpha_vantage import alpha_vantage_ttl_seconds
    _patch_now(monkeypatch, datetime(2026, 4, 20, 15, 59, tzinfo=ZoneInfo("America/New_York")))
    assert alpha_vantage_ttl_seconds() == 30 * 60.0


def test_ttl_weekday_after_hours(monkeypatch):
    """Weekday 20:00 ET → 4h TTL."""
    from src.integrations.macro.alpha_vantage import alpha_vantage_ttl_seconds
    _patch_now(monkeypatch, datetime(2026, 4, 20, 20, 0, tzinfo=ZoneInfo("America/New_York")))
    assert alpha_vantage_ttl_seconds() == 4 * 3600.0
