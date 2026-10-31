
from decimal import Decimal

import pytest

from actp.parser import parse_plan
from actp.validator import validate_plan


BUY_ONLY_PLAN = """PLAN XAUUSDr

BUY LIMIT (6 layer, 0.72 lot)
Area: 4119.83 - 4117.64
Cut Loss: 4116.98
SL: 4111.30 | TP: 4122.02
"""


SELL_ONLY_PLAN = """PLAN XAUUSDr

SELL LIMIT (6 layer, 0.72 lot)
Area: 4124.74 - 4127.19
Cut Loss: 4127.92
SL: 4134.29 | TP: 4122.30
"""


def test_validate_buy_only_plan():
    validate_plan(parse_plan(BUY_ONLY_PLAN))


def test_validate_sell_only_plan():
    validate_plan(parse_plan(SELL_ONLY_PLAN))


def test_reject_unauthorized_symbol():
    plan = parse_plan(BUY_ONLY_PLAN)
    plan["symbol"] = "EURUSD"

    with pytest.raises(ValueError, match="Unauthorized trading symbol"):
        validate_plan(plan)


def test_reject_excessive_layers():
    plan = parse_plan(BUY_ONLY_PLAN)
    plan["buy"]["layers"] = 21

    with pytest.raises(ValueError, match="layers must be between"):
        validate_plan(plan)


def test_reject_buy_cut_loss_inside_entry_range():
    plan = parse_plan(BUY_ONLY_PLAN)
    plan["buy"]["cut_loss"] = Decimal("4118.00")

    with pytest.raises(ValueError, match="BUY Cut Loss"):
        validate_plan(plan)


def test_reject_sell_take_profit_above_entry_range():
    plan = parse_plan(SELL_ONLY_PLAN)
    plan["sell"]["tp"] = Decimal("4128.00")

    with pytest.raises(ValueError, match="SELL Take Profit"):
        validate_plan(plan)


def test_reject_empty_plan():
    plan = parse_plan(BUY_ONLY_PLAN)
    plan["buy"] = None

    with pytest.raises(ValueError, match="must contain a BUY or SELL"):
        validate_plan(plan)


def test_reject_wrong_source_symbol():
    plan = parse_plan(BUY_ONLY_PLAN)
    plan["source_symbol"] = "EURUSD"

    with pytest.raises(ValueError, match="Unexpected source symbol"):
        validate_plan(plan)


def test_reject_price_with_more_than_two_decimal_places():
    plan = parse_plan(BUY_ONLY_PLAN)
    plan["buy"]["sl"] = Decimal("4111.301")

    with pytest.raises(ValueError, match="at most two decimal places"):
        validate_plan(plan)


def test_reject_invalid_price_type():
    plan = parse_plan(BUY_ONLY_PLAN)
    plan["buy"]["tp"] = "4122.02"

    with pytest.raises(ValueError, match="finite Decimal"):
        validate_plan(plan)


def test_reject_range_too_narrow_for_requested_layers():
    plan = parse_plan(BUY_ONLY_PLAN)
    plan["buy"]["area_start"] = Decimal("4119.83")
    plan["buy"]["area_end"] = Decimal("4119.84")
    plan["buy"]["layers"] = 6

    with pytest.raises(ValueError, match="Invalid BUY entry prices"):
        validate_plan(plan)
