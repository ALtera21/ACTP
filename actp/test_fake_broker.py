from decimal import Decimal

import pytest

from actp.broker import (
    BrokerRejected,
    BrokerUnavailable,
    OrderRequest,
    OrderStatus,
)
from actp.fake_broker import FakeBroker


def make_request(client_id: str = "m1:0", side: str = "BUY") -> OrderRequest:
    return OrderRequest(
        client_id=client_id,
        symbol="XAUUSDc",
        side=side,
        entry_price=Decimal("4117.64"),
        volume=Decimal("0.01"),
        sl=Decimal("4111.30"),
        tp=Decimal("4122.02"),
    )


def test_place_pending_creates_pending_orders_with_unique_tickets():
    broker = FakeBroker()

    first = broker.place_pending(make_request("m1:0"))
    second = broker.place_pending(make_request("m1:1"))

    assert first.status == OrderStatus.PENDING
    assert second.status == OrderStatus.PENDING
    assert first.ticket != second.ticket
    assert len(broker.list_orders()) == 2


def test_place_pending_with_same_client_id_is_idempotent():
    broker = FakeBroker()

    first = broker.place_pending(make_request("m1:0"))
    second = broker.place_pending(make_request("m1:0"))

    assert first.ticket == second.ticket
    assert len(broker.list_orders()) == 1


def test_cancel_pending_then_cancel_again_is_rejected():
    broker = FakeBroker()
    order = broker.place_pending(make_request())

    broker.cancel_pending(order.ticket)

    assert broker.list_orders()[0].status == OrderStatus.CANCELLED
    with pytest.raises(BrokerRejected):
        broker.cancel_pending(order.ticket)


def test_close_position_requires_a_filled_order():
    broker = FakeBroker()
    order = broker.place_pending(make_request())

    with pytest.raises(BrokerRejected):
        broker.close_position(order.ticket)

    broker.fill(order.ticket)
    broker.close_position(order.ticket)

    assert broker.list_orders()[0].status == OrderStatus.CLOSED


def test_modify_sltp_changes_active_orders_only():
    broker = FakeBroker()
    order = broker.place_pending(make_request())

    broker.modify_sltp(order.ticket, Decimal("4112.00"), Decimal("4123.00"))

    updated = broker.list_orders()[0]
    assert updated.sl == Decimal("4112.00")
    assert updated.tp == Decimal("4123.00")

    broker.cancel_pending(order.ticket)
    with pytest.raises(BrokerRejected):
        broker.modify_sltp(order.ticket, Decimal("4112.00"), Decimal("4123.00"))


def test_unknown_ticket_is_rejected():
    broker = FakeBroker()

    with pytest.raises(BrokerRejected, match="Unknown ticket"):
        broker.cancel_pending("nope")


def test_get_tick_requires_a_tick():
    broker = FakeBroker()

    with pytest.raises(BrokerUnavailable):
        broker.get_tick("XAUUSDc")

    broker.set_tick("XAUUSDc", Decimal("4116.90"), Decimal("4117.10"))
    tick = broker.get_tick("XAUUSDc")

    assert tick.bid == Decimal("4116.90")
    assert tick.ask == Decimal("4117.10")


def test_fault_before_action_leaves_nothing_placed_and_retry_works():
    broker = FakeBroker()
    broker.inject_fault("place_pending", on_call=1)

    with pytest.raises(BrokerUnavailable):
        broker.place_pending(make_request())

    assert broker.list_orders() == []

    broker.place_pending(make_request())
    assert len(broker.list_orders()) == 1


def test_fault_after_action_still_places_order_and_retry_does_not_duplicate():
    broker = FakeBroker()
    broker.inject_fault("place_pending", on_call=1, applied=True)

    # The caller sees a timeout, but the broker actually created the order.
    with pytest.raises(BrokerUnavailable):
        broker.place_pending(make_request())

    assert len(broker.list_orders()) == 1

    # A retry with the same client_id must not create a second order.
    broker.place_pending(make_request())
    assert len(broker.list_orders()) == 1


def test_fault_on_nth_cancel_leaves_that_order_pending():
    broker = FakeBroker()
    orders = [broker.place_pending(make_request(f"m1:{i}")) for i in range(6)]
    broker.inject_fault("cancel_pending", on_call=4)

    failed = []
    for order in orders:
        try:
            broker.cancel_pending(order.ticket)
        except BrokerUnavailable:
            failed.append(order.ticket)

    assert failed == [orders[3].ticket]
    statuses = {o.ticket: o.status for o in broker.list_orders()}
    assert statuses[orders[3].ticket] == OrderStatus.PENDING
    assert sum(s == OrderStatus.CANCELLED for s in statuses.values()) == 5

    # Retrying the failed cancel succeeds.
    broker.cancel_pending(orders[3].ticket)
    assert all(o.status == OrderStatus.CANCELLED for o in broker.list_orders())


def test_inject_fault_rejects_bad_arguments():
    broker = FakeBroker()

    with pytest.raises(ValueError, match="Unknown broker method"):
        broker.inject_fault("cancel_pendng", on_call=1)

    with pytest.raises(ValueError, match="at least 1"):
        broker.inject_fault("cancel_pending", on_call=0)

    with pytest.raises(ValueError, match="cannot have been applied"):
        broker.inject_fault(
            "cancel_pending", on_call=1, error=BrokerRejected, applied=True
        )

def test_place_market_creates_a_filled_order_and_is_idempotent():
    broker = FakeBroker()
    first = broker.place_market(make_request("m1:0"))
    second = broker.place_market(make_request("m1:0"))
    assert first.status == OrderStatus.FILLED
    assert first.ticket == second.ticket
    assert len(broker.list_orders()) == 1