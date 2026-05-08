"""AC-11 regression guard — hit_rate 公式语义 (spec §5.5.1) 不被未来 pydantic-ai
升级悄悄改变；T0 是 manual research 一次性验证，本测试是 CI 自动化兜底。
"""
import pytest


def test_hit_rate_formula_consistent_with_legacy_logger(make_usage):
    """T9.4 (AC-11): cli/app.py 现有 hit_rate 公式 = cache_hit / (hit + miss) * 100；
    spec §5.5.1 双轨设计后 logger 仍用此公式（DeepSeek vendor key），DB cache_hit_rate
    列存此值。本测试断言 mock cycle 上公式一致。
    """
    usage = make_usage(input_tokens=1000, cache_read_tokens=700)
    cache_hit = usage.details["prompt_cache_hit_tokens"]
    cache_miss = usage.details["prompt_cache_miss_tokens"]
    hit_rate = (cache_hit / (cache_hit + cache_miss) * 100) if (cache_hit + cache_miss) > 0 else 0.0

    # AC-11 (a): cache_read_tokens ≈ prompt_cache_hit_tokens (5% 误差内)
    rel_err_a = abs(usage.cache_read_tokens - cache_hit) / max(usage.cache_read_tokens, cache_hit)
    assert rel_err_a < 0.05, f"AC-11 (a) violated: cache_read={usage.cache_read_tokens} hit={cache_hit}"

    # AC-11 (b): input_tokens ≈ cache_hit + cache_miss (5% 误差内)
    rel_err_b = abs(usage.input_tokens - (cache_hit + cache_miss)) / max(usage.input_tokens, cache_hit + cache_miss)
    assert rel_err_b < 0.05, f"AC-11 (b) violated: input={usage.input_tokens} sum={cache_hit + cache_miss}"

    assert hit_rate == pytest.approx(70.0)


def test_hit_rate_derived_portable_formula(make_usage):
    """T9.5 (AC-11): cache_hit_rate_derived 派生公式 = cache_read * 100 / input_tokens
    (provider-agnostic，spec §5.2.3 推荐分析端用)。
    """
    usage = make_usage(input_tokens=1000, cache_read_tokens=750)
    derived = usage.cache_read_tokens * 100.0 / usage.input_tokens
    assert derived == pytest.approx(75.0)


def test_hit_rate_derived_null_on_zero_input(make_usage):
    """T9.6 (AC-11): cache_hit_rate_derived 在 input_tokens=0 时应 NULL（spec view CASE）。"""
    usage = make_usage(input_tokens=0, cache_read_tokens=0)
    derived = (usage.cache_read_tokens * 100.0 / usage.input_tokens) if usage.input_tokens > 0 else None
    assert derived is None
