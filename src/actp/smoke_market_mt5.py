"""Stage 4 smoke test: place_market. REAL ACCOUNT, tiny (0.01 lot)."""
import time
from decimal import Decimal

import MetaTrader5 as mt5

from actp.broker import OrderRequest, OrderStatus
from actp.config import LOT_PER_ORDER, SYMBOL
from actp.mt5_broker import Mt5Broker


def main() -> None:
    broker = Mt5Broker()
    broker.connect()
    results: list[bool] = []
    client_id = f"mkt{int(time.time())}:0"

    def check(name: str, ok: bool, detail: str = "") -> None:
        results.append(ok)
        print(f"[{'PASS' if ok else 'FAIL'}] {name} {detail}")

    def mine():
        return [o for o in broker.list_orders() if o.client_id == client_id]

    try:
        if not mt5.terminal_info().trade_allowed:
            print("Algo Trading is OFF.")
            return
        tick = broker.get_tick(SYMBOL)
        request = OrderRequest(
            client_id=client_id, symbol=SYMBOL, side="BUY",
            entry_price=tick.ask, volume=LOT_PER_ORDER,
            sl=tick.bid - Decimal("50"), tp=tick.ask + Decimal("50"),
        )
        print(f"Will BUY {LOT_PER_ORDER} lot at market ({tick.ask}), then close.")
        if input("Type YES to continue: ").strip() != "YES":
            return

        first = broker.place_market(request)
        check("returns FILLED", first.status == OrderStatus.FILLED,
              f"ticket {first.ticket} entry {first.entry_price}")
        time.sleep(1)
        found = mine()
        check("visible as one FILLED order",
              len(found) == 1 and found[0].status == OrderStatus.FILLED)
        check("ticket is the real position ticket",
              bool(mt5.positions_get(ticket=int(first.ticket))))

        again = broker.place_market(request)
        check("same client_id returns same ticket, no 2nd position",
              again.ticket == first.ticket
              and len(mt5.positions_get(symbol=SYMBOL) or ()) >= 1
              and len(mine()) == 1)

        broker.close_position(first.ticket)
        time.sleep(1)
        after = mine()
        check("after closing, history reports it as CLOSED",
              len(after) == 1 and after[0].status == OrderStatus.CLOSED,
              str([o.status.value for o in after]))
        retry = broker.place_market(request)
        check("resend after close does NOT buy again",
              retry.ticket == first.ticket
              and retry.status == OrderStatus.CLOSED)
    finally:
        for o in mine():
            if o.status == OrderStatus.FILLED:
                print(f"Cleanup: closing {o.ticket}")
                broker.close_position(o.ticket)
        broker.shutdown()
        print("ALL PASSED" if results and all(results)
              else f"{results.count(False)} CHECK(S) FAILED. Paste output back.")


if __name__ == "__main__":
    main()