import re

import pytest

from actp import i18n


@pytest.fixture(autouse=True)
def back_to_english():
    yield
    i18n.set_language("en")


def test_english_is_untouched():
    i18n.set_language("en")
    assert i18n.tr("Send") == "Send"
    assert i18n.tr("NOT SENT: x") == "NOT SENT: x"


def test_indonesian_exact_and_unknown_text():
    i18n.set_language("id")
    assert i18n.tr("Send") == "Kirim"
    assert i18n.tr("some unknown text") == "some unknown text"
    assert i18n.tr(5) == 5


def test_placeholders_and_whitespace_are_kept():
    i18n.set_language("id")
    assert i18n.tr("Close 3 open ACTP position(s)?\n\nPending orders are NOT touched.") \
        .startswith("Tutup 3 posisi ACTP terbuka")
    assert "yang profit" in i18n.tr(
        "Close 2 profitable open ACTP position(s)?\n\nPending orders are NOT touched.")
    out = i18n.tr("      Use the legacy timer instead (hold time + SCL, no candles)")
    assert out.startswith("      Pakai timer lama")


def test_log_level_prefix_and_nested_text():
    i18n.set_language("id")
    assert i18n.tr(
        "ALERT: BUY candle closed beyond the Cut Loss: all BUY orders were cancelled/closed."
    ) == ("PERINGATAN: Candle BUY ditutup melewati Cut Loss: "
          "semua order BUY dibatalkan/ditutup.")
    assert i18n.tr("NOT SENT: Cancelled.") == "TIDAK TERKIRIM: Dibatalkan."


def test_every_entry_keeps_its_placeholders():
    for english, indonesian in i18n.ID.items():
        assert sorted(re.findall(r"\{(\w+)\}", english)) == sorted(
            re.findall(r"\{(\w+)\}", indonesian)), english


def test_unknown_language_falls_back_to_english():
    i18n.set_language("xx")
    assert i18n.language() == "en"
