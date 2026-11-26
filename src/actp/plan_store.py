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

from actp.timeframes import TIMEFRAMES
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
    # Display names (Alpha, Beta, ...) by message id. Old files have none.
    names: dict[str, str] = field(default_factory=dict)
    # Candle cut-loss rule by message id: {"tf": "M5", "first": <open time of
    # the first candle that counts>}. A plan without an entry uses the legacy
    # hold-timer rule.
    cut_rules: dict[str, dict[str, Any]] = field(default_factory=dict)
    # TP levels set by hand that ACTP watches itself: message id -> {"BUY":
    # price}. Old files have none.
    tp_targets: dict[str, dict[str, Decimal]] = field(default_factory=dict)
    # Deepest the price went into a plan side's range, percent: message id ->
    # {"BUY": 63.5}. Kept so that a restart does not forget a dip.
    depths: dict[str, dict[str, Decimal]] = field(default_factory=dict)
    # The base lot (the Lot box) each plan was sent with, so a re-entry can
    # use "the same lot as before". Old files have none.
    base_lots: dict[str, Decimal] = field(default_factory=dict)


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
        "names": {m: n for m, n in state.names.items() if m in state.plans},
        "cut_rules": {
            m: r for m, r in state.cut_rules.items() if m in state.plans
        },
        "base_lots": {
            m: str(lot) for m, lot in state.base_lots.items()
            if m in state.plans
        },
        "depths": {
            m: {side: str(depth) for side, depth in sides.items()}
            for m, sides in state.depths.items()
            if m in state.plans and sides
        },
        "tp_targets": {
            m: {side: str(price) for side, price in sides.items()}
            for m, sides in state.tp_targets.items()
            if m in state.plans and sides
        },
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
        validate_plan(plan, stored=True)
        plans[message_id] = plan
        if item["completed"] is True:
            completed.add(message_id)
    if current is not None and current not in plans:
        raise ValueError("current_message_id has no stored plan.")
    names = raw.get("names", {})
    if not isinstance(names, dict) or not all(
        isinstance(k, str) and isinstance(v, str) for k, v in names.items()
    ):
        raise ValueError("Invalid plan names.")
    rules = raw.get("cut_rules", {})
    if not isinstance(rules, dict):
        raise ValueError("Invalid cut loss rules.")
    for rule in rules.values():
        if (
            not isinstance(rule, dict)
            or rule.get("tf") not in TIMEFRAMES
            or isinstance(rule.get("first"), bool)
            or not isinstance(rule.get("first"), int)
        ):
            raise ValueError("Invalid cut loss rule.")
    targets_raw = raw.get("tp_targets", {})
    if not isinstance(targets_raw, dict):
        raise ValueError("Invalid TP targets.")
    targets: dict[str, dict[str, Decimal]] = {}
    for message_id, sides in targets_raw.items():
        if message_id not in plans:
            continue
        if not isinstance(sides, dict):
            raise ValueError("Invalid TP targets.")
        decoded: dict[str, Decimal] = {}
        for side, price in sides.items():
            value = Decimal(price)  # InvalidOperation is an ArithmeticError
            if side not in ("BUY", "SELL") or not value.is_finite() or value <= 0:
                raise ValueError("Invalid TP target.")
            decoded[side] = value
        if decoded:
            targets[message_id] = decoded
    depths_raw = raw.get("depths", {})
    if not isinstance(depths_raw, dict):
        raise ValueError("Invalid depths.")
    depths: dict[str, dict[str, Decimal]] = {}
    for message_id, sides in depths_raw.items():
        if message_id not in plans:
            continue
        if not isinstance(sides, dict):
            raise ValueError("Invalid depths.")
        decoded_depths: dict[str, Decimal] = {}
        for side, depth in sides.items():
            value = Decimal(depth)
            if (side not in ("BUY", "SELL") or not value.is_finite()
                    or not 0 <= value <= 100):
                raise ValueError("Invalid depth.")
            decoded_depths[side] = value
        if decoded_depths:
            depths[message_id] = decoded_depths
    lots_raw = raw.get("base_lots", {})
    if not isinstance(lots_raw, dict):
        raise ValueError("Invalid base lots.")
    base_lots: dict[str, Decimal] = {}
    for message_id, lot in lots_raw.items():
        value = Decimal(lot)
        if not value.is_finite() or value <= 0:
            raise ValueError("Invalid base lot.")
        if message_id in plans:
            base_lots[message_id] = value
    return StoredState(
        current_message_id=current,
        plans=plans,
        completed=completed,
        names={m: n for m, n in names.items() if m in plans},
        cut_rules={
            m: {"tf": r["tf"], "first": r["first"]}
            for m, r in rules.items() if m in plans
        },
        tp_targets=targets,
        depths=depths,
        base_lots=base_lots,
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
