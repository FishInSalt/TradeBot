# tests/test_price_alert.py

import pytest


def test_alert_info_fields():
    """AlertInfo 应包含所有必需字段。"""
    from src.services.price_alert import AlertInfo
    info = AlertInfo(
        symbol="BTC/USDT:USDT",
        current_price=59000.0,
        reference_price=61000.0,
        change_pct=-3.28,
        window_minutes=5,
        timestamp=1712534400000,
    )
    assert info.symbol == "BTC/USDT:USDT"
    assert info.change_pct < 0
    assert info.reference_price == 61000.0


def test_no_alert_below_threshold():
    """价格变化未达阈值时不应触发警报。"""
    from src.services.price_alert import PriceAlertService
    service = PriceAlertService(
        symbol="BTC/USDT:USDT",
        window_minutes=5,
        threshold_pct=3.0,
        cooldown_minutes=15,
    )
    base_ts = 1_000_000_000_000
    service.check(60000.0, base_ts)
    result = service.check(59400.0, base_ts + 60_000)
    assert result is None


def test_alert_triggers_on_drop():
    """价格下跌超过阈值应触发 alert（change_pct 为负）。"""
    from src.services.price_alert import PriceAlertService
    service = PriceAlertService(
        symbol="BTC/USDT:USDT",
        window_minutes=5,
        threshold_pct=3.0,
        cooldown_minutes=15,
    )
    base_ts = 1_000_000_000_000
    service.check(60000.0, base_ts)
    result = service.check(57900.0, base_ts + 60_000)
    assert result is not None
    assert result.change_pct < -3.0
    assert result.reference_price == 60000.0
    assert result.current_price == 57900.0
    assert result.symbol == "BTC/USDT:USDT"


def test_alert_triggers_on_surge():
    """价格上涨超过阈值应触发 alert（change_pct 为正）。"""
    from src.services.price_alert import PriceAlertService
    service = PriceAlertService(
        symbol="BTC/USDT:USDT",
        window_minutes=5,
        threshold_pct=3.0,
        cooldown_minutes=15,
    )
    base_ts = 1_000_000_000_000
    service.check(60000.0, base_ts)
    result = service.check(62100.0, base_ts + 60_000)
    assert result is not None
    assert result.change_pct > 3.0
    assert result.reference_price == 60000.0
    assert result.current_price == 62100.0


def test_cooldown_blocks_same_direction():
    """同方向触发后在冷却期内不应重复触发。"""
    from src.services.price_alert import PriceAlertService
    service = PriceAlertService(
        symbol="BTC/USDT:USDT",
        window_minutes=5,
        threshold_pct=3.0,
        cooldown_minutes=15,
    )
    base_ts = 1_000_000_000_000
    service.check(60000.0, base_ts)
    result1 = service.check(57900.0, base_ts + 60_000)
    assert result1 is not None
    result2 = service.check(57000.0, base_ts + 120_000)
    assert result2 is None


def test_cooldown_allows_opposite_direction():
    """V 形反弹：下跌触发后，反方向上涨超阈值仍应触发。"""
    from src.services.price_alert import PriceAlertService
    service = PriceAlertService(
        symbol="BTC/USDT:USDT",
        window_minutes=5,
        threshold_pct=3.0,
        cooldown_minutes=15,
    )
    base_ts = 1_000_000_000_000
    service.check(60000.0, base_ts)
    result1 = service.check(57900.0, base_ts + 60_000)
    assert result1 is not None
    assert result1.change_pct < 0
    result2 = service.check(59700.0, base_ts + 120_000)
    assert result2 is not None
    assert result2.change_pct > 0


def test_cooldown_expires():
    """冷却期过后同方向应再次触发。"""
    from src.services.price_alert import PriceAlertService
    service = PriceAlertService(
        symbol="BTC/USDT:USDT",
        window_minutes=5,
        threshold_pct=3.0,
        cooldown_minutes=1,
    )
    base_ts = 1_000_000_000_000
    service.check(60000.0, base_ts)
    result1 = service.check(57900.0, base_ts + 30_000)
    assert result1 is not None
    new_base = base_ts + 120_000
    service.check(60000.0, new_base)
    result2 = service.check(57900.0, new_base + 30_000)
    assert result2 is not None


def test_window_eviction():
    """窗口外的旧数据应被淘汰，不影响当前计算。"""
    from src.services.price_alert import PriceAlertService
    service = PriceAlertService(
        symbol="BTC/USDT:USDT",
        window_minutes=1,
        threshold_pct=3.0,
        cooldown_minutes=1,
    )
    base_ts = 1_000_000_000_000
    service.check(60000.0, base_ts)
    result = service.check(58000.0, base_ts + 120_000)
    assert result is None


def test_update_params():
    """update_params 应更新内部参数。"""
    from src.services.price_alert import PriceAlertService
    service = PriceAlertService(
        symbol="BTC/USDT:USDT",
        window_minutes=5,
        threshold_pct=3.0,
        cooldown_minutes=15,
    )
    base_ts = 1_000_000_000_000
    service.check(60000.0, base_ts)
    result = service.check(59000.0, base_ts + 60_000)
    assert result is None
    service.update_params(threshold_pct=1.0, window_minutes=5, cooldown_minutes=15)
    result = service.check(58800.0, base_ts + 120_000)
    assert result is not None


def test_update_params_boundary_validation():
    """update_params 超出边界时应抛 ValueError。"""
    from src.services.price_alert import PriceAlertService
    service = PriceAlertService(
        symbol="BTC/USDT:USDT",
        window_minutes=5,
        threshold_pct=3.0,
        cooldown_minutes=15,
    )
    with pytest.raises(ValueError):
        service.update_params(threshold_pct=0.1, window_minutes=5, cooldown_minutes=15)
    with pytest.raises(ValueError):
        service.update_params(threshold_pct=3.0, window_minutes=0, cooldown_minutes=15)
    with pytest.raises(ValueError):
        service.update_params(threshold_pct=3.0, window_minutes=5, cooldown_minutes=0)
    with pytest.raises(ValueError):
        service.update_params(threshold_pct=3.0, window_minutes=5, cooldown_minutes=200)
    with pytest.raises(ValueError):
        service.update_params(threshold_pct=55.0, window_minutes=5, cooldown_minutes=15)
    with pytest.raises(ValueError):
        service.update_params(threshold_pct=3.0, window_minutes=70, cooldown_minutes=15)
