"""OHLCV 文件缓存（不写库）。缓存目录从正在使用的只读 engine 派生（spec §B）。

历史 sim 窗口固定永不过期，故缓存无 TTL，只靠 fetched_end_ms 覆盖判定：
current_end_ms <= fetched_end_ms 命中（已结束会话恒命中），> 则 miss（活跃会话窗口增长）。
"""
from __future__ import annotations

import json
from pathlib import Path

from sqlalchemy.ext.asyncio import AsyncEngine


def cache_dir_for(engine: AsyncEngine) -> Path | None:
    """从 engine.url.database 派生缓存目录；None / :memory: / 空 → None（降级不缓存）。

    为何从 engine 派生而非新增 app.state.db_path：端点测试 create_app() 默认 data/
    tradebot.db + dependency_overrides 注入内存 engine；从 engine 派生才天然跟随
    override（内存 → None，不污染真 data/）。spec §B 否决论证。
    """
    db = engine.url.database
    if not db or db == ":memory:":
        return None
    db = db.removeprefix("file:").split("?", 1)[0]
    return Path(db).parent / "ohlcv_cache"


def _cache_file(cache_dir: Path, sid: str, tf: str) -> Path:
    return cache_dir / f"{sid}_{tf}.json"


def read(cache_dir: Path | None, sid: str, tf: str, current_end_ms: int) -> list[list] | None:
    """命中（文件存在 且 current_end_ms <= fetched_end_ms）→ 裸行；否则 None。"""
    if cache_dir is None:
        return None
    path = _cache_file(cache_dir, sid, tf)
    if not path.is_file():
        return None
    blob = json.loads(path.read_text())
    if current_end_ms <= blob["fetched_end_ms"]:
        return blob["bars"]
    return None


def write(cache_dir: Path | None, sid: str, tf: str, symbol: str,
          fetched_end_ms: int, bars: list[list]) -> None:
    """落盘 <sid>_<tf>.json（mkdir parents + 覆盖写）。cache_dir None → no-op。"""
    if cache_dir is None:
        return
    cache_dir.mkdir(parents=True, exist_ok=True)
    payload = {"symbol": symbol, "timeframe": tf,
               "fetched_end_ms": fetched_end_ms, "bars": bars}
    _cache_file(cache_dir, sid, tf).write_text(json.dumps(payload))
