from decimal import Decimal

import pytest

from actp.broker import Tick
from actp.stale_plan import check_plan, is_marketable, stop_breach

D = Decimal


def section(side):
    if side == "buy":
        return dict(layers=6, area_start=D("4119.83"), area_end=D("4117.64"),
                    cut_loss=D("4116.98"), sl=D("4111.30"), tp=D("4122.02"))
    return dict(layers=6, area_start=D("4124.20"), area_end=D("4126.39"),
                cut_loss=D("4127.05"), sl=D("4132.73"), tp=D("4122.02"))


BUY_PRICES = ["4117.64", "4118.08", "4118.52", "4118.95", "4119.39", "4119.83"]
SELL_PRICES = ["4124.20", "4124.64", "4125.08", "4125.51", "4125.95", "4126.39"]


def make_plan():
    plan = {"type": "PLAN", "buy": section("buy"), "sell": section("sell")}
    proposed = [{"side": "BUY", "entry_price": D(p)} for p in BUY_PRICES]
    proposed += [{"side": "SELL", "entry_price": D(p)} for p in SELL_PRICES]
    return plan, proposed


def tick(bid, ask):
    return Tick(bid=D(bid), ask=D(ask))


def test_marketable_boundaries():
    assert is_marketable("BUY", D("4119.00"), tick("4118.80", "4119.00"))
    assert not is_marketable("BUY", D("4118.99"), tick("4118.80", "4119.00"))
    assert is_marketable("SELL", D("4125.00"), tick("4125.00", "4125.20"))
    assert not is_marketable("SELL", D("4125.01"), tick("4125.00", "4125.20"))


def test_fresh_plan_is_all_pending():
    plan, proposed = make_plan()
    result = check_plan(plan, proposed, tick("4121.00", "4121.20"))
    assert [(s.side, s.market, s.pending) for s in result.sides] == [
        ("BUY", 0, 6), ("SELL", 0, 6)]


def test_late_plan_splits_buy_layers():
    plan, proposed = make_plan()
    # ask 4119.00: layers 4119.39 and 4119.83 are at/above the ask.
    result = check_plan(plan, proposed, tick("4118.80", "4119.00"))
    buy = result.sides[0]
    assert (buy.market, buy.pending) == (2, 4)
    assert buy.price == D("4119.00")
    assert buy.cut_loss_distance == D("1.82")
    assert "2 at market now" in result.describe()


def test_cap_is_enforced_per_side():
    plan, proposed = make_plan()
    check_plan(plan, proposed, tick("4118.30", "4118.50"), cap=4)  # 4 market
    with pytest.raises(ValueError, match="limit 3"):
        check_plan(plan, proposed, tick("4118.30", "4118.50"))     # 4 > 3


def test_price_through_cut_loss_refuses_whole_plan():
    plan, proposed = make_plan()
    with pytest.raises(ValueError, match="Cut Loss"):
        check_plan(plan, proposed, tick("4116.98", "4117.20"))
    with pytest.raises(ValueError, match="Cut Loss"):
        check_plan(plan, proposed, tick("4126.90", "4127.05"))


def test_sl_above_cut_loss_is_the_binding_level():
    section_ = section("buy")
    section_["sl"] = D("4117.50")  # unusual, but must not slip through
    assert stop_breach("BUY", section_, tick("4117.50", "4117.70"))


def test_existing_layers_are_not_counted_again():
    plan, proposed = make_plan()
    result = check_plan(plan, proposed, tick("4118.30", "4118.50"),
                        skip={0, 1, 2}, cap=3)
    buy = result.sides[0]
    assert (buy.market, buy.pending, buy.already_placed) == (3, 0, 3)


def test_sell_only_plan():
    plan, proposed = make_plan()
    plan["buy"] = None
    proposed = [p for p in proposed if p["side"] == "SELL"]
    # bid 4125.10: layers 4124.20, 4124.64 and 4125.08 are at or below it.
    result = check_plan(plan, proposed, tick("4125.10", "4125.30"))
    assert len(result.sides) == 1 and result.sides[0].market == 3
