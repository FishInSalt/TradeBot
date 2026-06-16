"""OHLCV 文件缓存（不写库）。缓存目录从正在使用的只读 engine 派生（spec §B）。

历史 sim 窗口固定永不过期，故缓存无 TTL。本模块只管文件 I/O：read_raw 取整个 blob、
write 落盘；覆盖判定（current_end_ms <= fetched_end_ms 全命中 / > 则增量尾部 merge）
在 queries.get_ohlcv —— 活跃会话窗口增长时只补尾部、不全量重拉。
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


def read_raw(cache_dir: Path | None, sid: str, tf: str) -> dict | None:
    """文件存在且形态合法（含 fetched_end_ms + bars 键）→ 返回整个 blob；否则 None。

    覆盖判定（current_end_ms <= fetched_end_ms）上移到 queries.get_ohlcv —— 调用方需拿到
    旧 blob（连同 fetched_end_ms）做增量尾部 merge，故本函数不再自行命中判定。

    损坏缓存文件（空 / 截断 / 非法 JSON / 缺键——write_text 非原子，进程中途被杀可致）
    视为 None，等价于冷启动重拉（graceful degradation）。
    """
    if cache_dir is None:
        return None
    path = _cache_file(cache_dir, sid, tf)
    if not path.is_file():
        return None
    try:
        blob = json.loads(path.read_text())
        if isinstance(blob, dict) and "fetched_end_ms" in blob and "bars" in blob:
            return blob
    except (json.JSONDecodeError, TypeError, OSError):
        return None
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
