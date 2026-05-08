"""AC-4: PriceLevelAlertInfo 加 alert_id 字段 + auto-trigger 实例化 + trigger_context 镜像。"""
import pytest
from src.integrations.exchange.base import PriceLevelAlertInfo


def test_price_level_alert_info_has_alert_id_field():
    """T1.1: dataclass 7 字段含 alert_id（无默认值，必填）。"""
    info = PriceLevelAlertInfo(
        symbol="BTC/USDT:USDT", target_price=75000.0, direction="above",
        current_price=74950.0, reasoning="test alert",
        timestamp=1746098000000, alert_id="abc12345",
    )
    assert info.alert_id == "abc12345"


def test_price_level_alert_info_alert_id_required():
    """T1.2: alert_id 必填（dataclass 无默认值）— 缺失 raise TypeError。"""
    with pytest.raises(TypeError, match="alert_id"):
        PriceLevelAlertInfo(  # type: ignore[call-arg]
            symbol="BTC/USDT:USDT", target_price=75000.0, direction="above",
            current_price=74950.0, reasoning="test alert",
            timestamp=1746098000000,
        )


def test_price_level_alert_info_field_count():
    """T1.3: 字段总数为 7（防 future drift）。"""
    from dataclasses import fields
    assert len(fields(PriceLevelAlertInfo)) == 7
