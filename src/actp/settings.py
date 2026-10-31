"""
User settings, stored as JSON in the app folder.

Pure logic (no tkinter, no MT5). config.py reads the values once at start-up;
the settings window writes the file and the app restarts to apply it.
"""
import json
import os
import re
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any

DEFAULTS: dict[str, Any] = {
    "symbol": "XAUUSDc",
    "lot_per_order": "0.01",
    "auto_cut_loss": True,
    "cut_loss_hold_seconds": 60,
    "scl_enabled": True,
    "scl_fraction": "0.15",
    "max_market_layers": 3,
}

_SYMBOL = re.compile(r"[A-Za-z0-9._#+\-]{3,24}")


def _dec(value: Any, name: str) -> Decimal:
    try:
        result = Decimal(str(value))
    except InvalidOperation as exc:
        raise ValueError(f"{name} must be a number.") from exc
    if not result.is_finite():
        raise ValueError(f"{name} must be a number.")
    return result


def _int(value: Any, name: str, low: int, high: int) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError(f"{name} must be a whole number.")
    if not low <= value <= high:
        raise ValueError(f"{name} must be between {low} and {high}.")
    return value


def validate(raw: Any) -> dict[str, Any]:
    """Return a clean copy of the settings, or raise ValueError."""
    if not isinstance(raw, dict):
        raise ValueError("Settings must be a JSON object.")
    out = dict(DEFAULTS)
    out.update({key: raw[key] for key in DEFAULTS if key in raw})

    symbol = out["symbol"]
    if not isinstance(symbol, str):
        raise ValueError("Symbol must be text.")
    symbol = symbol.strip()
    if not _SYMBOL.fullmatch(symbol) or "XAUUSD" not in symbol.upper():
        raise ValueError(
            "Symbol must be an XAUUSD variant, for example XAUUSD, "
            "XAUUSDc, XAUUSDm or XAUUSD.r."
        )
    out["symbol"] = symbol

    lot = _dec(out["lot_per_order"], "Lot per order")
    cent = Decimal("0.01")
    if not Decimal("0.01") <= lot <= Decimal("10") or lot % cent != 0:
        raise ValueError(
            "Lot per order must be between 0.01 and 10, in steps of 0.01."
        )
    out["lot_per_order"] = format(lot.quantize(cent), "f")

    for key, label in (("auto_cut_loss", "Auto cut loss"),
                       ("scl_enabled", "SCL")):
        if not isinstance(out[key], bool):
            raise ValueError(f"{label} must be true or false.")

    out["cut_loss_hold_seconds"] = _int(
        out["cut_loss_hold_seconds"], "Hold time", 10, 3600
    )
    fraction = _dec(out["scl_fraction"], "SCL distance")
    if not Decimal("0.05") <= fraction <= Decimal("0.95"):
        raise ValueError("SCL distance must be between 5% and 95%.")
    out["scl_fraction"] = str(fraction)
    out["max_market_layers"] = _int(
        out["max_market_layers"], "Max layers at market", 0, 12
    )
    return out


def load(path: str | Path) -> dict[str, Any]:
    """Defaults if the file is missing; ValueError if it is unreadable."""
    path = Path(path)
    if not path.exists():
        return validate({})
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise ValueError(f"cannot read {path}: {exc}") from exc
    return validate(raw)


def save(path: str | Path, values: dict[str, Any]) -> None:
    clean = validate(values)
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(path.name + ".tmp")
    with open(temp, "w", encoding="utf-8") as handle:
        handle.write(json.dumps(clean, indent=2))
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(temp, path)