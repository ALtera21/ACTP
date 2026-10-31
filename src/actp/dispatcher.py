"""
Dispatcher: turns one pasted WhatsApp message into an action on the
ExecutionManager.

- parse_message() classifies the text.
- PLAN -> apply_plan, UPDATE -> update_side, CLOSE -> close_side.
- A fill confirmation is only noted. Fills are read from the broker, and
  the dispatcher never calls reconcile(): clearing a halt is a deliberate
  human action.

Message IDs are made here because a pasted message has none. They are
"<unix time>-<hash of the text>", which never contains ':'.

Two safety rules for manual pasting:
- Pasting the exact same text twice within DUPLICATE_WINDOW_SECONDS is
  rejected (a double click must not apply a plan twice).
- If applying a PLAN halted midway, sending the same text again REUSES the
  first message ID, so apply_plan resumes and places only the missing
  layers instead of starting a second plan.
"""
import hashlib
import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from actp.execution import ExecutionManager
from actp.parser import parse_message
from actp.broker import OrderStatus

DUPLICATE_WINDOW_SECONDS = 30

MAX_PLAN_ORDERS = 12  # the provider's 6 + 6; raise it on purpose if that changes

def _check_size(parsed: dict[str, Any]) -> None:
    if parsed["type"] != "PLAN":
        return
    total = sum(
        parsed[s]["layers"] for s in ("buy", "sell") if parsed[s] is not None
    )
    if total > MAX_PLAN_ORDERS:
        raise ValueError(
            f"Plan has {total} orders; the limit is {MAX_PLAN_ORDERS}."
        )

@dataclass(frozen=True)
class DispatchResult:
    kind: str
    message_id: str | None
    summary: str


def describe(parsed: dict[str, Any]) -> str:
    """Human-readable summary of a parsed message, for the confirm step."""
    kind = parsed["type"]
    if kind == "PLAN":
        lines = []
        total = 0
        for side in ("buy", "sell"):
            d = parsed[side]
            if d is None:
                continue
            total += d["layers"]
            lines.append(
                f"{side.upper()}: {d['layers']} layers, "
                f"area {d['area_start']} - {d['area_end']}, "
                f"cut loss {d['cut_loss']}, SL {d['sl']}, TP {d['tp']}"
            )
        header = f"PLAN {parsed['symbol']} - {total} pending orders"
        missing = [s.upper() for s in ("buy", "sell") if parsed[s] is None]
        if missing:
            lines.append(
                f"{'/'.join(missing)}: not in this plan, existing orders stay."
            )
        return "\n".join([header, *lines])
    if kind == "UPDATE":
        changes = ", ".join(
            f"{k.upper()} -> {v}" for k, v in parsed["changes"].items()
        )
        return f"UPDATE {parsed['side']}: {changes}"
    if kind == "CLOSE":
        side = parsed["side"]
        return (
            f"CLOSE {side}: close filled {side} positions only. "
            f"Pending {side} orders stay."
        )
    return "Fill confirmation (no action taken)"

def _digest(text: str) -> str:
    return hashlib.sha256(text.strip().encode("utf-8")).hexdigest()

class Dispatcher:
    def __init__(
        self,
        manager: ExecutionManager,
        clock: Callable[[], float] = time.time,
    ) -> None:
        self._manager = manager
        self._clock = clock
        self._recent: dict[str, float] = {}
        self._pending_ids: dict[str, str] = {}

    def preview(self, text: str) -> str:
        parsed = parse_message(text)
        _check_size(parsed)
        summary = describe(parsed)
        if parsed["type"] == "PLAN" and self._manager.check_stale:
            pending_id = self._pending_ids.get(_digest(text))
            check = self._manager.assess_plan(parsed, pending_id)
            summary += "\n\n" + check.describe()
        return summary

    def dispatch(self, text: str) -> DispatchResult:
        parsed = parse_message(text)
        _check_size(parsed)
        kind = parsed["type"]

        if kind == "FILL_CONFIRMATION":
            return DispatchResult(
                kind,
                None,
                "Fill confirmation noted. No action taken; fills are "
                "read from the broker.",
            )

        digest = _digest(text)
        now = self._clock()
        last = self._recent.get(digest)
        if last is not None and now - last < DUPLICATE_WINDOW_SECONDS:
            raise ValueError(
                "This exact message was just sent. Ignored as a duplicate."
            )

        message_id: str | None = None
        if kind == "PLAN":
            message_id = self._pending_ids.get(digest)
            if message_id is None:
                message_id = f"{int(now)}-{digest[:8]}"
                self._pending_ids[digest] = message_id
            orders = self._manager.apply_plan(message_id, parsed)
            self._pending_ids.pop(digest, None)
            market = sum(1 for o in orders if o.status != OrderStatus.PENDING)
            summary = f"Plan applied: {len(orders) - market} pending orders"
            if market:
                summary += f", {market} placed at market"
            summary += "."
        elif kind == "UPDATE":
            result = self._manager.update_side(
                parsed["side"], parsed["changes"]
            )
            skipped = result["skipped_fields"]
            summary = (
                f"Update {parsed['side']}: "
                f"{result['modified_orders']} modified, "
                f"{result['unchanged_orders']} unchanged"
                + (f", {skipped} value(s) not valid for some orders, "
                   "old value kept" if skipped else "")
                + "."
            )
        elif kind == "CLOSE":
            result = self._manager.close_side(
                parsed["side"], include_pending=False
            )
            summary = (
                f"Close {parsed['side']}: {result['closed']} closed, "
                f"{result['left_pending']} pending {parsed['side']} "
                "orders left in place."
            )
        else:
            raise ValueError(f"Unsupported message type: {kind}")

        self._recent[digest] = now
        return DispatchResult(kind, message_id, summary)

    def forget_pending(self) -> None:
        """Forget half-applied plans, e.g. after the kill switch."""
        self._pending_ids.clear()
