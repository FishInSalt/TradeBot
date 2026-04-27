"""所有外部数据源的连通性自检脚本。

并发对每个上游 endpoint 发起一次 GET，收到任何 HTTP 响应即视为可达。
不发送鉴权头，不消耗任何 API 配额。

Usage:
    uv run python scripts/check_external_sources.py
"""
from __future__ import annotations

import asyncio
import sys
import time
from dataclasses import dataclass
from pathlib import Path

import httpx
from rich.console import Console
from rich.table import Table

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.services.model_manager import ModelManager  # noqa: E402

TIMEOUT_SEC = 5.0

# 与 src/integrations/*/*.py 中的 _*_URL 模块级常量保持一致；新增源时手动同步。
DATA_SOURCE_ENDPOINTS: list[tuple[str, str]] = [
    ("exchange:okx",           "https://www.okx.com/api/v5/public/time"),
    ("news:coindesk",          "https://data-api.coindesk.com/news/v1/article/list"),
    ("news:fear_greed",        "https://api.alternative.me/fng/"),
    ("news:forexfactory",      "https://nfs.faireconomy.media/ff_calendar_thisweek.json"),
    ("news:okx_announcements", "https://www.okx.com/api/v5/support/announcements"),
    ("news:okx_status",        "https://www.okx.com/api/v5/system/status"),
    ("macro:fred",             "https://api.stlouisfed.org/fred/series/observations"),
    ("macro:alpha_vantage",    "https://www.alphavantage.co/query"),
    ("macro:coingecko",        "https://api.coingecko.com/api/v3/global"),
    ("onchain:defillama",      "https://stablecoins.llama.fi/stablecoins"),
    ("etf:sosovalue",          "https://openapi.sosovalue.com/openapi/v1/etfs/summary-history"),
]

# pydantic-ai 各 provider 的默认 base_url；ModelConfig.base_url 为 None 时使用。
LLM_DEFAULT_URLS: dict[str, str] = {
    "anthropic":  "https://api.anthropic.com",
    "openai":     "https://api.openai.com",
    "google-gla": "https://generativelanguage.googleapis.com",
    "groq":       "https://api.groq.com",
    "deepseek":   "https://api.deepseek.com",
}


@dataclass
class CheckResult:
    name: str
    url: str
    ok: bool
    latency_ms: int
    note: str


async def check_endpoint(client: httpx.AsyncClient, name: str, url: str) -> CheckResult:
    start = time.perf_counter()
    try:
        resp = await client.get(url, timeout=TIMEOUT_SEC, follow_redirects=True)
        latency_ms = int((time.perf_counter() - start) * 1000)
        return CheckResult(name, url, True, latency_ms, f"HTTP {resp.status_code}")
    except httpx.TimeoutException:
        latency_ms = int((time.perf_counter() - start) * 1000)
        return CheckResult(name, url, False, latency_ms, "timeout")
    except Exception as e:
        latency_ms = int((time.perf_counter() - start) * 1000)
        detail = str(e).strip().splitlines()[0][:60] if str(e) else ""
        note = f"{type(e).__name__}: {detail}" if detail else type(e).__name__
        return CheckResult(name, url, False, latency_ms, note)


def collect_llm_endpoints() -> list[tuple[str, str]]:
    """读取 config/models.json，按 base_url 去重，label 用 user-defined model id。"""
    try:
        models = ModelManager().load_models()
    except Exception as e:
        print(f"warning: failed to load models config — skipping LLM checks ({e})",
              file=sys.stderr)
        return []

    seen: dict[str, str] = {}  # url -> first model id with that url
    for m in models:
        url = m.base_url or LLM_DEFAULT_URLS.get(m.provider)
        if not url:
            continue
        seen.setdefault(url, m.id)

    return [(f"llm:{model_id}", url) for url, model_id in seen.items()]


async def main() -> int:
    endpoints = list(DATA_SOURCE_ENDPOINTS) + collect_llm_endpoints()

    headers = {"User-Agent": "TradeBot-LivenessCheck/1.0"}
    async with httpx.AsyncClient(headers=headers) as client:
        wall_start = time.perf_counter()
        results = await asyncio.gather(
            *(check_endpoint(client, n, u) for n, u in endpoints)
        )
        wall = time.perf_counter() - wall_start

    table = Table(title=f"External Source Liveness (timeout={TIMEOUT_SEC:.0f}s)")
    table.add_column("name")
    table.add_column("status", justify="center")
    table.add_column("latency", justify="right")
    table.add_column("note")
    for r in results:
        status = "[green]✓[/green]" if r.ok else "[red]✗[/red]"
        table.add_row(r.name, status, f"{r.latency_ms} ms", r.note)

    Console().print(table)
    ok_count = sum(1 for r in results if r.ok)
    fail_count = len(results) - ok_count
    print(f"\nTotal: {ok_count} OK / {fail_count} FAILED  ({wall:.1f}s wall)")
    return 1 if fail_count else 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
