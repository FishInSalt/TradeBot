"""LLM-facing tool descriptions for tools where pydantic-ai 1.78 / griffe
strips structured sections (Examples / Example output / inline admonitions)
from `tool.tool_def.description`.

Constants in this module are passed verbatim via `@tool(description=DESC_X)`
to bypass griffe parsing and reach the LLM. Args descriptions remain in the
source docstring (parsed normally into `parameters_json_schema`).

See docs/superpowers/specs/2026-05-19-iter-tool-opt-dead-example-promote-design.md
for the audit (7 tools / 4 loss categories) + design rationale.
"""

# Constants added by subsequent migration tasks (Tasks 2-6).


SET_NEXT_WAKE_DESCRIPTION = """Schedule the next scheduler wake-up after a relative minute interval.

Returns a confirmation, or a reject message describing the violation. Alerts, fills, and conditional triggers always interrupt scheduled wake.

Examples:
    set_next_wake(15, "consolidation phase, check in 15 min")
    → "Next wake set to 15 min. Reason: ..."

    set_next_wake(90, "...")
    → "Cannot set wake to 90 min: exceeds wake_max=60 min for this session."

    set_next_wake(0, "...")
    → "Cannot set wake to 0 min: below wake_min=1 min."
"""


SET_NEXT_WAKE_AT_DESCRIPTION = """Schedule the next scheduler wake-up at an absolute UTC time.

Returns a confirmation containing the resolved date-time, or a reject message describing the violation. Alerts, fills, and conditional triggers always interrupt scheduled wake.

Examples:
    set_next_wake_at("10:37", "align with 1h candle close at 11:00 UTC")
    → "Next wake set for 2026-05-12 10:37 UTC (in 14 min). Reason: ..."

    set_next_wake_at("12:00", "...")
    → "Cannot wake at 12:00 UTC: nearest future 2026-05-12 12:00 UTC (in 97 min) exceeds wake_max=60 min for this session."

    set_next_wake_at("10:23", "...")  # now=10:23, resolves to tomorrow
    → "Cannot wake at 10:23 UTC: nearest future 2026-05-13 10:23 UTC (in 1440 min) exceeds wake_max=60 min for this session."

    set_next_wake_at("foo", "...")
    → "Invalid target_time format: 'foo'. Expected 'HH:MM' UTC with 2-digit hour and minute (e.g., '10:37' or '03:05')."
"""
