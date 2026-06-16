"""ohlcv_cache 文件缓存——单元测试。"""
from __future__ import annotations

from pathlib import Path

from sqlalchemy.ext.asyncio import create_async_engine

from src.webui import ohlcv_cache
from src.webui.db import make_readonly_engine


def test_cache_dir_for_readonly_engine_strips_file_prefix(tmp_path):
    """只读 engine .database='file:/abs/x.db' → 剥 file: → <abs 父>/ohlcv_cache。"""
    db = tmp_path / "tradebot.db"
    db.write_text("")  # make_readonly_engine 用 abspath，不要求文件存在，但建之无害
    eng = make_readonly_engine(str(db))
    assert ohlcv_cache.cache_dir_for(eng) == tmp_path / "ohlcv_cache"


def test_cache_dir_for_plain_file():
    eng = create_async_engine("sqlite+aiosqlite:////tmp/x.db")
    assert ohlcv_cache.cache_dir_for(eng) == Path("/tmp/ohlcv_cache")


def test_cache_dir_for_memory_returns_none():
    eng = create_async_engine("sqlite+aiosqlite:///:memory:")
    assert ohlcv_cache.cache_dir_for(eng) is None


def test_read_write_roundtrip(tmp_path):
    cache_dir = tmp_path / "ohlcv_cache"
    bars = [[1_700_000_000_000, 1.0, 2.0, 0.5, 1.5, 10.0]]
    ohlcv_cache.write(cache_dir, "sid1", "1h", "BTC/USDT:USDT", 1_700_000_060_000, bars)
    # 覆盖判定：current_end <= fetched_end → 命中
    assert ohlcv_cache.read(cache_dir, "sid1", "1h", 1_700_000_060_000) == bars
    assert ohlcv_cache.read(cache_dir, "sid1", "1h", 1_700_000_000_000) == bars  # 更早也命中
    # current_end > fetched_end（活跃会话窗口增长）→ miss
    assert ohlcv_cache.read(cache_dir, "sid1", "1h", 1_700_000_120_000) is None


def test_read_missing_file_returns_none(tmp_path):
    assert ohlcv_cache.read(tmp_path / "ohlcv_cache", "nope", "1h", 1) is None


def test_read_write_none_cache_dir_noop():
    """cache_dir None（内存库降级）→ read None / write no-op，不抛。"""
    assert ohlcv_cache.read(None, "sid", "1h", 1) is None
    ohlcv_cache.write(None, "sid", "1h", "BTC/USDT:USDT", 1, [[1, 1, 1, 1, 1, 1]])  # 不抛


def test_read_corrupt_file_returns_none(tmp_path):
    """损坏缓存（空 / 非法 JSON / 缺键）→ 视为 miss 返回 None，不抛。"""
    cache_dir = tmp_path / "ohlcv_cache"
    cache_dir.mkdir(parents=True)
    # 空文件
    (cache_dir / "s1_1h.json").write_text("")
    assert ohlcv_cache.read(cache_dir, "s1", "1h", 1) is None
    # 非法 JSON
    (cache_dir / "s2_1h.json").write_text("{not json")
    assert ohlcv_cache.read(cache_dir, "s2", "1h", 1) is None
    # 合法 JSON 但缺 fetched_end_ms 键
    (cache_dir / "s3_1h.json").write_text('{"bars": []}')
    assert ohlcv_cache.read(cache_dir, "s3", "1h", 1) is None
