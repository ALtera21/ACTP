"""
MT5 adapter for the Broker protocol.

STAGE 3: all six Broker methods exist. Each write was added one at a time
and proven on the real terminal before the next.

Every write refuses to touch an order that does not carry our magic number,
so your manual trades on the same account are never modified or closed.

Design rules (see broker.py):
- Every failure here is BrokerUnavailable, never BrokerRejected. A read
  cannot be "definitely refused", and Unavailable halts safely.
- ACTP's orders are recognised by `magic` (a number all ACTP orders carry)
  and by `comment` (our client_id, "<message_id>:<index>").
- MT5 keeps pending orders (orders_get) and open positions (positions_get)
  in separate lists, and forgets finished ones. FakeBroker keeps CANCELLED
  and CLOSED orders forever, and apply_plan's resume logic depends on that
  (a missing client_id means "not placed yet"). So list_orders also reads
  recent history and reports finished orders as CANCELLED or CLOSED.

The `mt5` module is injectable so the mapping logic can be unit tested
without a terminal. In real use leave it as None.
"""

import re
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from typing import Any

from actp.broker import (
    BrokerOrder,
    BrokerRejected,
    BrokerUnavailable,
    OrderRequest,
    OrderStatus,
    Tick,
)

# Every ACTP order carries this number. Pick any int; never reuse it for
# manual trades, because list_orders treats everything with it as ACTP's.
DEFAULT_MAGIC = 26102301

# How far back history is read. It must be longer than the time between a
# plan being applied and the last time a retry of it could happen.
DEFAULT_HISTORY_DAYS = 7

# MT5 numeric values. The Python package does not export all of them, so
# they are written out here (from the MQL5 documentation).
TRADE_ACTION_DEAL = 1
TRADE_ACTION_PENDING = 5
TRADE_ACTION_SLTP = 6
TRADE_ACTION_MODIFY = 7
TRADE_ACTION_REMOVE = 8
ORDER_TIME_GTC = 0
ORDER_FILLING_FOK = 0
ORDER_FILLING_IOC = 1
ORDER_FILLING_RETURN = 2
ORDER_STATE_CANCELED = 2
ORDER_STATE_FILLED = 4
ORDER_STATE_REJECTED = 5
ORDER_STATE_EXPIRED = 6

# order_send retcodes.
_SUCCESS = {10008, 10009}  # order placed / request done
_NO_CHANGES = 10025
# The broker definitely did NOT act. Anything not listed here and not a
# success (timeout 10012, no connection 10031, error 10011, unknown codes)
# is treated as "outcome unknown".
_REFUSED = {
    10004, 10006, 10007, 10013, 10014, 10015, 10016, 10017, 10018, 10019,
    10020, 10021, 10022, 10024, 10026, 10027, 10028, 10029, 10030, 10032,
    10033, 10034, 10035, 10036, 10038, 10039, 10040, 10041, 10042, 10043,
    10044, 10045,
}

_CLIENT_ID = re.compile(r"^[^:\s]+:\d+$")


class Mt5Broker:
    def __init__(
        self,
        magic: int = DEFAULT_MAGIC,
        history_days: int = DEFAULT_HISTORY_DAYS,
        mt5: Any = None,
        pending_filling: int = ORDER_FILLING_RETURN,
        close_deviation: int = 50,
        market_deviation: int = 30,
    ) -> None:
        if mt5 is None:
            try:
                import MetaTrader5 as mt5  # type: ignore[no-redef]
            except ImportError as exc:
                raise RuntimeError(
                    "MetaTrader5 package missing. Windows only: "
                    "uv add MetaTrader5"
                ) from exc
        self._mt5 = mt5
        self._magic = magic
        self._history_days = history_days
        self._pending_filling = pending_filling
        # Max slippage (in points) accepted when closing at market. Closing
        # reduces risk, so this is deliberately wider than the spread.
        self._close_deviation = close_deviation
        self._market_deviation = market_deviation
        self._digits: dict[str, int] = {}

    # ---- Connection ---------------------------------------------------

    def connect(self, path: str | None = None) -> None:
        """Attach to the MT5 terminal that is already running and logged in."""
        ok = (
            self._mt5.initialize(path)
            if path
            else self._mt5.initialize()
        )
        if not ok:
            raise BrokerUnavailable(
                f"MT5 initialize failed: {self._mt5.last_error()}"
            )

    def shutdown(self) -> None:
        self._mt5.shutdown()

    # ---- Broker protocol: read side -----------------------------------

    def list_orders(self) -> list[BrokerOrder]:
        try:
            return self._list_orders()
        except BrokerUnavailable:
            raise
        except Exception as exc:  # any surprise means "state unknown"
            raise BrokerUnavailable(f"list_orders failed: {exc}") from exc

    def get_tick(self, symbol: str) -> Tick:
        try:
            # A symbol outside Market Watch returns no tick.
            self._mt5.symbol_select(symbol, True)
            raw = self._mt5.symbol_info_tick(symbol)
            if raw is None:
                raise self._fail(f"tick for {symbol}")
            digits = self._digits_for(symbol)
            bid = _dec(raw.bid, digits)
            ask = _dec(raw.ask, digits)
        except BrokerUnavailable:
            raise
        except Exception as exc:
            raise BrokerUnavailable(f"get_tick failed: {exc}") from exc
        if bid <= 0 or ask <= 0:
            raise BrokerUnavailable(f"Invalid tick for {symbol}: {bid}/{ask}")
        return Tick(bid=bid, ask=ask)

    # ---- Broker protocol: write side (stage 2) -------------------------

    def place_pending(self, request: OrderRequest) -> BrokerOrder:
        # Idempotency: if this client_id exists in ANY state, return it.
        # (Same as FakeBroker; list_orders includes recent history.)
        for existing in self.list_orders():
            if existing.client_id == request.client_id:
                return existing

        if request.side == "BUY":
            order_type = self._mt5.ORDER_TYPE_BUY_LIMIT
        elif request.side == "SELL":
            order_type = self._mt5.ORDER_TYPE_SELL_LIMIT
        else:
            raise BrokerRejected(f"Invalid side: {request.side!r}")
        if not _CLIENT_ID.match(request.client_id) or len(request.client_id) > 31:
            raise BrokerRejected(
                f"client_id {request.client_id!r} cannot be used as an MT5 comment."
            )
        digits = self._digits_for(request.symbol)
        for name in ("entry_price", "sl", "tp"):
            value = getattr(request, name)
            if value != value.quantize(Decimal(1).scaleb(-digits)):
                raise BrokerRejected(f"{name} has more than {digits} decimals.")

        result = self._send(
            {
                "action": TRADE_ACTION_PENDING,
                "symbol": request.symbol,
                "volume": float(request.volume),
                "type": order_type,
                "price": float(request.entry_price),
                "sl": float(request.sl),
                "tp": float(request.tp),
                "magic": self._magic,
                "comment": request.client_id,
                "type_time": ORDER_TIME_GTC,
                "type_filling": self._pending_filling,
            },
            "place_pending",
        )
        if not result.order:
            raise BrokerUnavailable("place_pending: broker returned no ticket.")
        return BrokerOrder(
            ticket=str(result.order),
            client_id=request.client_id,
            symbol=request.symbol,
            side=request.side,
            entry_price=request.entry_price,
            volume=request.volume,
            sl=request.sl,
            tp=request.tp,
            status=OrderStatus.PENDING,
        )

    def _check_request(self, request: OrderRequest) -> None:
        if request.side not in ("BUY", "SELL"):
            raise BrokerRejected(f"Invalid side: {request.side!r}")
        if not _CLIENT_ID.match(request.client_id) or len(request.client_id) > 31:
            raise BrokerRejected(
                f"client_id {request.client_id!r} cannot be used as an MT5 comment."
            )
        digits = self._digits_for(request.symbol)
        for name in ("entry_price", "sl", "tp"):
            value = getattr(request, name)
            if value != value.quantize(Decimal(1).scaleb(-digits)):
                raise BrokerRejected(f"{name} has more than {digits} decimals.")

    def place_market(self, request: OrderRequest) -> BrokerOrder:
        # Idempotency: same rule as place_pending.
        for existing in self.list_orders():
            if existing.client_id == request.client_id:
                return existing
        self._check_request(request)
        m = self._mt5
        raw = m.symbol_info_tick(request.symbol)
        if raw is None:
            raise self._fail(f"tick for {request.symbol}")
        if request.side == "BUY":
            order_type, price = m.ORDER_TYPE_BUY, raw.ask
        else:
            order_type, price = m.ORDER_TYPE_SELL, raw.bid
        if price <= 0:
            raise BrokerUnavailable(f"Invalid market price {price}.")
        digits = self._digits_for(request.symbol)
        result = self._send(
            {
                "action": TRADE_ACTION_DEAL,
                "symbol": request.symbol,
                "volume": float(request.volume),
                "type": order_type,
                "price": round(price, digits),
                "sl": float(request.sl),
                "tp": float(request.tp),
                "deviation": self._market_deviation,
                "magic": self._magic,
                "comment": request.client_id,
                "type_time": ORDER_TIME_GTC,
                "type_filling": self._market_filling(request.symbol),
            },
            "place_market",
        )
        if not result.order:
            raise BrokerUnavailable("place_market: broker returned no ticket.")
        fill = getattr(result, "price", 0) or 0
        entry = _dec(fill, digits) if fill > 0 else request.entry_price
        return BrokerOrder(
            ticket=str(result.order),
            client_id=request.client_id,
            symbol=request.symbol,
            side=request.side,
            entry_price=entry,
            volume=request.volume,
            sl=request.sl,
            tp=request.tp,
            status=OrderStatus.FILLED,
        )

    def cancel_pending(self, ticket: str) -> None:
        pending = self._own_pending(ticket)
        if pending is None:
            raise BrokerRejected("Only pending orders can be cancelled.")
        self._send(
            {"action": TRADE_ACTION_REMOVE, "order": int(ticket)},
            "cancel_pending",
        )

    def close_position(self, ticket: str) -> None:
        position = self._own_position(ticket)
        if position is None:
            raise BrokerRejected("Only filled orders can be closed.")
        m = self._mt5
        # Close by dealing the opposite way against this position:
        # a BUY is sold at the bid, a SELL is bought at the ask.
        raw = m.symbol_info_tick(position.symbol)
        if raw is None:
            raise self._fail(f"tick for {position.symbol}")
        digits = self._digits_for(position.symbol)
        if position.type == m.ORDER_TYPE_BUY:
            close_type, price = m.ORDER_TYPE_SELL, raw.bid
        elif position.type == m.ORDER_TYPE_SELL:
            close_type, price = m.ORDER_TYPE_BUY, raw.ask
        else:
            raise BrokerUnavailable(f"Unexpected position type {position.type}.")
        if price <= 0:
            raise BrokerUnavailable(f"Invalid close price {price}.")
        self._send(
            {
                "action": TRADE_ACTION_DEAL,
                "symbol": position.symbol,
                "volume": position.volume,
                "type": close_type,
                "position": int(ticket),
                "price": round(price, digits),
                "deviation": self._close_deviation,
                "magic": self._magic,
                "comment": "actp close",
                "type_time": ORDER_TIME_GTC,
                "type_filling": self._market_filling(position.symbol),
            },
            "close_position",
        )

    def _market_filling(self, symbol: str) -> int:
        """Pick a filling mode the symbol allows for market deals."""
        info = self._mt5.symbol_info(symbol)
        if info is None:
            raise self._fail(f"symbol_info({symbol})")
        flags = info.filling_mode  # bit 1 = FOK, bit 2 = IOC
        if flags & 1:
            return ORDER_FILLING_FOK
        if flags & 2:
            return ORDER_FILLING_IOC
        return ORDER_FILLING_RETURN

    def modify_sltp(self, ticket: str, sl: Decimal, tp: Decimal) -> None:
        pending = self._own_pending(ticket)
        if pending is not None:
            payload = {
                "action": TRADE_ACTION_MODIFY,
                "order": int(ticket),
                "price": pending.price_open,
                "sl": float(sl),
                "tp": float(tp),
                "type_time": ORDER_TIME_GTC,
            }
        else:
            position = self._own_position(ticket)
            if position is None:
                raise BrokerRejected("Only active orders can be modified.")
            payload = {
                "action": TRADE_ACTION_SLTP,
                "position": int(ticket),
                "symbol": position.symbol,
                "sl": float(sl),
                "tp": float(tp),
            }
        # 10025 "no changes" means SL/TP already match: that is success.
        self._send(payload, "modify_sltp", ok_codes={_NO_CHANGES})

    # ---- Write helpers --------------------------------------------------

    def _send(
        self,
        payload: dict[str, Any],
        what: str,
        ok_codes: set[int] | None = None,
    ) -> Any:
        """order_send with strict outcome mapping.

        Success codes pass. A code on the definite-refusal list is
        BrokerRejected. Everything else (timeouts, None, codes we do not
        know) is BrokerUnavailable: the outcome is unknown, so halt.
        """
        try:
            result = self._mt5.order_send(payload)
        except Exception as exc:
            raise BrokerUnavailable(f"{what} failed: {exc}") from exc
        if result is None:
            raise self._fail(what)
        code = result.retcode
        if code in _SUCCESS or code in (ok_codes or ()):
            return result
        detail = f"{what}: retcode {code} ({result.comment})"
        if code in _REFUSED:
            raise BrokerRejected(detail)
        raise BrokerUnavailable(detail)

    def _lookup(self, getter: Any, ticket: str, what: str) -> Any:
        """Return the one ACTP-owned object with this ticket, or None."""
        try:
            number = int(ticket)
        except (TypeError, ValueError) as exc:
            raise BrokerRejected(f"Invalid ticket: {ticket!r}") from exc
        try:
            raw = getter(ticket=number)
        except Exception as exc:
            raise BrokerUnavailable(f"{what} failed: {exc}") from exc
        if raw is None:
            # MT5 returns None for "nothing found" as well as for errors;
            # error code 1 means success, i.e. simply not found.
            if self._mt5.last_error()[0] == 1:
                return None
            raise self._fail(what)
        if not raw:
            return None
        found = raw[0]
        if found.magic != self._magic:
            raise BrokerRejected(f"Ticket {ticket} is not an ACTP order.")
        return found

    def _own_pending(self, ticket: str) -> Any:
        return self._lookup(self._mt5.orders_get, ticket, "orders_get")

    def _own_position(self, ticket: str) -> Any:
        return self._lookup(self._mt5.positions_get, ticket, "positions_get")

    # ---- Internals ----------------------------------------------------

    def _fail(self, what: str) -> BrokerUnavailable:
        return BrokerUnavailable(f"{what} failed: {self._mt5.last_error()}")

    def _digits_for(self, symbol: str) -> int:
        cached = self._digits.get(symbol)
        if cached is not None:
            return cached
        info = self._mt5.symbol_info(symbol)
        if info is None:
            raise self._fail(f"symbol_info({symbol})")
        self._digits[symbol] = int(info.digits)
        return self._digits[symbol]

    def _side(self, mt5_type: int) -> str:
        m = self._mt5
        if mt5_type in (m.ORDER_TYPE_BUY_LIMIT, m.ORDER_TYPE_BUY):
            return "BUY"
        if mt5_type in (m.ORDER_TYPE_SELL_LIMIT, m.ORDER_TYPE_SELL):
            return "SELL"
        raise BrokerUnavailable(f"Unexpected order type {mt5_type} on ACTP order.")

    def _list_orders(self) -> list[BrokerOrder]:
        m = self._mt5
        limit_types = (m.ORDER_TYPE_BUY_LIMIT, m.ORDER_TYPE_SELL_LIMIT)

        pendings = m.orders_get()
        if pendings is None:
            raise self._fail("orders_get")
        positions = m.positions_get()
        if positions is None:
            raise self._fail("positions_get")
        now = datetime.now(timezone.utc)
        history = m.history_orders_get(
            now - timedelta(days=self._history_days),
            now + timedelta(days=1),
        )
        if history is None:
            raise self._fail("history_orders_get")

        ours_history = [h for h in history if h.magic == self._magic]
        history_by_ticket = {h.ticket: h for h in ours_history}
        result: list[BrokerOrder] = []
        seen: set[str] = set()

        def add(order: BrokerOrder) -> None:
            if order.client_id in seen:
                raise BrokerUnavailable(
                    f"Two ACTP orders share client_id {order.client_id}."
                )
            seen.add(order.client_id)
            result.append(order)

        # 1. Active pending orders.
        for o in pendings:
            if o.magic != self._magic:
                continue
            if o.type not in limit_types:
                raise BrokerUnavailable(
                    f"Unexpected pending order type {o.type} (ticket {o.ticket})."
                )
            digits = self._digits_for(o.symbol)
            add(
                BrokerOrder(
                    ticket=str(o.ticket),
                    client_id=self._client_id(o.comment, o.ticket),
                    symbol=o.symbol,
                    side=self._side(o.type),
                    entry_price=_dec(o.price_open, digits),
                    volume=_dec_volume(o.volume_current),
                    sl=_dec(o.sl, digits),
                    tp=_dec(o.tp, digits),
                    status=OrderStatus.PENDING,
                )
            )

        # 2. Open positions (filled orders).
        open_ids: set[int] = set()
        for p in positions:
            if p.magic != self._magic:
                continue
            open_ids.add(p.identifier)
            # The position comment can be rewritten by the broker; the
            # originating order in history still has ours.
            comment = p.comment
            if not _CLIENT_ID.match(comment or ""):
                origin = history_by_ticket.get(p.identifier)
                comment = origin.comment if origin is not None else comment
            digits = self._digits_for(p.symbol)
            add(
                BrokerOrder(
                    ticket=str(p.ticket),
                    client_id=self._client_id(comment, p.ticket),
                    symbol=p.symbol,
                    side=self._side(p.type),
                    entry_price=_dec(p.price_open, digits),
                    volume=_dec_volume(p.volume),
                    sl=_dec(p.sl, digits),
                    tp=_dec(p.tp, digits),
                    status=OrderStatus.FILLED,
                )
            )

        # 3. Finished orders from history, so resume logic sees them.
        for h in ours_history:
            if h.type not in limit_types:
                # A layer opened at market is a plain BUY/SELL order that
                # carries our client_id. Closing orders carry "actp close"
                # or "[sl ...]", which never match the client_id pattern.
                market_types = (m.ORDER_TYPE_BUY, m.ORDER_TYPE_SELL)
                if not (h.type in market_types
                        and _CLIENT_ID.match(h.comment or "")):
                    continue
            if h.position_id in open_ids:
                continue  # filled and still open: already reported above
            if h.state == ORDER_STATE_FILLED:
                status = OrderStatus.CLOSED
            elif h.state in (
                ORDER_STATE_CANCELED,
                ORDER_STATE_EXPIRED,
                ORDER_STATE_REJECTED,
            ):
                status = OrderStatus.CANCELLED
            else:
                raise BrokerUnavailable(
                    f"Unexpected history state {h.state} (ticket {h.ticket})."
                )
            client_id = self._client_id(h.comment, h.ticket)
            if client_id in seen:
                continue  # an active order with this id wins
            digits = self._digits_for(h.symbol)
            add(
                BrokerOrder(
                    ticket=str(h.ticket),
                    client_id=client_id,
                    symbol=h.symbol,
                    side=self._side(h.type),
                    entry_price=_dec(h.price_open, digits),
                    volume=_dec_volume(h.volume_initial),
                    sl=_dec(h.sl, digits),
                    tp=_dec(h.tp, digits),
                    status=status,
                )
            )
        return result

    @staticmethod
    def _client_id(comment: str | None, ticket: int) -> str:
        if not _CLIENT_ID.match(comment or ""):
            raise BrokerUnavailable(
                f"ACTP order {ticket} has an unreadable comment {comment!r}."
            )
        return comment  # type: ignore[return-value]


def _dec(value: float, digits: int) -> Decimal:
    return Decimal(f"{value:.{digits}f}")


def _dec_volume(value: float) -> Decimal:
    return Decimal(f"{value:.2f}")

