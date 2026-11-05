from typing import Any

from actp.config import LOT_PER_ORDER, SYMBOL
from actp.price_calculator import calculate_entry_prices
from actp.layers import volumes_for

def build_plan_preview(plan: dict[str, Any]) -> list[dict[str, Any]]:
    """
    Build a dry-run preview of pending orders from a parsed PLAN.

    This function does not connect to MT5 or submit orders.
    """
    if plan.get("type") != "PLAN":
        raise ValueError("Expected a parsed PLAN message.")

    if plan.get("symbol") != SYMBOL:
        raise ValueError("Refusing to build a plan for an unauthorized symbol.")

    orders: list[dict[str, Any]] = []

    for side in ("buy", "sell"):
        details = plan.get(side)

        # A plan may contain only BUY or only SELL.
        if details is None:
            continue

        if not isinstance(details, dict):
            raise ValueError(f"Invalid {side.upper()} section.")

        layers = details.get("layers")
        if isinstance(layers, bool) or not isinstance(layers, int):
            raise ValueError(f"Invalid {side.upper()} layer count.")

        prices = calculate_entry_prices(
            details["area_start"],
            details["area_end"],
            layers=layers,
        )

        volumes = volumes_for(side, details, LOT_PER_ORDER)
        for price, volume in zip(prices, volumes):
            orders.append(
                {
                    "symbol": SYMBOL,
                    "side": side.upper(),
                    "order_type": (
                        "BUY_LIMIT" if side == "buy" else "SELL_LIMIT"
                    ),
                    "entry_price": price,
                    "volume": volume,
                    "sl": details["sl"],
                    "tp": details["tp"],
                    "cut_loss": details["cut_loss"],
                }
            )

    if not orders:
        raise ValueError("The plan contains no orders.")

    return orders
