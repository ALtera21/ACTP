from decimal import Decimal

import pytest

from actp.dispatcher import Dispatcher
from actp.execution import ExecutionManager
from actp.fake_broker import FakeBroker
from actp.layers import resolve_weights
from actp.manual import (
    build_plan_text, collect, default_cut_loss, default_notes,
    default_stop_loss, default_take_profit, parse_price, parse_range,
)
from actp.parser import parse_message
from actp.test_dispatcher import Clock
from actp.validator import validate_plan

D = Decimal


def fields(**given):
    base = {f"{s}_{k}": "" for s in ("buy", "sell")
            for k in ("a", "b", "cl", "sl", "tp")}
    base.update(given)
    return base


def test_defaults_for_a_buy_range_4000_to_4001():
    assert default_cut_loss("buy", D(4000), D(4001)) == D("3999.70")
    assert default_stop_loss("buy", D(4000), D(4001), D(2)) == D("3998.00")
    assert default_stop_loss("buy", D(4000), D(4001), D(3)) == D("3997.00")
    assert default_take_profit("buy", D(4000), D(4001)) == D("4002.00")


def test_defaults_for_a_sell_range_are_mirrored():
    assert default_cut_loss("sell", D(4010), D(4012)) == D("4012.60")
    assert default_stop_loss("sell", D(4010), D(4012), D(3)) == D("4018.00")
    assert default_take_profit("sell", D(4010), D(4012)) == D("4008.00")


def test_defaults_match_the_providers_own_take_profit():
    # Provider plan: BUY area 4119.83 - 4117.64, TP 4122.02 (= high + width).
    assert default_take_profit("buy", D("4117.64"), D("4119.83")) == D("4122.02")


def test_typed_values_beat_the_defaults_and_range_order_does_not_matter():
    got = collect("buy", fields(buy_a="4001", buy_b="4000", buy_cl="3999",
                                buy_sl="3990", buy_tp="4005"), D(3))
    (side,) = got
    assert (side.low, side.high) == (D(4000), D(4001))
    assert (side.cut_loss, side.sl, side.tp) == (D(3999), D(3990), D(4005))


def test_both_leaves_out_a_side_with_no_range():
    got = collect("both", fields(buy_a="4000", buy_b="4001"), D(3))
    assert [s.side for s in got] == ["buy"]
    both = collect("both", fields(buy_a="4000", buy_b="4001",
                                  sell_a="4010", sell_b="4011"), D(3))
    assert [s.side for s in both] == ["buy", "sell"]


def test_one_side_button_needs_that_sides_range():
    with pytest.raises(ValueError, match="BUY range"):
        collect("buy", fields(sell_a="4010", sell_b="4011"), D(3))
    with pytest.raises(ValueError, match="at least one"):
        collect("both", fields(), D(3))


def test_the_single_button_ignores_the_other_side():
    got = collect("sell", fields(buy_a="4000", buy_b="4001",
                                 sell_a="4010", sell_b="4011"), D(3))
    assert [s.side for s in got] == ["sell"]


@pytest.mark.parametrize("a,b", [("4000", ""), ("", "4001"), ("4000", "4000"),
                                 ("abc", "4001"), ("4,000", "4001"),
                                 ("4000.123", "4001"), ("-5", "4001"),
                                 ("0", "4001"), ("NaN", "4001")])
def test_bad_ranges_are_refused(a, b):
    with pytest.raises(ValueError):
        parse_range(a, b, "buy")


def test_blank_and_bad_prices():
    assert parse_price("  ", "x") is None
    assert parse_price(" 4000.5 ", "x") == D("4000.5")
    with pytest.raises(ValueError, match="dot"):
        parse_price("4000,5", "x")


def test_built_text_is_read_by_the_normal_parser_and_validator():
    sides = collect("both", fields(buy_a="4000", buy_b="4001",
                                   sell_a="4010", sell_b="4012"), D(3))
    plan = parse_message(build_plan_text(sides, 6))
    validate_plan(plan)
    assert plan["buy"]["layers"] == 6
    assert (plan["buy"]["area_start"], plan["buy"]["area_end"]) == (
        D("4001.00"), D("4000.00"))
    assert plan["buy"]["cut_loss"] == D("3999.70")
    assert plan["buy"]["sl"] == D("3997.00") and plan["buy"]["tp"] == D("4002.00")
    assert plan["sell"]["cut_loss"] == D("4012.60")
    assert plan["sell"]["sl"] == D("4018.00")  # 4012 + 3 x width 2
    assert plan["sell"]["tp"] == D("4008.00")


def test_a_manual_plan_places_orders_with_the_multilayer_lots():
    broker = FakeBroker()
    dispatcher = Dispatcher(ExecutionManager(broker), Clock())
    sides = collect("buy", fields(buy_a="4000", buy_b="4010"), D(3))
    weights = resolve_weights("1,1,2", "6", {})
    dispatcher.dispatch(build_plan_text(sides, len(weights)), weights)
    orders = broker.list_orders()
    assert len(orders) == 3 and all(o.side == "BUY" for o in orders)
    assert sorted(o.volume for o in orders) == [D("0.01"), D("0.01"), D("0.02")]
    assert orders[0].sl == D("3970.00") and orders[0].tp == D("4020.00")


def test_a_cut_loss_too_close_to_the_range_is_caught_by_the_validator():
    sides = collect("buy", fields(buy_a="4000", buy_b="4000.01"), D(3))
    with pytest.raises(ValueError, match="Cut Loss"):
        validate_plan(parse_message(build_plan_text(sides, 2)))


def test_notes_show_what_empty_boxes_will_use():
    notes = default_notes("buy", fields(buy_a="4000", buy_b="4001"), D(2))
    assert notes["cl"].endswith("3999.70")
    assert notes["sl"].endswith("3998.00")
    assert notes["tp"].endswith("4002.00")
    typed = default_notes("buy", fields(buy_a="4000", buy_b="4001",
                                        buy_cl="3999"), D(2))
    assert typed["cl"] == ""
    bare = default_notes("sell", fields(), D(3))
    assert "above" in bare["cl"] and ":" not in bare["cl"]
