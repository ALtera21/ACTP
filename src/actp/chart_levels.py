"""
Hands the Cut Loss / SCL lines to MetaTrader 5.

The MT5 Python API cannot draw on a chart, so this writes a small text
file into MT5's Common\\Files folder and the EA in mql5/ActpLevels.mq5
draws the lines from it. One line per side:
    BUY,<cut_loss>,<scl>
An empty file means "draw nothing". The file is rewritten only when the
levels change.
"""

import os
from decimal import Decimal
from pathlib import Path


def format_levels(levels: dict[str, tuple[Decimal, Decimal]]) -> str:
    return "".join(
        f"{side},{cut_loss},{scl}\n"
        for side, (cut_loss, scl) in sorted(levels.items())
    )


class ChartLevelsWriter:
    def __init__(self, path: str | os.PathLike[str] | None) -> None:
        self._path = None if not path else Path(path)
        self._last: str | None = None
        self.error: str | None = None

    def sync(self, levels: dict[str, tuple[Decimal, Decimal]]) -> bool:
        """Write if changed. Never raises. True if the file is current."""
        if self._path is None:
            return False
        text = format_levels(levels)
        if text == self._last:
            return True
        try:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self._path.with_name(self._path.name + ".tmp")
            tmp.write_text(text, encoding="ascii")
            os.replace(tmp, self._path)
        except OSError as exc:
            self.error = f"{type(exc).__name__}: {exc}"
            return False  # retried on the next poll
        self._last = text
        self.error = None
        return True
