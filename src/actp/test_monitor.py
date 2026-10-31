from actp.execution import ExecutionHalted
from actp.monitor import CutLossMonitor


class ScriptedManager:
    """Returns or raises each scripted item in turn."""

    def __init__(self, *script):
        self.script = list(script)
        self.times = []

    def check_cut_loss(self, now):
        self.times.append(now)
        item = self.script.pop(0)
        if isinstance(item, Exception):
            raise item
        return item


def levels(events):
    return [e.level for e in events]


def test_waiting_is_reported_once_then_closed_is_an_alert():
    m = CutLossMonitor(ScriptedManager(
        {"BUY": "WAITING"}, {"BUY": "WAITING"}, {"BUY": "CLOSED"}, {},
    ), clock=lambda: 1.0)
    assert levels(m.poll()) == ["warn"]
    assert m.poll() == []  # same state, not repeated
    closed = m.poll()
    assert levels(closed) == ["alert"] and "BUY" in closed[0].message
    assert m.poll() == []


def test_recovered_is_info_and_timer_restart_is_reported_again():
    m = CutLossMonitor(ScriptedManager(
        {"SELL": "WAITING"}, {"SELL": "RECOVERED"}, {}, {"SELL": "WAITING"},
    ))
    assert levels(m.poll()) == ["warn"]
    assert levels(m.poll()) == ["info"]
    assert m.poll() == []
    assert levels(m.poll()) == ["warn"]  # a new breach is news again


def test_halted_is_one_alert_then_recovery_message():
    halted = ExecutionHalted("list_orders failed: boom")
    m = CutLossMonitor(ScriptedManager(
        halted, ExecutionHalted("list_orders failed: boom"), {},
    ))
    first = m.poll()
    assert levels(first) == ["alert"] and "boom" in first[0].message
    assert m.error is not None
    assert m.poll() == []  # same failure, not repeated
    recovered = m.poll()
    assert levels(recovered) == ["info"]
    assert m.error is None


def test_unexpected_exception_does_not_escape():
    m = CutLossMonitor(ScriptedManager(RuntimeError("weird"), {}))
    events = m.poll()
    assert levels(events) == ["alert"] and "RuntimeError" in events[0].message
    assert m.error is not None
    m.poll()
    assert m.error is None


def test_clock_value_is_passed_to_the_manager():
    manager = ScriptedManager({}, {})
    ticks = iter([10.0, 13.0])
    m = CutLossMonitor(manager, clock=lambda: next(ticks))
    m.poll()
    m.poll()
    assert manager.times == [10.0, 13.0]


def test_holding_is_reported_once_and_closed_follows():
    m = CutLossMonitor(ScriptedManager(
        {"BUY": "WAITING"}, {"BUY": "HOLDING"}, {"BUY": "HOLDING"},
        {"BUY": "CLOSED"},
    ))
    assert levels(m.poll()) == ["warn"]
    holding = m.poll()
    assert levels(holding) == ["warn"] and "STOP CUT LOSS" in holding[0].message
    assert m.poll() == []
    assert levels(m.poll()) == ["alert"]
