"""
Manual Input: build a PLAN from typed prices instead of a pasted message.

No tkinter here, so it can be tested. The form turns its fields into the same
text a WhatsApp plan has, and that text goes through the normal pipeline
(parser, validator, stale-plan check, confirm popup, duplicate guard).

Defaults for empty fields (BUY shown; SELL is mirrored). With range
low..high and width = high - low:
- Cut Loss: 30% of the width BELOW the range    -> low - 0.3 x width
- Stop Loss: <multiple from Settings> x width below the range
                                                -> low - multiple x width
- Take Profit: 1:1, one width ABOVE the range   -> high + width
"""
from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation

CUT_LOSS_FRACTION = Decimal("0.3")
CENT = Decimal("0.01")
SIDES = ("buy", "sell")


def parse_price(text: str, label: str) -> Decimal | None:
    """None for a blank field. ValueError for anything unusable."""
    raw = text.strip()
    if not raw:
        return None
    if "," in raw:
        raise ValueError(f"{label}: use a dot for decimals, like 4000.50.")
    try:
        value = Decimal(raw)
    except InvalidOperation as exc:
        raise ValueError(f"{label}: '{raw}' is not a number.") from exc
    if not value.is_finite() or value <= 0:
        raise ValueError(f"{label} must be a number above 0.")
    if value != value.quantize(CENT):
        raise ValueError(f"{label}: use at most two decimal places.")
    return value


def parse_range(
    first: str, second: str, side: str
) -> tuple[Decimal, Decimal] | None:
    """(low, high), or None when both boxes are blank."""
    name = side.upper()
    a = parse_price(first, f"{name} range")
    b = parse_price(second, f"{name} range")
    if a is None and b is None:
        return None
    if a is None or b is None:
        raise ValueError(f"{name} range needs both prices.")
    if a == b:
        raise ValueError(f"{name} range needs two different prices.")
    return min(a, b), max(a, b)


def _q(value: Decimal) -> Decimal:
    return value.quantize(CENT, rounding=ROUND_HALF_UP)


def default_cut_loss(side: str, low: Decimal, high: Decimal) -> Decimal:
    offset = (high - low) * CUT_LOSS_FRACTION
    return _q(low - offset) if side == "buy" else _q(high + offset)


def default_stop_loss(
    side: str, low: Decimal, high: Decimal, multiple: Decimal
) -> Decimal:
    offset = (high - low) * multiple
    return _q(low - offset) if side == "buy" else _q(high + offset)


def default_take_profit(side: str, low: Decimal, high: Decimal) -> Decimal:
    width = high - low
    return _q(high + width) if side == "buy" else _q(low - width)


@dataclass(frozen=True)
class ManualSide:
    side: str  # "buy" or "sell"
    low: Decimal
    high: Decimal
    cut_loss: Decimal
    sl: Decimal
    tp: Decimal


def build_side(
    side: str,
    first: str,
    second: str,
    cut_loss: str,
    sl: str,
    tp: str,
    sl_multiple: Decimal,
) -> ManualSide | None:
    """None when the range is blank (the side is not part of the plan)."""
    rng = parse_range(first, second, side)
    if rng is None:
        return None
    low, high = rng
    name = side.upper()
    cl = parse_price(cut_loss, f"{name} cut loss")
    stop = parse_price(sl, f"{name} stop loss")
    profit = parse_price(tp, f"{name} take profit")
    return ManualSide(
        side=side, low=low, high=high,
        cut_loss=cl if cl is not None else default_cut_loss(side, low, high),
        sl=stop if stop is not None
        else default_stop_loss(side, low, high, sl_multiple),
        tp=profit if profit is not None
        else default_take_profit(side, low, high),
    )


def collect(
    which: str, fields: dict[str, str], sl_multiple: Decimal
) -> list[ManualSide]:
    """which: 'buy', 'sell' or 'both'. fields keys: '<side>_a', '<side>_b'
    (range), '<side>_cl', '<side>_sl', '<side>_tp'. With 'both', a side whose
    range is blank is simply left out."""
    wanted = SIDES if which == "both" else (which,)
    out: list[ManualSide] = []
    for side in wanted:
        built = build_side(
            side, fields[f"{side}_a"], fields[f"{side}_b"],
            fields[f"{side}_cl"], fields[f"{side}_sl"], fields[f"{side}_tp"],
            sl_multiple,
        )
        if built is None:
            if which != "both":
                raise ValueError(f"Fill the {side.upper()} range first.")
            continue
        out.append(built)
    if not out:
        raise ValueError("Fill at least one range (buy or sell).")
    return out


def _money(value: Decimal) -> str:
    return format(value.quantize(CENT), "f")


def build_plan_text(sides: list[ManualSide], layers: int) -> str:
    """The same text the signal provider sends, so the parser reads it."""
    lines = ["PLAN XAUUSDr", ""]
    for s in sides:
        lines += [
            f"{s.side.upper()} LIMIT ({layers} layer, manual)",
            # The provider writes BUY areas high-to-low and SELL low-to-high.
            f"Area: {_money(s.high)} - {_money(s.low)}"
            if s.side == "buy"
            else f"Area: {_money(s.low)} - {_money(s.high)}",
            f"Cut Loss: {_money(s.cut_loss)}",
            f"SL: {_money(s.sl)} | TP: {_money(s.tp)}",
            "",
        ]
    return "\n".join(lines).strip() + "\n"


def default_notes(
    side: str, fields: dict[str, str], sl_multiple: Decimal
) -> dict[str, str]:
    """Grey hints under the empty boxes: what ACTP will use."""
    notes = {"cl": "", "sl": "", "tp": ""}
    try:
        rng = parse_range(fields[f"{side}_a"], fields[f"{side}_b"], side)
    except ValueError:
        rng = None
    direction = "below" if side == "buy" else "above"
    if not fields[f"{side}_cl"].strip():
        notes["cl"] = f"empty = 30% of the range {direction} it" + (
            f": {_money(default_cut_loss(side, *rng))}" if rng else "")
    if not fields[f"{side}_sl"].strip():
        notes["sl"] = (
            f"empty = {format(sl_multiple.normalize(), 'f')} x the range "
            f"{direction} it"
            + (f": {_money(default_stop_loss(side, *rng, sl_multiple))}"
               if rng else ""))
    if not fields[f"{side}_tp"].strip():
        away = "above" if side == "buy" else "below"
        notes["tp"] = f"empty = 1:1 of the range, {away} it" + (
            f": {_money(default_take_profit(side, *rng))}" if rng else "")
    return notes
