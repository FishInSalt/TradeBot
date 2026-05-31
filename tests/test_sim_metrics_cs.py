"""Unit tests for _sim_metrics cs (contract_size) correctness.

B3: _compute_pnl ×cs (non-liquidation PnL)
B3-bis: _derive_close_amount ÷cs (fee back-solve →张数)

B3-bis hidden danger: without ÷cs, for cs<1 (BTC/ETH):
  derived = fee / (price × fee_rate) = amount_张 × cs   (too small by ×cs)
  derived <= amount × 1.01  →  恒放行 + derived_ok=True 静默
  → FIFO under-consumes / orphan lots
"""
from scripts._sim_metrics import _derive_close_amount, _compute_pnl


class _Fill:
    """Minimal fill stub matching scripts FIFO fill attribute names."""

    def __init__(self, fee: float, filled_price: float, amount: float) -> None:
        self.fee = fee
        self.filled_price = filled_price
        self.amount = amount


def test_derive_close_amount_divides_by_cs():
    """cs=0.01, 10 张 close @101000.
    内核存 fee = 101000 × (10×0.01) × 0.0005 = 5.05
    正确反推：fee / (price × cs × fee_rate) = 5.05 / (101000 × 0.01 × 0.0005) = 10.0 张
    旧实现（无 ÷cs）：5.05 / (101000 × 0.0005) = 0.1 → derived(0.1) ≤ amount(10)×1.01 恒放行
    """
    fill = _Fill(fee=5.05, filled_price=101_000.0, amount=10.0)
    derived, ok = _derive_close_amount(fill, fee_rate=0.0005, contract_size=0.01)
    assert ok is True
    assert abs(derived - 10.0) < 1e-6  # 张数（旧实现得 0.1，静默返 True）


def test_derive_close_amount_cs1_fallback_unchanged():
    """cs=1.0 (default): 行为与旧实现完全一致。
    fill.amount=0.1 BTC (base), fee = 101000 × 0.1 × 0.0005 = 5.05
    derived = 5.05 / (101000 × 1.0 × 0.0005) = 0.1
    """
    fill = _Fill(fee=5.05, filled_price=101_000.0, amount=0.1)
    derived, ok = _derive_close_amount(fill, fee_rate=0.0005, contract_size=1.0)
    assert ok is True
    assert abs(derived - 0.1) < 1e-6


def test_compute_pnl_scales_with_cs():
    """cs=0.01: 10 张 long open@100000 close@101000
    gross = (101000 - 100000) × 10 × 0.01 = 100.0 USDT
    """
    result = _compute_pnl(100_000.0, 101_000.0, 10.0, "long", contract_size=0.01)
    assert abs(result - 100.0) < 1e-6


def test_compute_pnl_short_scales_with_cs():
    """cs=0.01: 10 张 short open@101000 close@100000
    gross = (101000 - 100000) × 10 × 0.01 = 100.0 USDT
    """
    result = _compute_pnl(101_000.0, 100_000.0, 10.0, "short", contract_size=0.01)
    assert abs(result - 100.0) < 1e-6


def test_compute_pnl_cs1_unchanged():
    """cs=1.0 (default): 旧 base 语义不变。"""
    result = _compute_pnl(50_000.0, 51_000.0, 0.1, "long", contract_size=1.0)
    assert abs(result - 100.0) < 1e-6


def test_derive_close_amount_guard_still_rejects_bad_derive():
    """cs=0.01, fee 故意偏大 → derived > amount×1.01 → fallback False."""
    # fee 故意 10×: derived = 50.5 / (101000 × 0.01 × 0.0005) = 100 张 > 10×1.01
    fill = _Fill(fee=50.5, filled_price=101_000.0, amount=10.0)
    derived, ok = _derive_close_amount(fill, fee_rate=0.0005, contract_size=0.01)
    assert ok is False
    assert abs(derived - 10.0) < 1e-6  # fallback = fill.amount
