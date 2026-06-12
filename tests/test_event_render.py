"""§3 共享事件渲染器 — _format_event_breakdown 提取 + 模块归属。

被搬移函数（_format_relative_time / _format_event_age / _wake_time_suffix /
_format_price_level_alert_trigger / _render_event_block）的行为测试留在原文件
（test_wake_event_timestamp.py 等），仅改 import 路径——断言即 byte-identical 回归。
"""
from __future__ import annotations


def test_breakdown_single_fill():
    from src.services.event_render import _format_event_breakdown
    assert _format_event_breakdown([("conditional", None)]) == "1 fill"


def test_breakdown_plural_alerts():
    from src.services.event_render import _format_event_breakdown
    events = [("alert", None), ("alert", None)]
    assert _format_event_breakdown(events) == "2 alerts"


def test_breakdown_mixed_fill_first():
    """fill 在前——与堆优先级 conditional < alert 一致（spec §4）。"""
    from src.services.event_render import _format_event_breakdown
    events = [("conditional", None), ("alert", None), ("alert", None)]
    assert _format_event_breakdown(events) == "1 fill, 2 alerts"


def test_breakdown_unknown_types_fallback():
    """全未知类型 → 'N events' fallback（与 _wake_header_line 原行为一致）。"""
    from src.services.event_render import _format_event_breakdown
    events = [("mystery", None), ("mystery", None)]
    assert _format_event_breakdown(events) == "2 events"
