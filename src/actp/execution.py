"""
Execution layer: applies parsed plans and commands through a Broker.

Fail-safe policy:
- Every broker call goes through _call(). ANY BrokerError (unavailable or
  rejected) halts the manager immediately. No further plans, closes or
  updates run until reconcile() succeeds.
- The manager keeps no order state of its own. Every operation starts by
  reading list_orders() and acts on that snapshot, so the broker is the
  single source of truth.
- Orders are tagged with client_id "<message_id>:<index>", which makes
  apply_plan resumable: after a halt, retrying the same message places only
  the layers that are missing.
- Applied plans are saved through a PlanStore BEFORE any broker call, so a
  crash mid-way still leaves a record of what was intended.
- check_cut_loss() is deliberately allowed to run while halted: closing
  positions only reduces risk, and a halt must not leave them unprotected.
"""

import math
from collections.abc import Callable
from copy import deepcopy
from dataclasses import dataclass
from decimal import Decimal
from typing import Any, TypeVar

from actp.broker import (
    Broker,
    BrokerError,
    BrokerOrder,
    OrderRequest,
    OrderStatus,
    BrokerRejected,
    BrokerUnavailable
)
from actp.config import (
    CUT_LOSS_HOLD_SECONDS,
    SCL_FRACTION,
    MAX_MARKET_LAYERS_PER_SIDE,
    MAX_POLL_GAP_SECONDS,
    SYMBOL,
    SCL_ENABLED
)
from actp.levels import reached, scl_for
from actp.stale_plan import PlanCheck, check_plan, is_marketable
from actp.plan_builder import build_plan_preview
from actp.plan_store import PlanStore, StoredState
from actp.validator import _validate_price, validate_plan

T = TypeVar("T")

ACTIVE = (OrderStatus.PENDING, OrderStatus.FILLED)

# How many recent plans are kept in memory and on disk.
MAX_STORED_PLANS = 100


class ExecutionHalted(Exception):
    """Execution is halted. Call reconcile() before doing anything else."""


@dataclass(frozen=True)
class ReconcileResult:
    active_orders: list[BrokerOrder]
    # True when active orders belong to more than one message. This is
    # reported for alerting; the next apply_plan will clean it up.
    mixed_plans: bool


class ExecutionManager:
    def __init__(
        self,
        broker: Broker,
        store: PlanStore | None = None,
        *,
        check_stale: bool = False,
        market_cap: int = MAX_MARKET_LAYERS_PER_SIDE,
        scl_fraction: Decimal = SCL_FRACTION,
        scl_enabled: bool = SCL_ENABLED,
    ) -> None:
        scl_for("BUY", Decimal(2), Decimal(1), scl_fraction)  # validates
        self._broker = broker
        self._store = store
        self._check_stale = check_stale
        self._market_cap = market_cap
        self._scl_fraction = Decimal(scl_fraction)
        self._scl_enabled = bool(scl_enabled)
        self._plans: dict[str, dict[str, Any]] = {}
        self._completed: set[str] = set()
        self._current_message_id: str | None = None
        self._halt_reason: str | None = None
        # Side -> (time breach began, Cut Loss threshold)
        self._breaches: dict[str, tuple[float, Decimal]] = {}
        self._last_cut_loss_check: float | None = None
        # Side -> message id whose TP has already been moved to the first entry.
        self._tp_done: dict[str, str] = {}

        if store is not None:
            # A corrupt store raises PlanStoreError here: refuse to start
            # rather than run with unknown history.
            state = store.load()
            self._plans = dict(state.plans)
            self._completed = set(state.completed)
            self._current_message_id = state.current_message_id

    @property
    def halted(self) -> bool:
        return self._halt_reason is not None

    @property
    def check_stale(self) -> bool:
        return self._check_stale

    @property
    def halt_reason(self) -> str | None:
        return self._halt_reason

    # ---- Public operations -------------------------------------------

    def apply_plan(
        self,
        message_id: str,
        plan: dict[str, Any],
    ) -> list[BrokerOrder]:
        """
        Replace the active plan with this one.

        Safe to retry with the same message_id: orders that already exist
        are skipped, so a partial failure can be resumed without duplicates.
        A late retry of an older, superseded message never touches the
        broker.
        """
        self._ensure_running()
        _validate_message_id(message_id)
        validate_plan(plan)

        known = self._plans.get(message_id)
        if known is not None and known != plan:
            raise ValueError(
                "This message ID was already used for a different plan."
            )

        proposed = build_plan_preview(plan)
        if any(order["symbol"] != SYMBOL for order in proposed):
            raise ValueError("Refusing to place an unauthorized symbol.")

        if known is not None and (
            message_id in self._completed
            or message_id != self._current_message_id
        ):
            # Completed or superseded: a retry must not change anything.
            return self._orders_for(message_id)

        prefix = f"{message_id}:"
        sides_in_plan = {
            key.upper() for key in ("buy", "sell") if plan.get(key) is not None
        }
        snapshot = self._call("list_orders", self._broker.list_orders)

        # Stale-plan check. Runs BEFORE anything is saved or sent: a refusal
        # raises ValueError and leaves the current plan and the broker alone.
        if self._check_stale:
            tick = self._call(
                "get_tick", lambda: self._broker.get_tick(SYMBOL)
            )
            check_plan(
                plan, proposed, tick,
                skip=_placed_indexes(prefix, snapshot),
                cap=self._market_cap,
            )

        # Record the intent durably BEFORE touching the broker.
        self._plans[message_id] = deepcopy(plan)
        self._current_message_id = message_id
        for side in sides_in_plan:
            self._breaches.pop(side, None)
        self._persist()

        # Step 1: release active orders of the SAME SIDES that belong to
        # other plans. Orders on a side this plan does not mention stay.
        for order in snapshot:
            if (
                order.status in ACTIVE
                and order.side in sides_in_plan
                and not order.client_id.startswith(prefix)
            ):
                self._release(order)

        # Step 2: place any layer that does not exist yet. Each layer is
        # decided with a FRESH price, so a layer that crossed the market
        # while we were working is bought instead of sent as a bad limit.
        existing = {order.client_id for order in snapshot}
        market_count = {"BUY": 0, "SELL": 0}
        for index, item in enumerate(proposed):
            client_id = f"{prefix}{index}"
            if client_id in existing:
                continue
            request = OrderRequest(
                client_id=client_id,
                symbol=item["symbol"],
                side=item["side"],
                entry_price=item["entry_price"],
                volume=item["volume"],
                sl=item["sl"],
                tp=item["tp"],
            )
            at_market = False
            if self._check_stale:
                fresh = self._call(
                    "get_tick", lambda: self._broker.get_tick(SYMBOL)
                )
                at_market = is_marketable(
                    item["side"], item["entry_price"], fresh
                )
            if at_market:
                side = item["side"]
                market_count[side] += 1
                if market_count[side] > self._market_cap:
                    raise self._halt(
                        f"Price moved while placing: more than "
                        f"{self._market_cap} {side} layers would fill at "
                        "market. Check MT5, then Reconcile."
                    )
                self._call(
                    "place_market",
                    lambda r=request: self._broker.place_market(r),
                )
            else:
                self._call(
                    "place_pending",
                    lambda r=request: self._broker.place_pending(r),
                )

        self._completed.add(message_id)
        self._persist()
        return self._orders_for(message_id)

    def close_side(
        self, side: str, include_pending: bool = True
    ) -> dict[str, int]:
        """Close filled orders of one side. With include_pending=True
        (the default) pending orders are cancelled too."""
        self._ensure_running()
        normalized = _normalize_side(side)
        snapshot = self._call("list_orders", self._broker.list_orders)
        result = self._close_orders(
            normalized, snapshot, include_pending=include_pending
        )
        if not include_pending:
            result["left_pending"] = sum(
                1 for o in snapshot
                if o.side == normalized and o.status == OrderStatus.PENDING
            )
        return result

    def cancel_pending_side(self, side: str) -> dict[str, int]:
        """Cancel the pending orders of one side. Open positions stay."""
        self._ensure_running()
        normalized = _normalize_side(side)
        snapshot = self._call("list_orders", self._broker.list_orders)
        cancelled = 0
        for order in snapshot:
            if order.side == normalized and order.status == OrderStatus.PENDING:
                self._release(order)
                cancelled += 1
        left_open = sum(
            1 for o in snapshot
            if o.side == normalized and o.status == OrderStatus.FILLED
        )
        return {"cancelled": cancelled, "left_open": left_open}

    def update_side(
        self,
        side: str,
        changes: dict[str, Any],
    ) -> dict[str, int]:
        """
        Update SL and/or TP for one side.

        Every change is validated against every eligible order before the
        first modification is sent.
        """
        self._ensure_running()
        normalized = _normalize_side(side)
        if not isinstance(changes, dict) or not changes:
            raise ValueError("changes must contain SL and/or TP.")
        if not set(changes).issubset({"sl", "tp"}):
            raise ValueError("Only sl and tp can be updated.")
        for field, value in changes.items():
            _validate_price(value, field, normalized)

        snapshot = self._call("list_orders", self._broker.list_orders)
        eligible = [
            order for order in snapshot
            if order.side == normalized and order.status in ACTIVE
        ]

        # Decide every order's new values BEFORE sending anything.
        # A field that is not valid for an order keeps its old value.
        tick = None
        skipped = 0
        plan_ops = []
        for order in eligible:
            if order.status == OrderStatus.FILLED and tick is None:
                tick = self._call(
                    "get_tick", lambda: self._broker.get_tick(SYMBOL)
                )
            new_sl, new_tp = order.sl, order.tp
            for field, value in changes.items():
                if _update_allowed(normalized, field, value, order, tick):
                    if field == "sl":
                        new_sl = value
                    else:
                        new_tp = value
                else:
                    skipped += 1
            plan_ops.append((order, new_sl, new_tp))

        modified = 0
        unchanged = 0
        for order, new_sl, new_tp in plan_ops:
            if new_sl == order.sl and new_tp == order.tp:
                unchanged += 1
                continue
            self._call(
                "modify_sltp",
                lambda o=order, s=new_sl, t=new_tp: self._broker.modify_sltp(
                    o.ticket, s, t
                ),
            )
            modified += 1
        return {
            "modified_orders": modified,
            "unchanged_orders": unchanged,
            "skipped_fields": skipped,
        }

    def check_cut_loss(self, now: float) -> dict[str, str]:
        """
        Check the current tick against the Cut Loss of each active side.

        Pass a monotonic timestamp (time.monotonic()). A breach must last
        CUT_LOSS_HOLD_SECONDS without interruption before the side is
        closed. If checks are further apart than MAX_POLL_GAP_SECONDS, the
        breach was not observed continuously and its timer restarts.

        BUY uses the bid and SELL uses the ask. The Cut Loss level comes
        from the stored plan behind each side's active orders. If that
        cannot be determined, the manager halts and raises.

        Runs even while halted: closing only reduces risk.
        Closing needs BOTH: the breach has lasted the full hold time, AND
        price is at/beyond the SCL line (between Cut Loss and SL) at that
        moment. SCL is not latched: touching it earlier does not count.
        If the hold time is over but SCL is not reached, the side is
        HOLDING and closes the first poll where price reaches SCL, as long
        as the breach stays unbroken.

        Returns, per side: WAITING (hold timer running), HOLDING (hold
        over, waiting for SCL), RECOVERED or CLOSED.
        """
        if (
            isinstance(now, bool)
            or not isinstance(now, (int, float))
            or not math.isfinite(now)
        ):
            raise ValueError("now must be a finite timestamp.")
        last_check = self._last_cut_loss_check
        if last_check is not None:
            if now < last_check:
                raise ValueError("now must not go backwards.")
            if now - last_check > MAX_POLL_GAP_SECONDS:
                self._breaches.clear()
        self._last_cut_loss_check = float(now)

        tick = self._call(
            "get_tick", lambda: self._broker.get_tick(SYMBOL)
        )
        for name, price in (("bid", tick.bid), ("ask", tick.ask)):
            if (
                not isinstance(price, Decimal)
                or not price.is_finite()
                or price <= 0
            ):
                raise self._halt(f"Invalid {name} price from broker: {price}")
        snapshot = self._call("list_orders", self._broker.list_orders)

        results: dict[str, str] = {}
        for side, price in (("BUY", tick.bid), ("SELL", tick.ask)):
            active = [
                order
                for order in snapshot
                if order.side == side and order.status in ACTIVE
            ]
            if not active:
                self._breaches.pop(side, None)
                continue

            threshold, scl = self._levels_for(side, active)
            breached = reached(side, price, threshold)
            if not breached:
                if self._breaches.pop(side, None) is not None:
                    results[side] = "RECOVERED"
                continue

            previous = self._breaches.get(side)
            if previous is None or previous[1] != threshold:
                self._breaches[side] = (float(now), threshold)
                results[side] = "WAITING"
                continue
            if now - previous[0] < CUT_LOSS_HOLD_SECONDS:
                results[side] = "WAITING"
                continue
            if not reached(side, price, scl):
                results[side] = "HOLDING"
                continue

            self._close_orders(side, snapshot)
            self._breaches.pop(side, None)
            results[side] = "CLOSED"
        return results

    def reconcile(self) -> ReconcileResult:
        """
        Read the real state from the broker and clear the halt.

        If the broker cannot be read, the manager stays halted.
        """
        try:
            orders = self._broker.list_orders()
        except BrokerError as exc:
            raise ExecutionHalted(
                f"Reconcile failed; still halted: {exc}"
            ) from exc

        active = [order for order in orders if order.status in ACTIVE]
        mixed = any(
            len({
                o.client_id.rpartition(":")[0]
                for o in active if o.side == side
            }) > 1
            for side in ("BUY", "SELL")
        )
        self._halt_reason = None
        return ReconcileResult(active_orders=active, mixed_plans=mixed)

    def close_all(self) -> dict[str, int]:
        """
        Kill switch: cancel every pending and close every filled ACTP order.

        Allowed while halted, because it only reduces risk. If the broker
        fails, the manager halts again and raises. The current plan is
        marked completed so a late retry cannot re-place its layers.
        """
        snapshot = self._call("list_orders", self._broker.list_orders)
        total = {"cancelled": 0, "closed": 0}
        for side in ("BUY", "SELL"):
            partial = self._close_orders(side, snapshot)
            total["cancelled"] += partial["cancelled"]
            total["closed"] += partial["closed"]
        self._breaches.clear()
        if self._current_message_id is not None:
            self._completed.add(self._current_message_id)
            self._persist()
        return total

    def close_positions(self, only_profitable: bool = False) -> dict[str, int]:
        """Close open ACTP positions of both sides. Pending orders stay.
        With only_profitable=True, positions in profit are closed and
        the rest stay open. Allowed while halted: it only reduces risk."""
        snapshot = self._call("list_orders", self._broker.list_orders)
        tick = None
        if only_profitable:
            tick = self._call(
                "get_tick", lambda: self._broker.get_tick(SYMBOL)
            )
        closed = left_open = 0
        for order in snapshot:
            if order.status != OrderStatus.FILLED:
                continue
            if only_profitable and not position_in_profit(order, tick):
                left_open += 1
                continue
            self._release(order)
            closed += 1
        return {
            "closed": closed,
            "left_open": left_open,
            "left_pending": sum(
                1 for o in snapshot if o.status == OrderStatus.PENDING
            ),
        }

    def move_tp_when_full(self) -> dict[str, tuple[str, Decimal, int]]:
        """When every layer of a side is FILLED, move that side's TP to the
        plan's first entry (BUY: highest layer, SELL: lowest). Once per
        plan and side. Returns side -> (state, target, positions modified)
        for MOVED, PRICE_PAST (price already beyond the new TP, will retry)
        and REFUSED (MT5 said no, will retry). Does nothing while halted."""
        if self._halt_reason is not None:
            return {}
        snapshot = self._call("list_orders", self._broker.list_orders)
        tick = None
        results: dict[str, tuple[str, Decimal, int]] = {}
        for side in ("BUY", "SELL"):
            active = [
                o for o in snapshot
                if o.side == side and o.status in ACTIVE
            ]
            if not active:
                self._tp_done.pop(side, None)
                continue
            message_ids = {o.client_id.rpartition(":")[0] for o in active}
            if len(message_ids) != 1:
                continue
            message_id = next(iter(message_ids))
            plan = self._plans.get(message_id)
            section = None if plan is None else plan.get(side.lower())
            if section is None or self._tp_done.get(side) == message_id:
                continue
            if len(active) != section["layers"] or any(
                o.status != OrderStatus.FILLED for o in active
            ):
                continue
            prices = (section["area_start"], section["area_end"])
            target = max(prices) if side == "BUY" else min(prices)
            todo = [o for o in active if o.tp != target]
            if not todo:
                self._tp_done[side] = message_id
                continue
            if tick is None:
                tick = self._call(
                    "get_tick", lambda: self._broker.get_tick(SYMBOL)
                )
            in_range = tick.bid < target if side == "BUY" else tick.ask > target
            if not in_range:
                results[side] = ("PRICE_PAST", target, 0)
                continue
            modified = 0
            refused = False
            for order in todo:
                try:
                    self._broker.modify_sltp(order.ticket, order.sl, target)
                except BrokerRejected:
                    refused = True  # definitely not applied; retry later
                except BrokerUnavailable as exc:
                    raise self._halt(f"modify_sltp failed: {exc}") from exc
                else:
                    modified += 1
            if refused:
                results[side] = ("REFUSED", target, modified)
            else:
                self._tp_done[side] = message_id
                results[side] = ("MOVED", target, modified)
        return results

    def assess_plan(
        self,
        plan: dict[str, Any],
        message_id: str | None = None,
    ) -> PlanCheck:
        """
        Compare a plan with the live price without sending anything.
        Raises ValueError if the plan would be refused. `message_id` is the
        ID of a half-applied earlier attempt, so placed layers aren't counted.
        """
        validate_plan(plan)
        proposed = build_plan_preview(plan)
        snapshot = self._call("list_orders", self._broker.list_orders)
        tick = self._call("get_tick", lambda: self._broker.get_tick(SYMBOL))
        skip = (
            _placed_indexes(f"{message_id}:", snapshot) if message_id
            else frozenset()
        )
        return check_plan(plan, proposed, tick, skip=skip, cap=self._market_cap)

    def snapshot(self) -> dict[str, Any]:
        """Read-only view for status displays. Never halts the manager."""
        try:
            orders = self._broker.list_orders()
        except BrokerError as exc:
            return {"error": str(exc)}
        try:
            tick = self._broker.get_tick(SYMBOL)
        except BrokerError:
            tick = None
        return {
            "active": [o for o in orders if o.status in ACTIVE],
            "tick": tick,
        }

    # ---- Internals ---------------------------------------------------

    def _ensure_running(self) -> None:
        if self._halt_reason is not None:
            raise ExecutionHalted(
                f"Execution halted ({self._halt_reason}). "
                "Call reconcile() first."
            )

    def _halt(self, reason: str) -> ExecutionHalted:
        self._halt_reason = reason
        return ExecutionHalted(reason)

    def _call(self, description: str, action: Callable[[], T]) -> T:
        try:
            return action()
        except BrokerError as exc:
            raise self._halt(f"{description} failed: {exc}") from exc

    def _release(self, order: BrokerOrder) -> str:
        """Cancel a pending order or close a filled one."""
        if order.status == OrderStatus.PENDING:
            self._call(
                "cancel_pending",
                lambda: self._broker.cancel_pending(order.ticket),
            )
            return "cancelled"
        self._call(
            "close_position",
            lambda: self._broker.close_position(order.ticket),
        )
        return "closed"

    def _close_orders(
        self,
        side: str,
        snapshot: list[BrokerOrder],
        include_pending: bool = True,
    ) -> dict[str, int]:
        result = {"cancelled": 0, "closed": 0}
        for order in snapshot:
            if order.side != side or order.status not in ACTIVE:
                continue
            if order.status == OrderStatus.PENDING and not include_pending:
                continue
            result[self._release(order)] += 1
        return result

    def _orders_for(self, message_id: str) -> list[BrokerOrder]:
        prefix = f"{message_id}:"
        snapshot = self._call("list_orders", self._broker.list_orders)
        return [o for o in snapshot if o.client_id.startswith(prefix)]

    def _levels_for(
        self,
        side: str,
        active: list[BrokerOrder],
    ) -> tuple[Decimal, Decimal]:
        """(Cut Loss, SCL) from the stored plan behind the active orders."""
        message_ids = {o.client_id.rpartition(":")[0] for o in active}
        if len(message_ids) != 1:
            raise self._halt(
                f"Active {side} orders belong to {len(message_ids)} "
                "different plans; cannot choose a Cut Loss."
            )
        message_id = next(iter(message_ids))
        plan = self._plans.get(message_id)
        section = None if plan is None else plan.get(side.lower())
        if section is None:
            raise self._halt(
                f"No stored Cut Loss for active {side} orders "
                f"(message '{message_id}')."
            )
        cut_loss = section["cut_loss"]
        scl = (
            scl_for(side, cut_loss, section["sl"], self._scl_fraction)
            if self._scl_enabled
            else cut_loss
        )
        return cut_loss, scl

    def chart_levels(self) -> dict[str, tuple[Decimal, Decimal]] | None:
        """
        {side: (Cut Loss, SCL)} for sides that have active orders, for the
        chart lines. Read-only: never halts. None if it cannot be
        determined right now (broker error), so callers keep what they
        last drew.
        """
        try:
            snapshot = self._broker.list_orders()
        except Exception:
            return None
        levels: dict[str, tuple[Decimal, Decimal]] = {}
        for side in ("BUY", "SELL"):
            active = [
                o for o in snapshot
                if o.side == side and o.status in ACTIVE
            ]
            if not active:
                continue
            ids = {o.client_id.rpartition(":")[0] for o in active}
            if len(ids) != 1:
                return None
            plan = self._plans.get(next(iter(ids)))
            section = None if plan is None else plan.get(side.lower())
            if section is None:
                return None
            levels[side] = self._levels_for(side, active)
        return levels

    def _prune(self) -> None:
        while len(self._plans) > MAX_STORED_PLANS:
            oldest = next(iter(self._plans))
            if oldest == self._current_message_id:
                break
            del self._plans[oldest]
            self._completed.discard(oldest)

    def _persist(self) -> None:
        if self._store is None:
            return
        self._prune()
        state = StoredState(
            current_message_id=self._current_message_id,
            plans=deepcopy(self._plans),
            completed=set(self._completed),
        )
        try:
            self._store.save(state)
        except OSError as exc:
            raise self._halt(f"saving plan state failed: {exc}") from exc


def _validate_message_id(message_id: Any) -> None:
    if not isinstance(message_id, str) or not message_id.strip():
        raise ValueError("message_id must be a non-empty string.")
    if ":" in message_id:
        raise ValueError("message_id must not contain ':'.")


def _normalize_side(side: Any) -> str:
    if not isinstance(side, str) or side.upper() not in ("BUY", "SELL"):
        raise ValueError("side must be BUY or SELL.")
    return side.upper()

def _placed_indexes(
    prefix: str, snapshot: list[BrokerOrder]
) -> frozenset[int]:
    """Layer indexes of this message that already exist at the broker."""
    return frozenset(
        int(o.client_id[len(prefix):])
        for o in snapshot
        if o.client_id.startswith(prefix)
    )

def _update_allowed(
    side: str, field: str, value: Decimal, order: BrokerOrder, tick: Any
) -> bool:
    """Pending orders are checked against their entry, open positions
    against the live price (BUY closes at the bid, SELL at the ask)."""
    if order.status == OrderStatus.PENDING:
        ref = order.entry_price
    else:
        ref = tick.bid if side == "BUY" else tick.ask
    if field == "sl":
        return value < ref if side == "BUY" else value > ref
    return value > ref if side == "BUY" else value < ref

def position_in_profit(order: BrokerOrder, tick: Any) -> bool:
    """True if closing now beats the entry price (BUY closes at the bid,
    SELL at the ask). Spread is included; swap and commission are not."""
    if order.side == "BUY":
        return tick.bid > order.entry_price
    return tick.ask < order.entry_price