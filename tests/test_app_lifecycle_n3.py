"""Source-level regression guard for N3 service shutdown (spec §8.3 + §5.7).

Rationale: a replay-style unit test of the finally block tests a copy of
the logic, not src/cli/app.py itself — it gives zero regression protection
if someone later deletes `await deps.macro.close()` from the real file.
Instead, we assert directly on the source of src/cli/app.py:
  1. All 5 expected close() calls are present.
  2. They appear in the spec §5.7-mandated order.
  3. Each N3 close is preceded by a `try:` (so it is exception-wrapped).

This is brittle to source reformatting (e.g., using a class method instead
of a bare expression), but that brittleness is intentional — any refactor
of the shutdown block is exactly the change that should force this test to
be re-read. If the shutdown moves to a helper function, update both
app.py and this test together.
"""
from pathlib import Path

import pytest


_APP_PATH = Path(__file__).resolve().parent.parent / "src" / "cli" / "app.py"


def _source() -> str:
    return _APP_PATH.read_text()


def _find_line(source: str, needle: str) -> int:
    for i, line in enumerate(source.splitlines()):
        if needle in line:
            return i
    raise AssertionError(f"not found in src/cli/app.py: {needle!r}")


EXPECTED_CLOSE_ORDER = (
    "exchange.close()",
    "deps.news.close()",
    "deps.macro.close()",
    "deps.crypto_etf.close()",
    "deps.onchain.close()",
)


def test_all_expected_close_calls_present():
    source = _source()
    for call in EXPECTED_CLOSE_ORDER:
        assert call in source, f"Missing shutdown call: {call}"


def test_close_calls_in_spec_mandated_order():
    """Spec §5.7: exchange → news → macro → crypto_etf → onchain."""
    source = _source()
    line_numbers = [_find_line(source, call) for call in EXPECTED_CLOSE_ORDER]
    assert line_numbers == sorted(line_numbers), (
        f"Close calls out of order. Got line numbers {line_numbers} for "
        f"{EXPECTED_CLOSE_ORDER}"
    )


@pytest.mark.parametrize("close_call", [
    "deps.macro.close()",
    "deps.crypto_etf.close()",
    "deps.onchain.close()",
])
def test_n3_close_is_wrapped_in_try_except(close_call: str):
    """Each N3 close must live inside a try/except so a failing close
    does not abort cleanup of siblings (spec §5.7 'per-service try/except').

    Heuristic: within 5 lines before the close call, there should be a
    `try:` on its own line. This matches the N2 pattern for deps.news.close().
    """
    source = _source()
    lines = source.splitlines()
    line_no = _find_line(source, close_call)
    window = lines[max(0, line_no - 5):line_no]
    assert any(L.strip() == "try:" for L in window), (
        f"{close_call} is not preceded by a `try:` within 5 lines — "
        f"window was:\n" + "\n".join(window)
    )


def test_n3_close_calls_inside_finally_block():
    """The N3 closes must be inside the outer finally block (not after it,
    where they would not run on the exception path).

    Heuristic: the last `finally:` occurring BEFORE `deps.macro.close()`
    must also occur BEFORE `deps.onchain.close()`, and there must be no
    intervening `scheduler_task =` line (which signals re-entry into the
    try body above).
    """
    source = _source()
    macro_line = _find_line(source, "deps.macro.close()")
    onchain_line = _find_line(source, "deps.onchain.close()")
    lines = source.splitlines()
    finally_candidates = [
        i for i, L in enumerate(lines)
        if L.strip() == "finally:" and i < macro_line
    ]
    assert finally_candidates, "No `finally:` found before N3 closes"
    last_finally = max(finally_candidates)
    between = "\n".join(lines[last_finally:onchain_line])
    assert "scheduler_task =" not in between, (
        "N3 closes do not appear to live inside the expected finally block"
    )
