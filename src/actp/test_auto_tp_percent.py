from decimal import Decimal

import pytest

from actp.broker import OrderStatus
from actp.execution import ExecutionManager, auto_tp_trigger_price
from actp.fake_broker import FakeBroker
from actp.parser import parse_plan
from actp.test_execution import SAMPLE_PLAN, set_tick

D = Decimal
# BUY range 4119.83 (proximal) .. 4117.64 (distal): layers every 0.44
# SELL range 4124.20 (proximal) .. 4126.39 (distal)


def make():
    broker = FakeBroker()
    manager = ExecutionManager(broker)
    set_tick(broker, "4121.00", "4121.20")
    manager.apply_plan("m-001", parse_plan(SAMPLE_PLAN))
    return manager, broker


def side_orders(broker, side):
    """Layers of a side, the proximal (first) one first."""
    return sorted((o for o in broker.list_orders() if o.side == side),
                  key=lambda o: o.entry_price, reverse=(side == "BUY"))


def fill(broker, side, count):
    for order in side_orders(broker, side)[:count]:
        broker.fill(order.ticket)


def price(broker, bid, ask, manager=None):
    set_tick(broker, bid, ask)
    if manager is not None:
        manager.track_price_depth()


def test_a_dip_is_remembered_after_the_price_comes_back():
    manager, broker = make()
    fill(broker, "BUY", 1)  # only the top layer is open
    price(broker, "4118.10", "4118.30", manager)  # 70% into the range
    price(broker, "4119.00", "4119.20", manager)  # back up
    assert manager.move_tp_when_full(60) == {
        "BUY": ("MOVED", D("4119.83"), 6)}  # 1 open + 5 pending
    assert manager.move_tp_when_full(60) == {}  # once only


def test_not_deep_enough_waits():
    manager, broker = make()
    fill(broker, "BUY", 1)
    price(broker, "4118.75", "4118.95", manager)  # 40% in
    assert manager.move_tp_when_full(60) == {}
    price(broker, "4118.10", "4118.30", manager)  # 70% in
    assert manager.move_tp_when_full(60)["BUY"][0] == "MOVED"


def test_buy_follows_the_ask_and_sell_follows_the_bid():
    manager, broker = make()
    fill(broker, "BUY", 1)
    fill(broker, "SELL", 1)
    # bid far below, ask only 40% into the BUY range; bid 50% into SELL
    price(broker, "4117.00", "4118.95", manager)
    assert manager.move_tp_when_full(50) == {}  # BUY is 40%; SELL bid below
    price(broker, "4125.30", "4125.50", manager)  # SELL bid 50% in
    assert manager.move_tp_when_full(50) == {
        "SELL": ("MOVED", D("4124.20"), 6)}


def test_sell_side_depth():
    manager, broker = make()
    fill(broker, "SELL", 1)
    price(broker, "4125.30", "4125.50", manager)  # (4125.30-4124.20)/2.19
    assert manager.move_tp_when_full(50)["SELL"] == ("MOVED", D("4124.20"), 6)


def test_100_percent_means_the_price_reached_the_last_layer():
    manager, broker = make()
    fill(broker, "BUY", 1)
    price(broker, "4117.90", "4118.10", manager)
    assert manager.move_tp_when_full(100) == {}
    assert manager.move_tp_when_full() == {}  # 100 is the default
    price(broker, "4117.50", "4117.64", manager)  # ask at the last layer
    assert manager.move_tp_when_full(100)["BUY"][0] == "MOVED"


def test_it_needs_an_open_position():
    manager, broker = make()
    price(broker, "4117.50", "4117.64", manager)  # deep, but nothing filled
    assert manager.move_tp_when_full(50) == {}
    fill(broker, "BUY", 1)
    assert manager.move_tp_when_full(50)["BUY"][0] == "MOVED"


def test_after_a_restart_the_open_layers_still_count():
    manager, broker = make()
    fill(broker, "BUY", 4)  # deepest open layer is 4118.52, 60% in
    price(broker, "4119.30", "4119.50")  # nothing tracked, price back up
    manager._depth.clear()  # ACTP was restarted
    assert manager.move_tp_when_full(50)["BUY"][0] == "MOVED"


def test_a_new_depth_starts_after_a_re_entry():
    manager, broker = make()
    fill(broker, "BUY", 1)
    price(broker, "4117.50", "4117.64", manager)
    assert manager._depth[("BUY", "m-001")] == 100
    set_tick(broker, "4121.00", "4121.20")
    manager.reenter_side("m-001", "BUY", 6, None, None)
    assert ("BUY", "m-001") not in manager._depth


def test_trigger_price_for_the_re_entry_window():
    assert auto_tp_trigger_price("BUY", "4119.83", "4117.64", 50) == D("4118.74")
    assert auto_tp_trigger_price("BUY", "4117.64", "4119.83", 100) == D("4117.64")
    assert auto_tp_trigger_price("BUY", "4119.83", "4117.64", 0) == D("4119.83")
    assert auto_tp_trigger_price("SELL", "4124.20", "4126.39", 50) == D("4125.30")
    assert auto_tp_trigger_price("SELL", "4126.39", "4124.20", 100) == D("4126.39")


def test_a_bad_percent_is_refused():
    manager, _ = make()
    for bad in (0, 101, -5, "abc", "nan", None, ""):
        with pytest.raises(ValueError):
            manager.move_tp_when_full(bad)


def test_a_tp_dragged_in_mt5_is_left_alone_by_default():
    manager, broker = make()
    fill(broker, "BUY", 6)
    dragged = side_orders(broker, "BUY")[0]
    broker.modify_sltp(dragged.ticket, dragged.sl, D("4118.00"))  # in MT5
    assert manager.move_tp_when_full(100) == {}
    tps = {o.ticket: o.tp for o in side_orders(broker, "BUY")}
    assert tps[dragged.ticket] == D("4118.00")
    assert all(tp != D("4119.83") for tp in tps.values())


def test_priority_lets_auto_tp_replace_a_tp_dragged_in_mt5():
    manager, broker = make()
    fill(broker, "BUY", 6)
    dragged = side_orders(broker, "BUY")[0]
    broker.modify_sltp(dragged.ticket, dragged.sl, D("4118.00"))
    set_tick(broker, "4117.50", "4117.70")
    result = manager.move_tp_when_full(100, priority=True)
    assert result["BUY"][0] == "MOVED"
    assert all(o.tp == D("4119.83") for o in side_orders(broker, "BUY"))


def test_a_tp_set_with_the_button_blocks_auto_tp_unless_priority_is_on():
    manager, broker = make()
    fill(broker, "BUY", 6)
    set_tick(broker, "4117.50", "4117.70")
    manager.set_plan_tp("m-001", "BUY", "4118.00")
    assert manager.move_tp_when_full(100) == {}
    assert manager.move_tp_when_full(100, priority=True) == {}  # done flag


def test_priority_set_tp_does_not_block_auto_tp_and_drops_the_watch():
    manager, broker = make()
    fill(broker, "BUY", 6)
    set_tick(broker, "4117.50", "4117.70")
    manager.set_plan_tp("m-001", "BUY", "4118.00", auto_priority=True)
    assert manager.plans_overview()[0]["BUY"]["tp_target"] == D("4118.00")
    assert manager.move_tp_when_full(100, priority=True)["BUY"][0] == "MOVED"
    assert manager.plans_overview()[0]["BUY"]["tp_target"] is None
    assert all(o.tp == D("4119.83") for o in side_orders(broker, "BUY"))
