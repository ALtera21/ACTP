"""
Stage 2 smoke test. Run from the project root:
  uv run python -m actp.smoke_mt5

REAL ACCOUNT. It places ONE 0.01-lot BUY LIMIT about 300 price units below
the market, so it cannot fill unless gold falls ~$300 within a few seconds.
It then: re-sends the same request (must not duplicate), modifies SL/TP,
cancels, and re-sends once more (must return the cancelled order, not a new
one). A cleanup step cancels the order if anything fails midway.

Needs: MT5 terminal open and logged in, Algo Trading button ON.
"""

import time
from decimal import Decimal

import MetaTrader5 as mt5

from actp.broker import OrderRequest, OrderStatus
from actp.config import LOT_PER_ORDER, SYMBOL
from actp.mt5_broker import Mt5Broker

DISTANCE = Decimal("300")
CENT = Decimal("0.01")


def main() -> None:
    broker = Mt5Broker()
    broker.connect()
    results: list[bool] = []
    client_id = f"smoke{int(time.time())}:0"

    def check(name: str, ok: bool, detail: str = "") -> None:
        results.append(ok)
        print(f"[{'PASS' if ok else 'FAIL'}] {name} {detail}")

    def mine() -> list:
        return [o for o in broker.list_orders() if o.client_id == client_id]

    def wait_for(status: OrderStatus):
        for _ in range(10):
            found = mine()
            if len(found) == 1 and found[0].status == status:
                return found[0]
            time.sleep(0.5)
        return None

    try:
        if not mt5.terminal_info().trade_allowed:
            print("Algo Trading is OFF. Turn it on in the MT5 toolbar, then rerun.")
            return
        acc = mt5.account_info()
        tick = broker.get_tick(SYMBOL)
        price = (tick.bid - DISTANCE).quantize(CENT)
        request = OrderRequest(
            client_id=client_id,
            symbol=SYMBOL,
            side="BUY",
            entry_price=price,
            volume=LOT_PER_ORDER,
            sl=price - Decimal("50"),
            tp=price + Decimal("50"),
        )
        print(f"Account {acc.server} (balance {acc.balance} {acc.currency})")
        print(f"Market bid/ask {tick.bid}/{tick.ask}")
        print(f"Will place BUY LIMIT {LOT_PER_ORDER} lot at {price}, "
              f"SL {request.sl}, TP {request.tp}, comment {client_id}")
        if input("Type YES to continue: ").strip() != "YES":
            print("Aborted.")
            return

        first = broker.place_pending(request)
        check("place_pending returns a pending order",
              first.status == OrderStatus.PENDING, f"ticket {first.ticket}")

        seen = wait_for(OrderStatus.PENDING)
        check("order is visible in list_orders as PENDING", seen is not None)
        if seen is not None:
            check("fields round-trip",
                  (seen.entry_price, seen.sl, seen.tp, seen.volume, seen.side)
                  == (price, request.sl, request.tp, LOT_PER_ORDER, "BUY"))

        again = broker.place_pending(request)
        check("same client_id returns the same ticket",
              again.ticket == first.ticket)
        check("still exactly one order for this client_id", len(mine()) == 1)

        new_sl, new_tp = request.sl - Decimal("5"), request.tp + Decimal("5")
        broker.modify_sltp(first.ticket, new_sl, new_tp)
        time.sleep(0.5)
        changed = mine()[0]
        check("modify_sltp changed SL/TP",
              (changed.sl, changed.tp) == (new_sl, new_tp),
              f"sl={changed.sl} tp={changed.tp}")
        broker.modify_sltp(first.ticket, new_sl, new_tp)
        check("modify with identical values is accepted", True)

        broker.cancel_pending(first.ticket)
        cancelled = wait_for(OrderStatus.CANCELLED)
        check("cancelled order is reported as CANCELLED (from history)",
              cancelled is not None)

        retry = broker.place_pending(request)
        check("retry after cancel returns the cancelled order, no new order",
              retry.ticket == first.ticket
              and retry.status == OrderStatus.CANCELLED
              and len(mine()) == 1)
    finally:
        for order in mine():
            if order.status == OrderStatus.PENDING:
                print(f"Cleanup: cancelling {order.ticket}")
                broker.cancel_pending(order.ticket)
        broker.shutdown()

    print()
    print("ALL PASSED" if results and all(results)
          else f"{results.count(False)} CHECK(S) FAILED. Paste this output back.")


if __name__ == "__main__":
    main()
