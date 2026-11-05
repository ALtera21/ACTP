"""
MultiLayer: per-layer lot multipliers typed in the main window.

"1, 1, 1, 1, 2, 2" = 6 layers. Layer 1 is the first price the market
reaches (BUY: highest entry, SELL: lowest entry). Each number multiplies the
base lot (LOT_PER_ORDER). The list also sets the layer count: the plan's area
is split evenly into that many layers.

Weights are stored inside the plan as TEXT (["1", "1", "2"], layer 1 first)
so they survive the JSON plan store and take part in the "same message id,
different plan" safety check.
"""
import re
from copy import deepcopy
from decimal import Decimal, InvalidOperation
from typing import Any

MAX_WEIGHT = Decimal("10")
MIN_LAYERS = 2
MAX_LAYERS = 12
LOT_STEP = Decimal("0.01")


def fmt_weight(value: Decimal) -> str:
    return format(value.normalize(), "f")


def _weight(value: Any) -> Decimal:
    try:
        w = Decimal(str(value).strip())
    except InvalidOperation as exc:
        raise ValueError(f"'{value}' is not a number.") from exc
    if not w.is_finite() or not 0 < w <= MAX_WEIGHT:
        raise ValueError(
            f"Each number must be above 0 and at most {MAX_WEIGHT}."
        )
    return w


def parse_weights(text: str) -> list[Decimal] | None:
    """None for a blank box. ValueError for anything unusable."""
    parts = [p for p in re.split(r"[,;\s]+", text.strip()) if p]
    if not parts:
        return None
    weights = [_weight(p) for p in parts]
    if not MIN_LAYERS <= len(weights) <= MAX_LAYERS:
        raise ValueError(
            f"Use {MIN_LAYERS} to {MAX_LAYERS} numbers (one per layer); "
            f"you typed {len(weights)}."
        )
    return weights


def check_weights(raw: Any, layers: int, side: str) -> None:
    """Validate weights as stored in a plan."""
    if not isinstance(raw, list) or len(raw) != layers:
        raise ValueError(
            f"{side.upper()} weights must be a list with one entry per layer."
        )
    for item in raw:
        if not isinstance(item, str):
            raise ValueError(f"{side.upper()} weights must be stored as text.")
        _weight(item)


def apply_weights(plan: dict[str, Any], weights: list[Decimal] | None):
    """Return a copy of a PLAN with the weights and layer count applied.
    Blank weights, or a non-PLAN message, come back unchanged."""
    if not weights or plan.get("type") != "PLAN":
        return plan
    out = deepcopy(plan)
    texts = [fmt_weight(w) for w in weights]
    for side in ("buy", "sell"):
        if out.get(side) is not None:
            out[side]["layers"] = len(weights)
            out[side]["weights"] = list(texts)
    return out


def lots_for_weights(weights: list[Decimal], base_lot: Decimal) -> list[Decimal]:
    lots = []
    for w in weights:
        lot = base_lot * w
        if lot != lot.quantize(LOT_STEP):
            raise ValueError(
                f"{fmt_weight(w)} x {base_lot} = {lot}, not a multiple of "
                f"{LOT_STEP}. Use whole numbers or a bigger base lot."
            )
        lots.append(lot.quantize(LOT_STEP))
    return lots


def lots_by_layer(details: dict[str, Any], base_lot: Decimal) -> list[Decimal]:
    """Lots of one plan side, layer 1 first. No weights = base lot each."""
    raw = details.get("weights")
    layers = details["layers"]
    weights = [Decimal(w) for w in raw] if raw else [Decimal(1)] * layers
    if len(weights) != layers:
        raise ValueError("weights and layers do not match.")
    return lots_for_weights(weights, base_lot)


def volumes_for(side: str, details: dict[str, Any], base_lot: Decimal):
    """Lots in ASCENDING PRICE order (the order plan_builder creates orders).
    BUY layer 1 is the highest price, so BUY is reversed."""
    lots = lots_by_layer(details, base_lot)
    return lots[::-1] if side == "buy" else lots