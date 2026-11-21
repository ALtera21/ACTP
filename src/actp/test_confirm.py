import pytest

from actp import confirm


def test_defaults_ask_for_everything():
    assert all(confirm.defaults().values())
    assert set(confirm.defaults()) == set(confirm.ACTIONS)


def test_validate_fills_missing_with_on_and_rejects_junk():
    assert confirm.validate(None) == confirm.defaults()
    assert confirm.validate({"kill": False})["kill"] is False
    assert confirm.validate({"kill": False})["send"] is True
    with pytest.raises(ValueError):
        confirm.validate({"nope": True})
    with pytest.raises(ValueError):
        confirm.validate({"kill": "no"})
    with pytest.raises(ValueError):
        confirm.validate([])


def test_parent_follows_children():
    assert confirm.parent_on(confirm.set_all(True))
    assert not confirm.parent_on(confirm.set_all(False))
    one = confirm.set_all(False)
    one["send"] = True
    assert confirm.parent_on(one)


def test_code_is_five_digits_and_checked_exactly():
    for _ in range(50):
        code = confirm.new_code()
        assert len(code) == 5 and code.isdigit()
    assert confirm.code_matches(" 01234 ", "01234")
    assert not confirm.code_matches("1234", "01234")
    assert not confirm.code_matches("", "01234")
