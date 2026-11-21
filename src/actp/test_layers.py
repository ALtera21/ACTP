from decimal import Decimal

import pytest

from actp.broker import OrderStatus
from actp.config import LOT_PER_ORDER
from actp.dispatcher import Dispatcher
from actp.execution import ExecutionHalted, ExecutionManager
from actp.fake_broker import FakeBroker
from actp.layers import apply_weights, lots_for_weights, parse_weights
from actp.parser import parse_plan
from actp.plan_builder import build_plan_preview
from actp.plan_store import JsonPlanStore
from actp.price_calculator import calculate_entry_prices
from actp.test_dispatcher import PLAN_TEXT, Clock
from actp.validator import validate_plan

D = Decimal
L = LOT_PER_ORDER
W6 = "1, 1, 1, 1, 2, 2"


def test_parse_weights():
    assert parse_weights("") is None and parse_weights("   ") is None
    assert parse_weights(W6) == [D(1)] * 4 + [D(2)] * 2
    assert parse_weights("1 1 2") == [D(1), D(1), D(2)]


@pytest.mark.parametrize("text", [
    "1", "1,0", "1,-1", "1,x", "1,11", "1,NaN", "1,Infinity",
    ",".join(["1"] * 201),
])
def test_bad_weights_are_rejected(text):
    with pytest.raises(ValueError):
        parse_weights(text)


def test_layer_one_is_first_price_reached_on_each_side():
    plan = apply_weights(parse_plan(PLAN_TEXT), parse_weights(W6))
    orders = build_plan_preview(plan)  # prices ascending within a side
    buys = [o for o in orders if o["side"] == "BUY"]
    sells = [o for o in orders if o["side"] == "SELL"]
    assert [o["volume"] for o in buys] == [2 * L, 2 * L, L, L, L, L]
    assert [o["volume"] for o in sells] == [L, L, L, L, 2 * L, 2 * L]


def test_list_sets_layer_count_and_spacing():
    plan = apply_weights(parse_plan(PLAN_TEXT), parse_weights("1,1,1,2"))
    orders = build_plan_preview(plan)
    buys = [o for o in orders if o["side"] == "BUY"]
    assert len(orders) == 8
    assert [o["entry_price"] for o in buys] == calculate_entry_prices(
        "4082.51", "4084.77", layers=4)


def test_blank_box_changes_nothing():
    plan = parse_plan(PLAN_TEXT)
    assert apply_weights(plan, None) is plan
    assert all(o["volume"] == L for o in build_plan_preview(plan))


def test_fractional_weight_must_land_on_the_lot_step():
    with pytest.raises(ValueError, match="multiple"):
        lots_for_weights([D(1), D("1.5")], D("0.01"))
    assert lots_for_weights([D(1), D("1.5")], D("0.02")) == [D("0.02"), D("0.03")]


def test_validator_rejects_mismatched_weights():
    plan = apply_weights(parse_plan(PLAN_TEXT), parse_weights(W6))
    plan["buy"]["weights"] = ["1", "2"]
    with pytest.raises(ValueError, match="weights"):
        validate_plan(plan)


def make():
    broker, clock = FakeBroker(), Clock()
    return Dispatcher(ExecutionManager(broker), clock), broker, clock


def test_dispatch_places_weighted_orders_and_preview_shows_lots():
    dispatcher, broker, _ = make()
    weights = parse_weights(W6)
    assert "lots (layer 1 first)" in dispatcher.preview(PLAN_TEXT, weights)
    dispatcher.dispatch(PLAN_TEXT, weights)
    assert sorted(o.volume for o in broker.list_orders()) == sorted(
        [L] * 8 + [2 * L] * 4)


def test_same_text_with_other_weights_is_not_a_duplicate():
    dispatcher, broker, clock = make()
    dispatcher.dispatch(PLAN_TEXT, parse_weights("1,1,1,1,1,1"))
    clock.now += 1
    dispatcher.dispatch(PLAN_TEXT, parse_weights(W6))  # must not raise
    active = [o for o in broker.list_orders() if o.status == OrderStatus.PENDING]
    assert len(active) == 24  # independent plans: both stay live
    assert sum(o.volume for o in active) == 28 * L


def test_resume_after_halt_keeps_the_weights():
    dispatcher, broker, clock = make()
    weights = parse_weights(W6)
    broker.inject_fault("place_pending", on_call=3)
    with pytest.raises(ExecutionHalted):
        dispatcher.dispatch(PLAN_TEXT, weights)
    dispatcher._manager.reconcile()
    clock.now += 5
    dispatcher.dispatch(PLAN_TEXT, weights)
    orders = broker.list_orders()
    assert len(orders) == 12
    assert sorted(o.volume for o in orders) == sorted([L] * 8 + [2 * L] * 4)


def test_weights_survive_the_plan_store(tmp_path):
    store = JsonPlanStore(tmp_path / "state.json")
    plan = apply_weights(parse_plan(PLAN_TEXT), parse_weights("1,2,3"))
    ExecutionManager(FakeBroker(), store).apply_plan("m1", plan)
    assert store.load().plans["m1"]["buy"]["weights"] == ["1", "2", "3"]

# ---- v2.0: no 12-layer cap, Layer amount box, saved MultiLayer ------------

from actp.layers import (  # noqa: E402
    parse_layer_amount, resolve_weights, summarize_lots,
)
from actp.settings import validate  # noqa: E402


def test_up_to_200_layers_are_accepted():
    assert len(parse_weights(",".join(["1"] * 200))) == 200


def test_parse_layer_amount():
    assert parse_layer_amount(" 6 ") == 6
    for bad in ("", "abc", "1", "201", "-3", "2.5"):
        with pytest.raises(ValueError):
            parse_layer_amount(bad)


def test_typed_multilayer_beats_layer_amount_and_presets():
    got = resolve_weights("1,1,1,2,2", "9", {"9": "1,1,1,1,1,1,1,1,3"})
    assert got == [D(1), D(1), D(1), D(2), D(2)]


def test_empty_multilayer_uses_the_saved_preset_for_that_amount():
    presets = {"4": "1, 1, 1, 2"}
    assert resolve_weights("", "4", presets) == [D(1), D(1), D(1), D(2)]
    assert resolve_weights("  ", 4, presets) == [D(1), D(1), D(1), D(2)]


def test_layer_amount_without_a_preset_is_one_x_each():
    assert resolve_weights("", "5", {"4": "1,1,1,2"}) == [D(1)] * 5
    assert resolve_weights("", "5", {"5": " "}) == [D(1)] * 5


def test_preset_with_wrong_count_is_refused():
    with pytest.raises(ValueError, match="saved MultiLayer"):
        resolve_weights("", "4", {"4": "1,2"})


def test_summarize_lots_compresses_long_lists():
    assert summarize_lots([D("0.01"), D("0.02")]) == "0.01, 0.02"
    assert summarize_lots([D("0.01")] * 100 + [D("0.02")] * 50) == (
        "100 x 0.01, 50 x 0.02")


BIG_PLAN = """PLAN XAUUSDr
BUY LIMIT (6 layer, 0.72 lot)
Area: 4300.00 - 4100.00
Cut Loss: 4090.00
SL: 4080.00 | TP: 4310.00
SELL LIMIT (6 layer, 0.72 lot)
Area: 4400.00 - 4600.00
Cut Loss: 4610.00
SL: 4620.00 | TP: 4390.00
"""


def test_plan_with_200_layers_per_side_is_placed():
    dispatcher, broker, _ = make()
    dispatcher.dispatch(BIG_PLAN, resolve_weights("", "200", {}))
    orders = broker.list_orders()
    assert len(orders) == 400
    assert len({o.client_id for o in orders}) == 400


def test_layer_amount_overrides_the_plans_own_layer_count():
    dispatcher, broker, _ = make()
    dispatcher.dispatch(PLAN_TEXT, resolve_weights("", "4", {"4": "1,1,1,2"}))
    assert len(broker.list_orders()) == 8
    assert sorted(o.volume for o in broker.list_orders()) == sorted(
        [L] * 6 + [2 * L] * 2)


def test_settings_keep_and_check_saved_multilayers():
    out = validate({"saved_multilayers": {"4": "1,1,1,2", "3": " ", "10": "1,1,1,1,1,1,1,1,1,2"},
                    "layer_amount": 10})
    assert out["saved_multilayers"] == {
        "4": "1, 1, 1, 2", "10": "1, 1, 1, 1, 1, 1, 1, 1, 1, 2"}
    assert out["layer_amount"] == 10
    for bad in ({"4": "1,2"}, {"x": "1,2"}, {"201": "1"}, {"4": 5}):
        with pytest.raises(ValueError):
            validate({"saved_multilayers": bad})
    with pytest.raises(ValueError):
        validate({"layer_amount": 1})
