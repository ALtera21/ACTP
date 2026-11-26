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

from actp.confirm import defaults as _confirm_defaults
from actp.confirm import validate as _confirm_validate
from actp.timeframes import DEFAULT_TIMEFRAME, TIMEFRAMES

DEFAULTS: dict[str, Any] = {
    "symbol": "XAUUSDc",
    "lot_per_order": "0.01",
    "auto_cut_loss": True,
    "cut_loss_hold_seconds": 60,
    "scl_enabled": True,
    "scl_fraction": "0.15",
    "max_market_layers": 3,
    # "candle": cut when a completed candle of the chosen timeframe closes
    # beyond the Cut Loss. "legacy": the hold-timer rule (hold + SCL).
    "cut_loss_mode": "candle",
    "cut_loss_timeframe": DEFAULT_TIMEFRAME,
    "layer_amount": 6,
    # Manual Input: an empty Stop Loss box = this many times the range.
    "stop_loss_multiple": "3",
    # Saved MultiLayer: {"4": "1,1,1,2"}. Used when the MultiLayer box is
    # empty and the Layer amount is that number.
    "saved_multilayers": {},
    # Per action: True = normal OK / Cancel popup, False = type a 5-digit code.
    "confirm": _confirm_defaults(),
    # Window language: "en" (English) or "id" (Bahasa Indonesia).
    "language": "en",
    # "light" or "dark" (the sun / moon button on the main window).
    "theme": "light",
    # The Auto-TP tick box on the main window (remembered between runs).
    "auto_tp": True,
    # "Update TP also changes pending orders" tick box (main window).
    "tp_include_pending": True,
    # Auto-TP moves the TP once this much of a side is filled (percent).
    "auto_tp_percent": "100",
    # Auto-TP overrides a TP set by hand (button or dragged in MetaTrader).
    "auto_tp_priority": True,
    # Auto-remove: a plan side with no open position whose price already went
    # this far into its range loses its leftover pending orders.
    "auto_remove": False,
    "auto_remove_percent": "90",
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
        out["max_market_layers"], "Max layers at market", 0, 200
    )
    if out["cut_loss_mode"] not in ("candle", "legacy"):
        raise ValueError("Cut loss mode must be 'candle' or 'legacy'.")
    if out["cut_loss_timeframe"] not in TIMEFRAMES:
        raise ValueError(
            "Cut loss timeframe must be one of: " + ", ".join(TIMEFRAMES) + "."
        )
    multiple = _dec(out["stop_loss_multiple"], "Stop loss multiple")
    if not Decimal("0.1") <= multiple <= Decimal("100"):
        raise ValueError("Stop loss multiple must be between 0.1 and 100.")
    out["stop_loss_multiple"] = format(multiple.normalize(), "f")
    out["layer_amount"] = _int(out["layer_amount"], "Layer amount", 2, 200)
    out["saved_multilayers"] = _presets(out["saved_multilayers"])
    out["confirm"] = _confirm_validate(out["confirm"])
    if not isinstance(out["auto_tp"], bool):
        raise ValueError("Auto-TP must be true or false.")
    percent = _dec(out["auto_tp_percent"], "Auto-TP percent")
    if not 1 <= percent <= 100:
        raise ValueError("Auto-TP percent must be from 1 to 100.")
    out["auto_tp_percent"] = format(percent.normalize(), "f")
    if not isinstance(out["auto_remove"], bool):
        raise ValueError("Auto-remove must be true or false.")
    remove_pct = _dec(out["auto_remove_percent"], "Auto-remove percent")
    if not 1 <= remove_pct <= 100:
        raise ValueError("Auto-remove percent must be from 1 to 100.")
    out["auto_remove_percent"] = format(remove_pct.normalize(), "f")
    if not isinstance(out["auto_tp_priority"], bool):
        raise ValueError("The Auto-TP priority choice must be true or false.")
    if not isinstance(out["tp_include_pending"], bool):
        raise ValueError("The Update TP pending-orders choice must be true or false.")
    if out["theme"] not in ("light", "dark"):
        raise ValueError("Theme must be light or dark.")
    if out["language"] not in ("en", "id"):
        raise ValueError("Language must be English (en) or Bahasa Indonesia (id).")
    return out


def _presets(raw: Any) -> dict[str, str]:
    """{"layers": "w1,w2,..."} with one weight per layer."""
    from actp.layers import parse_weights  # local: layers has no settings import
    if not isinstance(raw, dict):
        raise ValueError("Saved MultiLayer must be a list of entries.")
    clean: dict[str, str] = {}
    for key, value in raw.items():
        if not isinstance(key, str) or not key.isdigit() or not 2 <= int(key) <= 200:
            raise ValueError(f"Saved MultiLayer: '{key}' is not a layer amount (2-200).")
        if not isinstance(value, str):
            raise ValueError(f"Saved MultiLayer for {key} layers must be text.")
        if not value.strip():
            continue  # empty = default 1x for every layer, nothing to store
        weights = parse_weights(value)
        if weights is None or len(weights) != int(key):
            raise ValueError(
                f"Saved MultiLayer for {key} layers needs exactly {key} numbers."
            )
        clean[str(int(key))] = ", ".join(format(w.normalize(), "f") for w in weights)
    return dict(sorted(clean.items(), key=lambda kv: int(kv[0])))


def update(path: str | Path, **changes: Any) -> dict[str, Any]:
    """Change some settings and save. Other values are kept."""
    values = load(path)
    values.update(changes)
    save(path, values)
    return values


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