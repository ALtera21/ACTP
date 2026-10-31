"""
Stage 3 smoke test. Run from the project root:
  uv run python -m actp.smoke_fill_mt5

REAL ACCOUNT, REAL MONEY (tiny). It opens ONE 0.01-lot BUY at market, then
modifies SL/TP on the open position and closes it, all within seconds. The
cost is roughly the spread plus a little slippage. It also checks that your
non-ACTP orders and positions are untouched.

Needs: MT5 open and logged in, Algo Trading ON, market open.
"""

import time
from decimal import Decimal

import MetaTrader5 as mt5

from actp.broker import BrokerRejected, OrderStatus
from actp.config import LOT_PER_ORDER, SYMBOL
from actp.mt5_broker import DEFAULT_MAGIC, Mt5Broker

ACTIVE = (OrderStatus.PENDING, OrderStatus.FILLED)


def foreign_count() -> int:
    orders = [o for o in (mt5.orders_get() or ()) if o.magic != DEFAULT_MAGIC]
    positions = [p for p in (mt5.positions_get() or ()) if p.magic != DEFAULT_MAGIC]
    return len(orders) + len(positions)


def main() -> None:
    broker = Mt5Broker()
    broker.connect()
    results: list[bool] = []
    client_id = f"fill{int(time.time())}:0"

    def check(name: str, ok: bool, detail: str = "") -> None:
        results.append(ok)
        print(f"[{'PASS' if ok else 'FAIL'}] {name} {detail}")

    def mine() -> list:
        return [o for o in broker.list_orders() if o.client_id == client_id]

    def wait_for(predicate):
        for _ in range(10):
            found = mine()
            if predicate(found):
                return found
            time.sleep(0.5)
        return None

    try:
        if not mt5.terminal_info().trade_allowed:
            print("Algo Trading is OFF. Turn it on, then rerun.")
            return
        acc = mt5.account_info()
        tick = broker.get_tick(SYMBOL)
        before_balance = acc.balance
        before_foreign = foreign_count()
        print(f"Account {acc.server}, balance {acc.balance} {acc.currency}")
        print(f"Market {tick.bid}/{tick.ask}. Will BUY {LOT_PER_ORDER} lot "
              f"at market, then close it. Comment {client_id}")
        if input("Type YES to continue: ").strip() != "YES":
            print("Aborted.")
            return

        info = mt5.symbol_info(SYMBOL)
        filling = 0 if info.filling_mode & 1 else 1 if info.filling_mode & 2 else 2
        result = mt5.order_send({
            "action": 1,  # TRADE_ACTION_DEAL
            "symbol": SYMBOL,
            "volume": float(LOT_PER_ORDER),
            "type": mt5.ORDER_TYPE_BUY,
            "price": float(tick.ask),
            "deviation": 50,
            "magic": DEFAULT_MAGIC,
            "comment": client_id,
            "type_time": 0,
            "type_filling": filling,
        })
        ok = result is not None and result.retcode == 10009
        check("market BUY opened", ok,
              "" if result is None else f"retcode {result.retcode} {result.comment}")
        if not ok:
            return

        filled = wait_for(lambda f: len(f) == 1 and f[0].status == OrderStatus.FILLED)
        check("position is visible as FILLED with our client_id", filled is not None)
        if filled is None:
            return
        order = filled[0]
        print(f"        ticket {order.ticket}, entry {order.entry_price}")

        new_sl = order.entry_price - Decimal("50")
        new_tp = order.entry_price + Decimal("50")
        broker.modify_sltp(order.ticket, new_sl, new_tp)
        time.sleep(0.5)
        changed = mine()[0]
        check("modify_sltp on an open position", (changed.sl, changed.tp) == (new_sl, new_tp),
              f"sl={changed.sl} tp={changed.tp}")

        broker.close_position(order.ticket)
        gone = wait_for(lambda f: not any(o.status in ACTIVE for o in f))
        check("position is closed (no active order for this client_id)", gone is not None)
        check("position no longer exists in MT5",
              not mt5.positions_get(ticket=int(order.ticket)))

        try:
            broker.close_position(order.ticket)
            check("closing again is refused", False)
        except BrokerRejected:
            check("closing again is refused", True)

        check("non-ACTP orders and positions untouched",
              foreign_count() == before_foreign)
        after = mt5.account_info().balance
        print(f"Balance {before_balance} -> {after} {acc.currency} "
              "(also moves with your other open positions)")
    finally:
        for order in mine():
            if order.status == OrderStatus.FILLED:
                print(f"Cleanup: closing {order.ticket}")
                broker.close_position(order.ticket)
        broker.shutdown()

    print()
    print("ALL PASSED" if results and all(results)
          else f"{results.count(False)} CHECK(S) FAILED. Paste this output back.")


if __name__ == "__main__":
    main()
