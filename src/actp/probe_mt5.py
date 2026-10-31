"""
Read-only MT5 probe. Sends NO orders.

Before running:
  1. Install and open the MT5 desktop terminal, logged in to your account.
  2. uv add MetaTrader5      (Windows only)

Run from the project root:
  uv run python -m actp.probe_mt5

It prints what Mt5Broker needs to know and ends with a list of WARNINGS.
Paste the whole output back (hide your account number if you like).
The only thing it touches is Market Watch (it adds the symbol, which is
harmless). It never calls order_send.
"""

from datetime import datetime, timezone
from decimal import Decimal

import MetaTrader5 as mt5

from actp.config import LOT_PER_ORDER, SYMBOL
from actp.mt5_broker import DEFAULT_MAGIC, Mt5Broker

# The Python MetaTrader5 package does not export these two, so use the
# documented values.
SYMBOL_ORDER_LIMIT = 2  # bit in symbol_info.order_mode: limit orders allowed
SYMBOL_TRADE_MODE_FULL = 4

MARGIN_MODES = {
    mt5.ACCOUNT_MARGIN_MODE_RETAIL_NETTING: "NETTING",
    mt5.ACCOUNT_MARGIN_MODE_EXCHANGE: "EXCHANGE",
    mt5.ACCOUNT_MARGIN_MODE_RETAIL_HEDGING: "HEDGING",
}
TRADE_MODES = {
    mt5.ACCOUNT_TRADE_MODE_DEMO: "DEMO",
    mt5.ACCOUNT_TRADE_MODE_CONTEST: "CONTEST",
    mt5.ACCOUNT_TRADE_MODE_REAL: "REAL",
}


def section(title: str) -> None:
    print(f"\n=== {title} ===")


def main() -> None:
    warnings: list[str] = []
    broker = Mt5Broker()
    broker.connect()
    try:
        # ---- Terminal -------------------------------------------------
        section("Terminal")
        term = mt5.terminal_info()
        print(f"Connected to broker : {term.connected}")
        print(f"Algo trading allowed: {term.trade_allowed}")
        print(f"Build               : {term.build}")
        if not term.connected:
            warnings.append("Terminal is not connected to the broker.")
        if not term.trade_allowed:
            warnings.append(
                "Algo Trading is OFF in the terminal (toolbar button). "
                "It must be ON before orders can be sent."
            )

        # ---- Account --------------------------------------------------
        section("Account")
        acc = mt5.account_info()
        mode = MARGIN_MODES.get(acc.margin_mode, str(acc.margin_mode))
        kind = TRADE_MODES.get(acc.trade_mode, str(acc.trade_mode))
        print(f"Server      : {acc.server}")
        print(f"Type        : {kind}")
        print(f"Currency    : {acc.currency}")
        print(f"Balance     : {acc.balance}")
        print(f"Leverage    : 1:{acc.leverage}")
        print(f"Margin mode : {mode}")
        print(f"Trade allowed (account / expert): "
              f"{acc.trade_allowed} / {acc.trade_expert}")
        if mode != "HEDGING":
            warnings.append(
                f"Account margin mode is {mode}. ACTP needs HEDGING: on "
                "netting, the 6 layers of a side merge into one position."
            )
        if not acc.trade_allowed or not acc.trade_expert:
            warnings.append("Trading or expert trading is disabled on the account.")

        # ---- Symbol ---------------------------------------------------
        section(f"Symbol {SYMBOL}")
        mt5.symbol_select(SYMBOL, True)
        info = mt5.symbol_info(SYMBOL)
        if info is None:
            warnings.append(f"Symbol {SYMBOL} does not exist on this broker.")
            similar = mt5.symbols_get("*XAU*") or ()
            print("Symbols containing XAU:", [s.name for s in similar])
        else:
            print(f"Digits / point        : {info.digits} / {info.point}")
            print(f"Volume min/step/max   : "
                  f"{info.volume_min} / {info.volume_step} / {info.volume_max}")
            print(f"Stops level (points)  : {info.trade_stops_level} "
                  f"(= {info.trade_stops_level * info.point:.{info.digits}f} price)")
            print(f"Freeze level (points) : {info.trade_freeze_level}")
            print(f"Trade mode            : {info.trade_mode} "
                  f"(4 = full trading)")
            print(f"Filling mode flags    : {info.filling_mode} "
                  f"(1 = FOK, 2 = IOC)")
            print(f"Order mode flags      : {info.order_mode} "
                  f"(limit orders need bit {SYMBOL_ORDER_LIMIT})")
            print(f"Spread (points)       : {info.spread}")

            if info.digits != 2:
                warnings.append(
                    f"Symbol has {info.digits} digits, but ACTP prices use 2."
                )
            vmin = Decimal(str(info.volume_min))
            vstep = Decimal(str(info.volume_step))
            vmax = Decimal(str(info.volume_max))
            if not vmin <= LOT_PER_ORDER <= vmax or (
                (LOT_PER_ORDER - vmin) % vstep != 0
            ):
                warnings.append(
                    f"LOT_PER_ORDER {LOT_PER_ORDER} is not valid for this "
                    f"symbol (min {vmin}, step {vstep}, max {vmax})."
                )
            if not info.order_mode & SYMBOL_ORDER_LIMIT:
                warnings.append("Symbol does not allow limit orders.")
            if info.trade_mode != SYMBOL_TRADE_MODE_FULL:
                warnings.append("Symbol is not in full trading mode right now.")

        # ---- Tick -----------------------------------------------------
        section("Tick")
        tick = broker.get_tick(SYMBOL)
        raw = mt5.symbol_info_tick(SYMBOL)
        print(f"Bid / Ask : {tick.bid} / {tick.ask}")
        print(f"Tick time : {datetime.fromtimestamp(raw.time, timezone.utc)} UTC")
        print("(Tick time is broker server time; it may differ from your "
              "clock by hours. Run twice: it should change if the market is open.)")

        # ---- ACTP orders ----------------------------------------------
        section(f"ACTP orders (magic {DEFAULT_MAGIC}), incl. recent history")
        orders = broker.list_orders()
        if not orders:
            print("None.")
        for o in orders:
            print(f"{o.ticket:>10} {o.client_id:<28} {o.side:<4} "
                  f"{o.status.value:<9} entry={o.entry_price} "
                  f"sl={o.sl} tp={o.tp} vol={o.volume}")

        # ---- Foreign orders (informational) ---------------------------
        section("Other orders/positions on this account (not ACTP's)")
        others = [
            o for o in (mt5.orders_get() or ()) if o.magic != DEFAULT_MAGIC
        ] + [p for p in (mt5.positions_get() or ()) if p.magic != DEFAULT_MAGIC]
        print(f"{len(others)} found. ACTP ignores these.")

    finally:
        broker.shutdown()

    section("WARNINGS")
    if warnings:
        for w in warnings:
            print(f"- {w}")
    else:
        print("None. Ready for stage 2 (single-order smoke test).")


if __name__ == "__main__":
    main()