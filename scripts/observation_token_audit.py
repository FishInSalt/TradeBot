#!/usr/bin/env python3
"""Iter 8 (T3-2 PR-AUDIT) — input token audit script.

Parses LLM request bodies from system.log Request options DEBUG lines, computes
per-section token breakdown for sampled cycles, outputs markdown report. Input
data for W2 'input context 削减' spec.

Sections:
- system prompt (messages[0] role=system)
- tool definitions × N (each tool's full def → token share)
- message history (per-message token + role classification)

Usage (run from repo root):
  uv run python scripts/observation_token_audit.py
  uv run python scripts/observation_token_audit.py --log logs/system.log --last 10
  uv run python scripts/observation_token_audit.py --out .working/audit.md
"""
from __future__ import annotations

import argparse
import ast
import json
import re
import sys
from collections import Counter
from pathlib import Path

import tiktoken


_REQUEST_LINE_RE = re.compile(r"Request options: (.*)$")


def parse_request_lines(log_path: Path) -> list[tuple[str, dict]]:
    """Yield (timestamp, request_dict) for every Request options line.

    Uses ast.literal_eval — safe Python literal parser per stdlib docs;
    accepts only strings, numbers, tuples, lists, dicts, sets, booleans,
    None, Ellipsis. Does not execute any code.
    """
    results: list[tuple[str, dict]] = []
    with log_path.open() as f:
        for line in f:
            m = _REQUEST_LINE_RE.search(line)
            if not m:
                continue
            try:
                req = ast.literal_eval(m.group(1))
            except (SyntaxError, ValueError):
                continue
            if not isinstance(req, dict):
                continue
            ts = line[:19] if len(line) >= 19 else ""
            results.append((ts, req))
    return results


def _count(text: str, enc: tiktoken.Encoding) -> int:
    return len(enc.encode(text)) if text else 0


def _content_to_str(content) -> str:
    if content is None:
        return ""
    if isinstance(content, str):
        return content
    return json.dumps(content, ensure_ascii=False)


def audit_request(req: dict, enc: tiktoken.Encoding) -> dict:
    """Break a single request body into token sections."""
    json_data = req.get("json_data") or {}
    messages = json_data.get("messages") or []
    tools = json_data.get("tools") or []

    sys_prompt = ""
    history_msgs = messages
    if messages and messages[0].get("role") == "system":
        sys_prompt = messages[0].get("content") or ""
        history_msgs = messages[1:]

    sys_tokens = _count(sys_prompt, enc)

    tool_breakdown: list[tuple[str, int]] = []
    for t in tools:
        # OpenAI shape: {"type": "function", "function": {"name", "description", "parameters"}}
        fn = t.get("function") if t.get("type") == "function" else t
        name = fn.get("name", "<unnamed>") if isinstance(fn, dict) else "<unnamed>"
        tool_str = json.dumps(t, ensure_ascii=False)
        tool_breakdown.append((name, _count(tool_str, enc)))

    history_tokens = 0
    role_breakdown: Counter[str] = Counter()
    for m in history_msgs:
        role = m.get("role", "unknown")
        body = _content_to_str(m.get("content"))
        if "tool_calls" in m and m["tool_calls"]:
            body += json.dumps(m["tool_calls"], ensure_ascii=False)
        toks = _count(body, enc)
        history_tokens += toks
        role_breakdown[role] += toks

    return {
        "system_tokens": sys_tokens,
        "tool_total_tokens": sum(t[1] for t in tool_breakdown),
        "tool_breakdown": sorted(tool_breakdown, key=lambda x: -x[1]),
        "history_tokens": history_tokens,
        "history_role_breakdown": dict(role_breakdown),
        "message_count": len(messages),
        "tool_count": len(tools),
    }


def render_report(samples: list[tuple[str, dict]], audits: list[dict]) -> str:
    """Render markdown report aggregating audit results."""
    if not audits:
        return "# Token Audit Report\n\nNo samples to report.\n"

    n = len(audits)
    avg_sys = sum(a["system_tokens"] for a in audits) // n
    avg_tool = sum(a["tool_total_tokens"] for a in audits) // n
    avg_hist = sum(a["history_tokens"] for a in audits) // n
    avg_total = avg_sys + avg_tool + avg_hist

    hist_min = min(a["history_tokens"] for a in audits)
    hist_max = max(a["history_tokens"] for a in audits)

    first_ts = samples[0][0]
    last_ts = samples[-1][0]

    out: list[str] = []
    out.append("# Input Token Audit Report (Iter 8 T3-2 PR-AUDIT)")
    out.append("")
    out.append(f"**Samples**: {n} request bodies from `{first_ts}` to `{last_ts}`")
    tool_consistent = all(a["tool_count"] == audits[0]["tool_count"] for a in audits)
    out.append(f"**Tool count**: {audits[0]['tool_count']} "
               f"({'consistent across samples' if tool_consistent else 'NO — drift detected'})")
    out.append("")

    out.append("## Per-section average input tokens")
    out.append("")
    out.append("| Section | avg tokens | % of input |")
    out.append("|---|---|---|")
    out.append(f"| system prompt | {avg_sys:,} | {avg_sys / avg_total * 100:.1f}% |")
    out.append(f"| tool definitions ({audits[0]['tool_count']}) | {avg_tool:,} | "
               f"{avg_tool / avg_total * 100:.1f}% |")
    out.append(f"| message history | {avg_hist:,} | {avg_hist / avg_total * 100:.1f}% |")
    out.append(f"| **estimated input total** | **{avg_total:,}** | 100.0% |")
    out.append("")
    out.append(f"**History range across samples**: {hist_min:,} – {hist_max:,} tokens")
    out.append("")

    out.append("## Tool definitions — top 15 by token cost (last sample)")
    out.append("")
    out.append("| Tool | tokens | % of tool defs |")
    out.append("|---|---|---|")
    last = audits[-1]
    tool_total = last["tool_total_tokens"] or 1
    for name, toks in last["tool_breakdown"][:15]:
        out.append(f"| `{name}` | {toks:,} | {toks / tool_total * 100:.1f}% |")
    out.append("")

    out.append("## Sample-by-sample history breakdown")
    out.append("")
    out.append("| # | timestamp | history tokens | message count | role split |")
    out.append("|---|---|---|---|---|")
    for i, ((ts, _), a) in enumerate(zip(samples, audits)):
        rbd = a["history_role_breakdown"]
        role_str = " / ".join(f"{role}: {toks}" for role, toks in sorted(rbd.items(), key=lambda x: -x[1]))
        out.append(f"| {i + 1} | {ts} | {a['history_tokens']:,} | {a['message_count']} | {role_str} |")
    out.append("")

    agg_roles: Counter[str] = Counter()
    for a in audits:
        agg_roles.update(a["history_role_breakdown"])
    total_role_tokens = sum(agg_roles.values()) or 1

    out.append("## Aggregate message role distribution (across all samples)")
    out.append("")
    out.append("| role | total tokens | % of history |")
    out.append("|---|---|---|")
    for role, toks in sorted(agg_roles.items(), key=lambda x: -x[1]):
        out.append(f"| {role} | {toks:,} | {toks / total_role_tokens * 100:.1f}% |")
    out.append("")

    out.append("## Notes")
    out.append("")
    out.append("- Token counts use `tiktoken cl100k_base` encoder (DeepSeek/OpenAI compatible "
               "approximation; absolute values may differ ±5% from DeepSeek native tokenizer).")
    out.append("- **Granularity**: token totals are **per LLM call** (single request body). "
               "Per-cycle accumulation = ~5x (multi-call agent loop, pydantic-ai tool-call "
               "feedback round). See Iter 1 cycle log `cache_hit/cache_miss` for cycle-level totals.")
    out.append("- Tool def token cost = full JSON serialization (function name + description + parameters schema).")
    out.append("- Message history excludes system prompt (counted separately).")
    out.append("- Cache hit/miss split not reflected here — see cycle log INFO lines for "
               "`cache_hit/miss/rate` (Iter 1 仪表化). True billing impact = cache_miss portion only.")

    return "\n".join(out) + "\n"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--log", default="logs/system.log", type=Path,
                        help="Path to system.log (default: logs/system.log)")
    parser.add_argument("--last", type=int, default=10,
                        help="Audit the last N Request options lines (default: 10)")
    parser.add_argument("--out", type=Path, default=None,
                        help="Output markdown file (default: stdout)")
    args = parser.parse_args()

    if not args.log.exists():
        print(f"ERROR: log file not found: {args.log}", file=sys.stderr)
        return 1

    enc = tiktoken.get_encoding("cl100k_base")
    parsed = parse_request_lines(args.log)
    if not parsed:
        print("ERROR: no Request options lines found.", file=sys.stderr)
        return 1

    # Filter trivial debug calls (single user msg, no tools — debug_agent_call.py)
    production = [(ts, req) for ts, req in parsed
                  if (req.get("json_data") or {}).get("tools")]
    if not production:
        print("ERROR: no production cycle requests found (only trivial debug calls).", file=sys.stderr)
        return 1

    samples = production[-args.last:]
    audits = [audit_request(req, enc) for _, req in samples]
    report = render_report(samples, audits)

    if args.out:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(report)
        print(f"Report written to {args.out} ({len(samples)} samples)")
    else:
        sys.stdout.write(report)
    return 0


if __name__ == "__main__":
    sys.exit(main())
