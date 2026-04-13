# tests/test_price_alert.py

import pytest


def test_alert_info_fields():
    """AlertInfo should contain all required fields."""
    from src.services.price_alert import AlertInfo
    info = AlertInfo(
        symbol="BTC/USDT:USDT", current_price=59000.0,
        reference_price=61000.0, change_pct=-3.28,
        window_minutes=60, timestamp=1712534400000,
    )
    assert info.symbol == "BTC/USDT:USDT"
    assert info.change_pct < 0


def test_no_alert_below_threshold():
    """Price change below threshold should not trigger."""
    from src.services.price_alert import PriceAlertService
    service = PriceAlertService(symbol="BTC/USDT:USDT", window_minutes=60, threshold_pct=5.0)
    base_ts = 1_000_000_000_000
    service.check(60000.0, base_ts)
    result = service.check(57500.0, base_ts + 60_000)  # -4.2%, below 5%
    assert result is None


def test_alert_triggers_on_drop():
    """Drop exceeding threshold should trigger with negative change_pct."""
    from src.services.price_alert import PriceAlertService
    service = PriceAlertService(symbol="BTC/USDT:USDT", window_minutes=60, threshold_pct=5.0)
    base_ts = 1_000_000_000_000
    service.check(60000.0, base_ts)
    result = service.check(56900.0, base_ts + 60_000)  # -5.17%
    assert result is not None
    assert result.change_pct < -5.0
    assert result.reference_price == 60000.0
    assert result.current_price == 56900.0


def test_alert_triggers_on_surge():
    """Surge exceeding threshold should trigger with positive change_pct."""
    from src.services.price_alert import PriceAlertService
    service = PriceAlertService(symbol="BTC/USDT:USDT", window_minutes=60, threshold_pct=5.0)
    base_ts = 1_000_000_000_000
    service.check(60000.0, base_ts)
    result = service.check(63100.0, base_ts + 60_000)  # +5.17%
    assert result is not None
    assert result.change_pct > 5.0
    assert result.reference_price == 60000.0


def test_window_reset_on_trigger():
    """After trigger, window clears. Need >=2 ticks to re-evaluate."""
    from src.services.price_alert import PriceAlertService
    service = PriceAlertService(symbol="BTC/USDT:USDT", window_minutes=60, threshold_pct=5.0)
    base_ts = 1_000_000_000_000
    service.check(60000.0, base_ts)
    result1 = service.check(56900.0, base_ts + 60_000)
    assert result1 is not None  # first trigger, window cleared

    # After clear: first tick re-establishes baseline
    result2 = service.check(56900.0, base_ts + 120_000)
    assert result2 is None  # only 1 tick after clear, len < 2

    result2b = service.check(56800.0, base_ts + 150_000)
    assert result2b is None  # 2 ticks now, but only 0.18% drop — below 5%

    # Drop 5.1% from new high (56900)
    result3 = service.check(54000.0, base_ts + 180_000)  # (54000-56900)/56900 = -5.10%
    assert result3 is not None
    assert result3.reference_price == 56900.0


def test_continuous_crash_alerts_every_threshold():
    """Continuous crash: alerts fire every ~5% drop. Baseline tick needed after each clear."""
    from src.services.price_alert import PriceAlertService
    service = PriceAlertService(symbol="BTC/USDT:USDT", window_minutes=60, threshold_pct=5.0)
    base_ts = 1_000_000_000_000

    service.check(100000.0, base_ts)
    r1 = service.check(94900.0, base_ts + 60_000)  # -5.1%, triggers, clear
    assert r1 is not None

    service.check(94900.0, base_ts + 90_000)          # baseline tick after clear
    r2 = service.check(90000.0, base_ts + 120_000)    # -5.2% from 94900
    assert r2 is not None

    service.check(90000.0, base_ts + 150_000)          # baseline tick after clear
    r3 = service.check(85400.0, base_ts + 180_000)    # -5.1% from 90000
    assert r3 is not None


def test_v_shape_rebound():
    """V-shape: drop triggers, baseline re-established, then rebound triggers."""
    from src.services.price_alert import PriceAlertService
    service = PriceAlertService(symbol="BTC/USDT:USDT", window_minutes=60, threshold_pct=5.0)
    base_ts = 1_000_000_000_000

    service.check(60000.0, base_ts)
    r1 = service.check(56900.0, base_ts + 60_000)  # drop triggers, clear
    assert r1 is not None and r1.change_pct < 0

    # After clear: baseline tick, then rebound
    service.check(56900.0, base_ts + 90_000)           # baseline tick
    r2 = service.check(59800.0, base_ts + 120_000)    # +5.1% from 56900
    assert r2 is not None and r2.change_pct > 0


def test_window_eviction():
    """Old ticks outside window should be evicted."""
    from src.services.price_alert import PriceAlertService
    service = PriceAlertService(symbol="BTC/USDT:USDT", window_minutes=1, threshold_pct=5.0)
    base_ts = 1_000_000_000_000
    service.check(60000.0, base_ts)
    # 2 minutes later, old tick evicted
    result = service.check(57000.0, base_ts + 120_000)
    assert result is None  # only 1 tick in window


def test_update_params():
    """update_params should change threshold and window."""
    from src.services.price_alert import PriceAlertService
    service = PriceAlertService(symbol="BTC/USDT:USDT", window_minutes=60, threshold_pct=5.0)
    base_ts = 1_000_000_000_000
    service.check(60000.0, base_ts)
    result = service.check(58500.0, base_ts + 60_000)
    assert result is None  # -2.5%, below 5%
    service.update_params(threshold_pct=2.0, window_minutes=60)
    result = service.check(58400.0, base_ts + 120_000)
    assert result is not None  # -2.7% from 60000, above 2%


def test_update_params_boundary_validation():
    """update_params should raise ValueError for out-of-range values."""
    from src.services.price_alert import PriceAlertService
    service = PriceAlertService(symbol="BTC/USDT:USDT", window_minutes=60, threshold_pct=5.0)
    with pytest.raises(ValueError):
        service.update_params(threshold_pct=0.1, window_minutes=60)
    with pytest.raises(ValueError):
        service.update_params(threshold_pct=55.0, window_minutes=60)
    with pytest.raises(ValueError):
        service.update_params(threshold_pct=5.0, window_minutes=0)
    with pytest.raises(ValueError):
        service.update_params(threshold_pct=5.0, window_minutes=250)


def test_single_tick_no_alert():
    """Single tick should not trigger (need >= 2)."""
    from src.services.price_alert import PriceAlertService
    service = PriceAlertService(symbol="BTC/USDT:USDT", window_minutes=60, threshold_pct=5.0)
    result = service.check(60000.0, 1_000_000_000_000)
    assert result is None
