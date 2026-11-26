from decimal import Decimal

import pytest

from actp.parser import parse_message, parse_plan


SAMPLE_PLAN = """📋 PLAN XAUUSDr

🟢 BUY LIMIT (6 layer, 0.72 lot)
Area: 4119.83 - 4117.64
Cut Loss: 4116.98 (close candle di bawah level)
SL: 4111.30 | TP: 4122.02

🔴 SELL LIMIT (6 layer, 0.72 lot)
Area: 4124.20 - 4126.39
Cut Loss: 4127.05 (close candle di atas level)
SL: 4132.73 | TP: 4122.02
"""


def test_parse_plan():
    plan = parse_plan(SAMPLE_PLAN)

    assert plan["symbol"] == "XAUUSDc"
    assert plan["source_symbol"] == "XAUUSDr"

    assert plan["buy"]["layers"] == 6
    assert plan["buy"]["area_start"] == Decimal("4119.83")
    assert plan["buy"]["area_end"] == Decimal("4117.64")
    assert plan["buy"]["cut_loss"] == Decimal("4116.98")
    assert plan["buy"]["sl"] == Decimal("4111.30")
    assert plan["buy"]["tp"] == Decimal("4122.02")

    assert plan["sell"]["layers"] == 6
    assert plan["sell"]["area_start"] == Decimal("4124.20")
    assert plan["sell"]["area_end"] == Decimal("4126.39")
    assert plan["sell"]["cut_loss"] == Decimal("4127.05")
    assert plan["sell"]["sl"] == Decimal("4132.73")
    assert plan["sell"]["tp"] == Decimal("4122.02")


def test_reject_wrong_symbol():
    try:
        parse_plan("📋 PLAN EURUSD")
    except ValueError:
        pass
    else:
        raise AssertionError("Expected invalid plan to be rejected")



def test_parse_sell_only_plan():
    message = """PLAN XAUUSDr

🔴 SELL LIMIT (6 layer, 0.72 lot)
Area: 4124.74 - 4127.19
Cut Loss: 4127.92 (close candle di atas level)
SL: 4134.29 | TP: 4122.30
"""

    plan = parse_plan(message)

    assert plan["symbol"] == "XAUUSDc"
    assert plan["buy"] is None

    assert plan["sell"]["layers"] == 6
    assert plan["sell"]["area_start"] == Decimal("4124.74")
    assert plan["sell"]["area_end"] == Decimal("4127.19")
    assert plan["sell"]["cut_loss"] == Decimal("4127.92")
    assert plan["sell"]["sl"] == Decimal("4134.29")
    assert plan["sell"]["tp"] == Decimal("4122.30")


def test_parse_update():
    message = """🔄 UPDATE XAUUSDr BUY

SL: 4111.30 ➜ 4111.30
TP: 4122.02 ➜ 4119.83
"""

    result = parse_message(message)

    assert result["type"] == "UPDATE"
    assert result["side"] == "BUY"
    assert result["changes"] == {
        "sl": Decimal("4111.30"),
        "tp": Decimal("4119.83"),
    }


def test_parse_close_sell():
    result = parse_message("CLOSE XAUUSDr SELL - PROFIT")

    assert result["type"] == "CLOSE"
    assert result["side"] == "SELL"


def test_parse_fill_confirmation():
    result = parse_message("XAUUSDr BUY - Entry ter-fill")

    assert result["type"] == "FILL_CONFIRMATION"
    assert result["side"] == "BUY"
    assert result["action"] == "RECONCILE_WITH_MT5"


def test_reject_unknown_message():
    with pytest.raises(ValueError):
        parse_message("Hello everyone")
