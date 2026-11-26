"""
Parser tests driven by real WhatsApp messages.

Layout (next to this file):
    fixtures/whatsapp/accept_<name>.txt    raw message, pasted exactly as received
    fixtures/whatsapp/accept_<name>.json   expected parse result (subset match)
    fixtures/whatsapp/reject_<name>.txt    raw message that must raise ValueError

Expected JSON: write prices as strings, e.g.
    {"type": "PLAN", "buy": {"layers": 6, "cut_loss": "4116.98"}}
Only the keys you list are compared, so you can check as much or as little
as you like.
"""
import json
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest

from actp.parser import parse_message
from actp.plan_builder import build_plan_preview
from actp.validator import validate_plan

FIXTURES = Path(__file__).parent / "fixtures" / "whatsapp"


def _files(prefix: str) -> list[Path]:
    return sorted(FIXTURES.glob(f"{prefix}_*.txt"))


def _plain(value: Any) -> Any:
    """Turn parser output into JSON-comparable data (Decimal -> str)."""
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, dict):
        return {k: _plain(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_plain(v) for v in value]
    return value


def _assert_subset(actual: Any, expected: Any, where: str = "result") -> None:
    if isinstance(expected, dict):
        assert isinstance(actual, dict), f"{where}: expected an object"
        for key, value in expected.items():
            assert key in actual, f"{where}: missing key {key!r}"
            _assert_subset(actual[key], value, f"{where}.{key}")
    else:
        assert actual == expected, f"{where}: {actual!r} != {expected!r}"


@pytest.mark.parametrize("path", _files("accept"), ids=lambda p: p.stem)
def test_real_message_parses_as_expected(path: Path) -> None:
    raw = path.read_text(encoding="utf-8")
    expected_path = path.with_suffix(".json")
    assert expected_path.exists(), f"Missing expected file: {expected_path.name}"
    expected = json.loads(expected_path.read_text(encoding="utf-8"))

    parsed = parse_message(raw)

    _assert_subset(_plain(parsed), expected)

    if parsed["type"] == "PLAN":
        # Anything the parser accepts must also survive validation and
        # produce the right number of orders.
        validate_plan(parsed)
        layers = sum(
            parsed[side]["layers"]
            for side in ("buy", "sell")
            if parsed[side] is not None
        )
        assert len(build_plan_preview(parsed)) == layers


@pytest.mark.parametrize("path", _files("reject"), ids=lambda p: p.stem)
def test_real_message_is_rejected(path: Path) -> None:
    raw = path.read_text(encoding="utf-8")
    with pytest.raises(ValueError):
        parse_message(raw)


def test_every_accept_fixture_has_an_expected_file() -> None:
    for path in _files("accept"):
        assert path.with_suffix(".json").exists(), path.name
