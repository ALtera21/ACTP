
from decimal import Decimal

import pytest

from actp.parser import parse_plan
from actp.plan_builder import build_plan_preview

from actp.config import LOT_PER_ORDER

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


SELL_ONLY_PLAN = """PLAN XAUUSDr

🔴 SELL LIMIT (6 layer, 0.72 lot)
Area: 4124.74 - 4127.19
Cut Loss: 4127.92 (close candle di atas level)
SL: 4134.29 | TP: 4122.30
"""

BUY_ONLY_PLAN = """PLAN XAUUSDr

🟢 BUY LIMIT (6 layer, 0.72 lot)
Area: 4119.83 - 4117.64
Cut Loss: 4116.98 (close candle di bawah level)
SL: 4111.30 | TP: 4122.02
"""


def test_build_full_plan_preview():
    plan = parse_plan(SAMPLE_PLAN)
    orders = build_plan_preview(plan)

    assert len(orders) == 12
    assert sum(o["side"] == "BUY" for o in orders) == 6
    assert sum(o["side"] == "SELL" for o in orders) == 6

    assert [o["entry_price"] for o in orders[:6]] == [
        Decimal("4117.64"),
        Decimal("4118.08"),
        Decimal("4118.52"),
        Decimal("4118.95"),
        Decimal("4119.39"),
        Decimal("4119.83"),
    ]

    assert all(o["symbol"] == "XAUUSDc" for o in orders)
    assert all(o["volume"] == LOT_PER_ORDER for o in orders)


def test_build_sell_only_preview():
    plan = parse_plan(SELL_ONLY_PLAN)
    orders = build_plan_preview(plan)

    assert len(orders) == 6
    assert all(o["side"] == "SELL" for o in orders)
    assert all(o["order_type"] == "SELL_LIMIT" for o in orders)

    assert [o["entry_price"] for o in orders] == [
        Decimal("4124.74"),
        Decimal("4125.23"),
        Decimal("4125.72"),
        Decimal("4126.21"),
        Decimal("4126.70"),
        Decimal("4127.19"),
    ]


def test_reject_non_plan_message():

    with pytest.raises(ValueError):
        build_plan_preview({"type": "CLOSE", "symbol": "XAUUSDc"})


def test_build_buy_only_preview():
    plan = parse_plan(BUY_ONLY_PLAN)
    orders = build_plan_preview(plan)

    assert len(orders) == 6
    assert all(o["side"] == "BUY" for o in orders)
    assert all(o["order_type"] == "BUY_LIMIT" for o in orders)
    assert all(o["symbol"] == "XAUUSDc" for o in orders)
    assert all(o["volume"] == LOT_PER_ORDER for o in orders)

    assert [o["entry_price"] for o in orders] == [
        Decimal("4117.64"),
        Decimal("4118.08"),
        Decimal("4118.52"),
        Decimal("4118.95"),
        Decimal("4119.39"),
        Decimal("4119.83"),
    ]