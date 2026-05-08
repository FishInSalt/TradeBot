"""AC-11 regression guard — pydantic-ai usage attrs vs hit_rate formula 双轨保护.

PR #42 三审 I-2 修订：原版"hit_rate consistency"测试用 make_usage factory，
factory 把 details 由入参构造，导致 `usage.cache_read_tokens == details['prompt_cache_hit_tokens']`
是 by construction 永真，无 drift-guard 价值。

改造为：
1. **SDK contract test** — 验证 pydantic-ai RunUsage 实际暴露 cli/app.py 依赖的
   字段名 (input_tokens / output_tokens / cache_read_tokens / cache_write_tokens / details)。
   未来 pydantic-ai 升级 rename 任一字段会立即失败，比 factory test 更有保护价值。
2. **Formula documentation** — 余下两个测试用 mock 数据展示派生公式语义，
   作可执行 docs（非 drift guard）。

T0 manual probe 仍是 vendor-key 对齐的真实回归保护（spec §5.5.1 Note 1）。
"""
import pytest


def test_pydantic_ai_run_usage_has_required_attrs():
    """T9.4 (AC-11 contract): pydantic-ai RunUsage 必须暴露 cli/app.py:599+ 读取
    的所有字段名。pydantic-ai 升级 rename / remove 字段时 fail loud。
    """
    from pydantic_ai.usage import RunUsage

    fields = set(RunUsage.__dataclass_fields__.keys())
    required = {
        "input_tokens",        # cli/app.py:622 usage.input_tokens
        "output_tokens",       # cli/app.py:623 usage.output_tokens
        "cache_read_tokens",   # cli/app.py:620 usage.cache_read_tokens
        "cache_write_tokens",  # cli/app.py:621 usage.cache_write_tokens
        "details",             # cli/app.py:601 usage.details (DeepSeek vendor keys)
    }
    missing = required - fields
    assert not missing, (
        f"pydantic-ai RunUsage missing required field(s): {missing}. "
        f"cli/app.py:599+ depends on these. Migration / pin needed."
    )


def test_hit_rate_derived_formula_documentation(make_usage):
    """T9.5 (AC-11 doc): 派生公式 cache_hit_rate_derived = cache_read * 100 / input_tokens
    (spec §5.2.3 推荐 portable 公式)。可执行文档，非 drift guard.
    """
    usage = make_usage(input_tokens=1000, cache_read_tokens=750)
    derived = usage.cache_read_tokens * 100.0 / usage.input_tokens
    assert derived == pytest.approx(75.0)


def test_hit_rate_derived_null_on_zero_input(make_usage):
    """T9.6 (AC-11 doc): cache_hit_rate_derived 在 input_tokens=0 时应 NULL
    (spec view CASE WHEN input_tokens > 0 THEN ... ELSE NULL)。
    """
    usage = make_usage(input_tokens=0, cache_read_tokens=0)
    derived = (usage.cache_read_tokens * 100.0 / usage.input_tokens) if usage.input_tokens > 0 else None
    assert derived is None
