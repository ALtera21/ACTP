"""
Stale-plan check: compares a parsed PLAN with the live price BEFORE anything
is sent. Pure logic, no broker calls.

Rules (agreed for ACTP):
- A BUY layer whose entry is at or above the ask would fill at once as a
  limit order, so it is bought at market. A SELL layer whose entry is at or
  below the bid is sold at market. Everything else stays pending.
- If price is already through a side's Cut Loss or SL, the whole plan is
  refused: those positions would be closed within a minute anyway.
- If more than `cap` layers of one side would fill at market, the whole plan
  is refused and the operator decides by hand.
"""
from collections.abc import Collection
from dataclasses import dataclass
from decimal import Decimal
from typing import Any

from actp.broker import Tick
from actp.config import MAX_MARKET_LAYERS_PER_SIDE


def is_marketable(side: str, entry: Decimal, tick: Tick) -> bool:
    """True if a limit order at `entry` would fill the moment it is placed."""
    if side == "BUY":
        return entry >= tick.ask
    return entry <= tick.bid


def stop_breach(side: str, section: dict[str, Any], tick: Tick) -> str | None:
    """Return a reason if price is already through the Cut Loss or SL."""
    if side == "BUY":
        level = max(section["cut_loss"], section["sl"])
        if tick.bid <= level:
            return (
                f"BUY: bid {tick.bid} is already at or below the "
                f"Cut Loss/SL level {level}."
            )
    else:
        level = min(section["cut_loss"], section["sl"])
        if tick.ask >= level:
            return (
                f"SELL: ask {tick.ask} is already at or above the "
                f"Cut Loss/SL level {level}."
            )
    return None


@dataclass(frozen=True)
class SideCheck:
    side: str
    market: int
    pending: int
    already_placed: int
    price: Decimal  # the price market layers deal at: ask for BUY, bid for SELL
    cut_loss: Decimal
    cut_loss_distance: Decimal  # measured the way the monitor measures it


@dataclass(frozen=True)
class PlanCheck:
    tick: Tick
    sides: tuple[SideCheck, ...]

    def describe(self) -> str:
        lines = [f"Live price: bid {self.tick.bid} / ask {self.tick.ask}"]
        for s in self.sides:
            label = "ask" if s.side == "BUY" else "bid"
            line = (
                f"{s.side}: {s.market} at market now ({label} {s.price}), "
                f"{s.pending} pending, Cut Loss {s.cut_loss} is "
                f"{s.cut_loss_distance} away"
            )
            if s.already_placed:
                line += f" ({s.already_placed} already placed)"
            lines.append(line)
        return "\n".join(lines)


def check_plan(
    plan: dict[str, Any],
    proposed: list[dict[str, Any]],
    tick: Tick,
    *,
    skip: Collection[int] = frozenset(),
    cap: int = MAX_MARKET_LAYERS_PER_SIDE,
) -> PlanCheck:
    """
    Classify the layers of `proposed` (build_plan_preview output) against
    `tick`. `skip` holds indexes of layers that already exist at the broker.
    Raises ValueError (nothing sent, nothing halted) if the plan is refused.
    """
    sides: list[SideCheck] = []
    for key in ("buy", "sell"):
        section = plan.get(key)
        if section is None:
            continue
        side = key.upper()
        breach = stop_breach(side, section, tick)
        if breach is not None:
            raise ValueError(f"Plan refused: {breach}")

        market = pending = placed = 0
        for index, item in enumerate(proposed):
            if item["side"] != side:
                continue
            if index in skip:
                placed += 1
            elif is_marketable(side, item["entry_price"], tick):
                market += 1
            else:
                pending += 1
        if market > cap:
            raise ValueError(
                f"Plan refused: {market} {side} layers would fill at market "
                f"now (limit {cap}). Price is already deep in the area; "
                "decide by hand."
            )
        if side == "BUY":
            price, distance = tick.ask, tick.bid - section["cut_loss"]
        else:
            price, distance = tick.bid, section["cut_loss"] - tick.ask
        sides.append(
            SideCheck(side, market, pending, placed, price,
                      section["cut_loss"], distance)
        )
    return PlanCheck(tick=tick, sides=tuple(sides))
