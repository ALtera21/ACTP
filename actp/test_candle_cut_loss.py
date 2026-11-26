"""v2.0: candle-close cut loss. A plan is cut when the last COMPLETED candle of
its timeframe closes beyond its Cut Loss, and that candle opened after the
plan was sent. Touching the line mid-candle does nothing."""
from decimal import Decimal

import pytest

from actp.broker import BrokerUnavailable, Candle, OrderStatus
from actp.execution import ExecutionHalted, ExecutionManager
from actp.fake_broker import FakeBroker
from actp.parser import parse_plan
from actp.plan_store import JsonPlanStore
from actp.test_execution import SAMPLE_PLAN, orders_of, set_tick

D = Decimal
SYM = "XAUUSDc"
T = 1_000_000  # open time of the candle running when the plan is sent (M1 grid)


def candles(broker, tf, *items):
    """items: (open_time, close) pairs, oldest first; the last is forming."""
    broker.set_candles(SYM, tf, [Candle(t, D(c)) for t, c in items])


def make(tf="M1", store=None, broker=None, mode="candle"):
    broker = broker or FakeBroker()
    manager = ExecutionManager(
        broker, store, scl_fraction=D("0.25"),
        cut_loss_mode=mode, cut_loss_timeframe=tf,
    )
    return manager, broker


def applied(tf="M1"):
    manager, broker = make(tf)
    set_tick(broker, "4121.00", "4121.20")
    candles(broker, tf, (T - 60, "4120.00"), (T, "4121.00"))
    manager.apply_plan("m-001", parse_plan(SAMPLE_PLAN))
    return manager, broker


def buy_orders(broker):
    return [o for o in broker.list_orders() if o.side == "BUY"]


def test_the_rule_is_saved_with_the_plan():
    manager, _ = applied("M5")
    assert manager.cut_rule("m-001") == {"tf": "M5", "first": T + 300}


def test_touching_the_cut_loss_mid_candle_does_nothing():
    manager, broker = applied()
    # Price is far below the Cut Loss (4116.98) right now, but no candle
    # has closed there yet.
    set_tick(broker, "4110.00", "4110.20")
    candles(broker, "M1", (T, "4121.00"), (T + 60, "4110.00"))
    for now in (1000, 1010, 1100, 5000):
        assert manager.check_cut_loss(now) == {}
    assert all(o.status == OrderStatus.PENDING for o in buy_orders(broker))


def test_the_candle_that_was_running_when_the_plan_was_sent_is_ignored():
    manager, broker = applied()
    set_tick(broker, "4121.00", "4121.20")
    # Candle T closed below the Cut Loss, but it opened before the plan.
    candles(broker, "M1", (T, "4116.00"), (T + 60, "4121.00"))
    assert manager.check_cut_loss(1000) == {}


def test_a_later_candle_closing_beyond_the_cut_loss_closes_the_plan():
    manager, broker = applied()
    set_tick(broker, "4121.00", "4121.20")
    candles(broker, "M1", (T, "4121.00"), (T + 60, "4116.97"), (T + 120, "4121"))
    assert manager.check_cut_loss(1000) == {"BUY": "CLOSED_CANDLE"}
    assert all(o.status == OrderStatus.CANCELLED for o in buy_orders(broker))
    sells = [o for o in broker.list_orders() if o.side == "SELL"]
    assert all(o.status == OrderStatus.PENDING for o in sells)


def test_a_close_exactly_on_the_line_is_not_beyond_it():
    manager, broker = applied()
    candles(broker, "M1", (T, "4121"), (T + 60, "4116.98"), (T + 120, "4121"))
    assert manager.check_cut_loss(1000) == {}


def test_sell_needs_a_close_above_its_cut_loss():
    manager, broker = applied()
    candles(broker, "M1", (T, "4121"), (T + 60, "4127.05"), (T + 120, "4121"))
    assert manager.check_cut_loss(1000) == {}
    candles(broker, "M1", (T, "4121"), (T + 60, "4127.06"), (T + 120, "4121"))
    assert manager.check_cut_loss(1010) == {"SELL": "CLOSED_CANDLE"}


def test_only_the_most_recent_completed_candle_counts():
    manager, broker = applied()
    # Candle T+60 closed below the line, but T+120 closed back above it.
    candles(broker, "M1", (T + 60, "4110"), (T + 120, "4120"), (T + 180, "4120"))
    assert manager.check_cut_loss(1000) == {}


def test_each_plan_uses_its_own_timeframe(tmp_path):
    first, broker = make("M1", JsonPlanStore(tmp_path / "s.json"))
    set_tick(broker, "4121.00", "4121.20")
    candles(broker, "M1", (T - 60, "4121"), (T, "4121"))
    candles(broker, "M5", (T - 300, "4121"), (T - 120, "4121"))
    first.apply_plan("m-001", parse_plan(SAMPLE_PLAN))

    second, _ = make("M5", JsonPlanStore(tmp_path / "s.json"), broker)
    second.apply_plan("m-002", parse_plan(SAMPLE_PLAN))
    assert second.cut_rule("m-001")["tf"] == "M1"
    assert second.cut_rule("m-002")["tf"] == "M5"

    # An M1 candle closes below the line; the M5 candle does not.
    candles(broker, "M1", (T, "4121"), (T + 60, "4110"), (T + 120, "4121"))
    candles(broker, "M5", (T - 300, "4121"), (T - 120, "4121"), (T + 180, "4121"))
    assert second.check_cut_loss(1000) == {"BUY #Alpha": "CLOSED_CANDLE"}
    assert all(o.status == OrderStatus.PENDING
               for o in orders_of(broker, "m-002") if o.side == "BUY")


def test_a_plan_from_before_the_upgrade_keeps_the_hold_timer(tmp_path):
    old, broker = make("M1", JsonPlanStore(tmp_path / "s.json"), mode="legacy")
    old.apply_plan("m-001", parse_plan(SAMPLE_PLAN))
    assert old.cut_rule("m-001") is None

    new, _ = make("M1", JsonPlanStore(tmp_path / "s.json"), broker)
    set_tick(broker, "4115.50", "4120.00")  # past Cut Loss and SCL
    assert new.check_cut_loss(1000) == {"BUY": "WAITING"}
    for now in range(1010, 1060, 10):
        new.check_cut_loss(now)
    assert new.check_cut_loss(1060) == {"BUY": "CLOSED"}


def test_rules_survive_a_restart(tmp_path):
    manager, broker = make("M3", JsonPlanStore(tmp_path / "s.json"))
    set_tick(broker, "4121.00", "4121.20")
    candles(broker, "M3", (T, "4121"))
    manager.apply_plan("m-001", parse_plan(SAMPLE_PLAN))
    restarted, _ = make("M1", JsonPlanStore(tmp_path / "s.json"), broker)
    assert restarted.cut_rule("m-001") == {"tf": "M3", "first": T + 180}


def test_a_candle_read_failure_halts_like_any_broker_failure():
    manager, broker = applied()
    broker.inject_fault("get_candles", on_call=2)  # call 1 was the plan being applied
    set_tick(broker, "4121.00", "4121.20")
    with pytest.raises(ExecutionHalted, match="get_candles"):
        manager.check_cut_loss(1000)
    assert manager.halted


def test_a_plan_cannot_be_applied_without_candle_data():
    manager, broker = make("M1")
    set_tick(broker, "4121.00", "4121.20")
    with pytest.raises(ExecutionHalted):
        manager.apply_plan("m-001", parse_plan(SAMPLE_PLAN))
    assert broker.list_orders() == []


def test_the_timer_counts_down_to_the_candle_close():
    manager, broker = applied("M5")
    set_tick(broker, "4121.00", "4121.20")
    broker.set_tick(SYM, D("4121.00"), D("4121.20"), time=T + 100)
    candles(broker, "M5", (T, "4121"))
    assert manager.candle_timers(["M5"]) == {"M5": 200}
    broker.set_tick(SYM, D("4121.00"), D("4121.20"), time=T + 400)
    assert manager.candle_timers(["M5"]) == {"M5": 0}


def test_the_timer_is_none_when_it_cannot_be_told():
    manager, broker = applied("M5")
    assert manager.candle_timers(["M5"]) == {"M5": None}  # tick has no time
    assert manager.candle_timers(["H1"]) == {"H1": None}  # no H1 candles


def test_overview_shows_each_plans_timeframe(tmp_path):
    manager, broker = make("M1", JsonPlanStore(tmp_path / "s.json"), mode="legacy")
    manager.apply_plan("m-001", parse_plan(SAMPLE_PLAN))
    new, _ = make("M15", JsonPlanStore(tmp_path / "s.json"), broker)
    set_tick(broker, "4121.00", "4121.20")
    candles(broker, "M15", (T, "4121"))
    new.apply_plan("m-002", parse_plan(SAMPLE_PLAN))
    assert [r["timeframe"] for r in new.plans_overview()] == [None, "M15"]


def test_bad_mode_or_timeframe_is_refused():
    with pytest.raises(ValueError):
        ExecutionManager(FakeBroker(), cut_loss_mode="fast")
    with pytest.raises(ValueError):
        ExecutionManager(FakeBroker(), cut_loss_timeframe="M7")


def test_timeframe_can_change_while_running_for_new_plans_only():
    import pytest
    from actp.execution import ExecutionManager
    from actp.fake_broker import FakeBroker
    manager = ExecutionManager(FakeBroker(), cut_loss_mode="candle",
                               cut_loss_timeframe="M1")
    manager.set_cut_loss_timeframe("M5")
    assert manager._cut_timeframe == "M5"
    with pytest.raises(ValueError):
        manager.set_cut_loss_timeframe("M7")
