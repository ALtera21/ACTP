
from decimal import Decimal

import pytest

from actp.price_calculator import calculate_entry_prices


def test_buy_entry_prices():
    prices = calculate_entry_prices("4119.83", "4117.64")

    assert prices == [
        Decimal("4117.64"),
        Decimal("4118.08"),
        Decimal("4118.52"),
        Decimal("4118.95"),
        Decimal("4119.39"),
        Decimal("4119.83"),
    ]


def test_sell_entry_prices():
    prices = calculate_entry_prices("4124.20", "4126.39")

    assert prices == [
        Decimal("4124.20"),
        Decimal("4124.64"),
        Decimal("4125.08"),
        Decimal("4125.51"),
        Decimal("4125.95"),
        Decimal("4126.39"),
    ]


def test_custom_layer_count():
    prices = calculate_entry_prices("100.00", "101.00", layers=3)

    assert prices == [
        Decimal("100.00"),
        Decimal("100.50"),
        Decimal("101.00"),
    ]


def test_reject_zero_width_range():
    with pytest.raises(ValueError):
        calculate_entry_prices("100.00", "100.00")


def test_reject_too_narrow_range():
    with pytest.raises(ValueError):
        calculate_entry_prices("100.00", "100.01", layers=3)


def test_reject_invalid_layer_count():
    with pytest.raises(ValueError):
        calculate_entry_prices("100.00", "101.00", layers=1)
