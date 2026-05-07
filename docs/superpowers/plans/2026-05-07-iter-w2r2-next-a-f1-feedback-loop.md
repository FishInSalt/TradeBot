# R2-Next-A — F1 Length Feedback Loop Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Close the F1 self-reinforcing length-drift loop exposed by sim #8 by injecting a 3-channel agent-facing length feedback signal (D1 visible truncation marker + D2 priors header word count + A3 explicit cap in persona), guarded by a silent secondary char floor against pathological single tokens.

**Architecture:** All state stays in current schema; no migration. Three changes converge on the same `CYCLE_DECISION_WORD_CAP=700` constant: persona text mentions it (A3), `_truncate_decision` uses it for word-boundary slicing + visible marker (D1), `_render_recent_summaries` shows each prior's `_count_words(decision)` in its header (D2). A second silent constant `CYCLE_DECISION_CHAR_HARD_FLOOR=8000` is the secondary safety net for `\S+`-bypass cases (long URL / JSON / CJK no-space / `|---|---|` separator).

**Tech Stack:** Python 3.13 / pydantic-ai 1.78 (frozen) / SQLAlchemy 2.x async / SQLite (aiosqlite) / pytest with caplog fixture.

**Spec:** `docs/superpowers/specs/2026-05-07-iter-w2r2-next-a-f1-feedback-loop-design.md`

**Branch:** `feature/iter-w2r2-next-a-f1-feedback-loop`

---

## File Structure

| File | Role | Status |
|---|---|---|
| `src/agent/persona.py` | hosts `CYCLE_DECISION_WORD_CAP` + `CYCLE_DECISION_CHAR_HARD_FLOOR` constants; persona Layer 1 text (A3 cap mention) | Modify |
| `src/cli/app.py` | hosts `_count_words` helper + rewritten `_truncate_decision` (D1) + updated `_render_recent_summaries` (D2) | Modify |
| `tests/test_cycle_summary_injection.py` | covers `_count_words`, `_truncate_decision`, `_render_recent_summaries` | Modify (3 existing) + Add (5 helper + 4 D1 main + 2 D1 secondary + 3 D2) |
| `tests/test_persona.py` | covers `_build_layer1` content + drift guards; cross-channel consistency test goes here | Modify (2 existing) + Add (3 A3 + 1 cross-channel) |
| `docs/metrics/agent-cycles-schema.md` | analyst observability doc | Modify (T6 housekeeping: A2 multi-LIKE SQL pattern note) |

**Decomposition note**: T1 only adds NEW constants and helper; existing `CYCLE_DECISION_HARD_CAP=4000` remains live. T2 then rewrites the function, removes the old constant, and updates the import. This avoids any broken intermediate state where the constant has been renamed but the function logic hasn't caught up.

---

## Task 1: `_count_words` helper + new module constants

**Files:**
- Modify: `src/agent/persona.py:10` (add 2 new constants alongside old)
- Modify: `src/cli/app.py:1-50` (add `_count_words` + `_WORD_RE`)
- Modify: `tests/test_cycle_summary_injection.py:1-100` (add 5 helper tests)

**Note on TDD:** This task adds a pure helper. We write all 5 tests first, run to confirm 5 fails (helper missing), then implement helper, then run to see 5 pass.

- [ ] **Step 1.1: Add 5 failing tests for `_count_words`**

Append to `tests/test_cycle_summary_injection.py` (after the existing `_truncate_decision` tests, around line 100):

```python
# ─── R2-Next-A: _count_words helper (T1) ───

def test_count_words_empty():
    """T1.1 (R2-Next-A): empty string → 0."""
    from src.cli.app import _count_words
    assert _count_words("") == 0


def test_count_words_whitespace_only():
    """T1.2 (R2-Next-A): whitespace-only string → 0 (no \\S+ runs)."""
    from src.cli.app import _count_words
    assert _count_words("   \t\n  ") == 0


def test_count_words_single_token():
    """T1.3 (R2-Next-A): single token → 1, regardless of internal punct."""
    from src.cli.app import _count_words
    assert _count_words("hello") == 1
    assert _count_words("hello-world") == 1  # hyphen NOT split (matches wc -w)
    assert _count_words("81,985.40") == 1    # comma/dot NOT split
    assert _count_words("don't") == 1        # apostrophe NOT split


def test_count_words_mixed_whitespace():
    """T1.4 (R2-Next-A): tabs, newlines, multi-space all delimit tokens."""
    from src.cli.app import _count_words
    assert _count_words("a\tb\nc d") == 4
    assert _count_words("  hello   world  ") == 2


def test_count_words_markdown_delimiters_count_as_words():
    """T1.5 (R2-Next-A): markdown `|`, `---`, `—` count as words.
    Naturally penalizes table-format inflation in agent's word budget
    without forcing a format change. See spec §4.3 + §3 Q1 (38.4% of
    cycles use markdown table delimiters)."""
    from src.cli.app import _count_words
    assert _count_words("| - Position |") == 4    # |, -, Position, |
    assert _count_words("|---|---|") == 1          # one continuous run


def test_count_words_unicode_handling():
    """T1.6 (R2-Next-A spec §9 risk mitigation): Unicode boundaries —
    emoji and CJK. `\\S+` is Unicode-aware in Python re; emoji and
    Chinese chars without whitespace count as one token. Matches helper
    design — Unicode-dense content is penalized the same way markdown
    noise is (no special handling, deterministic by whitespace only)."""
    from src.cli.app import _count_words
    assert _count_words("hello 😀 world") == 3
    assert _count_words("中文") == 1                    # no whitespace = 1 word
    assert _count_words("中文 测试") == 2               # space-delimited = 2 words
    assert _count_words("hello 中文 😀") == 3
```

- [ ] **Step 1.2: Run tests to verify they fail with ImportError**

Run: `uv run pytest tests/test_cycle_summary_injection.py -k "_count_words" -v`

Expected: 6 ERRORS with `ImportError: cannot import name '_count_words' from 'src.cli.app'`

- [ ] **Step 1.3: Add new constants to `src/agent/persona.py`**

Locate the existing constant at `src/agent/persona.py:6-10`:

```python
# R2-8d cycle decision hard cap — silent system safety net. NOT
# interpolated into persona text (D5: agent reads "never exceeding 600
# words" ceiling and self-controls; char cap protects against
# misbehavior). Used only by cli/app.py:_truncate_decision.
CYCLE_DECISION_HARD_CAP = 4000
```

Replace with (keep `CYCLE_DECISION_HARD_CAP=4000` for now; T2 removes it after rewriting the function):

```python
# R2-Next-A: hard cap exposed to agent via three channels:
#   D1 — _truncate_decision marker text (cli/app.py)
#   D2 — _render_recent_summaries header word count (cli/app.py)
#   A3 — persona §Cycle Closing Summary explicit "700 words" mention
# F1 length-loop closure (vs prior R2-8d D5 silent guardrail).
CYCLE_DECISION_WORD_CAP = 700

# Silent secondary char floor — defensive against pathological cases
# where a single `\S+` token is very large (long URL / JSON dump /
# no-space CJK / `|---|---|` table separator with no internal
# whitespace), which would bypass the word cap (counted as 1 word).
# NOT exposed to agent (no 4th channel) — preserves the word-unit
# primary signal of A3/D1/D2.
# sim #8 longest single token = 50 chars; max decision = 6131 chars
# → 8000 gives ~30% headroom over historical max; cap-bypass risk
# in current behavior is empirically zero, this is future-proofing.
CYCLE_DECISION_CHAR_HARD_FLOOR = 8000

# Legacy R2-8d constant — kept for one transitional task (T2 removes).
# DO NOT add new references; use CYCLE_DECISION_WORD_CAP instead.
CYCLE_DECISION_HARD_CAP = 4000
```

- [ ] **Step 1.4: Add `_count_words` helper to `src/cli/app.py`**

In `src/cli/app.py`, insert a new helper before the `_format_relative_time` function (around line 69). Add `import re` to the top of the file if not already present:

```python
import re

_WORD_RE = re.compile(r'\S+')


def _count_words(text: str) -> int:
    """Whitespace-split word count (wc -w convention).

    Single source of truth across:
      - _truncate_decision (D1: word-cap enforcement)
      - _render_recent_summaries (D2: priors header signal)
      - persona drift guards (A3: ceiling consistency)

    Convention: any consecutive non-whitespace run = 1 word. Markdown
    delimiters (`|`, `---`) count as words — naturally pressures agent
    toward concise output by penalizing formatting noise.
    """
    return len(_WORD_RE.findall(text))
```

Verify the existing `import re` at the top of `src/cli/app.py` (if missing, add to imports near `import logging`). The other imports (e.g., `logging`, `signal`, `uuid`) are already there.

- [ ] **Step 1.5: Run tests to verify all 6 helper tests pass**

Run: `uv run pytest tests/test_cycle_summary_injection.py -k "_count_words" -v`

Expected: 6 PASSED, 0 failed.

- [ ] **Step 1.6: Run full test suite to verify zero regression**

Run: `uv run pytest -x -q 2>&1 | tail -20`

Expected: 1221 PASSED (1215 baseline + 6 new helper tests), 3 SKIPPED, 0 FAILED.

- [ ] **Step 1.7: Commit**

```bash
git add src/agent/persona.py src/cli/app.py tests/test_cycle_summary_injection.py
git commit -m "$(cat <<'EOF'
feat(iter-w2r2-next-a): T1 _count_words helper + new constants

R2-Next-A T1: introduce CYCLE_DECISION_WORD_CAP (700) and
CYCLE_DECISION_CHAR_HARD_FLOOR (8000) constants alongside the legacy
CYCLE_DECISION_HARD_CAP (kept for T2 transitional removal). Add
_count_words helper using wc -w convention (`\S+` whitespace runs)
as single source of truth for word counting across D1/D2/A3 channels.

6 helper unit tests (T1.1-T1.6): empty / whitespace-only / single
token edge cases (hyphen / number / contraction not split) /
mixed whitespace / markdown delimiter accounting / Unicode (emoji + CJK).

Tests 1215 → 1221 (+6 net).

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 2: `_truncate_decision` rewrite (D1) + secondary char floor

**Files:**
- Modify: `src/cli/app.py:16` (import update — drop `HARD_CAP`, add `WORD_CAP` + `CHAR_HARD_FLOOR`)
- Modify: `src/cli/app.py:92-109` (rewrite `_truncate_decision`)
- Modify: `src/agent/persona.py:10` (remove legacy `CYCLE_DECISION_HARD_CAP`)
- Modify: `tests/test_cycle_summary_injection.py:5,68-98,342-356` (3 existing tests + add 6 new)

**TDD pattern:** Add 4 main + 2 secondary new tests (failing), update 3 existing tests to new contract (failing), implement function rewrite, run all to pass.

- [ ] **Step 2.1: Update existing 3 `_truncate_decision` tests to new contract**

Edit `tests/test_cycle_summary_injection.py:5` (module docstring):

```python
# Before:
"""...
  - _truncate_decision(text, hard_cap) -> str (WARNING log on truncation)
..."""

# After:
"""...
  - _count_words(text) -> int (whitespace-split, wc -w convention; T1)
  - _truncate_decision(text, hard_cap_words, hard_cap_chars) -> str (T2 D1; word-aware + silent secondary char floor)
..."""
```

Replace `tests/test_cycle_summary_injection.py:68-98` (3 existing tests):

```python
def test_truncate_decision_below_word_cap_returns_unchanged():
    """T2.1 (R2-Next-A): word count ≤ cap (700) returns unchanged, no log."""
    from src.cli.app import _truncate_decision

    text = " ".join(["word"] * 500)  # 500 words, well under 700
    assert _truncate_decision(text) == text


def test_truncate_decision_above_word_cap_truncates_with_marker_and_warning(caplog):
    """T2.2 (R2-Next-A): word count > cap → text cut at word boundary +
    standalone-line marker + WARNING log."""
    from src.cli.app import _truncate_decision

    text = " ".join(["word"] * 800)  # 800 words, over 700 cap
    with caplog.at_level(logging.WARNING, logger="src.cli.app"):
        result = _truncate_decision(text)
    # Marker on its own line, includes cap value
    assert result.endswith("\n... [truncated by system, cut at 700 words]")
    # Body before marker has exactly 700 words
    body = result.rsplit("\n... [truncated", 1)[0]
    assert len(body.split()) == 700
    # Word-aware boundary preserves token integrity (no mid-word cut)
    assert all(w == "word" for w in body.split())
    # WARNING log mentions word units
    assert any(
        "exceeded hard cap 700 words" in r.message and r.levelno == logging.WARNING
        for r in caplog.records
    )


def test_truncate_decision_does_not_truncate_at_exactly_word_cap():
    """T2.3 (R2-Next-A): boundary — exactly 700 words → no truncation."""
    from src.cli.app import _truncate_decision

    text = " ".join(["word"] * 700)
    result = _truncate_decision(text)
    assert result == text
    assert "[truncated" not in result
```

- [ ] **Step 2.2: Add 4 D1 main + 2 D1 secondary tests**

Append to `tests/test_cycle_summary_injection.py` (after the rewritten existing tests, around line 105):

```python
def test_truncate_marker_uses_constant_value():
    """T2.4 (R2-Next-A drift guard): marker text contains the literal
    `cut at {N} words` matching CYCLE_DECISION_WORD_CAP. Renaming or
    re-valuing the constant must update the marker — this test catches
    drift."""
    from src.cli.app import _truncate_decision
    from src.agent.persona import CYCLE_DECISION_WORD_CAP

    text = " ".join(["word"] * (CYCLE_DECISION_WORD_CAP + 50))
    result = _truncate_decision(text)
    assert f"cut at {CYCLE_DECISION_WORD_CAP} words" in result


def test_truncate_marker_on_standalone_newline():
    """T2.5 (R2-Next-A): marker is on its own line (preceded by `\\n`),
    not inline with truncated body. Visual standalone makes it obvious
    to the agent that content was cut here."""
    from src.cli.app import _truncate_decision

    text = " ".join(["word"] * 800)
    result = _truncate_decision(text)
    assert "\n... [truncated" in result, \
        "marker must be preceded by newline (standalone line)"


def test_truncate_word_boundary_does_not_split_token():
    """T2.6 (R2-Next-A): word-boundary slice preserves token integrity.
    With 800 long tokens, cap at 700, body must contain exactly 700
    intact tokens — never a partial word."""
    from src.cli.app import _truncate_decision

    long_word = "supercalifragilisticexpialidocious"  # 34 chars
    text = " ".join([long_word] * 800)
    result = _truncate_decision(text)
    body = result.rsplit("\n... [truncated", 1)[0]
    tokens = body.split()
    assert len(tokens) == 700
    assert all(t == long_word for t in tokens), \
        "all tokens must be intact (no mid-word slice)"


def test_truncate_pathological_single_token_falls_back_to_char_floor(caplog):
    """T2.7 (R2-Next-A P1 secondary): when the input is a single
    pathological token (no whitespace) far over the char floor, the
    word-cap path does NOT fire (1 word < 700) and the silent secondary
    char floor activates with legacy `[truncated]` marker.
    Tests the P1 belt-and-suspenders for `\\S+`-bypass cases."""
    from src.cli.app import _truncate_decision
    from src.agent.persona import CYCLE_DECISION_CHAR_HARD_FLOOR

    text = "x" * (CYCLE_DECISION_CHAR_HARD_FLOOR + 500)  # 8500 chars, 1 word
    with caplog.at_level(logging.WARNING, logger="src.cli.app"):
        result = _truncate_decision(text)
    # Secondary path uses legacy marker (silent — not "truncated by system")
    assert result.endswith(" ... [truncated]")
    assert "by system" not in result, \
        "secondary char floor must NOT use agent-facing word-cap marker"
    # Body sliced at char floor exactly
    body = result[:-len(" ... [truncated]")]
    assert len(body) == CYCLE_DECISION_CHAR_HARD_FLOOR
    # WARNING log mentions char path + words=1 diagnostic
    assert any(
        "exceeded char floor" in r.message and "words=1" in r.message
        for r in caplog.records
    )


def test_truncate_word_path_takes_precedence_over_char_floor():
    """T2.8 (R2-Next-A P1 secondary): when input exceeds BOTH word cap
    AND char floor, word-cap path wins (it's checked first). Marker is
    word-cap form, not legacy form."""
    from src.cli.app import _truncate_decision

    # 800 words, each "word" is 12 chars + 1 space = 800*13 = 10400 chars
    # Both caps exceeded, but word path checked first
    text = " ".join(["wordwordword"] * 800)
    assert len(text) > 8000  # above char floor
    assert len(text.split()) == 800  # above word cap
    result = _truncate_decision(text)
    # Word-cap marker, NOT legacy marker
    assert "cut at 700 words" in result
    assert not result.endswith(" ... [truncated]"), \
        "word-cap path should win — not legacy marker"
```

- [ ] **Step 2.3: Update `tests/test_cycle_summary_injection.py:342-371` render-side tests**

Locate `test_render_truncates_decision_above_hard_cap_via_truncate_decision` (around line 342) and replace with:

```python
def test_render_truncates_decision_above_word_cap_via_truncate_decision(caplog):
    """T2.9 (R2-Next-A): decisions > 700 words are word-truncated in
    the rendered block; marker on standalone line."""
    from src.cli.app import _render_recent_summaries

    now = datetime(2026, 5, 6, 12, 0, 0, tzinfo=timezone.utc)
    huge = " ".join(["wordy"] * 800)  # 800 words
    s = _make_summary(
        "abcdef01", "scheduled", huge,
        datetime(2026, 5, 6, 11, 55, 0, tzinfo=timezone.utc),
    )
    with caplog.at_level(logging.WARNING, logger="src.cli.app"):
        out = _render_recent_summaries([s], now)
    assert "\n... [truncated by system, cut at 700 words]" in out
    assert any("exceeded hard cap 700 words" in r.message for r in caplog.records)
```

Locate `test_render_keeps_full_decision_below_cap` (around line 359) and update docstring (test body unchanged — `"z" * 800` is 1 word, well under 700):

```python
def test_render_keeps_full_decision_below_cap():
    """T2.10 (R2-Next-A): under both word cap (1 word ≤ 700) and char
    floor (800 chars ≤ 8000), no truncation marker; body preserved."""
```

- [ ] **Step 2.4: Run updated tests to verify they fail (function not yet rewritten)**

Run: `uv run pytest tests/test_cycle_summary_injection.py -k "truncate" -v 2>&1 | tail -20`

Expected: All `truncate` tests FAIL — old tests no longer match (legacy `... [truncated]` form not produced; default arg name `hard_cap` doesn't match new tests). New T2.4-T2.8 fail too. This is the red phase of TDD.

- [ ] **Step 2.5: Update import in `src/cli/app.py:16`**

Replace:
```python
from src.agent.persona import CYCLE_DECISION_HARD_CAP, RuntimeConfig
```

with:
```python
from src.agent.persona import (
    CYCLE_DECISION_CHAR_HARD_FLOOR,
    CYCLE_DECISION_WORD_CAP,
    RuntimeConfig,
)
```

- [ ] **Step 2.6: Rewrite `_truncate_decision` in `src/cli/app.py:92-109`**

Replace the existing function (full rewrite — signature, body, docstring all change):

```python
def _truncate_decision(
    text: str,
    hard_cap_words: int = CYCLE_DECISION_WORD_CAP,
    hard_cap_chars: int = CYCLE_DECISION_CHAR_HARD_FLOOR,
) -> str:
    """Hard-truncate at word boundary with WARNING log + visible marker.

    R2-Next-A D1 (primary): word-unit aligned with persona ceiling.
    Word-boundary slice preserves whitespace-delimited token boundaries
    (no mid-word or mid-number cuts). Row-level integrity (markdown
    table rows / bullets) is NOT guaranteed — if cap falls between
    `|` cells of one row, that row will appear half-cut in the prior
    body. Acceptable: agent reads truncated priors as prose, not as
    rendered tables.

    Marker exposes word cap to agent (vs prior R2-8d D5 silent
    guardrail). Pairs with persona A3 explicit cap statement and D2
    priors header word count to close F1 length-feedback loop.

    Secondary defense (silent, NOT agent-facing): if word-cap path
    doesn't fire but len(text) > hard_cap_chars, fall back to silent
    char-slice with legacy `[truncated]` marker. Protects against
    pathological cases (long URL / JSON / `|---|---|` separator)
    where one `\\S+` token holds many chars.
    """
    matches = list(_WORD_RE.finditer(text))
    if len(matches) > hard_cap_words:
        cut_pos = matches[hard_cap_words].start()
        logger.warning(
            "Cycle decision exceeded hard cap %d words (got %d), truncating",
            hard_cap_words, len(matches),
        )
        return (
            f"{text[:cut_pos].rstrip()}\n"
            f"... [truncated by system, cut at {hard_cap_words} words]"
        )
    if len(text) > hard_cap_chars:  # P1 silent secondary safety net
        logger.warning(
            "Cycle decision exceeded char floor %d (got %d, words=%d), "
            "silent truncating",
            hard_cap_chars, len(text), len(matches),
        )
        return text[:hard_cap_chars] + " ... [truncated]"
    return text
```

- [ ] **Step 2.7: Remove legacy `CYCLE_DECISION_HARD_CAP` from `src/agent/persona.py`**

Delete these lines from `src/agent/persona.py` (added in T1 step 1.3):

```python
# Legacy R2-8d constant — kept for one transitional task (T2 removes).
# DO NOT add new references; use CYCLE_DECISION_WORD_CAP instead.
CYCLE_DECISION_HARD_CAP = 4000
```

- [ ] **Step 2.8: Run all `_truncate_decision` tests to verify they pass**

Run: `uv run pytest tests/test_cycle_summary_injection.py -k "truncate" -v 2>&1 | tail -25`

Expected: 9 PASSED (3 updated existing + 6 new T2.4-T2.8 + 1 render-side T2.9 + 1 below-cap T2.10), 0 FAILED.

- [ ] **Step 2.9: Run full test suite to verify zero regression**

Run: `uv run pytest -x -q 2>&1 | tail -10`

Expected: 1226 PASSED (1221 from T1 + 5 new T2 tests T2.4-T2.8; 3 existing _truncate tests + 2 existing render tests rewritten not added), 3 SKIPPED, 0 FAILED.

Note: D2 header tests (T3) and persona A3 tests (T4) still pending; will fail in T3/T4 — but at this point T2 has not touched render header field or persona, so those tests still pass with their current expectations.

- [ ] **Step 2.10: Commit**

```bash
git add src/agent/persona.py src/cli/app.py tests/test_cycle_summary_injection.py
git commit -m "$(cat <<'EOF'
feat(iter-w2r2-next-a): T2 _truncate_decision word-aware + secondary char floor

R2-Next-A T2 (D1): rewrite _truncate_decision to use word-boundary
slice with CYCLE_DECISION_WORD_CAP=700 + visible standalone-line
marker `... [truncated by system, cut at 700 words]`. Adds silent
secondary char floor at CYCLE_DECISION_CHAR_HARD_FLOOR=8000 chars
(legacy `... [truncated]` marker, not agent-facing) protecting
against pathological single-token cases (long URL / JSON / no-space
CJK / `|---|---|` separator) that would bypass word counting.

Reverses R2-8d D5 silent-guardrail design philosophy: agent now sees
the cap value when it fires (D1 channel of 3-channel signal stack).

Removes legacy CYCLE_DECISION_HARD_CAP constant; updates import in
cli/app.py to use new constants.

Tests: 3 existing _truncate_decision tests updated to new contract +
5 new (T2.4-T2.8 main/secondary edge cases) + 2 render-side tests
updated. Tests 1221 → 1226 (+5 net).

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 3: `_render_recent_summaries` D2 header word count

**Files:**
- Modify: `src/cli/app.py:189-214` (`_render_recent_summaries` body — add `_count_words` call + new header field)
- Modify: `tests/test_cycle_summary_injection.py:297-340` (3 existing render tests — header format change)
- Add: `tests/test_cycle_summary_injection.py` (3 new D2 tests)

**TDD pattern:** Update existing 3 tests to new format (failing), add 3 new (failing), implement render change, all pass.

- [ ] **Step 3.1: Update 3 existing render tests to new 5-field header format**

Edit `tests/test_cycle_summary_injection.py:297-313` `test_render_includes_header_and_one_block`. The header assertion needs to include `· {n} words]`:

```python
def test_render_includes_header_and_one_block():
    """T3.1 (R2-Next-A): single summary → header + one block with
    word count in the per-prior header (5-field format)."""
    from src.cli.app import _render_recent_summaries

    now = datetime(2026, 5, 6, 12, 0, 0, tzinfo=timezone.utc)
    body = "Stance: Holding long, thesis intact."
    s = _make_summary(
        "a3f2c1d8b", "scheduled", body,
        datetime(2026, 5, 6, 11, 52, 0, tzinfo=timezone.utc),
    )

    out = _render_recent_summaries([s], now)
    assert out.startswith(
        "Your prior cycle summaries (most recent N=3, from this session):"
    )
    # 5-field header: cycle · trigger · UTC (ago) · N words
    assert (
        "[cycle a3f2c1d8 · scheduled · 2026-05-06 11:52 UTC (8 min ago) "
        "· 5 words]" in out
    ), f"5-field header missing in output:\n{out}"
    assert body in out
```

Edit `tests/test_cycle_summary_injection.py:315-326` `test_render_truncates_cycle_id_to_8_chars`:

```python
def test_render_truncates_cycle_id_to_8_chars():
    """T3.2 (R2-Next-A): cycle_id sliced to [:8] in 5-field block header."""
    from src.cli.app import _render_recent_summaries

    now = datetime(2026, 5, 6, 12, 0, 0, tzinfo=timezone.utc)
    s = _make_summary(
        "a3f2c1d8b9c0d1e2", "alert", "body word",
        datetime(2026, 5, 6, 11, 55, 0, tzinfo=timezone.utc),
    )
    out = _render_recent_summaries([s], now)
    assert "[cycle a3f2c1d8 ·" in out
    assert "a3f2c1d8b9" not in out  # only first 8
    # 5-field header still well-formed
    assert "· 2 words]" in out
```

Edit `tests/test_cycle_summary_injection.py:329-339` `test_render_uses_absolute_and_relative_time`:

```python
def test_render_uses_absolute_and_relative_time():
    """T3.3 (R2-Next-A): header format `<UTC> (<ago>) · N words`."""
    from src.cli.app import _render_recent_summaries

    now = datetime(2026, 5, 6, 12, 0, 0, tzinfo=timezone.utc)
    s = _make_summary(
        "abcdef01", "scheduled", "body single",  # 2 words
        datetime(2026, 5, 6, 11, 0, 0, tzinfo=timezone.utc),
    )
    out = _render_recent_summaries([s], now)
    assert "2026-05-06 11:00 UTC (1 hour ago) · 2 words]" in out
```

- [ ] **Step 3.2: Add 3 new D2 tests**

Append to `tests/test_cycle_summary_injection.py` (after the existing render tests, around line 380):

```python
def test_header_shows_original_word_count_for_truncated_prior(caplog):
    """T3.4 (R2-Next-A D2): when a prior is over-cap, the header word
    count is the ORIGINAL count (pre-truncation), not the truncated
    body count. Agent compares header N vs cap to learn 'I exceeded
    the cap by X words'."""
    from src.cli.app import _render_recent_summaries

    now = datetime(2026, 5, 6, 12, 0, 0, tzinfo=timezone.utc)
    huge = " ".join(["word"] * 879)  # 879 words, will be cut to 700
    s = _make_summary(
        "abcdef01", "scheduled", huge,
        datetime(2026, 5, 6, 11, 55, 0, tzinfo=timezone.utc),
    )
    with caplog.at_level(logging.WARNING, logger="src.cli.app"):
        out = _render_recent_summaries([s], now)
    # Header shows ORIGINAL 879, not truncated 700
    assert "· 879 words]" in out
    # Body still has the word-cap marker
    assert "\n... [truncated by system, cut at 700 words]" in out


def test_header_word_count_matches_count_words_helper():
    """T3.5 (R2-Next-A D2 drift guard): header word count must equal
    `_count_words(s.decision)` exactly. Defends against future changes
    that compute count via a different convention."""
    from src.cli.app import _render_recent_summaries, _count_words

    now = datetime(2026, 5, 6, 12, 0, 0, tzinfo=timezone.utc)
    body = "| - Position | Entry: 81,985 | SL: 81,550 |"
    expected_count = _count_words(body)
    s = _make_summary(
        "abcdef01", "scheduled", body,
        datetime(2026, 5, 6, 11, 55, 0, tzinfo=timezone.utc),
    )
    out = _render_recent_summaries([s], now)
    assert f"· {expected_count} words]" in out


def test_header_word_count_present_for_each_of_three_priors():
    """T3.6 (R2-Next-A D2): in N=3 priors, every prior block has a
    word count in its 5-field header."""
    from src.cli.app import _render_recent_summaries

    now = datetime(2026, 5, 6, 12, 0, 0, tzinfo=timezone.utc)
    summaries = [
        _make_summary(
            f"cycle{i:03d}", "scheduled", f"body {i} body",
            datetime(2026, 5, 6, 11, 50 + i, 0, tzinfo=timezone.utc),
            sid=i,
        )
        for i in range(3)
    ]
    out = _render_recent_summaries(summaries, now)
    # Each block has `· 3 words]` (each body has 3 tokens)
    assert out.count("· 3 words]") == 3
```

- [ ] **Step 3.3: Run updated + new render tests to verify failures**

Run: `uv run pytest tests/test_cycle_summary_injection.py -k "render or header" -v 2>&1 | tail -25`

Expected: 3 updated tests FAIL (header format mismatch); 3 new tests FAIL (header field not yet implemented). Other render tests PASS.

- [ ] **Step 3.4: Update `_render_recent_summaries` in `src/cli/app.py:189-214`**

Replace the existing function body (signature unchanged):

```python
def _render_recent_summaries(
    summaries: list[CycleSummary], now: datetime,
) -> str:
    """Render summaries as a user-message-ready prefix block.

    Returns "" if list is empty (caller skips header append on first cycle).
    Sorts by (created_at, id) ASC so the reader sees oldest → newest naturally
    (review F4: id tie-breaker keeps same-timestamp ordering stable).

    R2-Next-A D2: each per-prior header includes `· {N} words` showing the
    ORIGINAL word count (pre-truncation). Pairs with D1 marker and A3
    persona text — agent compares header N vs the 700-word cap to detect
    over-budget priors and self-titrate.
    """
    if not summaries:
        return ""

    blocks = []
    for s in sorted(summaries, key=lambda x: (x.created_at, x.id)):
        cycle_id_short = s.cycle_id[:8]
        utc_str = s.created_at.strftime("%Y-%m-%d %H:%M UTC")
        ago = _format_relative_time(now, s.created_at)
        word_count = _count_words(s.decision or "")  # R2-Next-A D2
        body = _truncate_decision(s.decision)
        blocks.append(
            f"[cycle {cycle_id_short} · {s.triggered_by} · {utc_str} "
            f"({ago}) · {word_count} words]\n{body}"
        )

    header = "Your prior cycle summaries (most recent N=3, from this session):"
    return f"{header}\n\n" + "\n\n".join(blocks)
```

- [ ] **Step 3.5: Run all render tests to verify passes**

Run: `uv run pytest tests/test_cycle_summary_injection.py -k "render or header" -v 2>&1 | tail -25`

Expected: All `render` and `header` tests PASS.

- [ ] **Step 3.6: Run full test suite to verify zero regression**

Run: `uv run pytest -x -q 2>&1 | tail -10`

Expected: 1229 PASSED (1226 from T2 + 3 new T3.4-T3.6; T3.1-T3.3 are modifications of existing, no net add), 3 SKIPPED, 0 FAILED.

- [ ] **Step 3.7: Commit**

```bash
git add src/cli/app.py tests/test_cycle_summary_injection.py
git commit -m "$(cat <<'EOF'
feat(iter-w2r2-next-a): T3 _render_recent_summaries D2 header word count

R2-Next-A T3 (D2): each per-prior header in the priors injection block
now includes `· {N} words` showing the ORIGINAL word count
(pre-truncation, computed via _count_words(s.decision)). Header format
expands from 4-field (cycle · trigger · UTC (ago)) to 5-field
(+ N words).

Agent reading the priors block sees, for example, `· 879 words]`
followed by body that ends with `... [truncated by system, cut at
700 words]` — the 879-vs-700 contrast is the explicit feedback signal
agent uses to compress its own next-cycle decision.

Tests: 3 existing render tests updated to 5-field header format + 3
new (T3.4-T3.6: original count for truncated, helper drift guard,
3-prior coverage). Tests 1226 → 1229 (+3 net).

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 4: `persona.py` A3 explicit cap + drift guards

**Files:**
- Modify: `src/agent/persona.py:100` (Layer 1 § Cycle Closing Summary text)
- Modify: `tests/test_persona.py:503-510` (extend existing word ceiling anchor test)
- Modify: `tests/test_persona.py:518-528` (remove `"hard-truncates"` from forbidden list)
- Add: `tests/test_persona.py` (3 new A3 drift guards)

**TDD pattern:** Add 3 new tests + modify 2 existing (failing); update persona text; all pass.

- [ ] **Step 4.1: Modify `tests/test_persona.py:503-510` to extend word ceiling anchor**

Update `test_cycle_closing_summary_length_guidance_phrases_present` to add a `700 words` assertion:

```python
def test_cycle_closing_summary_length_guidance_phrases_present():
    """T-D5 (extended R2-Next-A): length guidance phrases include word
    ceilings (400/600) AND the new 700-word system cap (A3)."""
    from src.agent.persona import _build_layer1, RuntimeConfig
    layer1 = _build_layer1(RuntimeConfig())
    assert "400 words" in layer1
    assert "never exceeding 600 words" in layer1
    assert "700 words" in layer1                            # R2-Next-A A3
    assert "single sentence is sufficient" in layer1
    assert "Skip if no relevant observations" in layer1
```

- [ ] **Step 4.2: Modify `tests/test_persona.py:518-528` to retire the "hard-truncates" forbidden anchor**

Update `test_cycle_closing_summary_no_legacy_fiction_or_system_aware_phrases`:

```python
def test_cycle_closing_summary_no_legacy_fiction_or_system_aware_phrases():
    """T-D4+D5 (R2-Next-A calibrated): persona NOT 含 legacy fiction
    数字 + retired R2-8d-era system-aware phrases that still apply.

    Note (R2-Next-A): "hard-truncates" was removed from this list — A3
    deliberately surfaces system mechanic (3-channel feedback signal
    closes F1 length-loop). Remaining forbidden phrases still defend
    against R2-8d/PR #38 fiction (chars-based ceilings, target framing,
    SKIP fallback, summary-centric priming).
    """
    from src.agent.persona import _build_layer1, RuntimeConfig
    layer1 = _build_layer1(RuntimeConfig())
    forbidden = [
        "~600 chars",  # D4 撤
        "~800",        # D4 撤
        "~1200",       # D4 撤
        "Aim for",     # D4 撤 wishful target framing
        "is typically 1-3 sentences",  # D5 撤 per-field cap fiction
        # "hard-truncates" — retired in R2-Next-A (A3 surfaces system mechanic)
        "## SKIP",                     # D1 不引入 SKIP fallback
        "The summary IS the final response",  # D1 撤 summary-centric priming
        "~4000",       # D5 HARD_CAP 不暴露 (用 "~4000" anchor 而非 bare "4000"
                       # 避免与未来 RuntimeConfig 大数值字段 false-positive 冲突)
    ]
    for phrase in forbidden:
        assert phrase not in layer1, f"forbidden phrase leaked: {phrase!r}"
```

- [ ] **Step 4.3: Add 3 new A3 drift guards to `tests/test_persona.py`**

Append after the existing length-guidance test (around line 532):

```python
def test_cycle_closing_summary_explicit_word_cap_anchor():
    """T-A3.1 (R2-Next-A): persona Layer 1 contains the literal "700
    words" anchor — the explicit-cap channel of the 3-channel signal
    stack (paired with D1 marker + D2 header)."""
    from src.agent.persona import _build_layer1, RuntimeConfig
    layer1 = _build_layer1(RuntimeConfig())
    assert "700 words" in layer1


def test_cycle_closing_summary_truncation_consequence_phrase():
    """T-A3.2 (R2-Next-A): persona Layer 1 explicitly states the
    consequence of overflow — "lost from prior-cycle context" or
    similar. The consequence phrase triggers D11 self-reference
    awareness (priors are read 3.07x/cycle, sim #8). Anti-revert
    guard against future drift to mechanism-only wording."""
    from src.agent.persona import _build_layer1, RuntimeConfig
    layer1_lower = _build_layer1(RuntimeConfig()).lower()
    assert "truncated" in layer1_lower or "lost" in layer1_lower, \
        "A3 consequence phrase missing — agent must see that overflow " \
        "loses context to trigger self-correction"


def test_cycle_closing_summary_word_cap_matches_constant():
    """T-A3.3 (R2-Next-A drift guard): the literal "700 words" in
    persona Layer 1 must match CYCLE_DECISION_WORD_CAP. Renaming or
    re-valuing the constant must update the persona text — this test
    catches drift between persona / D1 marker / D2 helper."""
    import re
    from src.agent.persona import _build_layer1, RuntimeConfig, CYCLE_DECISION_WORD_CAP

    layer1 = _build_layer1(RuntimeConfig())
    expected = f"{CYCLE_DECISION_WORD_CAP} words"
    assert expected in layer1, \
        f"persona must mention '{expected}' (matching constant)"
```

- [ ] **Step 4.4: Run persona tests to verify failures**

Run: `uv run pytest tests/test_persona.py -v 2>&1 | tail -30`

Expected: `test_cycle_closing_summary_length_guidance_phrases_present` FAIL (`"700 words"` missing); 3 new T-A3.x tests FAIL (anchors missing). `test_cycle_closing_summary_no_legacy_fiction_or_system_aware_phrases` PASSES (forbidden list now smaller — but persona text doesn't contain `"hard-truncates"` yet either, so still passes).

- [ ] **Step 4.5: Update persona Layer 1 text in `src/agent/persona.py:100`**

Locate the current `Length:` paragraph in `_build_layer1`:

```python
Length: at most 400 words in normal cycles, never exceeding 600 words even in critical events (open/close/alert with action/SL trail with multiple history points/thesis transition/macro event proximity). A single sentence is sufficient when nothing actionable happened (e.g., "Watching, no position, routine tick — no changes")."""
```

Replace with (insert one new sentence between the existing critical-events list and the single-sentence shortcut):

```python
Length: at most 400 words in normal cycles, never exceeding 600 words even in critical events (open/close/alert with action/SL trail with multiple history points/thesis transition/macro event proximity). Beyond 700 words the system hard-truncates the summary as a safety net — when this happens, the truncated portion is lost from prior-cycle context. A single sentence is sufficient when nothing actionable happened (e.g., "Watching, no position, routine tick — no changes")."""
```

- [ ] **Step 4.6: Run persona tests to verify passes**

Run: `uv run pytest tests/test_persona.py -v 2>&1 | tail -25`

Expected: All persona tests PASS, including the 3 new T-A3.x and the updated word-ceiling-phrases test.

- [ ] **Step 4.7: Run full test suite to verify zero regression**

Run: `uv run pytest -x -q 2>&1 | tail -10`

Expected: 1232 PASSED (1229 from T3 + 3 new A3 drift guards), 3 SKIPPED, 0 FAILED.

- [ ] **Step 4.8: Commit**

```bash
git add src/agent/persona.py tests/test_persona.py
git commit -m "$(cat <<'EOF'
feat(iter-w2r2-next-a): T4 persona A3 explicit cap + drift guards

R2-Next-A T4 (A3): persona Layer 1 § Cycle Closing Summary now
explicitly states "Beyond 700 words the system hard-truncates the
summary as a safety net — when this happens, the truncated portion
is lost from prior-cycle context." This is the third channel of the
F1 length-loop closure signal stack (paired with D1 marker + D2
header).

The "lost from prior-cycle context" phrasing triggers D11
self-reference awareness — sim #8 measured 3.07 self-references per
cycle, so concrete consequence framing is more effective than abstract
limit statements.

Reverses R2-8d D5 silent-guardrail design philosophy: the previous
forbidden-phrase guard for "hard-truncates" is retired with explicit
note in test docstring; remaining 8 forbidden phrases still defend
against R2-8d/PR #38 fiction.

Tests: 1 existing word-ceiling-phrases extended with "700 words"
anchor + 1 forbidden-phrase guard amended (drop "hard-truncates")
+ 3 new (T-A3.1 explicit anchor / T-A3.2 consequence phrase /
T-A3.3 drift guard vs CYCLE_DECISION_WORD_CAP). Tests 1229 → 1232
(+3 net).

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 5: Cross-channel consistency drift guard + final smoke

**Files:**
- Add: `tests/test_persona.py` (1 cross-channel consistency test)

**Why here:** This test asserts the same `CYCLE_DECISION_WORD_CAP` value propagates to all three channels (persona text + D1 marker + D2 indirectly via `_count_words`). It's a meta drift-guard. Living next to other persona drift guards keeps related concerns together.

- [ ] **Step 5.1: Add cross-channel consistency test**

Append to `tests/test_persona.py` (after T-A3.3, around line 540):

```python
def test_word_cap_value_consistent_across_three_channels():
    """T5.1 (R2-Next-A cross-channel drift guard): the literal value of
    CYCLE_DECISION_WORD_CAP must propagate to:
      - persona Layer 1 text "700 words" (A3 channel)
      - _truncate_decision marker "cut at 700 words" (D1 channel)
      - D2 channel: _count_words helper uses the same `\\S+` convention
        (asserted indirectly via the helper's behavior; D2 doesn't bake
        the constant value, it just calls _count_words)

    Renaming or re-valuing the constant must keep all three in sync.
    This is the final defense against partial migrations.
    """
    from src.agent.persona import _build_layer1, RuntimeConfig, CYCLE_DECISION_WORD_CAP
    from src.cli.app import _truncate_decision, _count_words

    cap = CYCLE_DECISION_WORD_CAP
    expected_phrase = f"{cap} words"

    # A3 channel
    layer1 = _build_layer1(RuntimeConfig())
    assert expected_phrase in layer1, \
        f"persona missing '{expected_phrase}' — A3 channel out of sync"

    # D1 channel: marker must contain "cut at {cap} words"
    over_cap_text = " ".join(["w"] * (cap + 50))
    truncated = _truncate_decision(over_cap_text)
    assert f"cut at {expected_phrase}" in truncated, \
        f"_truncate_decision marker missing 'cut at {expected_phrase}' " \
        "— D1 channel out of sync"

    # D2 channel: _count_words convention is `\\S+` whitespace runs;
    # asserted by checking the helper agrees with the marker's measured
    # count. If the helper used a different convention, the body would
    # have a different word count than the cap.
    body = truncated.rsplit("\n... [truncated", 1)[0]
    assert _count_words(body) == cap, \
        f"_count_words convention diverged: expected exactly {cap} " \
        f"words in truncated body, got {_count_words(body)}"
```

- [ ] **Step 5.2: Run cross-channel test to verify pass**

Run: `uv run pytest tests/test_persona.py::test_word_cap_value_consistent_across_three_channels -v`

Expected: PASS.

- [ ] **Step 5.3: Run full test suite — final verification**

Run: `uv run pytest -q 2>&1 | tail -10`

Expected: **1233 PASSED** (matching spec §6.3 / §7.1 AC-B.1), 3 SKIPPED, 0 FAILED.

If count != 1233, audit which task added more or fewer tests than planned. Use `uv run pytest --collect-only -q | tail -5` to see actual count.

- [ ] **Step 5.4: Manual smoke — run a short sim cycle to verify integration**

This is OPTIONAL but recommended before T6. Per `feedback_long_walltime_experiments`, you (the user) run real-LLM sim cycles, not Claude.

Suggested command (user runs):
```bash
uv run python -m src.cli.app  # interactive wizard
# Select: simulated exchange / new BTC sim / accept defaults
# Let it run 3-5 cycles (ensures _build_recent_summaries_block fires)
# Inspect logs/session_*.log for:
#   - Priors block headers contain "· N words]"
#   - At least one cycle's decision → next cycle's prior shows that count
#   - If any prior was truncated, marker shows "cut at 700 words"
```

User will paste back any unexpected behavior; otherwise proceed to T6.

- [ ] **Step 5.5: Commit**

```bash
git add tests/test_persona.py
git commit -m "$(cat <<'EOF'
test(iter-w2r2-next-a): T5 cross-channel consistency drift guard

R2-Next-A T5: single test asserts CYCLE_DECISION_WORD_CAP propagates
to all three channels of the F1 length-feedback signal stack:
  - A3 (persona Layer 1 text "700 words")
  - D1 (_truncate_decision marker "cut at 700 words")
  - D2 (_count_words convention agrees with marker measurement)

Final defense against partial migrations: if anyone renames or
re-values the constant, this test fails with a clear channel-by-channel
diagnostic before any silent drift can land.

Tests 1232 → 1233 (+1 net) — matches spec §7.1 AC-B.1 final count.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 6: A2 docs SQL pattern note (housekeeping, no behavior change)

**Files:**
- Modify: `docs/metrics/agent-cycles-schema.md` (append A2 multi-LIKE SQL section)

**Why separate task:** Pure analyst documentation; no code, no tests. Per `feedback_docs_only_direct_merge` this could be a docs-only chore, but bundling here keeps R2-Next-A scope coherent in one PR.

- [ ] **Step 6.1: Append A2 SQL pattern section to `docs/metrics/agent-cycles-schema.md`**

Read the existing file first to find a good insertion point (likely end of file or after schema section):

```bash
cat docs/metrics/agent-cycles-schema.md | tail -30
```

Then append a new section. If the file ends without trailing blank line, add one before the new section:

```markdown
## SQL pattern for 5-field anchor detection (R2-Next-A A2)

5-field summary anchors may appear in markdown variants. SQLite default
没有 `REGEXP` operator（需 UDF 注册），所以用 multi-LIKE union 覆盖 4
个常见 markdown variants：

```sql
-- Multi-LIKE pattern (executable on default SQLite)
SELECT * FROM agent_cycles
WHERE decision LIKE '%(4) Thesis%'        -- plain or bold-wrap-whole `**(4) Thesis & ...**`
   OR decision LIKE '%(4) **Thesis%'      -- bold-inner-only `(4) **Thesis**`
   OR decision LIKE '%**(4) Thesis%'      -- bold-prefix `**(4) Thesis`
   OR decision LIKE '%**(4)** Thesis%';   -- bold-tag-only `**(4)** Thesis`

-- Narrow (legacy R2-8b/R2-8d, do not use): misses bold-inner variant
SELECT * FROM agent_cycles
WHERE decision LIKE '%(4) Thesis%';
```

复杂正则需求（如 case-insensitive / 任意空白匹配）建议走 Python helper：

```python
import re, sqlite3
PATTERN = re.compile(r'\(4\)\s*\*?\*?\s*Thesis', re.IGNORECASE)
con = sqlite3.connect('data/tradebot.db')
cycles = con.execute("SELECT cycle_id, decision FROM agent_cycles").fetchall()
matched = [c for c in cycles if c[1] and PATTERN.search(c[1])]
```

W2 sim #8 实测（177 ok cycles，其中含 5-field anchor 的 171 cycles）：
narrow LIKE 命中 100 / 171 = 58.5%（漏 41.5%，71/171 是 bold-inner-only 等变体）；
multi-LIKE 4-variant union 命中 171 / 171（0 missing / 0 extra vs Python regex baseline）。
```

- [ ] **Step 6.2: Verify the docs file renders the new section as Markdown**

Run: `cat docs/metrics/agent-cycles-schema.md | head -50` and `cat docs/metrics/agent-cycles-schema.md | tail -50`

Expected: new section visible at end of file; existing R2-7 schema section intact at top.

Optional: open in a Markdown viewer to confirm code blocks render correctly.

- [ ] **Step 6.3: Run full test suite (sanity — no code changed)**

Run: `uv run pytest -q 2>&1 | tail -5`

Expected: 1233 PASSED, 3 SKIPPED, 0 FAILED (unchanged from T5).

- [ ] **Step 6.4: Commit**

```bash
git add docs/metrics/agent-cycles-schema.md
git commit -m "$(cat <<'EOF'
docs(iter-w2r2-next-a): T6 A2 multi-LIKE SQL pattern for analyst use

R2-Next-A T6 (A2 housekeeping): document the multi-LIKE 4-variant
union SQL pattern for detecting 5-field summary anchors in
agent_cycles.decision, replacing the narrow LIKE that the inventory
phase used.

Default SQLite has no REGEXP operator (UDF registration required),
so analyst-side queries should use the multi-LIKE union (executable
on plain SQLite) or the Python regex helper for case-insensitive /
arbitrary-whitespace patterns.

W2 sim #8 实测：narrow LIKE 命中 100/171 (58.5%, 漏 41.5% bold-inner
variants); multi-LIKE union 命中 171/171 (0 missing/extra vs Python
regex baseline).

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Final verification

After all 6 tasks committed, run a top-level audit:

- [ ] **Step F.1: Verify final test count**

Run: `uv run pytest --collect-only -q 2>&1 | tail -3`

Expected: `1233 tests collected`.

- [ ] **Step F.2: Verify full test pass**

Run: `uv run pytest -q 2>&1 | tail -5`

Expected: `1233 passed, 3 skipped`.

- [ ] **Step F.3: Verify all spec AC-B build-time criteria**

| AC | Verification |
|---|---|
| AC-B.1 1233 pass | F.2 |
| AC-B.2 0 regression | F.2 (1215 pre-existing all still pass) |
| AC-B.3 4-channel drift guards pass | T5.2 cross-channel test |
| AC-B.4 R2-8d D2 (3)(4) order preserved | `uv run pytest tests/test_persona.py::test_cycle_closing_summary_field_order_delta_before_thesis -v` |
| AC-B.5 word-boundary preserves tokens | T2.6 `test_truncate_word_boundary_does_not_split_token` |
| AC-B.6 R2-8b fail-isolated injection | existing `test_build_block_returns_empty_on_db_error` (untouched) |
| AC-B.7 secondary char floor + word path precedence | T2.7 + T2.8 |

- [ ] **Step F.4: Verify commit history**

Run: `git log --oneline main..HEAD`

Expected (in chronological order):
```
xxxxxxx docs(iter-w2r2-next-a): T6 A2 multi-LIKE SQL pattern for analyst use
xxxxxxx test(iter-w2r2-next-a): T5 cross-channel consistency drift guard
xxxxxxx feat(iter-w2r2-next-a): T4 persona A3 explicit cap + drift guards
xxxxxxx feat(iter-w2r2-next-a): T3 _render_recent_summaries D2 header word count
xxxxxxx feat(iter-w2r2-next-a): T2 _truncate_decision word-aware + secondary char floor
xxxxxxx feat(iter-w2r2-next-a): T1 _count_words helper + new constants
xxxxxxx docs(iter-w2r2-next-a): F1 length feedback loop closure spec
```

(7 commits total: 1 spec + 1 plan + 6 task feat/test/docs.)

If history matches, the implementation is complete. Push to remote + open PR per project conventions (see `feedback_git_branch` + `feedback_review_before_commit`).

---

## Self-review notes (for the executing engineer)

**Common pitfalls to watch:**

1. **`re` import** — already present in `src/cli/app.py`? Check before adding. If not, add to imports near `import logging`.
2. **`_WORD_RE` placement** — module-level singleton (compiled once), placed before `_count_words`. Don't put inside the function.
3. **Test counting** — exact final number is 1233 only if the existing baseline is 1215 AND all task add/modify counts match. If pytest gives a different number, run `git diff --stat` per task to audit.
4. **`text.split()` vs `_WORD_RE.findall()`** — both give identical counts on this dataset. Plan uses `_WORD_RE.findall` for symmetry with `_WORD_RE.finditer` (positions for slicing). Don't mix conventions.
5. **`.rstrip()` placement** — only on the `text[:cut_pos]` slice (before joining marker). Don't rstrip the full output.
6. **Persona text indentation** — `_build_layer1` uses a triple-quoted f-string. The new sentence inserts INSIDE the same paragraph (no `\n\n`). Preserve existing whitespace exactly when editing.
7. **Test docstring `T*.x` numbering** — follows project convention; don't renumber existing tests.
8. **caplog level** — use `caplog.at_level(logging.WARNING, logger="src.cli.app")` (matches existing pattern at line 81); not the default root logger.

**If a step fails unexpectedly:**

- Read the error carefully; don't blindly re-run.
- If `assert "X" in out` fails, print `out` and inspect actual format.
- If a previously-passing test now fails, check if a constant rename / function signature change touched it.
- Use `git diff` to compare your code to the plan's exact code blocks.
- If stuck > 5 min on one step, re-read the spec section that drove the change.
