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
    def _validate_params(threshold_pct: float, window_minutes: int) -> None:
        if not (0.1 <= threshold_pct <= 50.0):
            raise ValueError(f"threshold_pct must be 0.1-50.0, got {threshold_pct}")
        if not (1 <= window_minutes <= 240):
            raise ValueError(f"window_minutes must be 1-240, got {window_minutes}")

    def __init__(
        self,
        symbol: str,
        window_minutes: int = 60,
        threshold_pct: float = 5.0,
    ):
        self._validate_params(threshold_pct, window_minutes)
        self._symbol = symbol
        self._window_ms = window_minutes * 60 * 1000
        self._window_minutes = window_minutes
        self._threshold_pct = threshold_pct
        self._ticks: deque[tuple[float, int]] = deque()

    def check(self, price: float, timestamp: int) -> AlertInfo | None:
        """Feed a tick price, return AlertInfo if threshold breached, else None.
        On trigger, clears the window (reset semantics)."""
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
            self._ticks.clear()
            return AlertInfo(
                symbol=self._symbol, current_price=price,
                reference_price=high, change_pct=drop_pct,
                window_minutes=self._window_minutes, timestamp=timestamp,
            )

        if rise_pct >= self._threshold_pct:
            self._ticks.clear()
            return AlertInfo(
                symbol=self._symbol, current_price=price,
                reference_price=low, change_pct=rise_pct,
                window_minutes=self._window_minutes, timestamp=timestamp,
            )

        return None

    def update_params(self, threshold_pct: float, window_minutes: int) -> None:
        """Update parameters at runtime. Raises ValueError if out of bounds."""
        self._validate_params(threshold_pct, window_minutes)
        self._threshold_pct = threshold_pct
        self._window_ms = window_minutes * 60 * 1000
        self._window_minutes = window_minutes
        self._ticks.clear()

    def get_params(self) -> tuple[float, int]:
        """Return current (threshold_pct, window_minutes)."""
        return (self._threshold_pct, self._window_minutes)
