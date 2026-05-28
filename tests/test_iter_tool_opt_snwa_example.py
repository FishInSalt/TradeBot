"""Regression test for iter-tool-opt-snwa-example-fix.

Guards against future drift in SET_NEXT_WAKE_AT_DESCRIPTION's first Example:
the showcased ``target_time`` must come at or just after the candle close cited
in the reasoning string (audit found a stale example with target 23 min *before*
the cited candle close — see `.working/tool-audits/2026-05-29-set_next_wake_at.md`).
"""
from __future__ import annotations
import re

from src.agent.tools_descriptions import SET_NEXT_WAKE_AT_DESCRIPTION


_FIRST_EXAMPLE = re.compile(
    r'set_next_wake_at\("(?P<target>\d{2}:\d{2})",\s*'
    r'"(?P<reasoning>[^"]*candle close[^"]*)"\)',
    re.IGNORECASE,
)

_CANDLE_CLOSE_TIME = re.compile(
    r'(?:close(?:s)? at|candle close at)\s+(?P<close>\d{2}:\d{2})',
    re.IGNORECASE,
)


def _to_minutes(hhmm: str) -> int:
    h, m = hhmm.split(":")
    return int(h) * 60 + int(m)


def test_first_example_target_matches_cited_candle_close():
    m = _FIRST_EXAMPLE.search(SET_NEXT_WAKE_AT_DESCRIPTION)
    assert m, "first Example with candle-close reasoning not found"
    target = m.group("target")
    reasoning = m.group("reasoning")
    close_match = _CANDLE_CLOSE_TIME.search(reasoning)
    assert close_match, (
        f"first Example reasoning {reasoning!r} mentions 'candle close' "
        "but no 'closes at HH:MM' / 'close at HH:MM' time follows it"
    )
    close = close_match.group("close")
    offset = _to_minutes(target) - _to_minutes(close)
    assert 0 <= offset <= 5, (
        f"first Example target={target} vs cited candle close={close}: "
        f"offset={offset} min out of [0, 5]. Wake target should be at or up "
        f"to 5 min after candle close (matches agent's observed +0~+3 offset "
        f"usage; large offsets imply the example reasoning and args drifted)."
    )


def test_first_example_return_resolves_to_same_target_time():
    """The success example's rendered '(in N min)' must equal target - target
    of the rendered date-time. Catches accidental drift where someone bumps
    one number without updating the other.
    """
    m = re.search(
        r'set_next_wake_at\("(\d{2}:\d{2})",[^\n]+\n\s*→\s*'
        r'"Next wake set for \d{4}-\d{2}-\d{2}\s+(\d{2}:\d{2})\s+UTC',
        SET_NEXT_WAKE_AT_DESCRIPTION,
    )
    assert m, "success example call + return not parseable"
    assert m.group(1) == m.group(2), (
        f"target_time={m.group(1)} but rendered date-time uses "
        f"{m.group(2)} — they must match"
    )
