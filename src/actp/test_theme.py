import pytest

from actp import theme


def test_both_palettes_have_the_same_keys():
    assert set(theme.PALETTES["light"]) == set(theme.PALETTES["dark"])
    assert theme.palette("nope") == theme.PALETTES["light"]


@pytest.mark.parametrize("name", theme.THEMES)
def test_timer_colour_goes_green_to_yellow_to_red(name):
    p = theme.PALETTES[name]
    assert theme.timer_colour(120, 120, name) == p["t_full"]
    assert theme.timer_colour(60, 120, name) == p["t_mid"]
    assert theme.timer_colour(0, 120, name) == p["t_low"]
    # between the stops it is a mix, and out-of-range input is clamped
    assert theme.timer_colour(90, 120, name) not in (p["t_full"], p["t_mid"])
    assert theme.timer_colour(500, 120, name) == p["t_full"]
    assert theme.timer_colour(-5, 120, name) == p["t_low"]
    assert theme.timer_colour(5, 0, name) == p["t_full"]
