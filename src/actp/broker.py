"""
Broker abstraction for ACTP.

Design rules:
- The broker is the source of truth. Local state is only a cache that can
  be rebuilt from list_orders().
- Failures come in two kinds:
  * BrokerRejected: the broker definitely did NOT perform the action.
  * BrokerUnavailable: the outcome is UNKNOWN (timeout, disconnect).
    Callers must reconcile with list_orders() before retrying.
- Nothing here chooses symbols or lot sizes. Callers validate first.
"""

from dataclasses import dataclass
from decimal import Decimal
from enum import Enum
from typing import Protocol


class BrokerError(Exception):
    """Base class for broker failures."""


class BrokerRejected(BrokerError):
    """The broker definitely did not perform the requested action."""


class BrokerUnavailable(BrokerError):
    """The outcome is unknown. Reconcile before retrying."""


class OrderStatus(str, Enum):
    PENDING = "PENDING"
    FILLED = "FILLED"
    CANCELLED = "CANCELLED"
    CLOSED = "CLOSED"


@dataclass(frozen=True)
class OrderRequest:
    # Stable identifier such as "<message_id>:<layer_index>".
    # Used for idempotency: placing the same client_id twice must not
    # create two orders.
    client_id: str
    symbol: str
    side: str  # "BUY" or "SELL"
    entry_price: Decimal
    volume: Decimal
    sl: Decimal
    tp: Decimal


@dataclass(frozen=True)
class BrokerOrder:
    """
    Unified view of an order. In MT5, pending orders and open positions
    are separate objects; the MT5 adapter merges them into this shape.
    """

    ticket: str
    client_id: str
    symbol: str
    side: str
    entry_price: Decimal
    volume: Decimal
    sl: Decimal
    tp: Decimal
    status: OrderStatus


@dataclass(frozen=True)
class Tick:
    bid: Decimal
    ask: Decimal


class Broker(Protocol):
    def place_pending(self, request: OrderRequest) -> BrokerOrder:
        """
        Place one pending limit order.
        If request.client_id already exists, return the existing order
        instead of creating a duplicate.
        """
        ...

    def cancel_pending(self, ticket: str) -> None:
        """Cancel a pending order. Raises BrokerRejected if not pending."""
        ...

    def close_position(self, ticket: str) -> None:
        """Close a filled order. Raises BrokerRejected if not filled."""
        ...

    def modify_sltp(self, ticket: str, sl: Decimal, tp: Decimal) -> None:
        """Change SL/TP of a pending or filled order."""
        ...

    def list_orders(self) -> list[BrokerOrder]:
        """Return every order ACTP owns. This is the source of truth."""
        ...

    def get_tick(self, symbol: str) -> Tick:
        """Return the current bid/ask for a symbol."""
        ...

    def place_market(self, request: OrderRequest) -> BrokerOrder:
        """
        Open one position at the current market price (BUY at the ask,
        SELL at the bid). request.entry_price is the plan's layer price,
        NOT the fill price. Idempotent on client_id, like place_pending.
        Returns the order with status FILLED.
        """
        ...