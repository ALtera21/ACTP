"""
STOP CUT LOSS (SCL) level.

SCL sits between the Cut Loss and the broker SL, a fixed fraction of the
way from Cut Loss to SL:
    BUY : SL < SCL < Cut Loss
    SELL: Cut Loss < SCL < SL
The signal provider never gives it, so it is derived from the plan.
"""

from decimal import ROUND_HALF_UP, Decimal

_CENT = Decimal("0.01")


def scl_for(
    side: str,
    cut_loss: Decimal,
    sl: Decimal,
    fraction: Decimal,
) -> Decimal:
    """
    Price of the SCL line. `fraction` is how far from Cut Loss to SL it
    sits (0.25 = a quarter of the way). If the plan is malformed (SL not
    beyond Cut Loss), SCL equals the Cut Loss, i.e. the old behaviour.
    """
    if side not in ("BUY", "SELL"):
        raise ValueError(f"Unknown side: {side!r}")
    fraction = Decimal(fraction)
    if not fraction.is_finite() or not Decimal(0) < fraction < Decimal(1):
        raise ValueError("SCL fraction must be between 0 and 1 (exclusive).")
    distance = cut_loss - sl if side == "BUY" else sl - cut_loss
    if distance <= 0:
        return cut_loss
    step = (distance * fraction).quantize(_CENT, rounding=ROUND_HALF_UP)
    return cut_loss - step if side == "BUY" else cut_loss + step


def reached(side: str, price: Decimal, level: Decimal) -> bool:
    """True when price is at or beyond `level` on the losing side."""
    return price <= level if side == "BUY" else price >= level
