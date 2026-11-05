from actp.layers import check_weights

from decimal import Decimal, InvalidOperation
from typing import Any

from actp.config import SOURCE_SYMBOL, SYMBOL
from actp.price_calculator import calculate_entry_prices


MAX_LAYERS = 20
CENT = Decimal("0.01")


def _validate_price(value: Any, field_name: str, side: str) -> None:
    """Require a positive, finite Decimal with at most two decimal places."""
    if not isinstance(value, Decimal) or not value.is_finite():
        raise ValueError(
            f"{side.upper()} {field_name} must be a finite Decimal."
        )

    if value <= 0:
        raise ValueError(
            f"{side.upper()} {field_name} must be greater than zero."
        )

    try:
        rounded = value.quantize(CENT)
    except InvalidOperation as exc:
        raise ValueError(
            f"{side.upper()} {field_name} is outside the supported precision range."
        ) from exc

    if value != rounded:
        raise ValueError(
            f"{side.upper()} {field_name} must have at most two decimal places."
        )


def validate_plan(plan: dict[str, Any]) -> None:
    """
    Validate a parsed PLAN before generating its order preview.

    Raises ValueError when the plan fails a safety check.
    Does not connect to MT5 or place orders.
    """
    if not isinstance(plan, dict):
        raise ValueError("Plan must be a dictionary.")

    if plan.get("type") != "PLAN":
        raise ValueError("Only PLAN messages can be validated for order creation.")

    if plan.get("symbol") != SYMBOL:
        raise ValueError("Unauthorized trading symbol.")

    if plan.get("source_symbol") != SOURCE_SYMBOL:
        raise ValueError("Unexpected source symbol.")

    found_side = False

    for side in ("buy", "sell"):
        details = plan.get(side)

        if details is None:
            continue

        found_side = True

        if not isinstance(details, dict):
            raise ValueError(f"Invalid {side.upper()} section.")

        layers = details.get("layers")

        if (
            isinstance(layers, bool)
            or not isinstance(layers, int)
            or not 2 <= layers <= MAX_LAYERS
        ):
            raise ValueError(
                f"{side.upper()} layers must be between 2 and {MAX_LAYERS}."
            )

        if details.get("weights") is not None:
            check_weights(details["weights"], layers, side)

        required_fields = (
            "area_start",
            "area_end",
            "cut_loss",
            "sl",
            "tp",
        )

        for field in required_fields:
            _validate_price(details.get(field), field, side)

        low = min(details["area_start"], details["area_end"])
        high = max(details["area_start"], details["area_end"])

        if low >= high:
            raise ValueError(
                f"{side.upper()} entry range must have positive width."
            )

        # Verify that the requested range can produce distinct entry prices.
        try:
            prices = calculate_entry_prices(
                low,
                high,
                layers=layers,
            )
        except ValueError as exc:
            raise ValueError(
                f"Invalid {side.upper()} entry prices: {exc}"
            ) from exc

        if len(prices) != layers or len(set(prices)) != layers:
            raise ValueError(
                f"{side.upper()} entry prices must be distinct."
            )

        if side == "buy":
            if details["cut_loss"] >= low:
                raise ValueError(
                    "BUY Cut Loss must be below the entire entry range."
                )
            if details["sl"] >= low:
                raise ValueError(
                    "BUY Stop Loss must be below the entire entry range."
                )
            if details["tp"] <= high:
                raise ValueError(
                    "BUY Take Profit must be above the entire entry range."
                )
        else:
            if details["cut_loss"] <= high:
                raise ValueError(
                    "SELL Cut Loss must be above the entire entry range."
                )
            if details["sl"] <= high:
                raise ValueError(
                    "SELL Stop Loss must be above the entire entry range."
                )
            if details["tp"] >= low:
                raise ValueError(
                    "SELL Take Profit must be below the entire entry range."
                )

    if not found_side:
        raise ValueError("Plan must contain a BUY or SELL section.")
