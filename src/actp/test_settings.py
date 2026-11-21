import json
from decimal import Decimal

import pytest

from actp.settings import DEFAULTS, load, save, update, validate


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
                {"max_market_layers": 201}, {"max_market_layers": -1},
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

def test_cut_loss_mode_and_timeframe():
    assert DEFAULTS["cut_loss_mode"] == "candle"
    assert DEFAULTS["cut_loss_timeframe"] == "M1"
    ok = validate({"cut_loss_mode": "legacy", "cut_loss_timeframe": "H1"})
    assert ok["cut_loss_mode"] == "legacy" and ok["cut_loss_timeframe"] == "H1"
    for bad in ({"cut_loss_mode": "fast"}, {"cut_loss_timeframe": "M7"},
                {"cut_loss_timeframe": "M45"}, {"cut_loss_timeframe": 5}):
        with pytest.raises(ValueError):
            validate(bad)


def test_stop_loss_multiple():
    assert DEFAULTS["stop_loss_multiple"] == "3"
    assert validate({"stop_loss_multiple": "2.50"})["stop_loss_multiple"] == "2.5"
    assert validate({"stop_loss_multiple": 4})["stop_loss_multiple"] == "4"
    for bad in ("0", "0.05", "101", "abc", "NaN"):
        with pytest.raises(ValueError):
            validate({"stop_loss_multiple": bad})


def test_confirm_settings_round_trip_and_survive_update(tmp_path):
    path = tmp_path / "s.json"
    values = validate({"confirm": {"kill": False}})
    assert values["confirm"]["kill"] is False and values["confirm"]["send"] is True
    save(path, values)
    update(path, layer_amount=7)
    assert load(path)["confirm"]["kill"] is False
    with pytest.raises(ValueError):
        validate({"confirm": {"bogus": True}})


def test_language_setting():
    assert validate({})["language"] == "en"
    assert validate({"language": "id"})["language"] == "id"
    with pytest.raises(ValueError):
        validate({"language": "fr"})


def test_theme_setting(tmp_path):
    assert validate({})["theme"] == "light"
    assert validate({"theme": "dark"})["theme"] == "dark"
    with pytest.raises(ValueError):
        validate({"theme": "blue"})
    path = tmp_path / "s.json"
    save(path, validate({}))
    update(path, theme="dark")
    assert load(path)["theme"] == "dark"


def test_auto_tp_choice_is_saved_and_loaded(tmp_path):
    path = tmp_path / "settings.json"
    update(path, auto_tp=True)
    assert load(path)["auto_tp"] is True
    update(path, auto_tp=False)
    assert load(path)["auto_tp"] is False
