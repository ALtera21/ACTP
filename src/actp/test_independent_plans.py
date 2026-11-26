"""v2.0: every PLAN is independent. Older plans stay live until the Cancel
buttons or the kill switch; Cut Loss, Auto-TP and UPDATE work per plan."""
from decimal import Decimal

from actp.broker import OrderStatus
from actp.execution import ExecutionManager
from actp.fake_broker import FakeBroker
from actp.parser import parse_plan
from actp.plan_store import JsonPlanStore
from actp.test_execution import SAMPLE_PLAN, orders_of, set_tick

D = Decimal


def two_plans(second_buy_cut_loss=None, store=None, broker=None):
    broker = broker or FakeBroker()
    manager = ExecutionManager(broker, store, scl_fraction=D("0.25"))
    manager.apply_plan("m-001", parse_plan(SAMPLE_PLAN))
    second = parse_plan(SAMPLE_PLAN)
    if second_buy_cut_loss is not None:
        second["buy"]["cut_loss"] = D(second_buy_cut_loss)
    manager.apply_plan("m-002", second)
    return manager, broker


def buys(broker, message_id):
    return [o for o in orders_of(broker, message_id) if o.side == "BUY"]


def test_cut_loss_closes_only_the_plan_that_breached():
    # Plan 1: Cut Loss 4116.98, SCL 4115.56. Plan 2: Cut Loss 4116.50, SCL 4115.20.
    manager, broker = two_plans("4116.50")
    set_tick(broker, "4115.50", "4120.00")  # past plan 1's SCL, not plan 2's
    for now in range(1000, 1060, 10):
        assert manager.check_cut_loss(now) == {
            "BUY #Alpha": "WAITING", "BUY #Beta": "WAITING"}
    assert manager.check_cut_loss(1060) == {
        "BUY #Alpha": "CLOSED", "BUY #Beta": "HOLDING"}
    assert all(o.status == OrderStatus.CANCELLED for o in buys(broker, "m-001"))
    assert all(o.status == OrderStatus.PENDING for o in buys(broker, "m-002"))
    assert all(o.status == OrderStatus.PENDING
               for o in orders_of(broker, "m-001") if o.side == "SELL")


def test_a_new_plan_does_not_reset_an_older_plans_timer():
    manager, broker = two_plans()
    # Plan 1 starts its breach alone, then plan 3 arrives.
    broker.set_tick("XAUUSDc", D("4115.50"), D("4120.00"))
    assert manager.check_cut_loss(1000)["BUY #Alpha"] == "WAITING"
    manager.apply_plan("m-003", parse_plan(SAMPLE_PLAN))
    for now in range(1010, 1060, 10):
        manager.check_cut_loss(now)
    result = manager.check_cut_loss(1060)
    assert result["BUY #Alpha"] == "CLOSED"


def test_update_changes_only_the_most_recent_plan():
    manager, broker = two_plans()
    result = manager.update_side("BUY", {"sl": D("4112.00")})
    assert result["modified_orders"] == 6
    assert all(o.sl == D("4112.00") for o in buys(broker, "m-002"))
    assert all(o.sl == D("4111.30") for o in buys(broker, "m-001"))


def test_update_skips_a_newer_plan_that_has_no_such_side():
    manager = ExecutionManager(FakeBroker())
    broker = manager._broker
    manager.apply_plan("m-001", parse_plan(SAMPLE_PLAN))
    sell_only = parse_plan(SAMPLE_PLAN)
    sell_only["buy"] = None
    manager.apply_plan("m-002", sell_only)
    manager.update_side("BUY", {"sl": D("4112.00")})
    assert all(o.sl == D("4112.00") for o in buys(broker, "m-001"))


def test_auto_tp_is_per_plan():
    manager, broker = two_plans()
    for o in buys(broker, "m-001"):
        broker.fill(o.ticket)
    set_tick(broker, "4117.50", "4117.70")
    assert manager.move_tp_when_full() == {
        "BUY #Alpha": ("MOVED", D("4119.83"), 6)}
    assert all(o.tp == D("4119.83") for o in buys(broker, "m-001"))
    assert all(o.tp == D("4122.02") for o in buys(broker, "m-002"))
    assert manager.move_tp_when_full() == {}  # once per plan


def test_cancel_pending_side_removes_every_plan_on_that_side():
    manager, broker = two_plans()
    first = buys(broker, "m-001")[0]
    broker.fill(first.ticket)
    result = manager.cancel_pending_side("BUY")
    assert result == {"cancelled": 11, "left_open": 1}
    assert all(o.status == OrderStatus.PENDING
               for o in broker.list_orders() if o.side == "SELL")


def test_kill_all_clears_every_plan():
    manager, broker = two_plans()
    broker.fill(buys(broker, "m-002")[0].ticket)
    assert manager.close_all() == {"cancelled": 23, "closed": 1}
    assert not any(o.status in (OrderStatus.PENDING, OrderStatus.FILLED)
                   for o in broker.list_orders())


def test_close_side_closes_filled_positions_of_every_plan():
    manager, broker = two_plans()
    broker.fill(buys(broker, "m-001")[0].ticket)
    broker.fill(buys(broker, "m-002")[0].ticket)
    assert manager.close_side("BUY", include_pending=False)["closed"] == 2


def test_plans_overview_has_one_row_per_live_plan():
    manager, broker = two_plans("4116.50")
    broker.fill(buys(broker, "m-001")[0].ticket)
    rows = manager.plans_overview()
    assert [r["message_id"] for r in rows] == ["m-001", "m-002"]
    assert rows[0]["BUY"] == {"filled": 1, "pending": 5,
                              "cut_loss": D("4116.98"), "sl": D("4111.30"),
                              "tp": D("4122.02"), "tp_target": None,
                              "tp_live": D("4122.02")}
    assert rows[1]["BUY"]["cut_loss"] == D("4116.50")
    assert rows[1]["SELL"]["pending"] == 6
    manager.close_all()
    assert manager.plans_overview() == []


def test_all_plans_keep_their_cut_loss_after_a_restart(tmp_path):
    store = JsonPlanStore(tmp_path / "state.json")
    manager, broker = two_plans("4116.50", store=store)
    restarted = ExecutionManager(
        broker, JsonPlanStore(tmp_path / "state.json"),
        scl_fraction=D("0.25"))
    set_tick(broker, "4116.70", "4120.00")  # only plan 1 is breached
    assert restarted.check_cut_loss(1000) == {"BUY #Alpha": "WAITING"}


# ---- plan names: Alpha, Beta, ... Omega, then the short id ---------------

from actp.execution import PLAN_NAMES  # noqa: E402


def test_plans_are_named_in_greek_order():
    manager, _ = two_plans()
    manager.apply_plan("m-003", parse_plan(SAMPLE_PLAN))
    assert [manager.plan_name(m) for m in ("m-001", "m-002", "m-003")] == [
        "Alpha", "Beta", "Gamma"]
    assert len(PLAN_NAMES) == 24 and PLAN_NAMES[-1] == "Omega"


def test_names_start_again_once_no_plan_is_live():
    manager, _ = two_plans()
    manager.close_all()
    manager.apply_plan("m-003", parse_plan(SAMPLE_PLAN))
    assert manager.plan_name("m-003") == "Alpha"
    assert manager.plan_name("m-001") == "Alpha"  # old plans keep their name


def test_a_free_name_in_the_middle_is_reused():
    manager, broker = two_plans()           # Alpha, Beta
    manager.apply_plan("m-003", parse_plan(SAMPLE_PLAN))   # Gamma
    for o in orders_of(broker, "m-001"):
        broker.cancel_pending(o.ticket)     # Alpha is free again
    manager.apply_plan("m-004", parse_plan(SAMPLE_PLAN))
    assert manager.plan_name("m-004") == "Alpha"


def test_after_omega_the_short_id_is_used():
    manager = ExecutionManager(FakeBroker())
    for i in range(1, 26):
        manager.apply_plan(f"170-{i:08x}", parse_plan(SAMPLE_PLAN))
    assert manager.plan_name("170-00000018") == "Omega"       # the 24th
    assert manager.plan_name("170-00000019") == "00000019"    # the 25th


def test_names_survive_a_restart_and_resume(tmp_path):
    store = JsonPlanStore(tmp_path / "state.json")
    manager, broker = two_plans(store=store)
    restarted = ExecutionManager(broker, JsonPlanStore(tmp_path / "state.json"))
    assert restarted.plan_name("m-001") == "Alpha"
    assert restarted.plan_name("m-002") == "Beta"
    restarted.apply_plan("m-003", parse_plan(SAMPLE_PLAN))
    assert restarted.plan_name("m-003") == "Gamma"


def test_a_plan_without_a_saved_name_shows_its_short_id():
    manager = ExecutionManager(FakeBroker())
    assert manager.plan_name("1763400000-a1b2c3d4") == "a1b2c3d4"


def test_overview_rows_carry_the_name():
    manager, _ = two_plans()
    assert [r["name"] for r in manager.plans_overview()] == ["Alpha", "Beta"]


# ---- close one plan / one side of one plan ---------------------------------

def test_close_plan_touches_only_that_plan():
    manager, broker = two_plans()
    broker.fill(buys(broker, "m-001")[0].ticket)
    assert manager.close_plan("m-001") == {"cancelled": 11, "closed": 1}
    assert not any(o.status in (OrderStatus.PENDING, OrderStatus.FILLED)
                   for o in orders_of(broker, "m-001"))
    assert all(o.status == OrderStatus.PENDING
               for o in orders_of(broker, "m-002"))


def test_close_plan_side_leaves_the_other_side_and_other_plans():
    manager, broker = two_plans()
    broker.fill(buys(broker, "m-001")[0].ticket)
    assert manager.close_plan("m-001", "BUY") == {"cancelled": 5, "closed": 1}
    assert all(o.status == OrderStatus.PENDING
               for o in orders_of(broker, "m-001") if o.side == "SELL")
    assert all(o.status == OrderStatus.PENDING
               for o in orders_of(broker, "m-002"))


def test_close_plan_works_while_halted_and_keeps_the_halt():
    manager, broker = two_plans()
    broker.inject_fault(
        "list_orders", on_call=broker._call_counts["list_orders"] + 1)
    try:
        manager.close_side("BUY")
    except Exception:
        pass
    assert manager.halted
    assert manager.close_plan("m-002") == {"cancelled": 12, "closed": 0}
    assert manager.halted


def test_a_closed_plan_cannot_be_placed_again_by_a_late_retry():
    manager, broker = two_plans()
    manager.close_plan("m-002")
    before = broker.list_orders()
    manager.apply_plan("m-002", parse_plan(SAMPLE_PLAN))
    assert broker.list_orders() == before


def test_close_plan_of_an_unknown_plan_does_nothing():
    manager, broker = two_plans()
    assert manager.close_plan("nope") == {"cancelled": 0, "closed": 0}
    assert manager.close_plan("m-001", "SELL")["cancelled"] == 6
    try:
        manager.close_plan("m-001", "HOLD")
    except ValueError:
        pass
    else:
        raise AssertionError("bad side accepted")
