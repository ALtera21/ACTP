from decimal import Decimal

import pytest

from actp.broker import OrderStatus
from actp.execution import ExecutionHalted, ExecutionManager
from actp.fake_broker import FakeBroker
from actp.parser import parse_plan
from actp.test_execution import SAMPLE_PLAN, orders_of, set_tick


def make():
    broker = FakeBroker()
    return ExecutionManager(broker, check_stale=True), broker


def test_fresh_plan_is_all_pending():
    manager, broker = make()
    set_tick(broker, "4121.00", "4121.20")
    orders = manager.apply_plan("message-001", parse_plan(SAMPLE_PLAN))
    assert all(o.status == OrderStatus.PENDING for o in orders)


def test_late_plan_buys_marketable_layers_and_keeps_the_rest_pending():
    manager, broker = make()
    set_tick(broker, "4118.80", "4119.00")  # BUY 4119.39 and 4119.83 >= ask
    orders = manager.apply_plan("message-001", parse_plan(SAMPLE_PLAN))
    buys = [o for o in orders if o.side == "BUY"]
    assert sum(o.status == OrderStatus.FILLED for o in buys) == 2
    assert sum(o.status == OrderStatus.PENDING for o in buys) == 4
    assert all(o.status == OrderStatus.PENDING for o in orders if o.side == "SELL")


def test_too_many_market_layers_refuses_and_keeps_the_current_plan():
    manager, broker = make()
    set_tick(broker, "4121.00", "4121.20")
    manager.apply_plan("message-001", parse_plan(SAMPLE_PLAN))
    set_tick(broker, "4118.30", "4118.50")  # 4 BUY layers at market > cap 3
    with pytest.raises(ValueError, match="limit 3"):
        manager.apply_plan("message-002", parse_plan(SAMPLE_PLAN))
    assert not manager.halted
    assert orders_of(broker, "message-002") == []
    assert all(o.status == OrderStatus.PENDING
               for o in orders_of(broker, "message-001"))


def test_price_through_cut_loss_refuses_the_plan():
    manager, broker = make()
    set_tick(broker, "4116.90", "4117.10")
    with pytest.raises(ValueError, match="Cut Loss"):
        manager.apply_plan("message-001", parse_plan(SAMPLE_PLAN))
    assert broker.list_orders() == []
    assert not manager.halted


def test_lost_reply_on_market_layer_does_not_double_buy_on_resume():
    manager, broker = make()
    set_tick(broker, "4118.80", "4119.00")
    plan = parse_plan(SAMPLE_PLAN)
    # The first market layer is bought but the reply is lost.
    broker.inject_fault("place_market", on_call=1, applied=True)
    with pytest.raises(ExecutionHalted):
        manager.apply_plan("message-001", plan)
    manager.reconcile()
    orders = manager.apply_plan("message-001", plan)
    assert len(orders) == 12
    assert len({o.client_id for o in broker.list_orders()}) == 12
    
def test_auto_tp_moves_to_first_entry_when_all_layers_fill():
    manager, broker = make()
    set_tick(broker, "4121.00", "4121.20")
    orders = manager.apply_plan("message-001", parse_plan(SAMPLE_PLAN))
    buys = [o for o in orders if o.side == "BUY"]
    for o in buys[:5]:
        broker.fill(o.ticket)
    assert manager.move_tp_when_full() == {}  # only 5 of 6
    broker.fill(buys[5].ticket)
    set_tick(broker, "4117.50", "4117.70")
    assert manager.move_tp_when_full() == {
        "BUY": ("MOVED", Decimal("4119.83"), 6)}
    now = broker.list_orders()
    assert all(o.tp == Decimal("4119.83") for o in now if o.side == "BUY")
    assert all(o.tp == Decimal("4122.02") for o in now if o.side == "SELL")
    assert manager.move_tp_when_full() == {}  # once only


def test_auto_tp_waits_while_price_is_past_the_new_tp():
    manager, broker = make()
    set_tick(broker, "4121.00", "4121.20")
    orders = manager.apply_plan("message-001", parse_plan(SAMPLE_PLAN))
    for o in orders:
        if o.side == "BUY":
            broker.fill(o.ticket)
    set_tick(broker, "4120.50", "4120.70")  # bid above 4119.83
    assert manager.move_tp_when_full() == {
        "BUY": ("PRICE_PAST", Decimal("4119.83"), 0)}
    set_tick(broker, "4118.00", "4118.20")
    assert manager.move_tp_when_full()["BUY"][0] == "MOVED"