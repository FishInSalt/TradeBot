# src/services/price_alert.py
from __future__ import annotations

from collections import deque
from dataclasses import dataclass


@dataclass
class AlertInfo:
    symbol: str
    current_price: float
    reference_price: float
    change_pct: float
    window_minutes: int
    timestamp: int


class PriceAlertService:
    @staticmethod
    def _validate_params(threshold_pct: float, window_minutes: int, cooldown_minutes: int) -> None:
        if not (0.5 <= threshold_pct <= 50.0):
            raise ValueError(f"threshold_pct must be 0.5-50.0, got {threshold_pct}")
        if not (1 <= window_minutes <= 60):
            raise ValueError(f"window_minutes must be 1-60, got {window_minutes}")
        if not (1 <= cooldown_minutes <= 120):
            raise ValueError(f"cooldown_minutes must be 1-120, got {cooldown_minutes}")

    def __init__(
        self,
        symbol: str,
        window_minutes: int,
        threshold_pct: float,
        cooldown_minutes: int,
    ):
        self._validate_params(threshold_pct, window_minutes, cooldown_minutes)
        self._symbol = symbol
        self._window_ms = window_minutes * 60 * 1000
        self._window_minutes = window_minutes
        self._threshold_pct = threshold_pct
        self._cooldown_ms = cooldown_minutes * 60 * 1000
        self._ticks: deque[tuple[float, int]] = deque()
        self._last_alert_ts: dict[str, int] = {}

    def check(self, price: float, timestamp: int) -> AlertInfo | None:
        """Feed a tick price, return AlertInfo or None."""
        self._ticks.append((price, timestamp))
        cutoff = timestamp - self._window_ms
        while self._ticks and self._ticks[0][1] < cutoff:
            self._ticks.popleft()

        if len(self._ticks) < 2:
            return None

        high = max(p for p, _ in self._ticks)
        low = min(p for p, _ in self._ticks)

        drop_pct = (price - high) / high * 100 if high > 0 else 0.0
        rise_pct = (price - low) / low * 100 if low > 0 else 0.0

        if abs(drop_pct) >= abs(rise_pct) and abs(drop_pct) >= self._threshold_pct:
            direction = "drop"
            change_pct = drop_pct
            reference_price = high
        elif abs(rise_pct) >= self._threshold_pct:
            direction = "surge"
            change_pct = rise_pct
            reference_price = low
        else:
            return None

        last_ts = self._last_alert_ts.get(direction, 0)
        if timestamp - last_ts < self._cooldown_ms:
            return None

        self._last_alert_ts[direction] = timestamp
        return AlertInfo(
            symbol=self._symbol,
            current_price=price,
            reference_price=reference_price,
            change_pct=change_pct,
            window_minutes=self._window_minutes,
            timestamp=timestamp,
        )

    def update_params(
        self,
        threshold_pct: float,
        window_minutes: int,
        cooldown_minutes: int,
    ) -> None:
        """Update parameters at runtime. Raises ValueError if out of bounds."""
        self._validate_params(threshold_pct, window_minutes, cooldown_minutes)
        self._threshold_pct = threshold_pct
        self._window_ms = window_minutes * 60 * 1000
        self._window_minutes = window_minutes
        self._cooldown_ms = cooldown_minutes * 60 * 1000
