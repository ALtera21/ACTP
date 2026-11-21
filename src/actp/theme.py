"""Light / dark colours, and the colour of the candle countdown.

Pure logic (no tkinter) so it can be tested.
"""

from __future__ import annotations

THEMES = ("light", "dark")
DEFAULT_THEME = "light"

# Light = the normal system look; only the colours ACTP sets itself are here.
PALETTES: dict[str, dict[str, str]] = {
    "light": {
        "bg": "", "fg": "black", "entry": "white", "button": "",
        "button_hover": "", "border": "#9aa0a6", "grey": "#37474f",
        "muted": "#5f6368", "green_text": "#1b5e20", "red_text": "#b00020",
        "orange": "#e65100", "danger_bg": "#fde8e8", "head_bg": "#e8eaed",
        "tab_accent": "#1a73e8", "tab_idle_bg": "#cfd3d9",
        "tab_idle_fg": "#5f6368", "tab_hover_bg": "#e3e6ea",
        "plan_btn": "#263238",
        "t_full": "#2e7d32", "t_mid": "#f9a825", "t_low": "#d32f2f",
    },
    "dark": {
        "bg": "#1e1f22", "fg": "#e6e6e6", "entry": "#2b2d31",
        "button": "#3a3d42", "button_hover": "#4a4d54", "border": "#5f6368",
        "grey": "#b0bec5", "muted": "#9aa0a6", "green_text": "#81c784",
        "red_text": "#ff8a80", "orange": "#ffb74d", "danger_bg": "#3b1f22",
        "head_bg": "#2b2d31", "tab_accent": "#8ab4f8",
        "tab_idle_bg": "#2b2d31", "tab_idle_fg": "#9aa0a6",
        "tab_hover_bg": "#35373c", "plan_btn": "#78909c",
        "t_full": "#66bb6a", "t_mid": "#ffd54f", "t_low": "#ff5252",
    },
}


def palette(name: str) -> dict[str, str]:
    return PALETTES.get(name, PALETTES[DEFAULT_THEME])


def _mix(a: str, b: str, amount: float) -> str:
    """Colour between a (amount 0) and b (amount 1); '#rrggbb' texts."""
    ra, ga, ba = (int(a[i:i + 2], 16) for i in (1, 3, 5))
    rb, gb, bb = (int(b[i:i + 2], 16) for i in (1, 3, 5))
    return "#{:02x}{:02x}{:02x}".format(
        round(ra + (rb - ra) * amount),
        round(ga + (gb - ga) * amount),
        round(ba + (bb - ba) * amount),
    )


def timer_colour(left: float, total: float, name: str) -> str:
    """Candle countdown colour: green when the candle is new, gradually
    yellow in the middle, red when it is about to close."""
    colours = palette(name)
    if total <= 0:
        return colours["t_full"]
    share = max(0.0, min(1.0, left / total))
    if share >= 0.5:
        return _mix(colours["t_mid"], colours["t_full"], (share - 0.5) * 2)
    return _mix(colours["t_low"], colours["t_mid"], share * 2)
