from decimal import Decimal

import pytest

from actp.broker import OrderStatus
from actp.execution import ExecutionManager
from actp.fake_broker import FakeBroker
from actp.parser import parse_plan
from actp.plan_store import JsonPlanStore
from actp.test_execution import SAMPLE_PLAN, set_tick

D = Decimal
# BUY range 4119.83 .. 4117.64, layers every 0.438 (6 layers)


def make(store=None):
    broker = FakeBroker()
    manager = ExecutionManager(broker, store=store)
    set_tick(broker, "4121.00", "4121.20")
    manager.apply_plan("m-001", parse_plan(SAMPLE_PLAN))
    return manager, broker


def layers(broker, side="BUY"):
    return sorted((o for o in broker.list_orders() if o.side == side),
                  key=lambda o: o.entry_price, reverse=(side == "BUY"))


def alive(broker, side):
    """Orders still pending or open (the fake broker also lists closed ones)."""
    return [o for o in broker.list_orders() if o.side == side
            and o.status in (OrderStatus.PENDING, OrderStatus.FILLED)]


def dip(manager, broker, bid, ask):
    set_tick(broker, bid, ask)
    manager.track_price_depth()


def test_leftover_layers_go_when_the_price_went_deep_and_no_position_is_left():
    manager, broker = make()
    for order in layers(broker)[:5]:
        broker.fill(order.ticket)  # 5 of 6 layers took
    dip(manager, broker, "4117.90", "4118.07")  # 80% into the range
    dip(manager, broker, "4120.00", "4120.20")  # and back up
    assert manager.remove_spent_sides(80) == []  # positions are still open
    for order in layers(broker)[:5]:
        broker.close_position(order.ticket)  # TP hit / closed by hand
    events = manager.remove_spent_sides(80)
    assert len(events) == 1
    assert events[0]["side"] == "BUY" and events[0]["removed"] == 1
    assert alive(broker, "BUY") == []
    assert len(alive(broker, "SELL")) == 6  # the other side is untouched
    assert manager.remove_spent_sides(80) == []


def test_not_deep_enough_keeps_the_pending_orders():
    manager, broker = make()
    broker.fill(layers(broker)[0].ticket)
    dip(manager, broker, "4118.75", "4118.95")  # 40%
    broker.close_position(layers(broker)[0].ticket)
    assert manager.remove_spent_sides(80) == []
    assert len(alive(broker, "BUY")) == 5


def test_a_side_the_price_never_entered_is_not_removed():
    manager, broker = make()
    dip(manager, broker, "4121.00", "4121.20")
    assert manager.remove_spent_sides(1) == []
    assert len(alive(broker, "BUY")) == 6


def test_one_open_position_stops_the_removal():
    manager, broker = make()
    for order in layers(broker)[:5]:
        broker.fill(order.ticket)
    dip(manager, broker, "4117.90", "4118.07")
    for order in layers(broker)[:4]:
        broker.close_position(order.ticket)  # one position is still open
    assert manager.remove_spent_sides(80) == []


def test_the_sell_side_follows_the_bid():
    manager, broker = make()
    sells = layers(broker, "SELL")
    for order in sells[:5]:
        broker.fill(order.ticket)
    dip(manager, broker, "4126.10", "4126.30")  # 87% into 4124.20..4126.39
    for order in sells[:5]:
        broker.close_position(order.ticket)
    events = manager.remove_spent_sides(80)
    assert [e["side"] for e in events] == ["SELL"]


def test_the_dip_is_remembered_across_a_restart(tmp_path):
    store = JsonPlanStore(tmp_path / "state.json")
    manager, broker = make(store)
    broker.fill(layers(broker)[0].ticket)
    dip(manager, broker, "4117.90", "4118.07")  # 80%
    set_tick(broker, "4121.00", "4121.20")
    again = ExecutionManager(broker, store=JsonPlanStore(tmp_path / "state.json"))
    assert again._depth[("BUY", "m-001")] > 80  # not forgotten
    broker.close_position(layers(broker)[0].ticket)
    assert len(again.remove_spent_sides(80)) == 1


def test_a_bad_percent_is_refused():
    manager, _ = make()
    for bad in (0, 101, "abc", None):
        with pytest.raises(ValueError):
            manager.remove_spent_sides(bad)


def test_auto_tp_also_uses_the_saved_dip_after_a_restart(tmp_path):
    store = JsonPlanStore(tmp_path / "state.json")
    manager, broker = make(store)
    broker.fill(layers(broker)[0].ticket)
    dip(manager, broker, "4118.10", "4118.30")  # about 70%
    set_tick(broker, "4119.00", "4119.20")
    again = ExecutionManager(broker, store=JsonPlanStore(tmp_path / "state.json"))
    assert again.move_tp_when_full(60)["BUY"][0] == "MOVED"


def test_a_fast_spike_between_two_checks_still_counts_through_the_filled_layers():
    # The price reached the 5th layer (about 80%) and was back up before ACTP's next
    # one-second look: the layers filled, but the price was never sampled deep.
    manager, broker = make()
    for order in layers(broker)[:5]:
        broker.fill(order.ticket)
    dip(manager, broker, "4120.00", "4120.20")  # the next look: far from it
    for order in layers(broker)[:5]:
        broker.close_position(order.ticket)  # TP hit
    events = manager.remove_spent_sides(79)
    assert len(events) == 1 and events[0]["removed"] == 1
    assert len(alive(broker, "BUY")) == 0
