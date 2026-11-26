from decimal import Decimal

import pytest

from actp.broker import BrokerRejected, OrderStatus
from actp.execution import ExecutionHalted, ExecutionManager
from actp.fake_broker import FakeBroker
from actp.parser import parse_plan
from actp.plan_store import JsonPlanStore
from actp.test_execution import SAMPLE_PLAN, set_tick

D = Decimal


def make(store=None):
    broker = FakeBroker()
    manager = ExecutionManager(broker, store=store)
    set_tick(broker, "4121.00", "4121.20")
    manager.apply_plan("m-001", parse_plan(SAMPLE_PLAN))
    return manager, broker


def buys(broker):
    return [o for o in broker.list_orders() if o.side == "BUY"]


def fill_buys(broker, count):
    for order in buys(broker)[:count]:
        broker.fill(order.ticket)


def test_tp_is_set_on_open_positions_only_by_default():
    manager, broker = make()
    fill_buys(broker, 2)
    set_tick(broker, "4117.50", "4117.70")
    result = manager.set_plan_tp("m-001", "BUY", "4119.00")
    assert result["state"] == "ARMED"
    assert result["hard_positions"] == 2 and result["hard_pending"] == 0
    for order in buys(broker):
        expected = D("4119.00") if order.status == OrderStatus.FILLED \
            else D("4122.02")
        assert order.tp == expected


def test_tp_also_goes_on_pending_orders_when_asked():
    manager, broker = make()
    fill_buys(broker, 2)
    set_tick(broker, "4117.50", "4117.70")
    result = manager.set_plan_tp("m-001", "BUY", "4119.00", include_pending=True)
    assert result["hard_positions"] == 2 and result["hard_pending"] == 4
    assert all(o.tp == D("4119.00") for o in buys(broker))


def test_other_side_and_other_plans_are_not_touched():
    manager, broker = make()
    fill_buys(broker, 1)
    set_tick(broker, "4117.50", "4117.70")
    sells_before = [o.tp for o in broker.list_orders() if o.side == "SELL"]
    manager.set_plan_tp("m-001", "BUY", "4119.00", include_pending=True)
    assert [o.tp for o in broker.list_orders() if o.side == "SELL"] == sells_before


def test_price_reaching_the_tp_closes_the_positions_and_keeps_pending():
    manager, broker = make()
    fill_buys(broker, 2)
    set_tick(broker, "4117.50", "4117.70")
    manager.set_plan_tp("m-001", "BUY", "4119.00")
    assert manager.check_tp_targets() == []  # bid 4117.50 < 4119
    set_tick(broker, "4118.90", "4119.10")
    assert manager.check_tp_targets() == []
    set_tick(broker, "4119.00", "4119.20")  # bid reaches the TP
    events = manager.check_tp_targets()
    assert len(events) == 1
    assert events[0]["side"] == "BUY" and events[0]["closed"] == 2
    assert events[0]["tp"] == D("4119.00")
    now = buys(broker)
    assert sum(o.status == OrderStatus.FILLED for o in now) == 0
    assert sum(o.status == OrderStatus.PENDING for o in now) == 4
    assert manager.check_tp_targets() == []  # fires once


def test_sell_tp_fires_on_the_ask():
    manager, broker = make()
    sells = [o for o in broker.list_orders() if o.side == "SELL"]
    broker.fill(sells[0].ticket)
    set_tick(broker, "4121.00", "4121.20")
    manager.set_plan_tp("m-001", "SELL", "4120.00")
    set_tick(broker, "4119.70", "4120.10")  # ask above the TP: wait
    assert manager.check_tp_targets() == []
    set_tick(broker, "4119.60", "4120.00")  # ask reaches the TP
    assert manager.check_tp_targets()[0]["closed"] == 1


def test_a_tp_inside_the_range_still_works_when_mt5_refuses_it():
    manager, broker = make()
    fill_buys(broker, 2)
    set_tick(broker, "4117.50", "4117.70")

    def refuse(ticket, sl, tp):
        raise BrokerRejected("Invalid stops")

    broker.modify_sltp = refuse
    result = manager.set_plan_tp("m-001", "BUY", "4118.00")
    assert result["state"] == "ARMED"
    assert result["refused_positions"] == 2 and result["hard_positions"] == 0
    set_tick(broker, "4118.10", "4118.30")
    assert manager.check_tp_targets()[0]["closed"] == 2


def test_price_already_past_closes_now_and_arms_nothing():
    manager, broker = make()
    fill_buys(broker, 2)
    set_tick(broker, "4119.50", "4119.70")
    assert manager.tp_would_close_now("m-001", "BUY", "4119.00") == 2
    assert manager.tp_would_close_now("m-001", "BUY", "4120.00") == 0
    result = manager.set_plan_tp("m-001", "BUY", "4119.00")
    assert result["state"] == "CLOSED" and result["closed"] == 2
    assert manager.check_tp_targets() == []


def test_price_past_with_no_open_position_is_kept_and_watched():
    manager, broker = make()
    set_tick(broker, "4119.50", "4119.70")
    assert manager.tp_would_close_now("m-001", "BUY", "4119.00") == 0
    result = manager.set_plan_tp("m-001", "BUY", "4119.00")
    assert result["state"] == "ARMED" and result["open"] == 0
    assert result["hard_positions"] == 0
    assert all(o.tp == D("4122.02") for o in buys(broker))  # MT5 untouched
    assert manager.check_tp_targets() == []  # nothing to close yet
    fill_buys(broker, 1)  # a layer opens while the price is at the TP
    assert manager.check_tp_targets()[0]["closed"] == 1


def test_sell_layer_above_the_tp_waits_for_the_price_to_come_back():
    manager, broker = make()
    set_tick(broker, "4117.00", "4117.20")  # far below the SELL layers
    result = manager.set_plan_tp("m-001", "SELL", "4120.00")
    assert result["state"] == "ARMED" and result["open"] == 0
    sells = [o for o in broker.list_orders() if o.side == "SELL"]
    broker.fill(sells[-1].ticket)
    set_tick(broker, "4121.50", "4121.70")  # price rose, layer open above TP
    assert manager.check_tp_targets() == []
    set_tick(broker, "4119.70", "4119.90")  # back down to the TP
    assert manager.check_tp_targets()[0]["closed"] == 1


def test_watching_waits_for_a_position_to_open():
    manager, broker = make()
    set_tick(broker, "4117.50", "4117.70")
    manager.set_plan_tp("m-001", "BUY", "4118.00")
    set_tick(broker, "4118.10", "4118.30")
    assert manager.check_tp_targets() == []  # nothing open: keep watching
    fill_buys(broker, 1)
    assert manager.check_tp_targets()[0]["closed"] == 1


def test_bad_input_is_refused():
    manager, broker = make()
    fill_buys(broker, 1)
    for bad in ("", "abc", "0", "-5", "nan", "inf"):
        with pytest.raises(ValueError):
            manager.set_plan_tp("m-001", "BUY", bad)
    with pytest.raises(ValueError, match="no BUY side"):
        manager.set_plan_tp("m-009", "BUY", "4119")
    with pytest.raises(ValueError, match="TP must be"):
        manager.set_plan_tp("m-001", "SELL", None)


def test_manual_tp_replaces_auto_tp_on_that_side():
    manager, broker = make()
    fill_buys(broker, 6)
    set_tick(broker, "4117.50", "4117.70")
    manager.set_plan_tp("m-001", "BUY", "4118.00")
    assert manager.move_tp_when_full() == {}  # auto TP stays out of it
    assert all(o.tp == D("4118.00") for o in buys(broker))


def test_target_survives_a_restart(tmp_path):
    store = JsonPlanStore(tmp_path / "state.json")
    manager, broker = make(store)
    fill_buys(broker, 2)
    set_tick(broker, "4117.50", "4117.70")
    manager.set_plan_tp("m-001", "BUY", "4118.00")
    again = ExecutionManager(broker, store=JsonPlanStore(tmp_path / "state.json"))
    rows = again.plans_overview()
    assert rows[0]["BUY"]["tp_target"] == D("4118.00")
    set_tick(broker, "4118.10", "4118.30")
    assert again.check_tp_targets()[0]["closed"] == 2


def test_closing_the_side_or_the_plan_forgets_the_target():
    manager, broker = make()
    fill_buys(broker, 1)
    set_tick(broker, "4117.50", "4117.70")
    manager.set_plan_tp("m-001", "BUY", "4118.00")
    manager.close_plan("m-001", "BUY")
    set_tick(broker, "4118.10", "4118.30")
    assert manager.check_tp_targets() == []
    assert not manager._tp_targets


def test_a_halted_manager_does_not_set_or_fire():
    manager, broker = make()
    fill_buys(broker, 1)
    set_tick(broker, "4117.50", "4117.70")
    manager.set_plan_tp("m-001", "BUY", "4118.00")
    broker.inject_fault("get_tick", on_call=broker._call_counts["get_tick"] + 1)
    with pytest.raises(ExecutionHalted):
        manager.check_tp_targets()
    assert manager.check_tp_targets() == []
    with pytest.raises(ExecutionHalted):
        manager.set_plan_tp("m-001", "BUY", "4118.50")
