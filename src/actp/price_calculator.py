
from decimal import Decimal, ROUND_HALF_UP


CENT = Decimal("0.01")


def calculate_entry_prices(
    price_a: Decimal | str | int | float,
    price_b: Decimal | str | int | float,
    layers: int = 6,
) -> list[Decimal]:
    """
    Calculate evenly spaced entry prices, including both endpoints.

    Prices are sorted from lowest to highest and rounded to two
    decimal places using conventional ROUND_HALF_UP rounding.

    This function calculates prices only; it does not place orders.
    """
    if isinstance(layers, bool) or not isinstance(layers, int) or layers < 2:
        raise ValueError("layers must be an integer of at least 2")

    try:
        low = Decimal(str(price_a))
        high = Decimal(str(price_b))
    except (ValueError, TypeError, ArithmeticError) as exc:
        raise ValueError("Prices must be valid decimal numbers") from exc

    if not low.is_finite() or not high.is_finite():
        raise ValueError("Prices must be finite numbers")

    low, high = sorted((low, high))

    if low == high:
        raise ValueError("The entry range must have a positive width")

    # Both endpoints must be representable at the required precision.
    low_rounded = low.quantize(CENT, rounding=ROUND_HALF_UP)
    high_rounded = high.quantize(CENT, rounding=ROUND_HALF_UP)

    if low != low_rounded or high != high_rounded:
        raise ValueError("Range endpoints must have at most two decimal places")

    interval = (high - low) / Decimal(layers - 1)
    prices = [
        (low + interval * i).quantize(CENT, rounding=ROUND_HALF_UP)
        for i in range(layers)
    ]

    # Rounding must not collapse two levels into the same price.
    if any(prices[i] >= prices[i + 1] for i in range(len(prices) - 1)):
        raise ValueError(
            "The range is too narrow to produce distinct prices "
            "at two decimal places"
        )

    return prices
