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
import re
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
from actp.timeframes import TIMEFRAME_SECONDS, TIMEFRAMES
from actp.stale_plan import PlanCheck, check_plan, is_marketable
from actp.plan_builder import build_plan_preview
from actp.plan_store import PlanStore, StoredState
from actp.validator import _validate_price, validate_plan

T = TypeVar("T")

ACTIVE = (OrderStatus.PENDING, OrderStatus.FILLED)

# Display names for plans, in order. The MT5 comment still carries the unique
# message id; the name is only for the window and the log.
PLAN_NAMES = (
    "Alpha", "Beta", "Gamma", "Delta", "Epsilon", "Zeta", "Eta", "Theta",
    "Iota", "Kappa", "Lambda", "Mu", "Nu", "Xi", "Omicron", "Pi", "Rho",
    "Sigma", "Tau", "Upsilon", "Phi", "Chi", "Psi", "Omega",
)

# How many recent plans are kept in memory and on disk.
MAX_STORED_PLANS = 100


class ExecutionHalted(Exception):
    """Execution is halted. Call reconcile() before doing anything else."""


@dataclass(frozen=True)
class ReconcileResult:
    active_orders: list[BrokerOrder]


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
        cut_loss_mode: str = "legacy",
        cut_loss_timeframe: str = "M1",
    ) -> None:
        scl_for("BUY", Decimal(2), Decimal(1), scl_fraction)  # validates
        if cut_loss_mode not in ("legacy", "candle"):
            raise ValueError("cut_loss_mode must be 'legacy' or 'candle'.")
        if cut_loss_timeframe not in TIMEFRAMES:
            raise ValueError(f"Unknown cut loss timeframe {cut_loss_timeframe!r}.")
        # The mode and timeframe are read when a plan is APPLIED and stored
        # with that plan, so every plan keeps the rule it started with.
        self._cut_mode = cut_loss_mode
        self._cut_timeframe = cut_loss_timeframe
        self._broker = broker
        self._store = store
        self._check_stale = check_stale
        self._market_cap = market_cap
        self._scl_fraction = Decimal(scl_fraction)
        self._scl_enabled = bool(scl_enabled)
        self._plans: dict[str, dict[str, Any]] = {}
        self._completed: set[str] = set()
        self._names: dict[str, str] = {}
        self._cut_rules: dict[str, dict[str, Any]] = {}
        self._current_message_id: str | None = None
        self._halt_reason: str | None = None
        # (side, message id) -> (time breach began, Cut Loss threshold).
        # Every plan keeps its own timer.
        self._breaches: dict[tuple[str, str], tuple[float, Decimal]] = {}
        self._last_cut_loss_check: float | None = None
        # (side, message id) pairs whose TP was already moved to the first entry.
        self._tp_done: set[tuple[str, str]] = set()

        if store is not None:
            # A corrupt store raises PlanStoreError here: refuse to start
            # rather than run with unknown history.
            state = store.load()
            self._plans = dict(state.plans)
            self._completed = set(state.completed)
            self._names = dict(state.names)
            self._cut_rules = {m: dict(r) for m, r in state.cut_rules.items()}
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
        Add this plan. Plans are independent: older plans stay live (their
        orders, Cut Loss and Auto-TP keep working) until cancelled by the
        Cancel buttons or the kill switch.

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
        if message_id not in self._names:
            own = short_id(message_id)
            self._names[message_id] = (
                own if own in PLAN_NAMES
                else self._next_name(message_id, snapshot))
        if known is None and self._cut_mode == "candle":
            self._cut_rules[message_id] = self._new_cut_rule()
        self._plans[message_id] = deepcopy(plan)
        self._current_message_id = message_id
        self._persist()

        # Place any layer that does not exist yet. Each layer is
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
        # Only the most recent plan that has this side is changed.
        target = self._latest_plan_with(normalized)
        eligible = [
            order for order in snapshot
            if order.side == normalized and order.status in ACTIVE
            and target is not None and _plan_id(order) == target
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

        Every plan is checked on its own, with its own Cut Loss and timer.
        The result key is the side ("BUY") when a side has one plan, and
        "BUY #<plan id>" when it has several.

        Returns, per plan: WAITING (hold timer running), HOLDING (hold
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
        live: set[tuple[str, str]] = set()
        last_closed: dict[str, Any] = {}  # timeframe -> last completed candle
        for side, price in (("BUY", tick.bid), ("SELL", tick.ask)):
            groups = self._groups(side, snapshot)
            for message_id, _active in groups:
                key = (side, message_id)
                live.add(key)
                label = self._label(side, message_id, len(groups))
                threshold, scl = self._levels_for(side, message_id)
                rule = self._cut_rules.get(message_id)
                if rule is not None:
                    # Candle rule: only a COMPLETED candle closing beyond the
                    # Cut Loss counts. Touching it mid-candle does nothing,
                    # and SCL / the hold timer are not used.
                    if self._candle_breach(
                        side, rule, threshold, last_closed
                    ):
                        self._close_orders(side, snapshot, message_id=message_id)
                        results[label] = "CLOSED_CANDLE"
                    continue
                breached = reached(side, price, threshold)
                if not breached:
                    if self._breaches.pop(key, None) is not None:
                        results[label] = "RECOVERED"
                    continue

                previous = self._breaches.get(key)
                if previous is None or previous[1] != threshold:
                    self._breaches[key] = (float(now), threshold)
                    results[label] = "WAITING"
                    continue
                if now - previous[0] < CUT_LOSS_HOLD_SECONDS:
                    results[label] = "WAITING"
                    continue
                if not reached(side, price, scl):
                    results[label] = "HOLDING"
                    continue

                self._close_orders(side, snapshot, message_id=message_id)
                self._breaches.pop(key, None)
                results[label] = "CLOSED"
        for key in [k for k in self._breaches if k not in live]:
            del self._breaches[key]  # that plan has no active orders left
        return results

    def _candle_breach(
        self,
        side: str,
        rule: dict[str, Any],
        cut_loss: Decimal,
        cache: dict[str, Any],
    ) -> bool:
        tf = rule["tf"]
        if tf not in cache:
            candles = self._call(
                "get_candles", lambda: self._broker.get_candles(SYMBOL, tf, 2)
            )
            cache[tf] = candles[-2] if len(candles) >= 2 else None
        candle = cache[tf]
        if candle is None or candle.open_time < rule["first"]:
            return False
        return candle.close < cut_loss if side == "BUY" else candle.close > cut_loss

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
        self._halt_reason = None
        return ReconcileResult(active_orders=active)

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

    def set_cut_loss_timeframe(self, timeframe: str) -> None:
        """Candle timeframe for plans applied from now on (plans already
        placed keep the one they were sent with)."""
        if timeframe not in TIMEFRAMES:
            raise ValueError(f"Unknown cut loss timeframe {timeframe!r}.")
        self._cut_timeframe = timeframe

    def close_plan(
        self, message_id: str, side: str | None = None
    ) -> dict[str, int]:
        """Cancel the pending orders and close the open positions of ONE
        plan: only one side of it, or (side=None) the whole plan. Other
        plans are not touched. Allowed while halted: it only reduces risk.
        A whole plan is also marked completed so a late retry of its message
        cannot place its layers again."""
        sides = ("BUY", "SELL") if side is None else (_normalize_side(side),)
        snapshot = self._call("list_orders", self._broker.list_orders)
        total = {"cancelled": 0, "closed": 0}
        for one in sides:
            part = self._close_orders(one, snapshot, message_id=message_id)
            total["cancelled"] += part["cancelled"]
            total["closed"] += part["closed"]
            self._breaches.pop((one, message_id), None)
        if side is None and message_id in self._plans:
            self._completed.add(message_id)
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
        """When every layer of a plan's side is FILLED, move that plan's TP
        to its first entry (BUY: highest layer, SELL: lowest). Once per plan
        and side; every plan is handled on its own. Returns label ->
        (state, target, positions modified) for MOVED, PRICE_PAST (price
        already beyond the new TP, will retry) and REFUSED (MT5 said no,
        will retry). The label is the side, or "BUY #<plan id>" when a side
        has several plans. Does nothing while halted."""
        if self._halt_reason is not None:
            return {}
        snapshot = self._call("list_orders", self._broker.list_orders)
        tick = None
        results: dict[str, tuple[str, Decimal, int]] = {}
        live: set[tuple[str, str]] = set()
        for side in ("BUY", "SELL"):
            groups = self._groups(side, snapshot)
            for message_id, active in groups:
                key = (side, message_id)
                live.add(key)
                label = self._label(side, message_id, len(groups))
                plan = self._plans.get(message_id)
                section = None if plan is None else plan.get(side.lower())
                if section is None or key in self._tp_done:
                    continue
                if len(active) != section["layers"] or any(
                    o.status != OrderStatus.FILLED for o in active
                ):
                    continue
                prices = (section["area_start"], section["area_end"])
                target = max(prices) if side == "BUY" else min(prices)
                todo = [o for o in active if o.tp != target]
                if not todo:
                    self._tp_done.add(key)
                    continue
                if tick is None:
                    tick = self._call(
                        "get_tick", lambda: self._broker.get_tick(SYMBOL)
                    )
                in_range = (
                    tick.bid < target if side == "BUY" else tick.ask > target
                )
                if not in_range:
                    results[label] = ("PRICE_PAST", target, 0)
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
                    results[label] = ("REFUSED", target, modified)
                else:
                    self._tp_done.add(key)
                    results[label] = ("MOVED", target, modified)
        self._tp_done &= live  # forget plans that have no active orders
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
        message_id: str | None = None,
    ) -> dict[str, int]:
        """Cancel/close the active orders of one side. With message_id, only
        that plan's orders."""
        result = {"cancelled": 0, "closed": 0}
        for order in snapshot:
            if order.side != side or order.status not in ACTIVE:
                continue
            if message_id is not None and _plan_id(order) != message_id:
                continue
            if order.status == OrderStatus.PENDING and not include_pending:
                continue
            result[self._release(order)] += 1
        return result

    def _orders_for(self, message_id: str) -> list[BrokerOrder]:
        prefix = f"{message_id}:"
        snapshot = self._call("list_orders", self._broker.list_orders)
        return [o for o in snapshot if o.client_id.startswith(prefix)]

    def _groups(
        self, side: str, snapshot: list[BrokerOrder]
    ) -> list[tuple[str, list[BrokerOrder]]]:
        """Active orders of one side grouped by plan, oldest plan first."""
        by_id: dict[str, list[BrokerOrder]] = {}
        for order in snapshot:
            if order.side == side and order.status in ACTIVE:
                by_id.setdefault(_plan_id(order), []).append(order)
        position = {mid: i for i, mid in enumerate(self._plans)}
        return sorted(by_id.items(), key=lambda kv: position.get(kv[0], -1))

    def _new_cut_rule(self) -> dict[str, Any]:
        """The candle rule for a plan being applied now. Only candles that
        OPEN after this moment count, so the one running now is skipped."""
        tf = self._cut_timeframe
        candles = self._call(
            "get_candles", lambda: self._broker.get_candles(SYMBOL, tf, 1)
        )
        return {"tf": tf, "first": candles[-1].open_time + TIMEFRAME_SECONDS[tf]}

    def cut_rule(self, message_id: str) -> dict[str, Any] | None:
        """{"tf", "first"} for a candle plan, None for a legacy-timer plan."""
        rule = self._cut_rules.get(message_id)
        return None if rule is None else dict(rule)

    def candle_timers(self, timeframes: Any) -> dict[str, int | None]:
        """Seconds until the running candle of each timeframe closes, for
        the window. Read-only: never halts. None when it cannot be told."""
        out: dict[str, int | None] = {}
        try:
            tick = self._broker.get_tick(SYMBOL)
        except BrokerError:
            tick = None
        for tf in set(timeframes):
            try:
                forming = self._broker.get_candles(SYMBOL, tf, 1)[-1]
            except BrokerError:
                out[tf] = None
                continue
            if tick is None or tick.time is None:
                out[tf] = None
                continue
            seconds = TIMEFRAME_SECONDS[tf]
            remaining = forming.open_time + seconds - tick.time
            out[tf] = max(0, min(seconds, remaining))
        return out

    def plan_name(self, message_id: str) -> str:
        """Alpha, Beta, ... Plans from before names existed show their
        short id."""
        return self._names.get(message_id) or short_id(message_id)

    def new_message_id(self, now: int, fallback: str) -> str:
        """'<unix time>-<Greek name>', e.g. 1763440000-Alpha. The id is also
        the MT5 comment of every layer ('1763440000-Alpha:3'), so the name
        seen in the window can be read in MT5. When MT5 cannot be read, or
        all names are used, `fallback` (random letters) takes the name's place."""
        snap = self.snapshot()
        if "error" in snap:
            return f"{now}-{fallback}"
        return f"{now}-{self._next_name(f'{now}-{fallback}', snap['active'])}"

    def _next_name(self, message_id: str, snapshot: list[BrokerOrder]) -> str:
        """The first Greek name that no live plan is using. With all 24 in
        use, the plan keeps its short id (the random part of its comment)."""
        live = {
            self._names.get(_plan_id(o))
            for o in snapshot if o.status in ACTIVE
        }
        for name in PLAN_NAMES:
            if name not in live:
                return name
        return short_id(message_id)

    def _label(self, side: str, message_id: str, plans_on_side: int) -> str:
        if plans_on_side == 1:
            return side
        return f"{side} #{self.plan_name(message_id)}"

    def _latest_plan_with(self, side: str) -> str | None:
        """The most recently stored plan that has a section for this side."""
        for message_id in reversed(list(self._plans)):
            if self._plans[message_id].get(side.lower()) is not None:
                return message_id
        return None

    def _levels_for(
        self,
        side: str,
        message_id: str,
    ) -> tuple[Decimal, Decimal]:
        """(Cut Loss, SCL) from the stored plan with this message id."""
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
        chart lines. With several plans on a side, the most recent plan's
        levels are drawn. Read-only: never halts. None if it cannot be
        determined right now (broker error), so callers keep what they
        last drew.
        """
        try:
            snapshot = self._broker.list_orders()
        except Exception:
            return None
        levels: dict[str, tuple[Decimal, Decimal]] = {}
        for side in ("BUY", "SELL"):
            groups = self._groups(side, snapshot)
            if not groups:
                continue
            message_id = groups[-1][0]
            plan = self._plans.get(message_id)
            if plan is None or plan.get(side.lower()) is None:
                return None
            levels[side] = self._levels_for(side, message_id)
        return levels

    def plans_overview(self) -> list[dict[str, Any]] | None:
        """One row per live plan, oldest first, for the plan list in the
        window. Read-only: never halts. None if the broker cannot be read.
        Row: {"message_id", "name", "sent_at" (unix time or None), "BUY"/"SELL":
        {"filled", "pending", "cut_loss", "sl", "tp"}}."""
        try:
            orders = self._broker.list_orders()
        except Exception:
            return None
        rows: dict[str, dict[str, Any]] = {}
        for side in ("BUY", "SELL"):
            for message_id, active in self._groups(side, orders):
                section = (self._plans.get(message_id) or {}).get(side.lower())
                row = rows.setdefault(message_id, {
                    "message_id": message_id,
                    "name": self.plan_name(message_id),
                    "timeframe": (self._cut_rules.get(message_id) or {}).get("tf"),
                    "sent_at": _sent_at(message_id),
                })
                row[side] = {
                    "filled": sum(
                        o.status == OrderStatus.FILLED for o in active),
                    "pending": sum(
                        o.status == OrderStatus.PENDING for o in active),
                    "cut_loss": None if section is None else section["cut_loss"],
                    "sl": None if section is None else section["sl"],
                    "tp": None if section is None else section["tp"],
                }
        position = {mid: i for i, mid in enumerate(self._plans)}
        return sorted(rows.values(), key=lambda r: position.get(r["message_id"], -1))

    def _prune(self) -> None:
        while len(self._plans) > MAX_STORED_PLANS:
            oldest = next(iter(self._plans))
            if oldest == self._current_message_id:
                break
            del self._plans[oldest]
            self._names.pop(oldest, None)
            self._cut_rules.pop(oldest, None)
            self._completed.discard(oldest)

    def _persist(self) -> None:
        if self._store is None:
            return
        self._prune()
        state = StoredState(
            current_message_id=self._current_message_id,
            plans=deepcopy(self._plans),
            completed=set(self._completed),
            names=dict(self._names),
            cut_rules={m: dict(r) for m, r in self._cut_rules.items()},
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


def _plan_id(order: BrokerOrder) -> str:
    """The message id inside a client id ("<message id>:<layer index>")."""
    return order.client_id.rpartition(":")[0]


plan_id = _plan_id  # public: the plan a broker order belongs to


def short_id(message_id: str) -> str:
    return message_id.rpartition("-")[2] or message_id


def _sent_at(message_id: str) -> int | None:
    match = re.match(r"^(\d{9,11})-", message_id)
    return int(match.group(1)) if match else None


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