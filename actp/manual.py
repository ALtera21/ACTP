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

One price on each side (the "both" button): the typed prices are the two
DISTAL ends of the plan, the SELL price the ceiling and the BUY price the
floor. With span = ceiling - floor, the plan is laid out as fibonacci-style
levels (0 = floor, 1 = ceiling):
    0     BUY distal          0.5   TP of both sides
    0.25  BUY proximal        0.75  SELL proximal
    1     SELL distal
and the defaults above then give Cut Loss -0.075 / 1.075 and (with the
default multiple of 3) Stop Loss -0.75 / 1.75.
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


def single_price(first: str, second: str, side: str) -> Decimal | None:
    """The one price typed in a range, or None when both boxes or neither
    box are filled."""
    name = side.upper()
    a = parse_price(first, f"{name} range")
    b = parse_price(second, f"{name} range")
    if (a is None) == (b is None):
        return None
    return a if a is not None else b


def distal_layout(floor: Decimal, ceiling: Decimal) -> dict[str, object]:
    """BUY distal = floor, SELL distal = ceiling. Each proximal end sits a
    quarter of the span in from its distal end, so the TP of both sides is
    exactly in the middle."""
    if ceiling <= floor:
        raise ValueError(
            "The SELL price must be above the BUY price (SELL is the "
            "ceiling, BUY is the floor).")
    span = ceiling - floor
    if span < Decimal("0.04"):
        raise ValueError("The BUY and SELL prices are too close together.")
    quarter = _q(span / 4)
    return {
        "buy": (floor, _q(floor + quarter)),
        "sell": (_q(ceiling - quarter), ceiling),
        "tp": _q(floor + span / 2),
    }


def auto_layout(fields: dict[str, str]) -> dict[str, object] | None:
    """The layout when BUY and SELL each have exactly one price typed,
    else None."""
    buy = single_price(fields["buy_a"], fields["buy_b"], "buy")
    sell = single_price(fields["sell_a"], fields["sell_b"], "sell")
    if buy is None or sell is None:
        return None
    return distal_layout(buy, sell)


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
    given_range: tuple[Decimal, Decimal] | None = None,
    default_tp: Decimal | None = None,
) -> ManualSide | None:
    """None when the range is blank (the side is not part of the plan).
    `given_range` replaces the two range boxes and `default_tp` the 1:1
    take profit (used by the one-price-per-side layout)."""
    rng = given_range or parse_range(first, second, side)
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
        else (default_tp if default_tp is not None
              else default_take_profit(side, low, high)),
    )


def collect(
    which: str, fields: dict[str, str], sl_multiple: Decimal
) -> list[ManualSide]:
    """which: 'buy', 'sell' or 'both'. fields keys: '<side>_a', '<side>_b'
    (range), '<side>_cl', '<side>_sl', '<side>_tp'. With 'both', a side whose
    range is blank is simply left out."""
    wanted = SIDES if which == "both" else (which,)
    layout = auto_layout(fields) if which == "both" else None
    out: list[ManualSide] = []
    for side in wanted:
        built = build_side(
            side, fields[f"{side}_a"], fields[f"{side}_b"],
            fields[f"{side}_cl"], fields[f"{side}_sl"], fields[f"{side}_tp"],
            sl_multiple,
            given_range=None if layout is None else layout[side],
            default_tp=None if layout is None else layout["tp"],
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
    notes = {"range": "", "cl": "", "sl": "", "tp": ""}
    try:
        layout = auto_layout(fields)  # one price typed on each side
    except ValueError:
        layout = None
    try:
        rng = parse_range(fields[f"{side}_a"], fields[f"{side}_b"], side)
    except ValueError:
        rng = None
    tp_default = None
    if layout is not None:  # one price per side: the rest is worked out
        rng = layout[side]
        tp_default = layout["tp"]
        notes["range"] = (
            f"distal {'floor' if side == 'buy' else 'ceiling'}: the range "
            f"will be {_money(rng[0])} - {_money(rng[1])}")
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
        if tp_default is not None:
            notes["tp"] = f"empty = the middle of both distals: {_money(tp_default)}"
        else:
            notes["tp"] = f"empty = 1:1 of the range, {away} it" + (
                f": {_money(default_take_profit(side, *rng))}" if rng else "")
    return notes
