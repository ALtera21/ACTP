"""Candle timeframes ACTP can use for the cut loss. These are the ones MT5
has between M1 and one hour (there is no M7, M8, M9, M11 or M45)."""

TIMEFRAME_SECONDS: dict[str, int] = {
    "M1": 60, "M2": 120, "M3": 180, "M4": 240, "M5": 300, "M6": 360,
    "M10": 600, "M12": 720, "M15": 900, "M20": 1200, "M30": 1800,
    "H1": 3600,
}
TIMEFRAMES = tuple(TIMEFRAME_SECONDS)
DEFAULT_TIMEFRAME = "M1"


def clock_text(seconds: int) -> str:
    """90 -> '01:30'."""
    seconds = max(0, int(seconds))
    return f"{seconds // 60:02d}:{seconds % 60:02d}"
