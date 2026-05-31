import pytest
import ccxt
from unittest.mock import MagicMock
from tests.test_simulated_exchange import _make_exchange


def _ex_with_ccxt(precision_fn):
    ex = _make_exchange()
    ex._ccxt = MagicMock()
    ex._ccxt.amount_to_precision = MagicMock(side_effect=precision_fn)
    return ex


def test_amount_to_precision_delegates_to_ccxt():
    ex = _ex_with_ccxt(lambda s, a: "0.16")   # ccxt TRUNCATE returns string
    assert ex.amount_to_precision("BTC/USDT:USDT", 0.1667) == 0.16


def test_amount_to_precision_sub_min_returns_zero():
    def _raise(s, a):
        raise ccxt.InvalidOrder("amount must be greater than minimum amount precision")
    ex = _ex_with_ccxt(_raise)
    assert ex.amount_to_precision("BTC/USDT:USDT", 1e-12) == 0.0   # too-small guard
