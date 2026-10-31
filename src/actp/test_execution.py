import tempfile
from decimal import Decimal
from pathlib import Path

import pytest

from actp.broker import BrokerUnavailable, OrderRequest, OrderStatus
from actp.execution import (
    MAX_STORED_PLANS,
    ExecutionHalted,
    ExecutionManager,
)
from actp.fake_broker import FakeBroker
from actp.parser import parse_plan
from actp.plan_store import JsonPlanStore, PlanStoreError, StoredState

from actp.config import LOT_PER_ORDER

SAMPLE_PLAN = """PLAN XAUUSDr
BUY LIMIT (6 layer, 0.72 lot)
Area: 4119.83 - 4117.64
Cut Loss: 4116.98
SL: 4111.30 | TP: 4122.02
SELL LIMIT (6 layer, 0.72 lot)
Area: 4124.20 - 4126.39
Cut Loss: 4127.05
SL: 4132.73 | TP: 4122.02
"""

BUY_ONLY = """PLAN XAUUSDr
BUY LIMIT (6 layer, 0.72 lot)
Area: 4119.83 - 4117.64
Cut Loss: 4116.50
SL: 4111.30 | TP: 4122.02
"""

def make_manager() -> tuple[ExecutionManager, FakeBroker]:
    broker = FakeBroker()
    # Pinned so the tests do not depend on the SCL_FRACTION set in config.py.
    return ExecutionManager(broker, scl_fraction=Decimal("0.25")), broker


def make_stored_manager(
    directory: str,
    broker: FakeBroker | None = None,
) -> tuple[ExecutionManager, FakeBroker]:
    if broker is None:
        broker = FakeBroker()
    store = JsonPlanStore(Path(directory) / "state.json")
    return ExecutionManager(broker, store), broker


def orders_of(broker: FakeBroker, message_id: str):
    return [
        o for o in broker.list_orders()
        if o.client_id.startswith(f"{message_id}:")
    ]


def set_tick(broker: FakeBroker, bid: str, ask: str) -> None:
    broker.set_tick("XAUUSDc", Decimal(bid), Decimal(ask))


# ---- apply_plan ------------------------------------------------------


def test_apply_plan_places_12_pending_orders_with_client_ids():
    manager, broker = make_manager()

    orders = manager.apply_plan("message-001", parse_plan(SAMPLE_PLAN))

    assert len(orders) == 12
    assert all(o.status == OrderStatus.PENDING for o in orders)
    assert all(o.symbol == "XAUUSDc" for o in orders)
    assert all(o.volume == LOT_PER_ORDER for o in orders)
    assert [o.client_id for o in orders] == [
        f"message-001:{i}" for i in range(12)
    ]
    assert len(broker.list_orders()) == 12


def test_exact_retry_of_completed_plan_creates_no_duplicates():
    manager, broker = make_manager()
    plan = parse_plan(SAMPLE_PLAN)

    first = manager.apply_plan("message-001", plan)
    second = manager.apply_plan("message-001", plan)

    assert [o.ticket for o in first] == [o.ticket for o in second]
    assert len(broker.list_orders()) == 12


def test_same_message_id_cannot_be_reused_for_different_plan():
    manager, _ = make_manager()
    plan = parse_plan(SAMPLE_PLAN)
    manager.apply_plan("message-001", plan)

    changed = parse_plan(SAMPLE_PLAN)
    changed["buy"]["tp"] = changed["buy"]["tp"] + 1

    with pytest.raises(ValueError, match="already used"):
        manager.apply_plan("message-001", changed)


def test_invalid_message_id_is_rejected():
    manager, _ = make_manager()
    plan = parse_plan(SAMPLE_PLAN)

    with pytest.raises(ValueError, match="non-empty"):
        manager.apply_plan("  ", plan)
    with pytest.raises(ValueError, match="must not contain"):
        manager.apply_plan("a:b", plan)


def test_new_plan_replaces_previous_active_plan():
    manager, broker = make_manager()
    plan = parse_plan(SAMPLE_PLAN)
    old = manager.apply_plan("message-001", plan)
    filled = next(o for o in old if o.side == "BUY")
    broker.fill(filled.ticket)

    new = manager.apply_plan("message-002", plan)

    assert len(new) == 12
    assert all(o.status == OrderStatus.PENDING for o in new)
    assert len(broker.list_orders()) == 24
    for order in orders_of(broker, "message-001"):
        expected = (
            OrderStatus.CLOSED
            if order.ticket == filled.ticket
            else OrderStatus.CANCELLED
        )
        assert order.status == expected


def test_invalid_plan_does_not_touch_the_broker():
    manager, broker = make_manager()
    plan = parse_plan(SAMPLE_PLAN)
    manager.apply_plan("message-001", plan)

    invalid = parse_plan(SAMPLE_PLAN)
    invalid["symbol"] = "EURUSD"
    with pytest.raises(ValueError, match="Unauthorized trading symbol"):
        manager.apply_plan("message-002", invalid)

    orders = broker.list_orders()
    assert len(orders) == 12
    assert all(o.status == OrderStatus.PENDING for o in orders)
    assert not manager.halted


def test_late_retry_of_old_message_does_not_replace_new_plan():
    manager, broker = make_manager()
    plan = parse_plan(SAMPLE_PLAN)
    manager.apply_plan("message-001", plan)
    manager.apply_plan("message-002", plan)

    returned = manager.apply_plan("message-001", plan)

    assert all(o.status == OrderStatus.CANCELLED for o in returned)
    assert len(orders_of(broker, "message-002")) == 12
    assert all(
        o.status == OrderStatus.PENDING
        for o in orders_of(broker, "message-002")
    )


# ---- Fail-safe halting -----------------------------------------------


def test_cancel_failure_halts_and_places_no_new_orders():
    manager, broker = make_manager()
    plan = parse_plan(SAMPLE_PLAN)
    manager.apply_plan("message-001", plan)
    broker.inject_fault("cancel_pending", on_call=4)

    with pytest.raises(ExecutionHalted):
        manager.apply_plan("message-002", plan)

    assert manager.halted
    assert orders_of(broker, "message-002") == []
    old = orders_of(broker, "message-001")
    assert sum(o.status == OrderStatus.CANCELLED for o in old) == 3
    assert sum(o.status == OrderStatus.PENDING for o in old) == 9

    # While halted, nothing else may run.
    before = broker.list_orders()
    with pytest.raises(ExecutionHalted, match="reconcile"):
        manager.apply_plan("message-003", plan)
    with pytest.raises(ExecutionHalted, match="reconcile"):
        manager.close_side("BUY")
    with pytest.raises(ExecutionHalted, match="reconcile"):
        manager.update_side("BUY", {"sl": Decimal("4112.00")})
    assert broker.list_orders() == before


def test_reconcile_then_retry_completes_the_replacement():
    manager, broker = make_manager()
    plan = parse_plan(SAMPLE_PLAN)
    manager.apply_plan("message-001", plan)
    broker.inject_fault("cancel_pending", on_call=4)
    with pytest.raises(ExecutionHalted):
        manager.apply_plan("message-002", plan)

    result = manager.reconcile()

    assert not manager.halted
    assert len(result.active_orders) == 9
    assert result.mixed_plans is False

    new = manager.apply_plan("message-002", plan)

    assert len(new) == 12
    assert all(o.status == OrderStatus.PENDING for o in new)
    assert all(
        o.status == OrderStatus.CANCELLED
        for o in orders_of(broker, "message-001")
    )


def test_lost_reply_on_place_does_not_create_duplicates_on_resume():
    manager, broker = make_manager()
    plan = parse_plan(SAMPLE_PLAN)
    # The 3rd placement succeeds at the broker but the reply is lost.
    broker.inject_fault("place_pending", on_call=3, applied=True)

    with pytest.raises(ExecutionHalted):
        manager.apply_plan("message-001", plan)

    assert len(broker.list_orders()) == 3

    manager.reconcile()
    orders = manager.apply_plan("message-001", plan)

    assert len(orders) == 12
    all_orders = broker.list_orders()
    assert len(all_orders) == 12
    assert len({o.client_id for o in all_orders}) == 12


def test_reconcile_stays_halted_if_broker_cannot_be_read():
    manager, broker = make_manager()
    plan = parse_plan(SAMPLE_PLAN)
    broker.inject_fault("place_pending", on_call=1)
    with pytest.raises(ExecutionHalted):
        manager.apply_plan("message-001", plan)
    # list_orders call #1 was inside apply_plan; #2 is the first reconcile.
    broker.inject_fault("list_orders", on_call=2)

    with pytest.raises(ExecutionHalted, match="still halted"):
        manager.reconcile()
    assert manager.halted

    manager.reconcile()
    assert not manager.halted


def test_reconcile_reports_mixed_plans():
    manager, broker = make_manager()
    for client_id in ("a:0", "b:0"):
        broker.place_pending(
            OrderRequest(
                client_id=client_id,
                symbol="XAUUSDc",
                side="BUY",
                entry_price=Decimal("4117.64"),
                volume=Decimal("0.01"),
                sl=Decimal("4111.30"),
                tp=Decimal("4122.02"),
            )
        )

    assert manager.reconcile().mixed_plans is True


def test_unavailable_error_type_is_what_halts_the_manager():
    manager, broker = make_manager()
    broker.inject_fault("list_orders", on_call=1, error=BrokerUnavailable)

    with pytest.raises(ExecutionHalted, match="list_orders failed"):
        manager.close_side("BUY")

    assert manager.halted


# ---- close_side / update_side ----------------------------------------


def test_close_side_closes_filled_and_cancels_pending_for_one_side():
    manager, broker = make_manager()
    orders = manager.apply_plan("message-001", parse_plan(SAMPLE_PLAN))
    first_buy = next(o for o in orders if o.side == "BUY")
    broker.fill(first_buy.ticket)

    result = manager.close_side("BUY")

    assert result == {"cancelled": 5, "closed": 1}
    after = broker.list_orders()
    assert all(
        o.status == OrderStatus.PENDING for o in after if o.side == "SELL"
    )
    assert next(
        o.status for o in after if o.ticket == first_buy.ticket
    ) == OrderStatus.CLOSED


def test_close_failure_halts_and_retry_finishes_the_job():
    manager, broker = make_manager()
    orders = manager.apply_plan("message-001", parse_plan(SAMPLE_PLAN))
    first_buy = next(o for o in orders if o.side == "BUY")
    broker.fill(first_buy.ticket)
    broker.inject_fault("close_position", on_call=1)

    with pytest.raises(ExecutionHalted):
        manager.close_side("BUY")

    manager.reconcile()
    result = manager.close_side("BUY")

    assert result == {"cancelled": 5, "closed": 1}


def test_close_side_rejects_invalid_side():
    manager, _ = make_manager()

    with pytest.raises(ValueError, match="side must be BUY or SELL"):
        manager.close_side("HOLD")
    with pytest.raises(ValueError, match="side must be BUY or SELL"):
        manager.close_side(None)


def test_update_side_changes_buy_orders_only():
    manager, broker = make_manager()
    manager.apply_plan("message-001", parse_plan(SAMPLE_PLAN))

    result = manager.update_side(
        "BUY", {"sl": Decimal("4112.00"), "tp": Decimal("4123.00")}
    )

    assert result == {
        "modified_orders": 6, "unchanged_orders": 0, "skipped_fields": 0,
    }
    orders = broker.list_orders()
    assert all(
        o.sl == Decimal("4112.00") and o.tp == Decimal("4123.00")
        for o in orders if o.side == "BUY"
    )
    assert all(
        o.sl == Decimal("4132.73") and o.tp == Decimal("4122.02")
        for o in orders if o.side == "SELL"
    )


def test_update_side_with_same_values_modifies_nothing():
    manager, _ = make_manager()
    manager.apply_plan("message-001", parse_plan(SAMPLE_PLAN))

    result = manager.update_side(
        "BUY", {"sl": Decimal("4111.30"), "tp": Decimal("4122.02")}
    )

    assert result == {
        "modified_orders": 0, "unchanged_orders": 6, "skipped_fields": 0,
    }


def test_update_keeps_old_value_for_layers_that_cannot_take_it():
    manager, broker = make_manager()
    manager.apply_plan("message-001", parse_plan(SAMPLE_PLAN))

    # TP 4118.00 is above only the first layer (entry 4117.64).
    result = manager.update_side(
        "BUY", {"sl": Decimal("4112.00"), "tp": Decimal("4118.00")}
    )

    assert result == {
        "modified_orders": 6, "unchanged_orders": 0, "skipped_fields": 5,
    }
    buys = sorted(
        (o for o in broker.list_orders() if o.side == "BUY"),
        key=lambda o: o.entry_price,
    )
    assert all(o.sl == Decimal("4112.00") for o in buys)
    assert buys[0].tp == Decimal("4118.00")
    assert all(o.tp == Decimal("4122.02") for o in buys[1:])
    assert not manager.halted


def test_tp_equal_to_top_layer_entry_is_not_rejected():
    manager, broker = make_manager()
    manager.apply_plan("message-001", parse_plan(SAMPLE_PLAN))

    # 4119.83 is the top BUY layer's entry (the case from the group).
    result = manager.update_side("BUY", {"tp": Decimal("4119.83")})

    assert result == {
        "modified_orders": 5, "unchanged_orders": 1, "skipped_fields": 1,
    }
    buys = sorted(
        (o for o in broker.list_orders() if o.side == "BUY"),
        key=lambda o: o.entry_price,
    )
    assert all(o.tp == Decimal("4119.83") for o in buys[:5])
    assert buys[5].tp == Decimal("4122.02")   # top layer keeps its old TP


def test_update_rejects_invalid_price_precision():
    manager, _ = make_manager()
    manager.apply_plan("message-001", parse_plan(SAMPLE_PLAN))

    with pytest.raises(ValueError, match="at most two decimal places"):
        manager.update_side("BUY", {"sl": Decimal("4112.001")})


def test_update_failure_halts_and_retry_only_touches_remaining_orders():
    manager, broker = make_manager()
    manager.apply_plan("message-001", parse_plan(SAMPLE_PLAN))
    # The 3rd modification fails before the broker acts.
    broker.inject_fault("modify_sltp", on_call=3)
    changes = {"sl": Decimal("4112.00")}

    with pytest.raises(ExecutionHalted):
        manager.update_side("BUY", changes)

    manager.reconcile()
    result = manager.update_side("BUY", changes)

    assert result == {
        "modified_orders": 4, "unchanged_orders": 2, "skipped_fields": 0,
    }
    assert all(
        o.sl == Decimal("4112.00")
        for o in broker.list_orders() if o.side == "BUY"
    )


# ---- Cut Loss monitor ------------------------------------------------


def test_buy_cut_loss_closes_after_60_seconds():
    manager, broker = make_manager()
    orders = manager.apply_plan("message-001", parse_plan(SAMPLE_PLAN))
    broker.fill(next(o for o in orders if o.side == "BUY").ticket)
    # BUY Cut Loss 4116.98, SCL 4115.56 (bid). Price sits beyond SCL.
    set_tick(broker, "4115.50", "4120.00")

    for now in range(1000, 1060, 10):
        assert manager.check_cut_loss(now) == {"BUY": "WAITING"}

    assert manager.check_cut_loss(1060) == {"BUY": "CLOSED"}

    after = broker.list_orders()
    assert all(
        o.status in (OrderStatus.CLOSED, OrderStatus.CANCELLED)
        for o in after if o.side == "BUY"
    )
    assert all(
        o.status == OrderStatus.PENDING for o in after if o.side == "SELL"
    )


def test_buy_cut_loss_timer_resets_after_recovery():
    manager, broker = make_manager()
    manager.apply_plan("message-001", parse_plan(SAMPLE_PLAN))

    set_tick(broker, "4116.90", "4120.00")
    for now in (1000, 1010, 1020):
        assert manager.check_cut_loss(now) == {"BUY": "WAITING"}

    set_tick(broker, "4117.10", "4120.00")
    assert manager.check_cut_loss(1030) == {"BUY": "RECOVERED"}

    # A new breach starts a fresh timer at 1040.
    set_tick(broker, "4116.90", "4120.00")
    for now in (1040, 1050, 1060, 1070, 1080, 1090):
        assert manager.check_cut_loss(now) == {"BUY": "WAITING"}

    # Only 59 seconds since the new breach.
    assert manager.check_cut_loss(1099) == {"BUY": "WAITING"}


def test_sell_cut_loss_uses_ask_price():
    manager, broker = make_manager()
    manager.apply_plan("message-001", parse_plan(SAMPLE_PLAN))
    # SELL threshold is 4127.05. Ask reaching it breaches.
    set_tick(broker, "4126.00", "4127.05")

    assert manager.check_cut_loss(2000) == {"SELL": "WAITING"}


def test_cut_loss_timer_restarts_after_polling_gap():
    manager, broker = make_manager()
    manager.apply_plan("message-001", parse_plan(SAMPLE_PLAN))
    set_tick(broker, "4116.90", "4120.00")

    assert manager.check_cut_loss(1000) == {"BUY": "WAITING"}
    # Monitoring was down for 5 minutes, so the breach isn't continuous.
    assert manager.check_cut_loss(1300) == {"BUY": "WAITING"}
    # The timer restarted at 1300, so this is only 10 seconds in.
    assert manager.check_cut_loss(1310) == {"BUY": "WAITING"}


def test_cut_loss_rejects_bad_timestamps():
    manager, broker = make_manager()
    set_tick(broker, "4120.00", "4120.00")
    manager.check_cut_loss(1000)

    with pytest.raises(ValueError, match="backwards"):
        manager.check_cut_loss(999)
    with pytest.raises(ValueError, match="finite timestamp"):
        manager.check_cut_loss(float("nan"))
    with pytest.raises(ValueError, match="finite timestamp"):
        manager.check_cut_loss(True)


def test_closing_buy_does_not_reset_sell_cut_loss_timer():
    manager, broker = make_manager()
    manager.apply_plan("message-001", parse_plan(SAMPLE_PLAN))
    set_tick(broker, "4115.50", "4128.50")  # both beyond their SCL

    assert manager.check_cut_loss(1000) == {
        "BUY": "WAITING",
        "SELL": "WAITING",
    }

    manager.close_side("BUY")

    for now in range(1010, 1060, 10):
        assert manager.check_cut_loss(now) == {"SELL": "WAITING"}

    assert manager.check_cut_loss(1060) == {"SELL": "CLOSED"}


def test_manual_close_and_new_plan_do_not_inherit_the_old_timer():
    manager, broker = make_manager()
    plan = parse_plan(SAMPLE_PLAN)
    manager.apply_plan("message-001", plan)
    set_tick(broker, "4116.90", "4120.00")
    assert manager.check_cut_loss(1000) == {"BUY": "WAITING"}

    manager.close_side("BUY")
    manager.apply_plan("message-002", plan)

    # The old timer (started at 1000) must not close the new orders.
    for now in range(1010, 1070, 10):
        assert manager.check_cut_loss(now) == {"BUY": "WAITING"}
    assert all(
        o.status == OrderStatus.PENDING
        for o in orders_of(broker, "message-002") if o.side == "BUY"
    )


def test_cut_loss_uses_the_plan_behind_the_active_orders():
    manager, broker = make_manager()
    manager.apply_plan("message-001", parse_plan(SAMPLE_PLAN))
    second = parse_plan(SAMPLE_PLAN)
    second["buy"]["cut_loss"] = Decimal("4116.50")
    manager.apply_plan("message-002", second)

    # Below the old level (4116.98) but above the new one (4116.50).
    set_tick(broker, "4116.70", "4120.00")
    assert manager.check_cut_loss(1000) == {}

    set_tick(broker, "4116.40", "4120.00")
    assert manager.check_cut_loss(1010) == {"BUY": "WAITING"}


def test_cut_loss_still_runs_while_halted():
    manager, broker = make_manager()
    orders = manager.apply_plan("message-001", parse_plan(SAMPLE_PLAN))
    broker.fill(next(o for o in orders if o.side == "BUY").ticket)
    broker.inject_fault("close_position", on_call=1)
    with pytest.raises(ExecutionHalted):
        manager.close_side("BUY")
    assert manager.halted

    set_tick(broker, "4115.50", "4120.00")
    for now in range(1000, 1060, 10):
        assert manager.check_cut_loss(now) == {"BUY": "WAITING"}
    assert manager.check_cut_loss(1060) == {"BUY": "CLOSED"}

    # Still halted: only reconcile() clears that.
    assert manager.halted


def test_cut_loss_halts_for_orders_with_no_stored_plan():
    manager, broker = make_manager()
    broker.place_pending(
        OrderRequest(
            client_id="x:0",
            symbol="XAUUSDc",
            side="BUY",
            entry_price=Decimal("4117.64"),
            volume=Decimal("0.01"),
            sl=Decimal("4111.30"),
            tp=Decimal("4122.02"),
        )
    )
    set_tick(broker, "4120.00", "4120.00")

    with pytest.raises(ExecutionHalted, match="No stored Cut Loss"):
        manager.check_cut_loss(1000)

    assert manager.halted


def test_missing_tick_halts_the_manager():
    manager, _ = make_manager()

    with pytest.raises(ExecutionHalted, match="get_tick failed"):
        manager.check_cut_loss(1000)

    assert manager.halted


# ---- Persistence -----------------------------------------------------


def test_restart_does_not_let_a_late_retry_replace_the_live_plan():
    with tempfile.TemporaryDirectory() as directory:
        manager, broker = make_stored_manager(directory)
        plan = parse_plan(SAMPLE_PLAN)
        manager.apply_plan("message-001", plan)
        manager.apply_plan("message-002", plan)

        restarted, _ = make_stored_manager(directory, broker)
        restarted.apply_plan("message-001", plan)

        live = orders_of(broker, "message-002")
        assert len(live) == 12
        assert all(o.status == OrderStatus.PENDING for o in live)


def test_restart_still_rejects_a_reused_message_id():
    with tempfile.TemporaryDirectory() as directory:
        manager, broker = make_stored_manager(directory)
        plan = parse_plan(SAMPLE_PLAN)
        broker.inject_fault("place_pending", on_call=3)
        with pytest.raises(ExecutionHalted):
            manager.apply_plan("message-001", plan)

        restarted, _ = make_stored_manager(directory, broker)
        changed = parse_plan(SAMPLE_PLAN)
        changed["buy"]["tp"] = changed["buy"]["tp"] + 1

        with pytest.raises(ValueError, match="already used"):
            restarted.apply_plan("message-001", changed)


def test_restart_keeps_cut_loss_levels():
    with tempfile.TemporaryDirectory() as directory:
        manager, broker = make_stored_manager(directory)
        manager.apply_plan("message-001", parse_plan(SAMPLE_PLAN))

        restarted, _ = make_stored_manager(directory, broker)
        set_tick(broker, "4116.90", "4120.00")

        assert restarted.check_cut_loss(1000) == {"BUY": "WAITING"}


def test_store_failure_halts_before_any_order_is_placed():
    class FailingStore:
        def load(self) -> StoredState:
            return StoredState()

        def save(self, state: StoredState) -> None:
            raise OSError("disk full")

    broker = FakeBroker()
    manager = ExecutionManager(broker, FailingStore())

    with pytest.raises(ExecutionHalted, match="saving plan state failed"):
        manager.apply_plan("message-001", parse_plan(SAMPLE_PLAN))

    assert broker.list_orders() == []
    assert manager.halted


def test_corrupt_state_file_refuses_to_start():
    with tempfile.TemporaryDirectory() as directory:
        (Path(directory) / "state.json").write_text("{not json")

        with pytest.raises(PlanStoreError):
            make_stored_manager(directory)


def test_old_plans_are_pruned_but_the_current_one_is_kept():
    with tempfile.TemporaryDirectory() as directory:
        manager, _ = make_stored_manager(directory)
        plan = parse_plan(SAMPLE_PLAN)
        for number in range(MAX_STORED_PLANS + 5):
            manager.apply_plan(f"message-{number:03d}", plan)

        state = JsonPlanStore(Path(directory) / "state.json").load()

        last = f"message-{MAX_STORED_PLANS + 4:03d}"
        assert len(state.plans) == MAX_STORED_PLANS
        assert state.current_message_id == last
        assert last in state.plans
        assert "message-000" not in state.plans

def test_snapshot_reads_without_halting():
    manager, broker = make_manager()
    manager.apply_plan("message-001", parse_plan(SAMPLE_PLAN))
    snap = manager.snapshot()
    assert len(snap["active"]) == 12 and snap["tick"] is None


def test_snapshot_failure_does_not_halt():
    manager, broker = make_manager()
    broker.inject_fault("list_orders", on_call=1)
    assert "error" in manager.snapshot()
    assert not manager.halted

def test_buy_only_plan_keeps_the_old_sell_orders():
    manager, broker = make_manager()
    manager.apply_plan("message-001", parse_plan(SAMPLE_PLAN))
    manager.apply_plan("message-002", parse_plan(BUY_ONLY))

    old = orders_of(broker, "message-001")
    assert all(o.status == OrderStatus.CANCELLED for o in old if o.side == "BUY")
    assert all(o.status == OrderStatus.PENDING for o in old if o.side == "SELL")
    new = orders_of(broker, "message-002")
    assert len(new) == 6 and all(o.status == OrderStatus.PENDING for o in new)


def test_cut_loss_uses_each_sides_own_plan_after_a_one_sided_plan():
    manager, broker = make_manager()
    manager.apply_plan("message-001", parse_plan(SAMPLE_PLAN))
    manager.apply_plan("message-002", parse_plan(BUY_ONLY))

    # BUY now uses 4116.50 (plan 2); SELL still uses 4127.05 (plan 1).
    set_tick(broker, "4116.70", "4127.10")
    assert manager.check_cut_loss(1000) == {"SELL": "WAITING"}


def test_one_sided_plan_does_not_restart_the_other_sides_timer():
    manager, broker = make_manager()
    manager.apply_plan("message-001", parse_plan(SAMPLE_PLAN))
    set_tick(broker, "4120.00", "4128.50")  # beyond SELL SCL 4128.47
    assert manager.check_cut_loss(1000) == {"SELL": "WAITING"}

    manager.apply_plan("message-002", parse_plan(BUY_ONLY))
    for now in range(1010, 1060, 10):
        assert manager.check_cut_loss(now) == {"SELL": "WAITING"}
    assert manager.check_cut_loss(1060) == {"SELL": "CLOSED"}


def test_mixed_plans_only_reported_within_one_side():
    manager, _ = make_manager()
    manager.apply_plan("message-001", parse_plan(SAMPLE_PLAN))
    manager.apply_plan("message-002", parse_plan(BUY_ONLY))
    assert manager.reconcile().mixed_plans is False

def test_close_side_without_pending_leaves_pending_orders():
    manager, broker = make_manager()
    orders = manager.apply_plan("message-001", parse_plan(SAMPLE_PLAN))
    first_buy = next(o for o in orders if o.side == "BUY")
    broker.fill(first_buy.ticket)

    result = manager.close_side("BUY", include_pending=False)

    assert result == {"cancelled": 0, "closed": 1, "left_pending": 5}
    buys = [o for o in broker.list_orders() if o.side == "BUY"]
    assert sum(o.status == OrderStatus.PENDING for o in buys) == 5


def test_leftover_pending_orders_are_replaced_by_the_next_plan_for_that_side():
    manager, broker = make_manager()
    orders = manager.apply_plan("message-001", parse_plan(SAMPLE_PLAN))
    broker.fill(next(o for o in orders if o.side == "BUY").ticket)
    manager.close_side("BUY", include_pending=False)

    manager.apply_plan("message-002", parse_plan(BUY_ONLY))

    old_buys = [o for o in orders_of(broker, "message-001") if o.side == "BUY"]
    assert not any(o.status == OrderStatus.PENDING for o in old_buys)
    assert len(orders_of(broker, "message-002")) == 6

def test_cancel_pending_side_cancels_only_pending_of_that_side():
    manager, broker = make_manager()
    orders = manager.apply_plan("message-001", parse_plan(SAMPLE_PLAN))
    first_buy = next(o for o in orders if o.side == "BUY")
    broker.fill(first_buy.ticket)

    result = manager.cancel_pending_side("BUY")

    assert result == {"cancelled": 5, "left_open": 1}
    after = broker.list_orders()
    buys = [o for o in after if o.side == "BUY"]
    assert sum(o.status == OrderStatus.CANCELLED for o in buys) == 5
    assert sum(o.status == OrderStatus.FILLED for o in buys) == 1
    assert all(o.status == OrderStatus.PENDING for o in after if o.side == "SELL")


def test_cancel_pending_side_rejects_invalid_side_and_halted_state():
    manager, broker = make_manager()
    with pytest.raises(ValueError, match="side must be BUY or SELL"):
        manager.cancel_pending_side("HOLD")
    broker.inject_fault("list_orders", on_call=1)
    with pytest.raises(ExecutionHalted):
        manager.cancel_pending_side("BUY")
    with pytest.raises(ExecutionHalted, match="reconcile"):
        manager.cancel_pending_side("BUY")

# ---- STOP CUT LOSS (SCL) ---------------------------------------------
# Sample plan: BUY Cut Loss 4116.98 / SL 4111.30 -> SCL 4115.56 (0.25).
#              SELL Cut Loss 4127.05 / SL 4132.73 -> SCL 4128.47.


def buy_manager():
    manager, broker = make_manager()
    manager.apply_plan("message-001", parse_plan(SAMPLE_PLAN))
    return manager, broker


def buy_closed(broker) -> bool:
    return all(
        o.status in (OrderStatus.CLOSED, OrderStatus.CANCELLED)
        for o in broker.list_orders() if o.side == "BUY"
    )


def test_scl_not_reached_after_60s_means_holding_not_closed():
    manager, broker = buy_manager()
    set_tick(broker, "4116.50", "4120.00")  # past Cut Loss, above SCL
    for now in range(1000, 1060, 10):
        assert manager.check_cut_loss(now) == {"BUY": "WAITING"}
    for now in range(1060, 1300, 10):
        assert manager.check_cut_loss(now) == {"BUY": "HOLDING"}
    assert not buy_closed(broker)


def test_closes_when_scl_is_reached_after_the_hold_while_still_breached():
    manager, broker = buy_manager()
    set_tick(broker, "4116.50", "4120.00")
    for now in range(1000, 1100, 10):
        manager.check_cut_loss(now)
    set_tick(broker, "4115.56", "4120.00")  # exactly SCL counts
    assert manager.check_cut_loss(1100) == {"BUY": "CLOSED"}
    assert buy_closed(broker)


def test_touching_scl_before_the_hold_never_closes_by_itself():
    manager, broker = buy_manager()
    set_tick(broker, "4115.00", "4120.00")
    for now in range(1000, 1060, 10):
        assert manager.check_cut_loss(now) == {"BUY": "WAITING"}
    # Price pops back above SCL (still past Cut Loss) exactly at 60 s.
    set_tick(broker, "4116.50", "4120.00")
    assert manager.check_cut_loss(1060) == {"BUY": "HOLDING"}
    assert not buy_closed(broker)


def test_recovery_while_holding_resets_everything():
    manager, broker = buy_manager()
    set_tick(broker, "4116.50", "4120.00")
    for now in range(1000, 1070, 10):
        manager.check_cut_loss(now)
    set_tick(broker, "4117.10", "4120.00")
    assert manager.check_cut_loss(1070) == {"BUY": "RECOVERED"}
    # A fresh breach starts a new 60 s timer, even straight at SCL.
    set_tick(broker, "4115.00", "4120.00")
    assert manager.check_cut_loss(1080) == {"BUY": "WAITING"}
    for now in range(1090, 1140, 10):
        assert manager.check_cut_loss(now) == {"BUY": "WAITING"}
    assert manager.check_cut_loss(1140) == {"BUY": "CLOSED"}


def test_sell_scl_uses_ask_and_is_above_cut_loss():
    manager, broker = make_manager()
    manager.apply_plan("message-001", parse_plan(SAMPLE_PLAN))
    set_tick(broker, "4120.00", "4128.00")  # past Cut Loss, below SCL
    for now in range(1000, 1060, 10):
        manager.check_cut_loss(now)
    assert manager.check_cut_loss(1060) == {"SELL": "HOLDING"}
    set_tick(broker, "4120.00", "4128.47")
    assert manager.check_cut_loss(1070) == {"SELL": "CLOSED"}


def test_scl_fraction_is_configurable():
    broker = FakeBroker()
    manager = ExecutionManager(broker, scl_fraction=Decimal("0.5"))
    manager.apply_plan("message-001", parse_plan(SAMPLE_PLAN))
    assert manager.chart_levels()["BUY"] == (
        Decimal("4116.98"), Decimal("4114.14"),
    )
    with pytest.raises(ValueError):
        ExecutionManager(FakeBroker(), scl_fraction=Decimal("1"))


def test_chart_levels_follow_active_orders():
    manager, broker = make_manager()
    assert manager.chart_levels() == {}
    manager.apply_plan("message-001", parse_plan(SAMPLE_PLAN))
    assert manager.chart_levels() == {
        "BUY": (Decimal("4116.98"), Decimal("4115.56")),
        "SELL": (Decimal("4127.05"), Decimal("4128.47")),
    }
    manager.close_side("BUY")
    assert list(manager.chart_levels()) == ["SELL"]

def test_scl_disabled_closes_right_after_the_hold():
    broker = FakeBroker()
    manager = ExecutionManager(
        broker, scl_fraction=Decimal("0.25"), scl_enabled=False
    )
    manager.apply_plan("message-001", parse_plan(SAMPLE_PLAN))
    set_tick(broker, "4116.50", "4120.00")  # past Cut Loss, above the SCL
    for now in range(1000, 1060, 10):
        assert manager.check_cut_loss(now) == {"BUY": "WAITING"}
    assert manager.check_cut_loss(1060) == {"BUY": "CLOSED"}
    assert manager.chart_levels() == {
        "SELL": (Decimal("4127.05"), Decimal("4127.05")),
    }