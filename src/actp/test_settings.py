import json
from decimal import Decimal

import pytest

from actp.settings import DEFAULTS, load, save, validate


def test_defaults_are_valid():
    assert validate({}) == DEFAULTS


@pytest.mark.parametrize("symbol", [
    "XAUUSD", "XAUUSDc", "XAUUSDm", "XAUUSD.r", "xauusd#", "XAUUSD_i",
])
def test_xauusd_variants_are_accepted(symbol):
    assert validate({"symbol": symbol})["symbol"] == symbol


@pytest.mark.parametrize("symbol", ["EURUSD", "", "XAU", "XAUUSD; x", 5])
def test_other_symbols_are_rejected(symbol):
    with pytest.raises(ValueError):
        validate({"symbol": symbol})


@pytest.mark.parametrize("lot", ["0", "0.005", "0.015", "11", "abc", "NaN"])
def test_bad_lots_are_rejected(lot):
    with pytest.raises(ValueError):
        validate({"lot_per_order": lot})


def test_lot_is_normalised():
    assert validate({"lot_per_order": "0.10"})["lot_per_order"] == "0.10"
    assert validate({"lot_per_order": 1})["lot_per_order"] == "1.00"


def test_hold_fraction_and_cap_ranges():
    for bad in ({"cut_loss_hold_seconds": 5},
                {"cut_loss_hold_seconds": True},
                {"cut_loss_hold_seconds": "60"},
                {"scl_fraction": "0.01"}, {"scl_fraction": "1"},
                {"max_market_layers": 13}, {"max_market_layers": -1},
                {"auto_cut_loss": "yes"}, {"scl_enabled": 1}):
        with pytest.raises(ValueError):
            validate(bad)
    ok = validate({"cut_loss_hold_seconds": 120, "scl_fraction": "0.5",
                   "max_market_layers": 0})
    assert ok["cut_loss_hold_seconds"] == 120


def test_save_and_load_round_trip(tmp_path):
    path = tmp_path / "sub" / "settings.json"
    save(path, {"symbol": "XAUUSDm", "auto_cut_loss": False})
    loaded = load(path)
    assert loaded["symbol"] == "XAUUSDm" and loaded["auto_cut_loss"] is False
    assert not list(path.parent.glob("*.tmp"))


def test_missing_file_gives_defaults_and_corrupt_file_raises(tmp_path):
    assert load(tmp_path / "none.json") == DEFAULTS
    bad = tmp_path / "bad.json"
    bad.write_text("{not json")
    with pytest.raises(ValueError):
        load(bad)
    bad.write_text(json.dumps({"symbol": "EURUSD"}))
    with pytest.raises(ValueError):
        load(bad)