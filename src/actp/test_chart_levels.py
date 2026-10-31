from decimal import Decimal

from actp.chart_levels import ChartLevelsWriter, format_levels

LEVELS = {"SELL": (Decimal("4127.05"), Decimal("4128.47")),
          "BUY": (Decimal("4116.98"), Decimal("4115.56"))}


def test_format_is_sorted_one_line_per_side():
    assert format_levels(LEVELS) == (
        "BUY,4116.98,4115.56\nSELL,4127.05,4128.47\n"
    )
    assert format_levels({}) == ""


def test_writes_only_on_change(tmp_path):
    path = tmp_path / "sub" / "levels.txt"
    w = ChartLevelsWriter(path)
    assert w.sync(LEVELS)
    first = path.stat().st_mtime_ns
    assert w.sync(LEVELS)
    assert path.stat().st_mtime_ns == first
    assert w.sync({})
    assert path.read_text() == ""


def test_disabled_and_failure_never_raise(tmp_path):
    assert ChartLevelsWriter("").sync(LEVELS) is False
    blocker = tmp_path / "file"
    blocker.write_text("x")
    w = ChartLevelsWriter(blocker / "levels.txt")  # parent is a file
    assert w.sync(LEVELS) is False and w.error
