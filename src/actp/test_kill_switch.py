"""
Tests for ExecutionManager.close_all() and the plan-size cap.
Needs the two small patches described in the chat (close_all in
execution.py; MAX_PLAN_ORDERS, forget_pending and _check_size in
dispatcher.py).
"""

import pytest

from actp.broker import OrderStatus
from actp.dispatcher import MAX_PLAN_ORDERS, Dispatcher
from actp.execution import ExecutionHalted, ExecutionManager
from actp.fake_broker import FakeBroker
from actp.test_dispatcher import PLAN_TEXT, Clock
from decimal import Decimal

def make():
    broker = FakeBroker()
    clock = Clock()
    manager = ExecutionManager(broker)
    return Dispatcher(manager, clock), manager, broker, clock


def active(broker):
    return [
        o for o in broker.list_orders()
        if o.status in (OrderStatus.PENDING, OrderStatus.FILLED)
    ]


def test_close_all_cancels_pending_and_closes_filled():
    dispatcher, manager, broker, _ = make()
    dispatcher.dispatch(PLAN_TEXT)
    first_buy = next(o for o in broker.list_orders() if o.side == "BUY")
    broker.fill(first_buy.ticket)
    assert manager.close_all() == {"cancelled": 11, "closed": 1}
    assert active(broker) == []


def test_close_all_works_while_halted_and_keeps_the_halt():
    dispatcher, manager, broker, _ = make()
    broker.inject_fault("place_pending", on_call=3)
    with pytest.raises(ExecutionHalted):
        dispatcher.dispatch(PLAN_TEXT)
    assert manager.halted
    assert manager.close_all() == {"cancelled": 2, "closed": 0}
    assert active(broker) == []
    assert manager.halted  # only reconcile() clears a halt


def test_resending_after_kill_starts_a_fresh_plan():
    dispatcher, manager, broker, clock = make()
    broker.inject_fault("place_pending", on_call=3)
    with pytest.raises(ExecutionHalted):
        dispatcher.dispatch(PLAN_TEXT)
    manager.close_all()
    dispatcher.forget_pending()
    manager.reconcile()
    clock.now += 60
    result = dispatcher.dispatch(PLAN_TEXT)
    orders = active(broker)
    assert len(orders) == 12
    assert {o.client_id.rpartition(":")[0] for o in orders} == {result.message_id}


def test_oversized_plan_is_refused_in_preview_and_dispatch():
    dispatcher, _, broker, _ = make()
    big = PLAN_TEXT.replace("6 layer", "11 layer")  # 22 orders
    with pytest.raises(ValueError, match=str(MAX_PLAN_ORDERS)):
        dispatcher.preview(big)
    with pytest.raises(ValueError, match=str(MAX_PLAN_ORDERS)):
        dispatcher.dispatch(big)
    assert broker.list_orders() == []

def test_close_positions_leaves_pending_and_can_keep_losers():
    dispatcher, manager, broker, _ = make()
    dispatcher.dispatch(PLAN_TEXT)
    buys = [o for o in broker.list_orders() if o.side == "BUY"]
    broker.fill(buys[0].ticket)  # entry 4082.51
    broker.fill(buys[5].ticket)  # entry 4084.77
    broker.set_tick("XAUUSDc", Decimal("4083.50"), Decimal("4083.70"))
    # bid 4083.50: the first is in profit, the second is losing.
    assert manager.close_positions(only_profitable=True) == {
        "closed": 1, "left_open": 1, "left_pending": 10}
    assert manager.close_positions() == {
        "closed": 1, "left_open": 0, "left_pending": 10}