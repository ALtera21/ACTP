import pytest

from actp.broker import OrderStatus
from actp.dispatcher import (
    DUPLICATE_WINDOW_SECONDS,
    Dispatcher,
)
from actp.execution import ExecutionHalted, ExecutionManager
from actp.fake_broker import FakeBroker

PLAN_TEXT = """📋 PLAN XAUUSDr

🟢 BUY LIMIT (6 layer, 0.72 lot)
Area: 4084.77 - 4082.51
Cut Loss: 4081.83 (close candle di bawah level)
SL: 4075.95 | TP: 4087.04

🔴 SELL LIMIT (6 layer, 0.72 lot)
Area: 4089.30 - 4091.56
Cut Loss: 4092.24 (close candle di atas level)
SL: 4098.12 | TP: 4087.04
"""

UPDATE_TEXT = """🔄 UPDATE XAUUSDr BUY
SL: 4075.95 ➜ 4076.00
TP: 4087.04 ➜ 4088.00
"""


class Clock:
    def __init__(self, start: float = 1_000_000.0) -> None:
        self.now = start

    def __call__(self) -> float:
        return self.now


def make() -> tuple[Dispatcher, FakeBroker, Clock]:
    broker = FakeBroker()
    clock = Clock()
    return Dispatcher(ExecutionManager(broker), clock), broker, clock


def test_plan_message_places_12_orders():
    dispatcher, broker, _ = make()
    result = dispatcher.dispatch(PLAN_TEXT)
    assert result.kind == "PLAN"
    assert result.message_id is not None and ":" not in result.message_id
    assert len(broker.list_orders()) == 12
    assert "12 pending orders" in result.summary


def test_preview_has_no_side_effects():
    dispatcher, broker, _ = make()
    text = dispatcher.preview(PLAN_TEXT)
    assert "12 pending orders" in text
    assert broker.list_orders() == []


def test_duplicate_paste_inside_window_is_rejected():
    dispatcher, broker, clock = make()
    dispatcher.dispatch(PLAN_TEXT)
    clock.now += DUPLICATE_WINDOW_SECONDS - 1
    with pytest.raises(ValueError, match="duplicate"):
        dispatcher.dispatch(PLAN_TEXT)
    assert len(broker.list_orders()) == 12


def test_same_text_after_the_window_is_a_new_message():
    dispatcher, broker, clock = make()
    first = dispatcher.dispatch(PLAN_TEXT)
    clock.now += DUPLICATE_WINDOW_SECONDS + 1
    second = dispatcher.dispatch(PLAN_TEXT)
    assert first.message_id != second.message_id
    active = [
        o for o in broker.list_orders() if o.status == OrderStatus.PENDING
    ]
    assert len(active) == 12


def test_update_message_changes_buy_orders():
    dispatcher, broker, _ = make()
    dispatcher.dispatch(PLAN_TEXT)
    result = dispatcher.dispatch(UPDATE_TEXT)
    assert result.kind == "UPDATE"
    assert "6 modified" in result.summary


def test_close_message_closes_only_filled_positions_of_that_side():
    dispatcher, broker, _ = make()
    dispatcher.dispatch(PLAN_TEXT)
    first_sell = next(o for o in broker.list_orders() if o.side == "SELL")
    broker.fill(first_sell.ticket)

    result = dispatcher.dispatch("CLOSE XAUUSDr SELL - PROFIT")

    assert result.kind == "CLOSE"
    assert "1 closed" in result.summary
    assert "5 pending" in result.summary
    sells = [o for o in broker.list_orders() if o.side == "SELL"]
    assert sum(o.status == OrderStatus.CLOSED for o in sells) == 1
    assert sum(o.status == OrderStatus.PENDING for o in sells) == 5
    assert all(
        o.status == OrderStatus.PENDING
        for o in broker.list_orders() if o.side == "BUY"
    )


def test_fill_confirmation_is_noted_and_does_nothing():
    dispatcher, broker, _ = make()
    dispatcher.dispatch(PLAN_TEXT)
    before = broker.list_orders()
    result = dispatcher.dispatch("XAUUSDr BUY - Entry ter-fill")
    assert result.kind == "FILL_CONFIRMATION"
    assert broker.list_orders() == before


def test_unrecognised_message_is_rejected():    
    dispatcher, broker, _ = make()
    with pytest.raises(ValueError):
        dispatcher.dispatch("7 area entry menghasilkan 697 usd")
    assert broker.list_orders() == []


def test_resending_after_a_halt_resumes_instead_of_duplicating():
    dispatcher, broker, clock = make()
    broker.inject_fault("place_pending", on_call=3)
    with pytest.raises(ExecutionHalted):
        dispatcher.dispatch(PLAN_TEXT)
    assert len(broker.list_orders()) == 2

    dispatcher._manager.reconcile()
    clock.now += 5  # the retry must reuse the first ID, not a new one
    result = dispatcher.dispatch(PLAN_TEXT)

    orders = broker.list_orders()
    assert len(orders) == 12
    assert {o.client_id.rpartition(":")[0] for o in orders} == {
        result.message_id
    }
    assert all(o.status == OrderStatus.PENDING for o in orders)
