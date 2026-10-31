"""
Durable storage for applied plans.

The ExecutionManager needs this to survive a restart:
- which messages it has already handled (so a late retry cannot replace
  the live plan), and
- the Cut Loss levels of the plans behind the active orders (the broker
  never sees them).

Fail-safe rules:
- Writes are atomic (temp file, fsync, rename), so a crash cannot leave a
  half-written file.
- A corrupt or tampered file is NEVER silently replaced by an empty state.
  load() raises PlanStoreError and the operator decides what to do.
"""

import json
import os
from dataclasses import dataclass, field
from decimal import Decimal
from pathlib import Path
from typing import Any, Protocol

from actp.validator import validate_plan

STATE_VERSION = 1
PRICE_FIELDS = ("area_start", "area_end", "cut_loss", "sl", "tp")


class PlanStoreError(Exception):
    """The stored state is unreadable or invalid. Do not guess."""


@dataclass
class StoredState:
    current_message_id: str | None = None
    # Insertion order is the order messages were first seen (oldest first).
    plans: dict[str, dict[str, Any]] = field(default_factory=dict)
    completed: set[str] = field(default_factory=set)


class PlanStore(Protocol):
    def load(self) -> StoredState:
        ...

    def save(self, state: StoredState) -> None:
        ...


class JsonPlanStore:
    def __init__(self, path: str | Path) -> None:
        self._path = Path(path)

    def load(self) -> StoredState:
        if not self._path.exists():
            return StoredState()
        try:
            raw = json.loads(self._path.read_text(encoding="utf-8"))
            return _decode_state(raw)
        except (
            OSError,
            ValueError,
            KeyError,
            TypeError,
            AttributeError,
            ArithmeticError,
        ) as exc:
            raise PlanStoreError(f"Cannot read {self._path}: {exc}") from exc

    def save(self, state: StoredState) -> None:
        payload = json.dumps(_encode_state(state), indent=2)
        self._path.parent.mkdir(parents=True, exist_ok=True)
        temp = self._path.with_name(self._path.name + ".tmp")
        with open(temp, "w", encoding="utf-8") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp, self._path)


def _encode_state(state: StoredState) -> dict[str, Any]:
    return {
        "version": STATE_VERSION,
        "current_message_id": state.current_message_id,
        "plans": [
            {
                "message_id": message_id,
                "completed": message_id in state.completed,
                "plan": _encode_plan(plan),
            }
            for message_id, plan in state.plans.items()
        ],
    }


def _decode_state(raw: Any) -> StoredState:
    if raw["version"] != STATE_VERSION:
        raise ValueError(f"Unsupported state version: {raw['version']}")
    current = raw["current_message_id"]
    plans: dict[str, dict[str, Any]] = {}
    completed: set[str] = set()
    for item in raw["plans"]:
        message_id = item["message_id"]
        if not isinstance(message_id, str) or message_id in plans:
            raise ValueError("Invalid or duplicate message_id.")
        plan = _decode_plan(item["plan"])
        validate_plan(plan)
        plans[message_id] = plan
        if item["completed"] is True:
            completed.add(message_id)
    if current is not None and current not in plans:
        raise ValueError("current_message_id has no stored plan.")
    return StoredState(
        current_message_id=current,
        plans=plans,
        completed=completed,
    )


def _encode_plan(plan: dict[str, Any]) -> dict[str, Any]:
    encoded = dict(plan)
    for side in ("buy", "sell"):
        section = plan.get(side)
        if section is None:
            encoded[side] = None
            continue
        encoded[side] = {
            key: str(value) if key in PRICE_FIELDS else value
            for key, value in section.items()
        }
    return encoded


def _decode_plan(raw: dict[str, Any]) -> dict[str, Any]:
    plan = dict(raw)
    for side in ("buy", "sell"):
        section = raw.get(side)
        if section is None:
            plan[side] = None
            continue
        decoded = dict(section)
        for name in PRICE_FIELDS:
            value = section[name]
            if not isinstance(value, str):
                raise ValueError(f"{side} {name} must be stored as text.")
            decoded[name] = Decimal(value)
        plan[side] = decoded
    return plan
