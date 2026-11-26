from decimal import Decimal

import pytest

from actp import config
from actp.broker import OrderStatus
from actp.execution import ExecutionManager, plan_id
from actp.fake_broker import FakeBroker
from actp.parser import parse_plan
from actp.plan_store import JsonPlanStore
from actp.test_execution import SAMPLE_PLAN, set_tick

D = Decimal


def make(store=None, stale=False):
    broker = FakeBroker()
    manager = ExecutionManager(broker, store=store, check_stale=stale)
    set_tick(broker, "4121.00", "4121.20")
    manager.apply_plan("m-001", parse_plan(SAMPLE_PLAN))
    return manager, broker


def side(broker, name):
    return [o for o in broker.list_orders()
            if o.side == name and o.status in (OrderStatus.PENDING,
                                               OrderStatus.FILLED)]


def test_same_as_before_replaces_one_side_and_keeps_the_rest():
    manager, broker = make()
    for order in side(broker, "BUY")[:2]:
        broker.fill(order.ticket)
    sells_before = [o.ticket for o in side(broker, "SELL")]
    old_buys = {o.ticket for o in side(broker, "BUY")}
    s = manager.side_settings("m-001", "BUY")
    result = manager.reenter_side("m-001", "BUY", s["layers"], s["weights"],
                                  s["lot"])
    assert result == {"cancelled": 4, "closed": 2, "placed": 6, "market": 0}
    new_buys = side(broker, "BUY")
    assert len(new_buys) == 6
    assert not old_buys & {o.ticket for o in new_buys}
    assert all(o.status == OrderStatus.PENDING for o in new_buys)
    assert [o.ticket for o in side(broker, "SELL")] == sells_before
    ids = [o.client_id for o in side(broker, "BUY") + side(broker, "SELL")]
    assert len(set(ids)) == len(ids)  # no clash with the SELL layer numbers
    assert {plan_id(o) for o in side(broker, "BUY")} == {"m-001"}


def test_the_plan_keeps_its_row_and_name():
    manager, broker = make()
    name = manager.plan_name("m-001")
    s = manager.side_settings("m-001", "SELL")
    manager.reenter_side("m-001", "SELL", s["layers"], s["weights"], s["lot"])
    rows = manager.plans_overview()
    assert len(rows) == 1 and rows[0]["name"] == name
    assert rows[0]["BUY"]["pending"] == 6 and rows[0]["SELL"]["pending"] == 6


def test_levels_stay_as_before():
    manager, broker = make()
    before = {o.side: (o.sl, o.tp) for o in broker.list_orders()}
    cut = manager.plans_overview()[0]["BUY"]["cut_loss"]
    manager.reenter_side("m-001", "BUY", 6, None)
    after = {o.side: (o.sl, o.tp) for o in broker.list_orders()}
    assert after == before
    assert manager.plans_overview()[0]["BUY"]["cut_loss"] == cut


def test_current_layer_amount_and_lot():
    manager, broker = make()
    manager.reenter_side("m-001", "BUY", 4, ["1", "1", "2", "2"], D("0.05"))
    buys = side(broker, "BUY")
    assert len(buys) == 4
    assert sorted(o.volume for o in buys) == [D("0.05"), D("0.05"),
                                              D("0.10"), D("0.10")]
    s = manager.side_settings("m-001", "BUY")
    assert s["layers"] == 4 and s["weights"] == ["1", "1", "2", "2"]
    assert s["lot"] == D("0.05")
    assert len(side(broker, "SELL")) == 6


def test_same_as_before_uses_the_lot_of_the_first_send(monkeypatch):
    manager, broker = make()
    first = side(broker, "BUY")[0].volume
    monkeypatch.setattr(config, "LOT_PER_ORDER", D("0.50"))  # Lot box changed
    s = manager.side_settings("m-001", "BUY")
    assert s["lot"] == first
    manager.reenter_side("m-001", "BUY", s["layers"], s["weights"], s["lot"])
    assert all(o.volume == first for o in side(broker, "BUY"))


def test_a_refused_reentry_changes_nothing():
    manager, broker = make(stale=True)
    snapshot = [(o.ticket, o.status) for o in broker.list_orders()]
    set_tick(broker, "4116.00", "4116.20")  # through the BUY Cut Loss
    with pytest.raises(ValueError, match="refused"):
        manager.reenter_side("m-001", "BUY", 6, None)
    assert [(o.ticket, o.status) for o in broker.list_orders()] == snapshot
    assert not manager.halted


def test_hand_set_tp_keeps_being_watched():
    manager, broker = make()
    for order in side(broker, "BUY")[:1]:
        broker.fill(order.ticket)
    set_tick(broker, "4117.50", "4117.70")
    manager.set_plan_tp("m-001", "BUY", "4118.50")
    manager.reenter_side("m-001", "BUY", 6, None)
    assert manager.plans_overview()[0]["BUY"]["tp_target"] == D("4118.50")


def test_bad_side_or_plan():
    manager, broker = make()
    with pytest.raises(ValueError):
        manager.reenter_side("m-009", "BUY", 6, None)
    with pytest.raises(ValueError):
        manager.side_settings("m-009", "BUY")


def test_base_lot_survives_a_restart(tmp_path):
    store = JsonPlanStore(tmp_path / "state.json")
    manager, broker = make(store)
    lot = manager.side_settings("m-001", "BUY")["lot"]
    again = ExecutionManager(broker, store=JsonPlanStore(tmp_path / "state.json"))
    assert again.side_settings("m-001", "BUY")["lot"] == lot
    assert lot == config.LOT_PER_ORDER
