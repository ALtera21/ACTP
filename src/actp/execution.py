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
from decimal import ROUND_HALF_UP, Decimal
from typing import Any, TypeVar

from actp import config as _config
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
        # TP levels set by hand, watched by ACTP: (side, message id) -> price.
        self._tp_targets: dict[tuple[str, str], Decimal] = {}
        # Deepest the price has gone into a plan side's range, in percent
        # (0 = at the first entry, 100 = at the last one). For Auto-TP.
        self._depth: dict[tuple[str, str], Decimal] = {}
        # Base lot (the Lot box) of each plan when it was sent.
        self._base_lots: dict[str, Decimal] = {}

        if store is not None:
            # A corrupt store raises PlanStoreError here: refuse to start
            # rather than run with unknown history.
            state = store.load()
            self._plans = dict(state.plans)
            self._completed = set(state.completed)
            self._names = dict(state.names)
            self._cut_rules = {m: dict(r) for m, r in state.cut_rules.items()}
            self._tp_targets = {
                (side, m): price
                for m, sides in state.tp_targets.items()
                for side, price in sides.items()
            }
            self._depth = {
                (side, m): depth
                for m, sides in state.depths.items()
                for side, depth in sides.items()
            }
            self._base_lots = dict(state.base_lots)
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
        if known is None:
            self._base_lots[message_id] = _config.LOT_PER_ORDER
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
        cancelled = self._release_many([
            o for o in snapshot
            if o.side == normalized and o.status == OrderStatus.PENDING
        ])["cancelled"]
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
        self._tp_targets.clear()
        self._depth.clear()
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
            self._tp_targets.pop((one, message_id), None)
            self._depth.pop((one, message_id), None)
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
        left_open = 0
        chosen = []
        for order in snapshot:
            if order.status != OrderStatus.FILLED:
                continue
            if only_profitable and not position_in_profit(order, tick):
                left_open += 1
                continue
            chosen.append(order)
        closed = self._release_many(chosen)["closed"]
        return {
            "closed": closed,
            "left_open": left_open,
            "left_pending": sum(
                1 for o in snapshot if o.status == OrderStatus.PENDING
            ),
        }

    def move_tp_when_full(
        self, percent: Any = 100, priority: bool = False
    ) -> dict[str, tuple[str, Decimal, int]]:
        """When the price has gone `percent` of the way into a plan side's
        range (0 = at the first entry, 100 = at the last one), move that
        plan's TP to its first entry, the proximal one (BUY: highest layer,
        SELL: lowest). The deepest point the price reached counts, so a
        price that dipped and came back still triggers it: track_price_depth
        remembers it. At least one position must be open. The TP of the
        pending orders moves too. Once per plan and side; every plan is
        handled on its own.

        A plan side whose TP was set by hand (the update TP button, or
        dragged in MetaTrader: some order carries a TP that is neither the
        plan's own nor the proximal one) is left alone, unless `priority` is
        on: then Auto-TP replaces the hand-set TP too, and ACTP stops
        watching a level that was set by hand on that side. Returns label ->
        (state, target, positions modified) for MOVED, PRICE_PAST (price
        already beyond the new TP, will retry) and REFUSED (MT5 said no,
        will retry). The label is the side, or "BUY #<plan id>" when a side
        has several plans. Does nothing while halted."""
        pct = _check_percent(percent)
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
                filled = [o for o in active if o.status == OrderStatus.FILLED]
                if not filled:
                    continue
                # The tracked depth, and the deepest layer that is open now
                # (covers a restart of ACTP, which forgets the tracking).
                depth = max(
                    [self._depth.get(key, Decimal(0))]
                    + [_depth_of(side, section, o.entry_price)
                       for o in filled]
                )
                if depth < pct:
                    continue
                prices = (section["area_start"], section["area_end"])
                target = max(prices) if side == "BUY" else min(prices)
                if not priority and any(
                    o.tp not in (section["tp"], target) for o in active
                ):
                    continue  # a TP set by hand stays
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
                    self._tp_targets.pop(key, None)  # Auto-TP took over
                    results[label] = ("MOVED", target, modified)
        self._tp_done &= live  # forget plans that have no active orders
        return results

    # ---- Re-entry (refresh one side of one plan) ------------------------
    def side_settings(self, message_id: str, side: str) -> dict[str, Any]:
        """What "the same as before" means for one side of a plan:
        {"layers", "weights" (text list or None), "lot" (base lot or None
        for plans stored before it was recorded), "area_start",
        "area_end"}. Read-only."""
        side = _normalize_side(side)
        section = (self._plans.get(message_id) or {}).get(side.lower())
        if section is None:
            raise ValueError(f"This plan has no {side} side.")
        weights = section.get("weights")
        return {
            "layers": section["layers"],
            "weights": list(weights) if weights else None,
            "lot": self._base_lots.get(message_id),
            "area_start": section["area_start"],
            "area_end": section["area_end"],
        }

    def reenter_side(
        self,
        message_id: str,
        side: str,
        layers: int,
        weights: list[str] | None,
        lot: Any = None,
    ) -> dict[str, Any]:
        """Refresh ONE side of ONE plan: cancel its pending orders, close its
        open positions, then place the layers again on the same range with
        the same Cut Loss, SL and TP. The plan keeps its id and its name, and
        its other side and every other plan are not touched. `layers`,
        `weights` (one text per layer, layer 1 first, or None for 1x) and
        `lot` (base lot, None = the Lot box now) are what the new layers use.

        The price check runs FIRST: if the new layers would be refused (price
        through Cut Loss, too many layers at market) nothing is changed.
        A TP set by hand on that side keeps being watched. If the broker
        fails half way, the manager halts: reconcile, then press re-entry
        again (it always clears the side first). Not allowed while halted.

        Returns {"cancelled", "closed", "placed", "market"}."""
        self._ensure_running()
        side = _normalize_side(side)
        key = (side, message_id)
        plan = self._plans.get(message_id)
        section = None if plan is None else plan.get(side.lower())
        if section is None:
            raise ValueError(f"This plan has no {side} side.")
        base_lot = _config.LOT_PER_ORDER if lot is None else Decimal(str(lot))
        new_section = dict(section)
        new_section["layers"] = layers
        if weights:
            new_section["weights"] = list(weights)
        else:
            new_section.pop("weights", None)
        new_plan = deepcopy(plan)
        new_plan[side.lower()] = new_section
        validate_plan(new_plan)
        other = "sell" if side == "BUY" else "buy"
        one_side = dict(new_plan)
        one_side[other] = None
        proposed = build_plan_preview(one_side, lot=base_lot)
        if any(item["symbol"] != SYMBOL for item in proposed):
            raise ValueError("Refusing to place an unauthorized symbol.")

        snapshot = self._call("list_orders", self._broker.list_orders)
        if self._check_stale:
            tick = self._call("get_tick", lambda: self._broker.get_tick(SYMBOL))
            check_plan(one_side, proposed, tick, cap=self._market_cap)
        prefix = f"{message_id}:"
        # New layer numbers continue after every number this plan has used
        # (the other side keeps its own), so none can clash.
        start = max(_placed_indexes(prefix, snapshot), default=-1) + 1
        target = self._tp_targets.get(key)

        # Record the intent durably BEFORE touching the broker.
        self._plans[message_id][side.lower()] = deepcopy(new_section)
        self._base_lots[message_id] = base_lot
        self._persist()

        closed = self.close_plan(message_id, side)
        market_count = 0
        for index, item in enumerate(proposed):
            request = OrderRequest(
                client_id=f"{prefix}{start + index}",
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
                    "get_tick", lambda: self._broker.get_tick(SYMBOL))
                at_market = is_marketable(
                    item["side"], item["entry_price"], fresh)
            if at_market:
                market_count += 1
                if market_count > self._market_cap:
                    raise self._halt(
                        f"Price moved while placing: more than "
                        f"{self._market_cap} {side} layers would fill at "
                        "market. Check MT5, then Reconcile.")
                self._call("place_market",
                           lambda r=request: self._broker.place_market(r))
            else:
                self._call("place_pending",
                           lambda r=request: self._broker.place_pending(r))

        self._tp_done.discard(key)
        self._depth.pop(key, None)  # the new layers start from scratch
        if target is not None:  # the TP set by hand stays watched
            self._tp_targets[key] = target
            self._tp_done.add(key)
        self._persist()
        return {"cancelled": closed["cancelled"], "closed": closed["closed"],
                "placed": len(proposed), "market": market_count}

    def track_price_depth(self) -> None:
        """Remember how far the price has gone into the range of every plan
        side (for Auto-TP and Auto-remove). A BUY side follows the ask (what a
        buy limit fills at), a SELL side the bid. Also counts the deepest
        layer that is filled, which catches a spike between two looks. One
        tick and one order read; call it about once a second. The depths are saved with the plans, so a
        restart of ACTP does not forget them. Does nothing while halted."""
        if self._halt_reason is not None:
            return
        keys = [
            (side, message_id)
            for message_id, plan in self._plans.items()
            for side in ("BUY", "SELL")
            if plan.get(side.lower()) is not None
        ]
        if not keys:
            return
        tick = self._call("get_tick", lambda: self._broker.get_tick(SYMBOL))
        snapshot = self._call("list_orders", self._broker.list_orders)
        grew = False
        for side, message_id in keys:
            section = self._plans[message_id][side.lower()]
            price = tick.ask if side == "BUY" else tick.bid
            depth = _depth_of(side, section, price)
            # A price spike can fill a layer and be gone before the next look
            # (once a second). A layer that is filled proves the price was
            # there, so the deepest filled layer counts too.
            for order in self._side_orders(snapshot, side, message_id):
                if order.status == OrderStatus.FILLED:
                    depth = max(depth, _depth_of(side, section, order.entry_price))
            if depth > self._depth.get((side, message_id), Decimal(-1)):
                self._depth[(side, message_id)] = depth
                grew = True
        if grew:
            self._persist()

    def remove_spent_sides(self, percent: Any) -> list[dict[str, Any]]:
        """Auto-remove: a plan side with NO open position whose price has
        already gone `percent` of the way into its range (the deepest point
        counts) has its leftover pending orders cancelled. Returns one event
        per side removed: {"message_id", "name", "side", "removed"}. The
        other side and other plans are not touched. Does nothing while
        halted."""
        pct = _check_percent(percent)
        if self._halt_reason is not None:
            return []
        due = [(side, message_id)
               for (side, message_id), depth in self._depth.items()
               if depth >= pct and message_id in self._plans
               and self._plans[message_id].get(side.lower()) is not None]
        if not due:
            return []
        snapshot = self._call("list_orders", self._broker.list_orders)
        events: list[dict[str, Any]] = []
        for side, message_id in due:
            mine = self._side_orders(snapshot, side, message_id)
            if not mine or any(o.status == OrderStatus.FILLED for o in mine):
                continue  # nothing left, or a position is still open
            result = self.close_plan(message_id, side)
            events.append({"message_id": message_id,
                           "name": self.plan_name(message_id),
                           "side": side, "removed": result["cancelled"]})
        return events

    # ---- Manual TP (one plan, one side) ---------------------------------
    @staticmethod
    def _tp_hit(side: str, tick: Any, tp: Decimal) -> bool:
        """A BUY closes at the bid, a SELL at the ask."""
        return tick.bid >= tp if side == "BUY" else tick.ask <= tp

    def _side_orders(
        self, snapshot: list[BrokerOrder], side: str, message_id: str
    ) -> list[BrokerOrder]:
        return [
            o for o in snapshot
            if o.side == side and o.status in ACTIVE
            and _plan_id(o) == message_id
        ]

    def tp_would_close_now(self, message_id: str, side: str, tp: Any) -> int:
        """How many open positions of this plan side a TP at `tp` would
        close right now because the price is already there (0 = none). Only
        reads."""
        side = _normalize_side(side)
        price = _check_tp_price(tp)
        snapshot = self._call("list_orders", self._broker.list_orders)
        tick = self._call("get_tick", lambda: self._broker.get_tick(SYMBOL))
        if not self._tp_hit(side, tick, price):
            return 0
        return sum(
            o.status == OrderStatus.FILLED
            for o in self._side_orders(snapshot, side, message_id)
        )

    def set_plan_tp(
        self,
        message_id: str,
        side: str,
        tp: Any,
        include_pending: bool = False,
        auto_priority: bool = False,
    ) -> dict[str, Any]:
        """Set the TP of ONE side of ONE plan.

        The TP goes onto the open positions at MetaTrader (and onto the
        pending orders when include_pending is on). MetaTrader refuses a TP
        on the wrong side of the price, for example inside the plan's range,
        so ACTP also WATCHES the price itself and closes that side's open
        positions when it reaches the TP (BUY: bid >= TP, SELL: ask <= TP);
        pending orders are left alone. If the price is already there, the
        positions are closed right away and nothing is watched. With no open
        position yet the level is simply kept and watched: it acts when
        positions open and the price is at the TP (a SELL layer above the TP
        waits for the price to come back down to it).

        Returns {"state": "ARMED" | "CLOSED", "open": n, "closed": n,
        "hard_positions", "hard_pending", "refused_positions",
        "refused_pending"}. Setting a TP by hand replaces Auto-TP for that
        plan side, unless `auto_priority` is on (then Auto-TP may still
        replace it when its percent is reached). Not allowed while halted."""
        self._ensure_running()
        side = _normalize_side(side)
        price = _check_tp_price(tp)
        plan = self._plans.get(message_id)
        if plan is None or plan.get(side.lower()) is None:
            raise ValueError(f"This plan has no {side} side.")
        snapshot = self._call("list_orders", self._broker.list_orders)
        mine = self._side_orders(snapshot, side, message_id)
        if not mine:
            raise ValueError(f"This plan has no live {side} orders.")
        positions = [o for o in mine if o.status == OrderStatus.FILLED]
        pending = [o for o in mine if o.status == OrderStatus.PENDING]
        tick = self._call("get_tick", lambda: self._broker.get_tick(SYMBOL))
        result = {"state": "ARMED", "closed": 0, "hard_positions": 0,
                  "hard_pending": 0, "refused_positions": 0,
                  "refused_pending": 0}
        key = (side, message_id)
        result["open"] = len(positions)
        if positions and self._tp_hit(side, tick, price):
            self._tp_targets.pop(key, None)
            result["state"] = "CLOSED"
            result["closed"] = self._release_many(positions)["closed"]
            self._persist()
            return result
        self._tp_targets[key] = price
        if not auto_priority:
            self._tp_done.add(key)  # the manual TP replaces Auto-TP here
        self._persist()  # on disk before any order is touched
        batches = [("positions", positions)]
        if include_pending:
            batches.append(("pending", pending))
        for name, orders in batches:
            for order in orders:
                try:
                    self._broker.modify_sltp(order.ticket, order.sl, price)
                except BrokerRejected:
                    result[f"refused_{name}"] += 1  # ACTP watches the price
                except BrokerUnavailable as exc:
                    raise self._halt(f"modify_sltp failed: {exc}") from exc
                else:
                    result[f"hard_{name}"] += 1
        return result

    def check_tp_targets(self) -> list[dict[str, Any]]:
        """Watch the TP levels set by hand. When the price reaches one, close
        the open positions of that plan side (pending orders stay). Returns
        one event per level that fired: {"message_id", "name", "side", "tp",
        "closed"}. Cheap when nothing is watched. Does nothing while halted."""
        if not self._tp_targets or self._halt_reason is not None:
            return []
        tick = self._call("get_tick", lambda: self._broker.get_tick(SYMBOL))
        hit = [(key, tp) for key, tp in self._tp_targets.items()
               if self._tp_hit(key[0], tick, tp)]
        if not hit:
            return []
        snapshot = self._call("list_orders", self._broker.list_orders)
        events: list[dict[str, Any]] = []
        changed = False
        for (side, message_id), tp in hit:
            mine = self._side_orders(snapshot, side, message_id)
            if not mine:  # the plan side is gone: stop watching
                del self._tp_targets[(side, message_id)]
                changed = True
                continue
            positions = [o for o in mine if o.status == OrderStatus.FILLED]
            if not positions:
                continue  # nothing open yet: keep watching
            closed = self._release_many(positions)["closed"]
            del self._tp_targets[(side, message_id)]
            changed = True
            events.append({"message_id": message_id,
                           "name": self.plan_name(message_id),
                           "side": side, "tp": tp, "closed": closed})
        if changed:
            self._persist()
        return events

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

    def _release_many(self, orders: list[BrokerOrder]) -> dict[str, int]:
        """Cancel/close many orders at once (pending ones first). Brokers
        with a batch call send them together; others one by one. If any of
        them fails the manager halts, after the rest were still tried."""
        result = {"cancelled": 0, "closed": 0}
        if not orders:
            return result
        bulk = getattr(self._broker, "release_many", None)
        if bulk is None:
            for order in orders:
                result[self._release(order)] += 1
            return result
        outcomes = self._call("release_many", lambda: bulk(orders))
        first_error = None
        for order, outcome in zip(orders, outcomes):
            if outcome is not None:
                first_error = first_error or outcome
            elif order.status == OrderStatus.PENDING:
                result["cancelled"] += 1
            else:
                result["closed"] += 1
        if first_error is not None:
            raise self._halt(f"close/cancel failed: {first_error}") from first_error
        return result

    def _close_orders(
        self,
        side: str,
        snapshot: list[BrokerOrder],
        include_pending: bool = True,
        message_id: str | None = None,
    ) -> dict[str, int]:
        """Cancel/close the active orders of one side. With message_id, only
        that plan's orders."""
        chosen = []
        for order in snapshot:
            if order.side != side or order.status not in ACTIVE:
                continue
            if message_id is not None and _plan_id(order) != message_id:
                continue
            if order.status == OrderStatus.PENDING and not include_pending:
                continue
            chosen.append(order)
        return self._release_many(chosen)

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
                    # what the TP boxes show: the level ACTP is watching,
                    # else the TP the open positions carry now, else the
                    # plan's own TP
                    "tp_target": self._tp_targets.get((side, message_id)),
                    "tp_live": _live_tp(active),
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
            self._base_lots.pop(oldest, None)
            for side in ("BUY", "SELL"):
                self._tp_targets.pop((side, oldest), None)
                self._depth.pop((side, oldest), None)

    def _depths_by_plan(self) -> dict[str, dict[str, Decimal]]:
        out: dict[str, dict[str, Decimal]] = {}
        for (side, message_id), depth in self._depth.items():
            out.setdefault(message_id, {})[side] = depth
        return out

    def _tp_targets_by_plan(self) -> dict[str, dict[str, Decimal]]:
        out: dict[str, dict[str, Decimal]] = {}
        for (side, message_id), price in self._tp_targets.items():
            out.setdefault(message_id, {})[side] = price
        return out

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
            tp_targets=self._tp_targets_by_plan(),
            depths=self._depths_by_plan(),
            base_lots=dict(self._base_lots),
        )
        try:
            self._store.save(state)
        except OSError as exc:
            raise self._halt(f"saving plan state failed: {exc}") from exc


def _check_tp_price(value: Any) -> Decimal:
    try:
        price = Decimal(str(value).strip())
    except Exception:
        raise ValueError("The TP must be a price, for example 4120.50.") from None
    if not price.is_finite() or price <= 0:
        raise ValueError("The TP must be a price above 0.")
    return price


def _depth_of(side: str, section: dict[str, Any], price: Any) -> Decimal:
    """How far `price` is into a plan side's range, in percent: 0 at the
    first (proximal) entry, 100 at the last (distal) one; never outside
    0..100."""
    high = max(section["area_start"], section["area_end"])
    low = min(section["area_start"], section["area_end"])
    price = Decimal(str(price))
    into = (high - price) if side == "BUY" else (price - low)
    run = high - low
    if run <= 0:
        return Decimal(100) if into >= 0 else Decimal(0)
    return max(Decimal(0), min(Decimal(100), into / run * 100))


def auto_tp_trigger_price(
    side: str, area_start: Any, area_end: Any, percent: Any
) -> Decimal:
    """The price at which Auto-TP fires for a side: `percent` of the way
    from the first entry (proximal) to the last one (distal)."""
    high = max(Decimal(str(area_start)), Decimal(str(area_end)))
    low = min(Decimal(str(area_start)), Decimal(str(area_end)))
    share = Decimal(str(percent)) / 100 * (high - low)
    price = high - share if side == "BUY" else low + share
    return price.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


def _check_percent(value: Any) -> Decimal:
    try:
        pct = Decimal(str(value).strip())
    except Exception:
        raise ValueError("The Auto-TP percent must be a number.") from None
    if not pct.is_finite() or not 1 <= pct <= 100:
        raise ValueError("The Auto-TP percent must be from 1 to 100.")
    return pct


def _live_tp(active: list[BrokerOrder]) -> Decimal | None:
    """The TP of the open positions (else of the pending orders): the most
    common value among them."""
    for status in (OrderStatus.FILLED, OrderStatus.PENDING):
        values = [o.tp for o in active if o.status == status and o.tp]
        if values:
            return max(set(values), key=values.count)
    return None


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