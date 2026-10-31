from decimal import Decimal

import pytest

from actp.levels import reached, scl_for

D = Decimal


def test_buy_scl_is_between_sl_and_cut_loss():
    scl = scl_for("BUY", D("4116.98"), D("4111.30"), D("0.25"))
    assert scl == D("4115.56")
    assert D("4111.30") < scl < D("4116.98")


def test_sell_scl_is_between_cut_loss_and_sl():
    scl = scl_for("SELL", D("4127.05"), D("4132.73"), D("0.25"))
    assert scl == D("4128.47")
    assert D("4127.05") < scl < D("4132.73")


def test_malformed_plan_falls_back_to_cut_loss():
    assert scl_for("BUY", D("100"), D("105"), D("0.25")) == D("100")


@pytest.mark.parametrize("fraction", ["0", "1", "-0.1", "1.5", "NaN"])
def test_bad_fraction_rejected(fraction):
    with pytest.raises(ValueError):
        scl_for("BUY", D("10"), D("5"), D(fraction))


def test_reached_is_inclusive_on_the_losing_side():
    assert reached("BUY", D("10"), D("10")) and reached("BUY", D("9"), D("10"))
    assert not reached("BUY", D("11"), D("10"))
    assert reached("SELL", D("10"), D("10")) and not reached("SELL", D("9"), D("10"))
