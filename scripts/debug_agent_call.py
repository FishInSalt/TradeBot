"""轻量 LLM 调用调试脚本 — 不过完整 agent / OKX / scheduler，仅验证 model + thinking 行为。

Usage:
    uv run python scripts/debug_agent_call.py
    uv run python scripts/debug_agent_call.py --model deepseek-v4-pro
    uv run python scripts/debug_agent_call.py --prompt "..."
"""
from __future__ import annotations

import argparse
import asyncio
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from pydantic_ai import Agent
from pydantic_ai.messages import TextPart, ThinkingPart, ToolCallPart  # noqa: E402

from src.services.model_manager import ModelManager, get_optimal_settings  # noqa: E402

DEFAULT_PROMPT = (
    "BTC 当前价 65000，过去 24h funding rate 由 0.01% 上升到 0.05%，"
    "未平仓量从 8B 增到 12B，FGI 从 50 升到 78。"
    "现在是该追多还是观望？给出推理理由。"
)


async def run(model_id: str | None, prompt: str) -> int:
    mgr = ModelManager()
    configs = mgr.load_models()
    if not configs:
        print("error: no models in config/models.json", file=sys.stderr)
        return 1

    if model_id:
        cfg = next((c for c in configs if c.id == model_id), None)
        if cfg is None:
            ids = [c.id for c in configs]
            print(f"error: model id '{model_id}' not found; available: {ids}", file=sys.stderr)
            return 1
    else:
        cfg = configs[0]

    print(f"# model: id={cfg.id} provider={cfg.provider} name={cfg.model}\n")

    agent = Agent(
        mgr.create_model(cfg),
        model_settings=get_optimal_settings(cfg.model),
    )

    start = time.perf_counter()
    result = await agent.run(prompt)
    wall = time.perf_counter() - start

    print("=== PROMPT ===")
    print(prompt)

    print("\n=== OUTPUT ===")
    print(result.output)

    print("\n=== PARTS (thinking / tool_call / text) ===")
    saw_thinking = False
    for msg in result.all_messages():
        for part in getattr(msg, "parts", []):
            if isinstance(part, ThinkingPart):
                saw_thinking = True
                print(f"[thinking] {part.content}")
            elif isinstance(part, ToolCallPart):
                print(f"[tool_call] {part.tool_name}({part.args})")
            elif isinstance(part, TextPart):
                pass  # 已在 OUTPUT 打印

    print("\n=== USAGE ===")
    print(result.usage())
    print(f"\n=== TIMING ===\nwall: {wall:.2f}s")
    print(f"\nthinking_active: {saw_thinking}")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--model", type=str, default=None,
                        help="model id from config/models.json (default: first)")
    parser.add_argument("--prompt", type=str, default=DEFAULT_PROMPT,
                        help="prompt to send (default: trading scenario)")
    args = parser.parse_args()
    return asyncio.run(run(args.model, args.prompt))


if __name__ == "__main__":
    sys.exit(main())
