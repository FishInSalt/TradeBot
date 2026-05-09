"""Test fixtures for Phase 2 cross-sim analytics. Underscore = internal."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from uuid import uuid4

from scripts._sim_metrics import R2_7_MERGED_AT


def _safe_created_at(offset_days: int = 1) -> datetime:
    """Default session created_at: post-R2-7 cutoff to avoid legacy reject."""
    return R2_7_MERGED_AT + timedelta(days=offset_days)


def _resolve_db_path(engine) -> str:
    """Extract sqlite filesystem path from async engine URL (for subprocess tests)."""
    url = str(engine.url)
    return url.replace("sqlite+aiosqlite:///", "")


# Fixture builders populated by T2 (make_session / make_cycle / make_open_lot / make_close_fill).
