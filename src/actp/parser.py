
import re
from decimal import Decimal
from typing import Any

from actp.config import SOURCE_SYMBOL, SYMBOL

def _decimal(value: str, field_name: str) -> Decimal:
    try:
        result = Decimal(value)
    except Exception as exc:
        raise ValueError(f"Invalid numeric value: {field_name}") from exc

    if not result.is_finite():
        raise ValueError(f"Non-finite numeric value: {field_name}")

    return result


def _extract_one(
    text: str,
    pattern: str,
    field_name: str,
) -> Decimal:
    match = re.search(pattern, text, flags=re.IGNORECASE)
    if not match:
        raise ValueError(f"Missing or invalid field: {field_name}")
    return _decimal(match.group(1), field_name)


def parse_plan(message: str) -> dict[str, Any]:
    """Parse a PLAN containing BUY, SELL, or both sides."""
    if not re.search(
        r"\bPLAN\s+XAUUSDr\b",
        message,
        flags=re.IGNORECASE,
    ):
        raise ValueError("Message is not a recognized XAUUSDr PLAN.")

    headers = list(
        re.finditer(
            r"^[^\S\r\n]*.*?\b(BUY|SELL)\s+LIMIT\s*\(([^)]*)\)\s*$",
            message,
            flags=re.IGNORECASE | re.MULTILINE,
        )
    )

    if not headers:
        raise ValueError("PLAN must contain at least one BUY LIMIT or SELL LIMIT section.")

    sides: dict[str, Any] = {"buy": None, "sell": None}

    for index, header in enumerate(headers):
        side = header.group(1).lower()

        if sides[side] is not None:
            raise ValueError(f"Duplicate {side.upper()} LIMIT section.")

        metadata = header.group(2)
        layers_match = re.search(
            r"(\d+)\s*layer\b",
            metadata,
            flags=re.IGNORECASE,
        )
        if not layers_match:
            raise ValueError(f"Missing or invalid {side.upper()} layer count.")

        layers = int(layers_match.group(1))
        if layers < 2:
            raise ValueError("Each side must contain at least two layers.")

        section_end = (
            headers[index + 1].start()
            if index + 1 < len(headers)
            else len(message)
        )
        section = message[header.end():section_end]

        area_match = re.search(
            r"\bArea:\s*(\d+(?:\.\d+)?)\s*-\s*(\d+(?:\.\d+)?)",
            section,
            flags=re.IGNORECASE,
        )
        if not area_match:
            raise ValueError(f"Missing or invalid field: {side.upper()} Area")

        area_start = _decimal(area_match.group(1), f"{side} area start")
        area_end = _decimal(area_match.group(2), f"{side} area end")

        if area_start == area_end:
            raise ValueError(f"{side.upper()} entry range must have positive width.")

        cut_loss = _extract_one(
            section,
            r"\bCut Loss:\s*(\d+(?:\.\d+)?)",
            f"{side} Cut Loss",
        )

        sl_match = re.search(
            r"\bSL:\s*(\d+(?:\.\d+)?)\s*\|\s*TP:\s*(\d+(?:\.\d+)?)[ \t]*$",
            section,
            flags=re.IGNORECASE | re.MULTILINE,
        )
        if not sl_match:
            raise ValueError(f"Missing or invalid field: {side.upper()} SL/TP")

        sl = _decimal(sl_match.group(1), f"{side} SL")
        tp = _decimal(sl_match.group(2), f"{side} TP")

        sides[side] = {
            "layers": layers,
            "area_start": area_start,
            "area_end": area_end,
            "cut_loss": cut_loss,
            "sl": sl,
            "tp": tp,
        }

    return {
        "type": "PLAN",
        "symbol": SYMBOL,
        "source_symbol": SOURCE_SYMBOL,
        "buy": sides["buy"],
        "sell": sides["sell"],
    }


def parse_message(message: str) -> dict[str, Any]:
    """Classify and parse a supported WhatsApp trading message."""
    text = message.strip()

    if re.search(r"\bPLAN\s+XAUUSDr\b", text, re.IGNORECASE):
        return parse_plan(text)

    update_header = re.search(
        r"🔄\s*UPDATE\s+XAUUSDr\s+(BUY|SELL)\b",
        text,
        flags=re.IGNORECASE,
    )
    if update_header:
        side = update_header.group(1).upper()
        changes: dict[str, Decimal] = {}

        for field in ("SL", "TP"):
            match = re.search(
                rf"^\s*{field}:\s*(\d+(?:\.\d+)?)\s*"
                rf"(?:➜|→|->)\s*(\d+(?:\.\d+)?)\s*$",
                text,
                flags=re.IGNORECASE | re.MULTILINE,
            )
            if match:
                # The value after the arrow is the requested new value.
                changes[field.lower()] = _decimal(
                    match.group(2),
                    f"new {field}",
                )

        if not changes:
            raise ValueError("UPDATE must contain a valid SL and/or TP change.")

        return {
            "type": "UPDATE",
            "symbol": SYMBOL,
            "source_symbol": SOURCE_SYMBOL,
            "side": side,
            "changes": changes,
        }

    close_match = re.search(
        r"\bCLOSE\s+XAUUSDr\s+(BUY|SELL)\b",
        text,
        flags=re.IGNORECASE,
    )
    if close_match:
        return {
            "type": "CLOSE",
            "symbol": SYMBOL,
            "source_symbol": SOURCE_SYMBOL,
            "side": close_match.group(1).upper(),
        }

    fill_match = re.search(
        r"\bXAUUSDr\s+(BUY|SELL)\s*-\s*Entry\s+ter-fill\b",
        text,
        flags=re.IGNORECASE,
    )
    if fill_match:
        return {
            "type": "FILL_CONFIRMATION",
            "symbol": SYMBOL,
            "source_symbol": SOURCE_SYMBOL,
            "side": fill_match.group(1).upper(),
            "action": "RECONCILE_WITH_MT5",
        }

    raise ValueError("Unrecognized or unsupported WhatsApp message.")
