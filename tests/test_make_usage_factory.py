"""T9: make_usage factory 单元测试 — defaults + override + 双轨 details mirror。"""


def test_make_usage_defaults(make_usage):
    """T9.1: 无参调用返回 DeepSeek-typical mock。"""
    usage = make_usage()
    assert usage.input_tokens == 1000
    assert usage.output_tokens == 200
    assert usage.total_tokens == 1200
    assert usage.cache_read_tokens == 700
    assert usage.cache_write_tokens == 0
    assert usage.details["prompt_cache_hit_tokens"] == 700
    assert usage.details["prompt_cache_miss_tokens"] == 300


def test_make_usage_override(make_usage):
    """T9.2: 可独立覆盖每个属性。"""
    usage = make_usage(
        input_tokens=500, output_tokens=100,
        cache_read_tokens=400, cache_write_tokens=10,
    )
    assert usage.input_tokens == 500
    assert usage.cache_read_tokens == 400
    assert usage.details["prompt_cache_hit_tokens"] == 400
    assert usage.details["prompt_cache_miss_tokens"] == 100


def test_make_usage_custom_details(make_usage):
    """T9.3: 显式传 details 覆盖默认（vendor mismatch 测试场景）。"""
    usage = make_usage(
        input_tokens=1000, cache_read_tokens=500,
        details={"reasoning_tokens": 50},
    )
    assert usage.details == {"reasoning_tokens": 50}
    assert usage.cache_read_tokens == 500
