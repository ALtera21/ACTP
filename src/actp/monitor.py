"""
Cut-loss monitor: one poll() call = one check_cut_loss() call.

Kept free of tkinter so it can be tested. The app calls poll() every few
seconds and shows the returned events. Rules:
- poll() never raises. A failed check becomes an "alert" event, and the
  next poll simply tries again (check_cut_loss is allowed to run while the
  manager is halted, because closing only reduces risk).
- Repeated identical states are reported once, not every few seconds.
"""

import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from actp.config import CUT_LOSS_HOLD_SECONDS, SCL_ENABLED
from actp.execution import ExecutionHalted

_NEEDS = f"{CUT_LOSS_HOLD_SECONDS}s" + (
    " and STOP CUT LOSS" if SCL_ENABLED else ""
)

@dataclass(frozen=True)
class Event:
    level: str  # "info", "warn" or "alert"
    message: str


class CutLossMonitor:
    def __init__(
        self,
        manager: Any,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._manager = manager
        self._clock = clock
        self._last: dict[str, str] = {}
        self._error: str | None = None
        self.checks_ok = 0

    @property
    def error(self) -> str | None:
        """Why the last poll failed, or None if it succeeded."""
        return self._error

    def poll(self) -> list[Event]:
        try:
            results = self._manager.check_cut_loss(self._clock())
        except ExecutionHalted as exc:
            return self._failed(f"Cut-loss check halted: {exc}")
        except Exception as exc:  # the loop must survive anything
            return self._failed(
                f"Cut-loss check crashed: {type(exc).__name__}: {exc}"
            )

        events: list[Event] = []
        if self._error is not None:
            self._error = None
            events.append(Event("info", "Cut-loss monitoring recovered."))
        self.checks_ok += 1

        for side, state in results.items():
            if self._last.get(side) == state:
                continue
            if state == "WAITING":
                events.append(Event(
                    "warn",
                    f"{side} Cut Loss breached. Needs {_NEEDS} to close."
                    f"{CUT_LOSS_HOLD_SECONDS}s and STOP CUT LOSS to close.",
                ))
            elif state == "HOLDING":
                events.append(Event(
                    "warn",
                    f"{side} Cut Loss held {CUT_LOSS_HOLD_SECONDS}s but "
                    "price has not reached STOP CUT LOSS. Not closing; "
                    "will close if it does.",
                ))
            elif state == "CLOSED":
                events.append(Event(
                    "alert",
                    f"{side} Cut Loss held {CUT_LOSS_HOLD_SECONDS}s"
                    + (" and STOP CUT LOSS was reached" if SCL_ENABLED else "")
                    + f": all {side} orders were cancelled/closed."
                ))
            elif state == "RECOVERED":
                events.append(Event(
                    "info", f"{side} price recovered; Cut Loss timer reset."
                ))
        self._last = dict(results)
        return events

    def _failed(self, message: str) -> list[Event]:
        if message == self._error:
            return []  # already reported
        self._error = message
        return [Event("alert", message)]
