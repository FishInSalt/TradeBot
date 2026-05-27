"""Parse a TradeBot session log into (Action tools, following Reasoning) JSONL records.

This is the bedrock for cross-tool attribution: any "field X is mentioned in
reasoning" claim must split by which tool was actually in the preceding Action.
Grep alone overstates because shared fields (MA20 / ATR / RSI) appear in multiple
tools' rendered output but the agent's reasoning citation has only one true source.

Usage:
    python scripts/parse_session_log.py <session.log> <out.jsonl>

Output: one JSON line per reasoning/decision block, e.g.:
    {"cycle": "685e", "action_tools": ["get_market_data", "get_position"],
     "line": 422, "reasoning": "Let me analyze...", "kind": "reasoning"}
"""
from __future__ import annotations
import json
import re
import sys
from pathlib import Path


TOOL_RE = re.compile(r"^  ⚙ ([a-z_]+)\(")
ACTION_RE = re.compile(r"^▾ Action ")
REASON_RE = re.compile(r"^▾ Reasoning ")
DECISION_RE = re.compile(r"^▾ Decision")
CYCLE_RE = re.compile(r"^  Cycle ([a-f0-9]+)\s+•\s+(\S+)\s+UTC")


def parse(slog_path: Path) -> list[dict]:
    """Return list of records. State machine over session log lines."""
    lines = slog_path.read_text().splitlines()
    records: list[dict] = []
    state = "scan"
    current_cycle: str | None = None
    current_tools: list[str] = []
    current_reasoning: list[str] = []
    record_template: dict | None = None

    def flush() -> None:
        nonlocal record_template, current_reasoning
        if record_template is not None:
            record_template["reasoning"] = "\n".join(current_reasoning).strip()
            records.append(record_template)
        record_template = None
        current_reasoning = []

    for i, line in enumerate(lines):
        m_cycle = CYCLE_RE.match(line)
        if m_cycle:
            flush()
            current_cycle = m_cycle.group(1)
            state = "scan"
            continue

        if ACTION_RE.match(line):
            flush()
            current_tools = []
            state = "collecting_action_tools"
            continue

        if REASON_RE.match(line):
            flush()
            record_template = {
                "cycle": current_cycle,
                "action_tools": current_tools[:],
                "line": i + 1,
                "kind": "reasoning",
            }
            state = "collecting_reasoning"
            continue

        if DECISION_RE.match(line):
            flush()
            record_template = {
                "cycle": current_cycle,
                "action_tools": current_tools[:],
                "line": i + 1,
                "kind": "decision",
            }
            state = "collecting_reasoning"
            continue

        if state == "collecting_action_tools":
            m_tool = TOOL_RE.match(line)
            if m_tool:
                current_tools.append(m_tool.group(1))
        elif state == "collecting_reasoning":
            current_reasoning.append(line)

    flush()
    return records


def bucket_of(action_tools: list[str], target: str = "get_market_data") -> str:
    """Five-bucket classification for attribution.

    - target_only: action contains target + non-TA tools only
    - mts_only: action contains MTS + non-TA tools only
    - htf_only: action contains HTF + non-TA tools only
    - multi_TA: action contains ≥2 TA tools
    - other: no TA tools at all
    """
    TA = {"get_market_data", "get_multi_timeframe_snapshot", "get_higher_timeframe_view"}
    tools = set(action_tools)
    ta_in_action = tools & TA
    if not ta_in_action:
        return "other"
    if len(ta_in_action) > 1:
        return "multi_TA"
    only_ta = next(iter(ta_in_action))
    if only_ta == target:
        return "target_only"
    if only_ta == "get_multi_timeframe_snapshot":
        return "mts_only"
    return "htf_only"


def summary(records: list[dict], target: str = "get_market_data") -> None:
    """Print bucket sizes to help auditor verify."""
    from collections import Counter
    sizes = Counter(bucket_of(r["action_tools"], target) for r in records)
    total = len(records)
    print(f"Parsed {total} reasoning/decision records", file=sys.stderr)
    print(f"Bucket sizes (target={target}):", file=sys.stderr)
    for b in ["target_only", "mts_only", "htf_only", "multi_TA", "other"]:
        print(f"  {b:<14s} {sizes[b]:5d}", file=sys.stderr)


def main() -> int:
    if len(sys.argv) < 3:
        print("Usage: parse_session_log.py <session.log> <out.jsonl> [target_tool]",
              file=sys.stderr)
        return 2
    slog = Path(sys.argv[1])
    out = Path(sys.argv[2])
    target = sys.argv[3] if len(sys.argv) > 3 else "get_market_data"

    records = parse(slog)
    with out.open("w") as f:
        for r in records:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")
    summary(records, target)
    return 0


if __name__ == "__main__":
    sys.exit(main())
