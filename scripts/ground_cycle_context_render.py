#!/usr/bin/env python3
"""Grounding stats for the session-log Context-section iter (2026-05-31).

Reproduces the empirical figures cited in
docs/superpowers/specs/2026-05-31-session-log-cycle-context-design.md §2,
so the data-driven design decisions can be re-verified on demand
(per CLAUDE.md "实证优先于直觉" meta-principle).

Measures, over a session's agent_cycles.decision (the cycle closing summaries):
  - 5-field format compliance (binary: all-5 / none / partial)
  - the 4 cosmetic marker styles (incl. the `### (N)` markdown-heading variant)
  - field-1 (Stance) + field-4 (Thesis) extracted lengths
  - the ①Stance-only vs ①+④ rendered footprint
  - the field-extraction parser's success / fallback rate

Usage:
    python3 scripts/ground_cycle_context_render.py [DB_PATH] [SESSION_ID]

Defaults: data/tradebot.db, BTC sim #12 (f0f7b24f-...).
"""
from __future__ import annotations

import re
import sqlite3
import statistics
import sys

DB_DEFAULT = "data/tradebot.db"
SID_DEFAULT = "f0f7b24f-276b-461a-9464-65c36a959786"  # BTC sim #12

FIELDS = ["Stance", "Active commitments", "This cycle delta", "Thesis", "Watch"]
# Tolerates the 4 observed line-anchored styles:
#   **(N) Field  /  (N) **Field  /  (N) Field  /  ### (N) Field (markdown heading)
MARKER = re.compile(r"(?m)^(?:#{1,6}\s*)?\**\s*\(([1-5])\)\s*")


def split_fields(dec: str) -> dict[int, str]:
    """Position-slice a decision into {field_num: content}. {} if no markers."""
    marks = [(m.start(), int(m.group(1)), m.end()) for m in MARKER.finditer(dec)]
    if not marks:
        return {}
    out: dict[int, str] = {}
    for i, (_, num, end) in enumerate(marks):
        nxt = marks[i + 1][0] if i + 1 < len(marks) else len(dec)
        out[num] = dec[end:nxt].strip()
    return out


def detect_fields(dec: str) -> int:
    """Count how many of the 5 field keywords appear (loose, name-anchored)."""
    return sum(
        bool(re.search(rf"\(?{i}\)?\s*\**\s*{re.escape(f)}", dec, re.I))
        for i, f in enumerate(FIELDS, 1)
    )


def _stat(label: str, vals: list[int]) -> str:
    return (
        f"{label}: median={int(statistics.median(vals))} "
        f"p90={int(sorted(vals)[int(0.9 * len(vals))])} max={max(vals)}"
    )


def main() -> None:
    db = sys.argv[1] if len(sys.argv) > 1 else DB_DEFAULT
    sid = sys.argv[2] if len(sys.argv) > 2 else SID_DEFAULT
    con = sqlite3.connect(db)
    rows = con.execute(
        "SELECT cycle_id, triggered_by, execution_status, decision "
        "FROM agent_cycles WHERE session_id=? ORDER BY created_at ASC",
        (sid,),
    ).fetchall()

    with_dec = [r for r in rows if r[3]]
    n = len(with_dec)
    print(f"session {sid}  —  {len(rows)} cycles, {n} with a decision summary\n")

    # --- format compliance (binary) ---
    full = sum(1 for r in with_dec if detect_fields(r[3]) == 5)
    none = sum(1 for r in with_dec if detect_fields(r[3]) == 0)
    print("=== 5-field compliance ===")
    print(f"  all-5: {full} ({100*full/n:.1f}%)  none: {none} ({100*none/n:.1f}%)  "
          f"partial: {n-full-none}")
    print("  (none = persona-allowed terse one-liners + forensic-prior bodies; "
          "not malformed)\n")

    # --- marker styles (field-1) ---
    styles: dict[str, int] = {}
    for _, _, _, dec in with_dec:
        m = re.search(r"(?m)^.{0,12}?Stance", dec)
        if m:
            styles[m.group(0)] = styles.get(m.group(0), 0) + 1
    print("=== field-1 marker styles ===")
    for k, v in sorted(styles.items(), key=lambda x: -x[1]):
        print(f"  {v:4d} | {k!r}")
    print()

    # --- parser success + field lengths ---
    ok = 0
    fb = 0
    len1: list[int] = []
    len4: list[int] = []
    len14: list[int] = []
    len_whole: list[int] = []
    for _, _, _, dec in with_dec:
        f = split_fields(dec)
        # success criterion (spec §3.4): ① and ④ both locatable
        if 1 in f and 4 in f:
            ok += 1
            s1 = re.sub(r"\s+", " ", f[1]).strip()
            s4 = re.sub(r"\s+", " ", f[4]).strip()
            len1.append(len(s1))
            len4.append(len(s4))
            len14.append(len(s1) + len(s4))
            len_whole.append(len(dec))  # whole structured summary (the cited 2425 figure)
        else:
            fb += 1
    print("=== parser (spec §3.4 gate: ① & ④ both present) ===")
    print(f"  success: {ok} ({100*ok/n:.1f}%)  fallback: {fb} ({100*fb/n:.1f}%)")
    print(f"  {_stat('① Stance only (chars)', len1)}")
    print(f"  {_stat('④ Thesis only (chars)', len4)}")
    print(f"  {_stat('①+④ combined (chars)', len14)}")
    print(f"  {_stat('whole structured summary (chars)', len_whole)}")


if __name__ == "__main__":
    main()
