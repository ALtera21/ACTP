"""
In-memory Broker for tests, with fault injection.

Implements the Broker protocol and never connects to MT5.
Test-only helpers (not part of Broker): set_tick, fill, inject_fault.
"""

from collections import defaultdict
from collections.abc import Callable
from dataclasses import dataclass, replace
from decimal import Decimal
from typing import TypeVar

from actp.broker import (
    BrokerError,
    BrokerOrder,
    BrokerRejected,
    BrokerUnavailable,
    OrderRequest,
    OrderStatus,
    Tick,
)

T = TypeVar("T")

BROKER_METHODS = frozenset(
    {
        "place_pending",
        "cancel_pending",
        "close_position",
        "modify_sltp",
        "list_orders",
        "get_tick",
        "place_market"
    }
)


@dataclass(frozen=True)
class _Fault:
    on_call: int
    error: type[BrokerError]
    applied: bool


class FakeBroker:
    def __init__(self) -> None:
        self._orders: dict[str, BrokerOrder] = {}
        self._ticket_by_client_id: dict[str, str] = {}
        self._ticks: dict[str, Tick] = {}
        self._next_ticket = 1
        self._call_counts: dict[str, int] = defaultdict(int)
        self._faults: dict[str, list[_Fault]] = defaultdict(list)

    # ---- Test-only helpers -------------------------------------------

    def inject_fault(
        self,
        method: str,
        *,
        on_call: int,
        error: type[BrokerError] = BrokerUnavailable,
        applied: bool = False,
    ) -> None:
        """
        Make the Nth call (1-based) to `method` raise `error`.

        applied=False: the action is NOT performed (a timeout before the
        broker acted). applied=True: the action IS performed but the
        caller still sees the error (a timeout after the broker acted).
        applied=True only makes sense for BrokerUnavailable, because a
        rejection means nothing happened.
        """
        if method not in BROKER_METHODS:
            raise ValueError(f"Unknown broker method: {method}")
        if isinstance(on_call, bool) or not isinstance(on_call, int) or on_call < 1:
            raise ValueError("on_call must be an integer of at least 1.")
        if error is BrokerRejected and applied:
            raise ValueError("A rejected action cannot have been applied.")
        self._faults[method].append(_Fault(on_call, error, applied))

    def set_tick(self, symbol: str, bid: Decimal, ask: Decimal) -> None:
        self._ticks[symbol] = Tick(bid=bid, ask=ask)

    def fill(self, ticket: str) -> None:
        """Simulate a pending order being filled by the market."""
        order = self._get(ticket)
        if order.status != OrderStatus.PENDING:
            raise ValueError("Only pending orders can be filled.")
        self._orders[ticket] = replace(order, status=OrderStatus.FILLED)

    # ---- Broker protocol ---------------------------------------------

    def place_pending(self, request: OrderRequest) -> BrokerOrder:
        return self._run("place_pending", lambda: self._place(request))

    def cancel_pending(self, ticket: str) -> None:
        self._run("cancel_pending", lambda: self._cancel(ticket))

    def close_position(self, ticket: str) -> None:
        self._run("close_position", lambda: self._close(ticket))

    def modify_sltp(self, ticket: str, sl: Decimal, tp: Decimal) -> None:
        self._run("modify_sltp", lambda: self._modify(ticket, sl, tp))

    def list_orders(self) -> list[BrokerOrder]:
        return self._run("list_orders", lambda: list(self._orders.values()))

    def get_tick(self, symbol: str) -> Tick:
        return self._run("get_tick", lambda: self._tick(symbol))

    def place_market(self, request: OrderRequest) -> BrokerOrder:
        return self._run("place_market", lambda: self._place_market(request))

    # ---- Internals ---------------------------------------------------

    def _run(self, method: str, action: Callable[[], T]) -> T:
        self._call_counts[method] += 1
        call_number = self._call_counts[method]
        fault = next(
            (f for f in self._faults[method] if f.on_call == call_number),
            None,
        )
        if fault is None:
            return action()
        if fault.applied:
            action()
        raise fault.error(f"Injected fault on {method} call #{call_number}.")

    def _get(self, ticket: str) -> BrokerOrder:
        try:
            return self._orders[ticket]
        except KeyError as exc:
            raise BrokerRejected(f"Unknown ticket: {ticket}") from exc

    def _place(self, request: OrderRequest) -> BrokerOrder:
        existing = self._ticket_by_client_id.get(request.client_id)
        if existing is not None:
            return self._orders[existing]
        ticket = f"FAKE-{self._next_ticket:04d}"
        self._next_ticket += 1
        order = BrokerOrder(
            ticket=ticket,
            client_id=request.client_id,
            symbol=request.symbol,
            side=request.side,
            entry_price=request.entry_price,
            volume=request.volume,
            sl=request.sl,
            tp=request.tp,
            status=OrderStatus.PENDING,
        )
        self._orders[ticket] = order
        self._ticket_by_client_id[request.client_id] = ticket
        return order

    def _cancel(self, ticket: str) -> None:
        order = self._get(ticket)
        if order.status != OrderStatus.PENDING:
            raise BrokerRejected("Only pending orders can be cancelled.")
        self._orders[ticket] = replace(order, status=OrderStatus.CANCELLED)

    def _close(self, ticket: str) -> None:
        order = self._get(ticket)
        if order.status != OrderStatus.FILLED:
            raise BrokerRejected("Only filled orders can be closed.")
        self._orders[ticket] = replace(order, status=OrderStatus.CLOSED)

    def _modify(self, ticket: str, sl: Decimal, tp: Decimal) -> None:
        order = self._get(ticket)
        if order.status not in (OrderStatus.PENDING, OrderStatus.FILLED):
            raise BrokerRejected("Only active orders can be modified.")
        self._orders[ticket] = replace(order, sl=sl, tp=tp)

    def _tick(self, symbol: str) -> Tick:
        try:
            return self._ticks[symbol]
        except KeyError as exc:
            raise BrokerUnavailable(f"No tick available for {symbol}.") from exc

    def _place_market(self, request: OrderRequest) -> BrokerOrder:
        existing = self._ticket_by_client_id.get(request.client_id)
        if existing is not None:
            return self._orders[existing]
        order = self._place(request)
        filled = replace(order, status=OrderStatus.FILLED)
        self._orders[order.ticket] = filled
        return filled
