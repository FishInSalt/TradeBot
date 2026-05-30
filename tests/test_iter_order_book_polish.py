"""PR #64 review follow-up: docstring 示例格式必须可由渲染器复现 (I-1) + notional 边界 (M-3)。

根因：wrapper docstring 的 call→output 示例是 LLM 最高杠杆通道，但其数值格式（$K/$M、
span pts）若与 `_fmt_ob_notional` / 渲染器 f-string 规则脱节，会给 agent 不可能出现的格式
范本（违原则 2/7）。现有测试只断松散子串，catch 不到格式 drift。本文件 pin 住二者一致。
"""
import re

import pytest

from src.agent.tools_perception import _fmt_ob_notional


def _order_book_desc() -> str:
    from src.agent.trader import create_trader_agent
    from src.config import PersonaConfig

    agent = create_trader_agent(model="test", persona_config=PersonaConfig())
    return agent._function_toolset.tools["get_order_book"].tool_def.description


def test_get_order_book_docstring_notional_examples_renderer_reproducible():
    """desc 中每个 $K/$M notional token 必须能被 `_fmt_ob_notional` 逐字复现
    （存在 usd 使输出 == token）—— catch $241K(应 $241.0K) / $0.29M(渲染器永不产) 类 drift。"""
    desc = _order_book_desc()
    tokens = re.findall(r"\$[\d.]+[KM]", desc)
    assert tokens, "desc 应含 $K/$M 示例 token"
    for tok in tokens:
        usd = float(tok[1:-1]) * (1e6 if tok.endswith("M") else 1e3)
        assert _fmt_ob_notional(usd) == tok, (
            f"示例 token {tok} 渲染器无法复现（_fmt_ob_notional({usd}) = {_fmt_ob_notional(usd)}）"
        )


def test_get_order_book_docstring_span_pts_two_decimals():
    """desc 中 span pts 必须 2 位小数（impl f-string `{span:.2f} pts`）—— catch span 3.2/2.0 drift。"""
    desc = _order_book_desc()
    spans = re.findall(r"span (\d[\d.]*) pts", desc)
    assert spans, "desc 应含 span pts 示例"
    for s in spans:
        assert re.fullmatch(r"\d+\.\d{2}", s), f"span pts {s!r} 非 .2f（渲染器输出 2 位小数）"


def test_fmt_ob_notional_no_1000K_seam():
    """M-3: [999_950, 1e6) 应进位 $1.00M 而非 $1000.0K（K/M 衔接无缝）。"""
    assert _fmt_ob_notional(999_950) == "$1.00M"
    assert _fmt_ob_notional(999_999) == "$1.00M"
    assert _fmt_ob_notional(1_000_000) == "$1.00M"
    # 下边界仍走 K，且不出现 $1000.0K
    assert _fmt_ob_notional(999_949) == "$999.9K"
    assert "1000.0K" not in _fmt_ob_notional(999_949)


def test_get_order_book_docstring_bid_share_labeled_by_size():
    """P2: docstring 示例 bid share 标注 'by size'，与 impl 输出一致（消 vs notional 歧义）。"""
    assert "by size" in _order_book_desc()

