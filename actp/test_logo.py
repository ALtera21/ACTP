import pytest

from actp import logo


def test_default_logo_file_is_found():
    path = logo.find_file(logo.APP_ICON)
    assert path is not None and path.suffix == ".png"
    assert logo.find_file("no_such_logo.png") is None


def test_emoji_logo_is_drawn_as_a_square():
    PIL = pytest.importorskip("PIL")
    del PIL
    image = logo.render_text("A", 64)
    assert image.size == (64, 64)
    assert image.getbbox() is not None
