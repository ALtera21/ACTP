"""Which actions ask 'are you sure?', and the 5-digit code used instead.

Pure logic (no tkinter) so it can be tested. When an action's confirmation
is ON the normal OK / Cancel popup is shown. Turning one OFF (in Settings)
needs a random 5-digit code, so a stray click cannot remove the safety; once
off, the action runs with no popup.
"""

from __future__ import annotations

import secrets
from typing import Any

# key -> label shown in Settings (order = display order)
ACTIONS: dict[str, str] = {
    "send": "Send plan",
    "cancel_orders": "Cancel PO buy / sell",
    "kill": "KILL ALL",
    "close_all": "Close all positions",
    "close_profitable": "Close profitable only",
    "close_plan_side": "Plan table: cancel PO + close one side",
    "close_plan": "Plan table: cancel entire plan",
    "reenter": "Plan table: re-entry (refresh one side of a plan)",
    "tp_past": "Plan table: Update TP when the price is already past it",
    "quit": "Quit ACTP",
}

CODE_LENGTH = 5


def defaults() -> dict[str, bool]:
    return {key: True for key in ACTIONS}


def validate(raw: Any) -> dict[str, bool]:
    """Missing keys = ON (the safe default). Wrong types are an error."""
    if raw is None:
        return defaults()
    if not isinstance(raw, dict):
        raise ValueError("Popup confirmation must be a list of on/off values.")
    out = defaults()
    for key, value in raw.items():
        if key not in ACTIONS:
            raise ValueError(f"Popup confirmation: unknown action '{key}'.")
        if not isinstance(value, bool):
            raise ValueError(f"Popup confirmation '{key}' must be on or off.")
        out[key] = value
    return out


def parent_on(values: dict[str, bool]) -> bool:
    """The master checkbox is ticked while at least one action asks."""
    return any(values.values())


def set_all(on: bool) -> dict[str, bool]:
    return {key: on for key in ACTIONS}


def new_code(rng=None) -> str:
    """5 random digits (leading zero allowed)."""
    pick = rng or secrets.SystemRandom()
    return "".join(str(pick.randrange(10)) for _ in range(CODE_LENGTH))


def code_matches(typed: str, code: str) -> bool:
    return typed.strip() == code
